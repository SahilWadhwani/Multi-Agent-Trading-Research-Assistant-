"""
Broker-backed exits: SL / smart-exit / EOD square-off via Upstox SELL orders.
Paper/shadow closes locally only (no broker).

Live mode: local book is updated only after a confirmed broker fill (or paper path).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pytz

from brain.smart_exit import should_exit
from brain.position_tracker import get_position_tracker
from execution import order_tracker, runtime_safety
from execution.risk_runtime import log_risk_audit

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

_KILL_SWITCH_CYCLE_COMPLETE = False

EXIT_FILL_TIMEOUT_S = float(os.getenv("EXIT_FILL_TIMEOUT_S", "45"))
EXIT_FILL_RETRY_EXTRA_S = float(os.getenv("EXIT_FILL_RETRY_EXTRA_S", "60"))

_GTT_TERMINAL = {"COMPLETED", "TRIGGERED", "CANCELLED", "EXPIRED", "FAILED"}


def _is_fo_row(row: Dict[str, Any]) -> bool:
    seg = str(row.get("segment") or row.get("exchange") or "").upper()
    if "FO" in seg or "NFO" in seg or "BFO" in seg:
        return True
    sym = str(row.get("tradingsymbol") or row.get("trading_symbol") or row.get("symbol") or "")
    return sym.endswith("CE") or sym.endswith("PE")


def _signed_net_qty(row: Dict[str, Any]) -> int:
    for key in ("net_quantity", "quantity_net", "quantity", "day_buy_quantity"):
        v = row.get(key)
        if v is not None:
            try:
                return int(round(float(v)))
            except (TypeError, ValueError):
                continue
    return 0


def _instrument_key_row(row: Dict[str, Any]) -> Optional[str]:
    for key in ("instrument_key", "instrument_token"):
        v = row.get(key)
        if v:
            return str(v).strip()
    return None


def broker_fo_net_by_instrument(client) -> Dict[str, int]:
    """Signed net qty per instrument_key (+ = long)."""
    out: Dict[str, int] = {}
    pos_resp = client.get_positions()
    if pos_resp.get("status") != "success":
        raise RuntimeError(str(pos_resp.get("message") or pos_resp.get("errors") or "positions_failed"))
    rows = pos_resp.get("data")
    if isinstance(rows, dict) and "positions" in rows:
        rows = rows.get("positions") or []
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not _is_fo_row(r):
            continue
        q = _signed_net_qty(r)
        if q == 0:
            continue
        ik = _instrument_key_row(r)
        if ik:
            out[ik] = out.get(ik, 0) + q
    return out


def _eod_square_off_now() -> bool:
    """Force square-off from 15:15 IST onward."""
    now = datetime.now(IST)
    return now.hour > 15 or (now.hour == 15 and now.minute >= 15)


def _eod_critical_zone() -> bool:
    """After 15:20, square-off is CRITICAL — broker closes at 15:30."""
    now = datetime.now(IST)
    return now.hour > 15 or (now.hour == 15 and now.minute >= 20)


def check_and_exit_positions(use_broker_exits: Optional[bool] = None) -> List[Dict[str, Any]]:
    """
    Evaluate SL/target/smart/EOD for open positions.
    When use_broker_exits is True (live modes), place MARKET SELL and confirm fill.
    Returns list of closed trade summaries.
    """
    global _KILL_SWITCH_CYCLE_COMPLETE
    if not runtime_safety.kill_switch_active():
        _KILL_SWITCH_CYCLE_COMPLETE = False

    tracker = get_position_tracker()
    tracker.sync_from_decision_log()
    tracker.refresh_open_metrics_only()

    mode = runtime_safety.load_trading_mode()
    if use_broker_exits is None:
        use_broker_exits = mode in (
            runtime_safety.TradingMode.MICRO_LIVE,
            runtime_safety.TradingMode.LIVE,
        )

    client = None
    broker_ready = False
    if use_broker_exits:
        try:
            from database.operations import is_token_valid
            from mcp_server.upstox_client import get_upstox_client

            if is_token_valid():
                client = get_upstox_client()
                broker_ready = bool(client.is_authenticated())
        except Exception as e:
            logger.warning("Broker exit skipped (client init): %s", e)

    now = datetime.now(IST)

    # Kill switch: flatten at broker when possible; sync local only if broker F&O is flat
    if runtime_safety.kill_switch_active():
        if _KILL_SWITCH_CYCLE_COMPLETE:
            return []
        closed_ks, done = _handle_kill_switch(
            tracker=tracker,
            use_broker_exits=use_broker_exits,
            broker_ready=broker_ready,
            client=client,
            ist_now=now,
        )
        if done:
            _KILL_SWITCH_CYCLE_COMPLETE = True
        return closed_ks

    positions = tracker.get_open_positions()
    closed: List[Dict[str, Any]] = []

    # Phase G: fetch ATR once per exit cycle (cached 5 min, cheap)
    _atr_cache: Dict[str, float] = {}
    try:
        from data_feeds.fo_data_feed import get_fo_data_feed
        _fo = get_fo_data_feed()
        for _sym in set(p.symbol for p in positions):
            atr_data = _fo.get_spot_atr(_sym)
            if not atr_data.get("error"):
                _atr_cache[_sym] = atr_data["atr_pct"]
    except Exception:
        pass

    for pos in positions:
        current_price = tracker.estimate_current_price(pos)
        pnl_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
        highest = max(pos.highest_pnl_pct or 0, pnl_pct)

        # Phase G: compute time context for dynamic exits
        hours_held = 0.0
        if pos.entry_time:
            try:
                delta = datetime.now(IST) - pos.entry_time
                hours_held = delta.total_seconds() / 3600
            except Exception:
                pass
        current_hour = now.hour
        atr_pct = _atr_cache.get(pos.symbol, 0.0)

        exit_now, exit_reason = should_exit(
            pnl_pct, highest,
            hours_held=hours_held,
            current_hour_ist=current_hour,
            atr_pct=atr_pct,
        )
        if _eod_square_off_now():
            exit_now = True
            exit_reason = "eod_square_off_15_15"

        if not exit_now:
            continue

        if use_broker_exits:
            if not broker_ready or client is None:
                log_risk_audit(
                    "exit_deferred_no_broker",
                    {
                        "decision_id": pos.decision_id,
                        "symbol": pos.symbol,
                        "reason": exit_reason,
                    },
                )
                continue

            summary = exit_position_via_broker_safely(
                tracker=tracker,
                pos=pos,
                exit_reason=exit_reason,
                client=client,
                mode=mode.value,
            )
            if summary:
                closed.append(summary)
            continue

        rec = tracker.close_position_record(
            pos,
            exit_price=current_price,
            exit_reason=exit_reason,
            ist_now=now,
        )
        closed.append(rec)

    # EOD Critical Zone: if positions are STILL open after 15:20, log loud warnings
    if _eod_critical_zone() and use_broker_exits:
        remaining = tracker.get_open_positions()
        if remaining:
            msg = f"EOD CRITICAL: {len(remaining)} positions STILL OPEN after 15:20!"
            logger.critical(msg)
            log_risk_audit("eod_critical_positions_open", {
                "count": len(remaining),
                "positions": [p.decision_id for p in remaining],
            })
            print(f"\n{'!'*60}")
            print(f"   {msg}")
            print(f"   Manual intervention may be needed!")
            print(f"{'!'*60}\n")

    return closed


def _handle_kill_switch(
    *,
    tracker,
    use_broker_exits: bool,
    broker_ready: bool,
    client,
    ist_now: datetime,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Returns (closed_summaries, cycle_complete).
    cycle_complete True when kill-switch handling finished successfully (or paper path).
    """
    if not use_broker_exits:
        closed: List[Dict[str, Any]] = []
        for pos in tracker.get_open_positions():
            px = tracker.estimate_current_price(pos)
            closed.append(
                tracker.close_position_record(
                    pos,
                    exit_price=px,
                    exit_reason="kill_switch_paper_close",
                    ist_now=ist_now,
                )
            )
        return closed, True

    if not broker_ready or client is None:
        runtime_safety.set_trading_freeze(
            "Kill switch: cannot reach broker to flatten — manual intervention required",
            source="exit_manager",
        )
        log_risk_audit("kill_switch_no_broker", {})
        return [], False

    # Cancel all outstanding protective GTTs before emergency flatten
    _cancel_all_gtts_for_positions(client, tracker, tracker.get_open_positions())

    flatten_out = emergency_flatten_open_fo_positions(client=client)
    if not flatten_out.get("ok"):
        runtime_safety.set_trading_freeze(
            f"Kill switch: emergency flatten incomplete: {flatten_out.get('errors') or flatten_out.get('results')}",
            source="exit_manager",
        )
        log_risk_audit("kill_switch_flatten_failed", flatten_out)
        return [], False

    try:
        remaining = broker_fo_net_by_instrument(client)
    except Exception as e:
        runtime_safety.set_trading_freeze(
            f"Kill switch: could not verify broker flat: {e}",
            source="exit_manager",
        )
        return [], False

    if any(q != 0 for q in remaining.values()):
        runtime_safety.set_trading_freeze(
            f"Kill switch: broker still has F&O positions after flatten: {remaining}",
            source="exit_manager",
        )
        log_risk_audit("kill_switch_broker_not_flat", {"remaining": remaining})
        return [], False

    closed_kill: List[Dict[str, Any]] = []
    for pos in tracker.get_open_positions():
        px = tracker.estimate_current_price(pos)
        closed_kill.append(
            tracker.close_position_record(
                pos,
                exit_price=px,
                exit_reason="kill_switch_emergency_flatten_verified",
                ist_now=ist_now,
            )
        )
    return closed_kill, True


class GTTCancelFailed(RuntimeError):
    """Raised when a GTT cancel cannot be verified — SELL must not proceed."""
    pass


def _cancel_one_gtt(client, gtt_id: str) -> Optional[Dict[str, Any]]:
    """
    Cancel a single GTT and VERIFY it is no longer active.

    Returns its rule summary if it already triggered/completed
    (so the caller knows the broker already sold).
    Returns None on verified successful cancel.
    Raises GTTCancelFailed if cancel cannot be confirmed.
    """
    try:
        info = client.gtt_rule_status(gtt_id)
        if not info.get("ok"):
            # Status unknown — GTT may still be active. Try cancel + verify.
            try:
                client.cancel_gtt_order(gtt_id)
            except Exception:
                pass
            import time as _t
            _t.sleep(0.5)
            try:
                recheck = client.gtt_rule_status(gtt_id)
                if recheck.get("ok"):
                    for rule in recheck.get("rules", []):
                        rst = (rule.get("status") or "").upper()
                        if rst in _GTT_TERMINAL:
                            return rule if rst in ("COMPLETED", "TRIGGERED") else None
                    raise GTTCancelFailed(f"GTT {gtt_id} status unknown after cancel (still active?)")
            except GTTCancelFailed:
                raise
            except Exception:
                pass
            # If we still can't verify, block the SELL
            raise GTTCancelFailed(f"GTT {gtt_id} status unverifiable (ok=False, cancel attempted)")
        for rule in info.get("rules", []):
            st = (rule.get("status") or "").upper()
            if st in ("COMPLETED", "TRIGGERED"):
                return rule
            if st in ("CANCELLED", "EXPIRED", "FAILED"):
                return None
        # Still SCHEDULED / OPEN — cancel it
        client.cancel_gtt_order(gtt_id)

        # Verify cancellation succeeded by re-checking status
        import time
        time.sleep(0.5)
        try:
            verify = client.gtt_rule_status(gtt_id)
            if verify.get("ok"):
                for rule in verify.get("rules", []):
                    vst = (rule.get("status") or "").upper()
                    if vst in _GTT_TERMINAL:
                        return rule if vst in ("COMPLETED", "TRIGGERED") else None
                # Still active after cancel — unsafe to sell
                raise GTTCancelFailed(f"GTT {gtt_id} still active after cancel attempt")
            # ok=False after cancel — status unknown, GTT may still be live
            raise GTTCancelFailed(
                f"GTT {gtt_id} verification returned ok=False after cancel; "
                f"cannot confirm GTT is dead — blocking SELL"
            )
        except GTTCancelFailed:
            raise
        except Exception as verify_ex:
            # Verification itself failed — cannot confirm GTT is dead
            raise GTTCancelFailed(f"GTT {gtt_id} cancel verification failed: {verify_ex}")

    except GTTCancelFailed:
        raise
    except Exception as ex:
        log_risk_audit("gtt_cancel_error", {"gtt_id": gtt_id, "error": str(ex)})
        try:
            client.cancel_gtt_order(gtt_id)
        except Exception:
            pass
        raise GTTCancelFailed(f"GTT {gtt_id} cancel unverified: {ex}")


def _handle_gtt_before_exit(
    client, tracker, pos
) -> Optional[Dict[str, Any]]:
    """
    Cancel the protective SL GTT before an agent-driven exit.

    Only one SL GTT exists per position (no target GTT — avoids double-sell).
    If the SL GTT already triggered and the broker sold, return a summary so
    the caller skips placing its own SELL.
    Otherwise cancel the GTT and return None (caller proceeds with SELL).
    """
    sl_id = pos.gtt_sl_order_id
    if not sl_id:
        return None

    try:
        triggered_rule = _cancel_one_gtt(client, sl_id)
    except GTTCancelFailed as e:
        log_risk_audit("gtt_cancel_blocked_sell", {
            "decision_id": pos.decision_id, "gtt_id": sl_id, "error": str(e),
        })
        logger.error("SELL BLOCKED — GTT cancel unverified for %s: %s", pos.decision_id, e)
        return "BLOCKED"

    # Clear GTT ID from tracker only after verified cancel or terminal state
    try:
        tracker.clear_gtt_ids(pos.decision_id)
    except Exception:
        pass

    if triggered_rule:
        child_order_id = triggered_rule.get("order_id")
        exit_px = 0.0
        if child_order_id:
            try:
                fill = client.wait_for_fill(str(child_order_id), timeout_s=30.0, poll_s=2.0)
                exit_px = float(fill.get("average_price") or 0)
            except Exception:
                pass
        if exit_px <= 0:
            exit_px = float(triggered_rule.get("trigger_price") or 0)
        log_risk_audit("gtt_sl_triggered", {
            "decision_id": pos.decision_id,
            "order_id": child_order_id,
            "exit_price": exit_px,
        })
        return {
            "already_exited": True,
            "exit_price": exit_px,
            "order_id": child_order_id,
            "reason": "gtt_sl_triggered",
        }

    return None


def _cancel_all_gtts_for_positions(client, tracker, positions) -> None:
    """Best-effort cancel all SL GTTs across positions (kill switch / EOD)."""
    for pos in positions:
        sl_id = pos.gtt_sl_order_id
        if sl_id:
            try:
                client.cancel_gtt_order(sl_id)
            except Exception:
                pass
            try:
                tracker.clear_gtt_ids(pos.decision_id)
            except Exception:
                pass


def _exit_via_broker(
    *,
    tracker,
    pos,
    qty: int,
    exit_reason: str,
    client,
    mode: str,
) -> Optional[Dict[str, Any]]:
    """Place FO SELL; only close local state on confirmed full fill."""
    instrument_key = str(pos.instrument_key)
    last_err = None
    for attempt in range(2):
        resp = client.place_fo_order(
            instrument_token=instrument_key,
            transaction_type="SELL",
            quantity=qty,
            order_type="MARKET",
            product="I",
        )
        ok = resp.get("status") == "success"
        oid = None
        if isinstance(resp.get("data"), dict):
            oid = resp["data"].get("order_id")
        intent_id = order_tracker.log_intent(
            decision_id=pos.decision_id,
            symbol=pos.symbol,
            instrument_key=instrument_key,
            transaction_type="SELL",
            quantity=qty,
            product="I",
            mode=mode,
            status="PLACED" if ok else "REJECTED",
            broker_order_id=str(oid) if oid else None,
            broker_response=resp,
            error=None if ok else json.dumps(resp)[:2000],
        )
        if not ok or not oid:
            last_err = resp
            log_risk_audit(
                "exit_sell_rejected",
                {"decision_id": pos.decision_id, "resp": resp, "attempt": attempt},
            )
            continue

        fill = client.wait_for_fill(str(oid), timeout_s=EXIT_FILL_TIMEOUT_S, poll_s=2.0)
        if fill.get("normalized") != "complete" or fill.get("timed_out"):
            fill = client.wait_for_fill(
                str(oid), timeout_s=EXIT_FILL_RETRY_EXTRA_S, poll_s=2.0
            )

        exit_px = float(fill.get("average_price") or 0)
        filled_qty = int(fill.get("filled_quantity") or 0)
        norm = fill.get("normalized")

        if norm != "complete" or exit_px <= 0 or filled_qty <= 0:
            runtime_safety.set_trading_freeze(
                f"Exit SELL not confirmed for {pos.decision_id} order={oid} fill={fill}",
                source="exit_manager",
            )
            log_risk_audit(
                "exit_freeze_unconfirmed_fill",
                {"decision_id": pos.decision_id, "order_id": oid, "fill": fill},
            )
            return None

        if filled_qty != qty:
            runtime_safety.set_trading_freeze(
                f"Partial exit fill {filled_qty}/{qty} for {pos.decision_id} order={oid} — manual reconcile",
                source="exit_manager",
            )
            log_risk_audit(
                "exit_freeze_partial",
                {
                    "decision_id": pos.decision_id,
                    "filled": filled_qty,
                    "requested": qty,
                    "order_id": oid,
                },
            )
            return None

        rec = tracker.close_position_record(
            pos,
            exit_price=exit_px,
            exit_reason=f"{exit_reason}|broker_sell_{oid}",
            ist_now=datetime.now(IST),
        )
        rec["broker_order_id"] = oid
        rec["intent_id"] = intent_id
        rec["fill"] = fill
        return rec

    runtime_safety.set_trading_freeze(
        f"Exit SELL failed after retry for {pos.decision_id}: {last_err}",
        source="exit_manager",
    )
    log_risk_audit("exit_freeze_after_fail", {"decision_id": pos.decision_id, "error": str(last_err)})
    return None


def exit_position_via_broker_safely(
    *,
    tracker,
    pos,
    exit_reason: str,
    client,
    mode: str,
) -> Optional[Dict[str, Any]]:
    """
    Canonical live exit path used by both scheduler and tick-by-tick exits.

    It atomically claims the local position, verifies/cancels the protective
    GTT before placing a SELL, waits for a full fill, and only then closes
    local state.
    """
    if not tracker.mark_exiting(pos.decision_id):
        logger.debug("Position %s already being exited", pos.decision_id)
        return None

    try:
        if not pos.instrument_key:
            runtime_safety.set_trading_freeze(
                f"Cannot exit live: missing instrument_key for {pos.decision_id}",
                source="exit_manager",
            )
            log_risk_audit("exit_freeze_missing_ik", {"decision_id": pos.decision_id})
            tracker.revert_exiting(pos.decision_id)
            return None

        gtt_result = _handle_gtt_before_exit(client, tracker, pos)
        if gtt_result == "BLOCKED":
            logger.warning("Skipping exit for %s — GTT cancel unverified", pos.decision_id)
            tracker.revert_exiting(pos.decision_id)
            return None
        if gtt_result and isinstance(gtt_result, dict) and gtt_result.get("already_exited"):
            gtt_exit_px = float(gtt_result.get("exit_price") or 0)
            if gtt_exit_px <= 0:
                gtt_exit_px = tracker.estimate_current_price(pos)
            rec = tracker.close_position_record(
                pos,
                exit_price=gtt_exit_px,
                exit_reason=f"{gtt_result.get('reason', 'gtt_triggered')}|{exit_reason}",
                ist_now=datetime.now(IST),
            )
            rec["broker_order_id"] = gtt_result.get("order_id")
            return rec

        qty = int(pos.lots) * int(pos.lot_size)
        summary = _exit_via_broker(
            tracker=tracker,
            pos=pos,
            qty=qty,
            exit_reason=exit_reason,
            client=client,
            mode=mode,
        )
        if summary is None:
            tracker.revert_exiting(pos.decision_id)
        return summary
    except Exception as ex:
        tracker.revert_exiting(pos.decision_id)
        log_risk_audit("exit_safe_path_exception", {
            "decision_id": pos.decision_id,
            "error": str(ex),
        })
        runtime_safety.set_trading_freeze(
            f"Safe exit path failed for {pos.decision_id}: {ex}",
            source="exit_manager",
        )
        return None


def emergency_flatten_open_fo_positions(client: Any = None) -> Dict[str, Any]:
    """
    Close all non-zero F&O broker positions with MARKET orders (kill switch / circuit breaker).
    Sets ['ok'] True only when no errors and every place_fo_order succeeded.
    """
    out: Dict[str, Any] = {"attempted": 0, "errors": [], "results": [], "ok": False}
    try:
        if client is None:
            from mcp_server.upstox_client import get_upstox_client

            client = get_upstox_client()
        if not client.is_authenticated():
            out["errors"].append("not_authenticated")
            return out
        pos_resp = client.get_positions()
        if pos_resp.get("status") != "success":
            out["errors"].append(str(pos_resp.get("message") or pos_resp))
            return out
        rows = pos_resp.get("data")
        if isinstance(rows, dict) and "positions" in rows:
            rows = rows.get("positions") or []
        if not isinstance(rows, list):
            rows = []

        for r in rows:
            if not _is_fo_row(r):
                continue
            net = _signed_net_qty(r)
            if net == 0:
                continue
            sym = str(r.get("tradingsymbol") or r.get("trading_symbol") or "")
            ikey = _instrument_key_row(r)
            if not ikey:
                out["errors"].append(f"missing instrument for {sym}")
                continue
            side = "SELL" if net > 0 else "BUY"
            qty = abs(net)
            out["attempted"] += 1
            resp = client.place_fo_order(
                instrument_token=str(ikey),
                transaction_type=side,
                quantity=qty,
                order_type="MARKET",
                product=str(r.get("product") or "I"),
            )
            oid = None
            if isinstance(resp.get("data"), dict):
                oid = resp["data"].get("order_id")

            fill_confirmed = False
            if oid:
                fill = client.wait_for_fill(str(oid), timeout_s=60.0, poll_s=2.0)
                fill_confirmed = (
                    fill.get("normalized") == "complete"
                    or int(fill.get("filled_quantity") or 0) >= qty
                )
                if not fill_confirmed:
                    out["errors"].append(f"fill_unconfirmed {sym} oid={oid} fill={fill.get('normalized')}")

            out["results"].append({
                "symbol": sym, "side": side, "qty": qty,
                "resp": resp, "fill_confirmed": fill_confirmed,
            })
            order_tracker.log_intent(
                decision_id=None,
                symbol=sym,
                instrument_key=str(ikey),
                transaction_type=side,
                quantity=qty,
                product=str(r.get("product") or "I"),
                mode="emergency_flatten",
                status="FILLED" if fill_confirmed else ("PLACED" if resp.get("status") == "success" else "REJECTED"),
                broker_order_id=str(oid) if oid else None,
                broker_response=resp,
                error=None if fill_confirmed else json.dumps(resp)[:1000],
            )
            if resp.get("status") != "success":
                out["errors"].append(f"order_failed {sym} {resp}")
    except Exception as e:
        out["errors"].append(str(e))

    out["ok"] = (
        len(out["errors"]) == 0
        and all(x.get("fill_confirmed", False) for x in out["results"])
    )
    if out["attempted"] == 0 and len(out["errors"]) == 0:
        out["ok"] = True
    return out
