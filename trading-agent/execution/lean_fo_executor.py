"""
Place Lean F&O broker orders when runtime safety allows (micro_live / live).

Entry lifecycle (transactional ordering):
  1. Log INTENT before calling broker  (crash-safe breadcrumb)
  2. Place BUY at broker
  3. Wait for fill confirmation
  4. Create local position row IMMEDIATELY after fill  (row must exist before GTT)
  5. Place protective SL GTT
  6. If GTT placement fails → flatten or freeze (never leave position unprotected)
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

import pytz

from data_feeds.fo_data_feed import get_fo_data_feed
from brain.position_tracker import get_position_tracker, OpenPosition
from execution import order_tracker, runtime_safety
from execution.risk_runtime import log_risk_audit

IST = pytz.timezone("Asia/Kolkata")


def _place_protective_sl_gtt(
    *,
    client: Any,
    decision_id: str,
    instrument_key: str,
    qty: int,
    avg_px: float,
    stop_loss_pct: float,
) -> Dict[str, Any]:
    """
    Place ONE single-leg GTT SELL as broker-side disaster protection.

    Returns {"ok": bool, "gtt_id": str|None, "error": str|None}.

    In live mode, a GTT failure is CRITICAL — the caller must flatten or freeze.
    """
    result: Dict[str, Any] = {"ok": False, "gtt_id": None, "error": None}
    if avg_px <= 0:
        result["error"] = "avg_px_zero"
        return result

    sl_price = round(avg_px * (1 - stop_loss_pct / 100), 2)
    sl_price = max(sl_price, 0.05)

    tracker = get_position_tracker()

    try:
        sl_resp = client.place_gtt_order(
            gtt_type="SINGLE",
            quantity=qty,
            product="I",
            instrument_token=instrument_key,
            transaction_type="SELL",
            rules=[{
                "strategy": "ENTRY",
                "trigger_type": "BELOW",
                "trigger_price": sl_price,
                "market_protection": -1,
            }],
        )
        if sl_resp.get("status") == "success":
            ids = sl_resp.get("data", {}).get("gtt_order_ids", [])
            sl_gtt_id = ids[0] if ids else None
            if sl_gtt_id:
                tracker.store_gtt_ids(decision_id, sl_gtt_id, None)
                result["ok"] = True
                result["gtt_id"] = sl_gtt_id
            else:
                result["error"] = "no_gtt_id_in_response"
            log_risk_audit("gtt_sl_placed", {
                "decision_id": decision_id,
                "gtt_id": sl_gtt_id,
                "trigger_price": sl_price,
                "entry_price": avg_px,
                "sl_pct": stop_loss_pct,
            })
        else:
            result["error"] = f"gtt_api_failed: {str(sl_resp)[:200]}"
            log_risk_audit("gtt_sl_failed", {
                "decision_id": decision_id,
                "response": str(sl_resp)[:500],
            })
    except Exception as ex:
        result["error"] = str(ex)
        log_risk_audit("gtt_sl_exception", {"decision_id": decision_id, "error": str(ex)})

    return result


def resolve_option_instrument_key(
    symbol: str,
    expiry: str,
    strike: float,
    option_type: str,
) -> Optional[str]:
    """Resolve instrument_key from latest option chain row."""
    feed = get_fo_data_feed()
    chain = feed.get_option_chain(symbol, expiry)
    if not chain or chain.get("error"):
        return None
    rows = chain.get("calls" if option_type.upper() == "CE" else "puts", [])
    for r in rows:
        try:
            if abs(float(r.get("strike", 0)) - float(strike)) < 0.51:
                ik = r.get("instrument_key")
                if ik:
                    return str(ik)
        except (TypeError, ValueError):
            continue
    return None


def _try_cancel_pending_order(client: Any, order_id: str, decision_id: str) -> None:
    """Cancel a BUY that didn't fill. Freezes trading if cancel fails or is rejected."""
    try:
        resp = client.cancel_order(order_id)
        ok = isinstance(resp, dict) and resp.get("status") == "success"
        if ok:
            log_risk_audit("entry_timeout_order_cancelled", {
                "order_id": order_id, "decision_id": decision_id,
            })
        else:
            log_risk_audit("entry_timeout_cancel_rejected", {
                "order_id": order_id, "decision_id": decision_id,
                "response": resp,
            })
            runtime_safety.set_trading_freeze(
                f"BUY order {order_id} cancel returned non-success after fill timeout; "
                f"response: {resp}. Manual check required.",
                source="lean_fo_executor",
            )
    except Exception as ex:
        log_risk_audit("entry_timeout_cancel_failed", {
            "order_id": order_id, "decision_id": decision_id, "error": str(ex),
        })
        runtime_safety.set_trading_freeze(
            f"BUY order {order_id} may be pending at broker after fill timeout; "
            f"cancel failed: {ex}. Manual check required.",
            source="lean_fo_executor",
        )


def _has_local_open_position(instrument_key: str) -> bool:
    """Prevent duplicate same-contract entries from repeated scans/restarts."""
    try:
        tracker = get_position_tracker()
        for pos in tracker.get_open_positions():
            if (pos.instrument_key or "").strip() == str(instrument_key).strip():
                return True
    except Exception:
        pass
    return False


def _immediate_flatten(client: Any, instrument_key: str, qty: int, decision_id: str) -> None:
    """Emergency: immediately sell back a position that has no GTT protection.
    Only closes local state if broker fill is CONFIRMED.
    """
    log_risk_audit("immediate_flatten_no_gtt", {
        "decision_id": decision_id, "instrument_key": instrument_key, "qty": qty,
    })
    try:
        resp = client.place_fo_order(
            instrument_token=str(instrument_key),
            transaction_type="SELL",
            quantity=qty,
            order_type="MARKET",
            product="I",
        )
        oid = None
        if isinstance(resp.get("data"), dict):
            oid = resp["data"].get("order_id")

        fill_confirmed = False
        exit_price = 0.0
        if oid:
            fill = client.wait_for_fill(str(oid), timeout_s=60.0, poll_s=2.0)
            filled_qty = int(fill.get("filled_quantity") or 0)
            fill_confirmed = (
                fill.get("normalized") == "complete" or filled_qty >= qty
            )
            exit_price = float(fill.get("average_price") or 0)

        intent_status = "FILLED" if fill_confirmed else (
            "PLACED" if resp.get("status") == "success" else "REJECTED"
        )
        order_tracker.log_intent(
            decision_id=decision_id,
            symbol="",
            instrument_key=str(instrument_key),
            transaction_type="SELL",
            quantity=qty,
            product="I",
            mode="gtt_fail_flatten",
            status=intent_status,
            broker_order_id=str(oid) if oid else None,
            broker_response=resp,
        )

        if fill_confirmed:
            tracker = get_position_tracker()
            positions = tracker.get_open_positions()
            for p in positions:
                if p.decision_id == decision_id:
                    tracker.close_position_record(
                        p,
                        exit_price=exit_price if exit_price > 0 else p.entry_price * 0.7,
                        exit_reason="gtt_fail_flatten_confirmed",
                    )
                    break
        else:
            # SELL placed but fill NOT confirmed — position may still be open at broker
            log_risk_audit("immediate_flatten_fill_unconfirmed", {
                "decision_id": decision_id, "oid": str(oid), "resp_status": resp.get("status"),
            })
            runtime_safety.set_trading_freeze(
                f"GTT flatten SELL placed but fill unconfirmed for {decision_id}. "
                f"Local position kept OPEN — manual check required.",
                source="lean_fo_executor",
            )
    except Exception as ex:
        log_risk_audit("immediate_flatten_failed", {"decision_id": decision_id, "error": str(ex)})
        runtime_safety.set_trading_freeze(
            f"GTT failed AND flatten failed for {decision_id}: {ex}",
            source="lean_fo_executor",
        )


def maybe_execute_lean_fo_order(
    *,
    symbol: str,
    analyze_result: Dict[str, Any],
    broker_orders_allowed: bool,
    decision_id: Optional[str],
    trading_mode: runtime_safety.TradingMode,
) -> Dict[str, Any]:
    """
    If decision is EXECUTE and mode is micro_live/live, place F&O BUY order.

    Lifecycle (each step depends on the previous):
      1. Validate inputs & resolve instrument
      2. Log durable ORDER INTENT (crash breadcrumb)
      3. Place BUY at broker
      4. Wait for fill
      5. Create local position row (NOW the position exists in DB)
      6. Place protective SL GTT (row MUST exist so store_gtt_ids works)
      7. If GTT fails → flatten immediately or freeze

    Returns execution payload for logging / result merge.
    """
    out: Dict[str, Any] = {"executed": False, "mode": trading_mode.value}
    if analyze_result.get("decision") != "EXECUTE":
        return out
    if not broker_orders_allowed:
        out["reason"] = "broker_orders_not_allowed"
        return out
    if trading_mode not in (
        runtime_safety.TradingMode.MICRO_LIVE,
        runtime_safety.TradingMode.LIVE,
    ):
        out["reason"] = "mode_is_not_live"
        return out

    sig = analyze_result.get("signal") or {}
    symbol = (sig.get("symbol") or symbol or "").upper()
    if not symbol:
        out["error"] = "missing_symbol"
        return out

    strike = float(sig.get("strike", 0))
    option_type = str(sig.get("option_type", "CE"))
    lots = int(sig.get("lots", 1))
    lot_size = int(sig.get("lot_size", 50))
    expiry = str(sig.get("expiry") or "")
    instrument_key = sig.get("instrument_key")

    if not instrument_key and expiry:
        instrument_key = resolve_option_instrument_key(symbol, expiry, strike, option_type)
    if not instrument_key:
        out["error"] = "could_not_resolve_instrument_key"
        return out
    if _has_local_open_position(str(instrument_key)):
        out["error"] = "duplicate_open_position_same_instrument"
        log_risk_audit("entry_duplicate_block_local", {
            "decision_id": decision_id,
            "instrument_key": str(instrument_key),
        })
        return out

    qty = lots * lot_size
    order_value = float(sig.get("premium", 0)) * qty
    if trading_mode == runtime_safety.TradingMode.MICRO_LIVE:
        cap = runtime_safety.micro_live_max_order_value()
        if order_value > cap:
            out["error"] = f"micro_live_order_value {order_value:.0f} exceeds cap {cap:.0f}"
            log_risk_audit("micro_live_cap_block", {"order_value": order_value, "cap": cap})
            return out

    from mcp_server.upstox_client import get_upstox_client
    client = get_upstox_client()
    if not client.is_authenticated():
        out["error"] = "not_authenticated"
        return out

    # ─── STEP 1: Log durable intent BEFORE calling broker ───
    intent_id = order_tracker.log_intent(
        decision_id=decision_id,
        symbol=symbol,
        instrument_key=str(instrument_key),
        transaction_type="BUY",
        quantity=qty,
        product="I",
        mode=trading_mode.value,
        status="SUBMITTED",
        broker_order_id=None,
        broker_response=None,
    )
    out["intent_id"] = intent_id

    # ─── STEP 2: Place BUY at broker ───
    resp = client.place_fo_order(
        instrument_token=str(instrument_key),
        transaction_type="BUY",
        quantity=qty,
        order_type="MARKET",
        product="I",
    )
    ok = resp.get("status") == "success"
    oid = None
    if isinstance(resp.get("data"), dict):
        oid = resp["data"].get("order_id")

    out["broker_order_id"] = oid
    out["broker_response"] = resp

    if not ok or not oid:
        order_tracker.update_intent_status(intent_id, "REJECTED", broker_response=resp)
        out["error"] = "broker_order_rejected"
        return out

    # Persist broker order id immediately after place (crash recovery)
    order_tracker.update_intent_status(
        intent_id, "PLACED", broker_order_id=str(oid), broker_response=resp
    )

    # ─── STEP 3: Wait for fill confirmation ───
    fill_payload = client.wait_for_fill(str(oid), timeout_s=45.0, poll_s=2.0)
    norm = fill_payload.get("normalized")
    avg_px = float(fill_payload.get("average_price") or 0)
    filled_qty = int(fill_payload.get("filled_quantity") or 0)

    if norm != "complete" and filled_qty <= 0:
        # Fill not confirmed — try to cancel the pending order to avoid orphan
        _try_cancel_pending_order(client, str(oid), decision_id or intent_id)
        order_tracker.update_intent_status(intent_id, "FILL_TIMEOUT", broker_response=fill_payload)
        out["error"] = "fill_not_confirmed"
        return out
    if avg_px <= 0:
        _try_cancel_pending_order(client, str(oid), decision_id or intent_id)
        order_tracker.update_intent_status(intent_id, "FILL_NO_PRICE", broker_response=fill_payload)
        out["error"] = "missing_average_price"
        return out

    adj_lots = lots
    if filled_qty > 0 and lot_size > 0:
        adj_lots = max(1, filled_qty // lot_size)
        if filled_qty < qty:
            log_risk_audit(
                "entry_partial_fill",
                {"requested_qty": qty, "filled_qty": filled_qty, "decision_id": decision_id},
            )
            _try_cancel_pending_order(client, str(oid), decision_id or intent_id)
        if filled_qty % lot_size != 0:
            log_risk_audit(
                "entry_odd_lot_fill",
                {
                    "requested_qty": qty,
                    "filled_qty": filled_qty,
                    "lot_size": lot_size,
                    "decision_id": decision_id,
                },
            )
            _immediate_flatten(client, str(instrument_key), filled_qty, decision_id or intent_id)
            order_tracker.update_intent_status(
                intent_id, "ODD_LOT_FLATTENED", broker_order_id=str(oid), broker_response=fill_payload
            )
            out["error"] = "odd_lot_fill_flattened"
            return out

    # ─── STEP 4: Create position row IMMEDIATELY after fill ───
    tracker = get_position_tracker()
    now_ist = datetime.now(IST)
    position = OpenPosition(
        decision_id=decision_id or intent_id,
        symbol=symbol,
        strike=strike,
        option_type=option_type,
        entry_price=avg_px,
        entry_time=now_ist,
        lots=adj_lots,
        lot_size=lot_size,
        instrument_key=str(instrument_key),
        highest_pnl_pct=0,
        status="OPEN",
        stop_loss_pct=float(sig.get("stop_loss", 25)),
        target_pct=float(sig.get("target", 50)),
    )
    tracker.add_position(position)

    # Register with exit_ticker for real-time SL/target monitoring (tick-by-tick)
    try:
        from execution.exit_ticker import get_exit_ticker
        exit_ticker = get_exit_ticker()
        sl_pct = float(sig.get("stop_loss", 25))
        target_pct = float(sig.get("target", 50))
        registered_qty = filled_qty if filled_qty > 0 else qty
        exit_ticker.register_position(
            decision_id=decision_id or intent_id,
            symbol=symbol,
            instrument_key=str(instrument_key),
            entry_price=avg_px,
            sl_pct=sl_pct,
            target_pct=target_pct,
            qty=registered_qty,
            lot_size=lot_size,
        )
        log_risk_audit("position_registered_exit_ticker", {
            "decision_id": decision_id,
            "symbol": symbol,
            "entry_price": avg_px,
            "sl_pct": sl_pct,
            "target_pct": target_pct,
        })
    except Exception as ex:
        log_risk_audit("exit_ticker_registration_failed", {
            "decision_id": decision_id,
            "error": str(ex),
        })

    # Also update decision log
    if decision_id:
        try:
            from memory.decision_log import get_decision_log
            dl = get_decision_log()
            dl.patch_entry_fill(decision_id, avg_px, adj_lots)
            conn = dl._get_conn()
            conn.execute(
                "UPDATE decisions SET strategy_name = 'FILL_CONFIRMED' WHERE decision_id = ?",
                (decision_id,),
            )
            conn.commit()
            conn.close()
        except Exception as ex:
            log_risk_audit("entry_fill_patch_failed", {"decision_id": decision_id, "error": str(ex)})

    order_tracker.update_intent_status(intent_id, "FILLED", broker_order_id=str(oid), broker_response=fill_payload)
    out["fill"] = fill_payload
    out["entry_average_price"] = avg_px
    out["lots_after_fill"] = adj_lots
    out["executed"] = True

    # ─── STEP 5: Place protective SL GTT (position row EXISTS now) ───
    actual_qty = filled_qty if filled_qty > 0 else qty
    gtt_result = _place_protective_sl_gtt(
        client=client,
        decision_id=decision_id or intent_id,
        instrument_key=str(instrument_key),
        qty=actual_qty,
        avg_px=avg_px,
        stop_loss_pct=float(sig.get("stop_loss", 25)),
    )

    # ─── STEP 6: If GTT failed in live mode → flatten or freeze ───
    if not gtt_result["ok"]:
        out["gtt_error"] = gtt_result["error"]
        log_risk_audit("gtt_failed_post_entry", {
            "decision_id": decision_id,
            "instrument_key": str(instrument_key),
            "error": gtt_result["error"],
        })
        if trading_mode == runtime_safety.TradingMode.LIVE:
            # Full live: immediately sell back — cannot leave unprotected position
            _immediate_flatten(client, str(instrument_key), actual_qty, decision_id or intent_id)
            out["executed"] = False
            out["error"] = "gtt_failed_position_flattened"
        else:
            # KNOWN LIMITATION: In micro-live, GTT failure freezes new trading
            # but does NOT close this position. The position is OPEN at the broker
            # with NO broker-side SL. If the laptop sleeps or agent crashes, this
            # position will only be closed by broker MIS auto-square-off (3:15-3:30 IST).
            # This is acceptable ONLY for supervised micro-live testing.
            import sys
            warn_msg = (
                f"\n{'='*60}\n"
                f"  CRITICAL: MICRO-LIVE POSITION HAS NO BROKER-SIDE SL\n"
                f"  Decision: {decision_id}\n"
                f"  Instrument: {instrument_key}\n"
                f"  GTT Error: {gtt_result['error']}\n"
                f"  ACTION: Monitor manually or flatten via broker terminal\n"
                f"{'='*60}\n"
            )
            print(warn_msg, file=sys.stderr)
            runtime_safety.set_trading_freeze(
                f"GTT placement failed for {decision_id}: {gtt_result['error']}. "
                f"Position is OPEN without broker-side SL protection. "
                f"KNOWN LIMITATION: micro-live does not auto-flatten; requires supervision.",
                source="lean_fo_executor",
            )

    return out
