"""
Broker vs local reconciliation — match by instrument key / quantity (not only counts).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pytz

from execution import runtime_safety

IST = pytz.timezone("Asia/Kolkata")


def _state_path() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "data_cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "reconciliation_state.json")


def _mismatch_counter_path() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "data_cache", "reconciliation_mismatch_count.json")


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _normalize_broker_positions(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not resp:
        return []
    data = resp.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "positions" in data:
        inner = data.get("positions")
        return inner if isinstance(inner, list) else []
    return []


def _is_fo_position(row: Dict[str, Any]) -> bool:
    seg = str(row.get("segment") or row.get("exchange") or "").upper()
    if "FO" in seg or "NFO" in seg or "BFO" in seg:
        return True
    sym = str(row.get("tradingsymbol") or row.get("trading_symbol") or row.get("symbol") or "")
    if sym.endswith("CE") or sym.endswith("PE"):
        return True
    return False


def _net_qty(row: Dict[str, Any]) -> int:
    """Signed net quantity (prefer net_quantity over raw quantity)."""
    for key in ("net_quantity", "quantity_net", "quantity", "day_buy_quantity"):
        v = row.get(key)
        if v is not None:
            try:
                return int(round(float(v)))
            except (TypeError, ValueError):
                continue
    return 0


def _instrument_key_from_row(row: Dict[str, Any]) -> Optional[str]:
    for key in ("instrument_key", "instrument_token", "tradingsymbol"):
        v = row.get(key)
        if v:
            return str(v).strip()
    return None


def _try_flatten_orphan(client: Any, instrument_key: str, br_info: Dict[str, Any]) -> bool:
    """Attempt to flatten an orphan broker position with a MARKET order.

    Returns True only if the SELL/BUY fill is confirmed by the broker.
    """
    from execution.risk_runtime import log_risk_audit
    from execution import order_tracker
    from execution import runtime_safety

    net = int(br_info.get("net_quantity", 0))
    if net == 0:
        return True
    side = "SELL" if net > 0 else "BUY"
    qty = abs(net)
    sym = br_info.get("symbol", instrument_key)

    log_risk_audit("orphan_auto_flatten", {
        "instrument_key": instrument_key,
        "symbol": sym,
        "side": side,
        "qty": qty,
    })

    try:
        resp = client.place_fo_order(
            instrument_token=str(instrument_key),
            transaction_type=side,
            quantity=qty,
            order_type="MARKET",
            product="I",
        )
        oid = None
        if isinstance(resp.get("data"), dict):
            oid = resp["data"].get("order_id")

        fill_confirmed = False
        if oid:
            fill = client.wait_for_fill(str(oid), timeout_s=60.0, poll_s=2.0)
            filled_qty = int(fill.get("filled_quantity") or 0)
            fill_confirmed = (
                fill.get("normalized") == "complete" or filled_qty >= qty
            )

        intent_status = "FILLED" if fill_confirmed else (
            "PLACED" if resp.get("status") == "success" else "REJECTED"
        )
        order_tracker.log_intent(
            decision_id=None,
            symbol=sym,
            instrument_key=str(instrument_key),
            transaction_type=side,
            quantity=qty,
            product="I",
            mode="orphan_flatten",
            status=intent_status,
            broker_order_id=str(oid) if oid else None,
            broker_response=resp,
        )

        if not fill_confirmed:
            log_risk_audit("orphan_flatten_fill_unconfirmed", {
                "instrument_key": instrument_key, "oid": str(oid),
            })
            runtime_safety.set_trading_freeze(
                f"Orphan flatten for {instrument_key} placed but fill NOT confirmed. "
                f"Manual check required.",
                source="reconciliation",
            )
        return fill_confirmed

    except Exception as ex:
        log_risk_audit("orphan_flatten_failed", {"instrument_key": instrument_key, "error": str(ex)})
        return False


def reconcile_state(
    *,
    token_valid: bool,
    fetch_broker: bool,
    client: Any = None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Returns (reconciliation_ok, report dict).
    When not fetch_broker (paper/shadow or no client), returns OK with note.
    """
    from brain.position_tracker import get_position_tracker

    report: Dict[str, Any] = {
        "checked_at": datetime.now(IST).isoformat(),
        "token_valid": token_valid,
        "broker_fetch_attempted": False,
    }

    local_open = get_position_tracker().get_open_positions()
    report["local_open_count"] = len(local_open)

    if not fetch_broker or client is None or not token_valid:
        report["status"] = "SKIPPED_NO_BROKER"
        report["reconciliation_ok"] = True
        _save_json(_state_path(), report)
        _save_json(_mismatch_counter_path(), {"count": 0})
        return True, report

    report["broker_fetch_attempted"] = True
    try:
        pos_resp = client.get_positions()
    except Exception as e:
        report["status"] = "BROKER_ERROR"
        report["error"] = str(e)
        _save_json(_state_path(), report)
        return False, report

    if pos_resp.get("status") != "success" and pos_resp.get("errors"):
        report["status"] = "BROKER_ERROR"
        report["error"] = pos_resp.get("errors") or pos_resp.get("message")
        _save_json(_state_path(), report)
        return False, report

    rows = _normalize_broker_positions(pos_resp)
    broker_fo: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not _is_fo_position(r):
            continue
        q = _net_qty(r)
        if q == 0:
            continue
        ik = _instrument_key_from_row(r)
        if not ik:
            report.setdefault("warnings", []).append("broker_row_missing_instrument_key")
            continue
        broker_fo[ik] = {
            "net_quantity": q,
            "row": r,
            "symbol": str(r.get("tradingsymbol") or r.get("trading_symbol") or ""),
        }

    local_by_key: Dict[str, Dict[str, Any]] = {}
    for p in local_open:
        ik = (p.instrument_key or "").strip()
        if not ik:
            continue
        qty = int(p.lots) * int(p.lot_size)
        local_by_key[ik] = {
            "qty": qty,
            "decision_id": p.decision_id,
            "symbol": p.symbol,
            "strike": p.strike,
        }

    prev = _load_json(_mismatch_counter_path(), {"count": 0})

    mismatches: List[str] = []
    phantom_locals: List[str] = []
    orphans: List[str] = []
    qty_mismatch: List[str] = []

    for ik, loc in local_by_key.items():
        br = broker_fo.get(ik)
        if not br:
            phantom_locals.append(
                f"Local has {loc['symbol']} {loc['strike']} ({ik}) but broker has no matching position"
            )
            continue
        if int(br["net_quantity"]) != int(loc["qty"]):
            qty_mismatch.append(
                f"Qty/sign mismatch {ik}: local_long_qty={loc['qty']} broker_net={br['net_quantity']}"
            )

    for ik, br in broker_fo.items():
        if ik not in local_by_key:
            orphans.append(
                f"Broker has orphan F&O {ik} ({br.get('symbol')}) qty={br['net_quantity']}"
            )
            # Auto-flatten orphan if reconciliation has failed in a previous cycle
            if prev.get("count", 0) >= 1:
                flat_ok = _try_flatten_orphan(client, ik, br)
                if flat_ok:
                    orphans[-1] += " [FLATTENED]"
                else:
                    orphans[-1] += " [FLATTEN FAILED — MANUAL CHECK]"

    mismatches.extend(phantom_locals)
    mismatches.extend(orphans)
    mismatches.extend(qty_mismatch)

    report["broker_fo_positions"] = len(broker_fo)
    report["local_with_instrument_key"] = len(local_by_key)
    report["phantom_locals"] = phantom_locals
    report["orphan_broker_positions"] = orphans
    report["quantity_mismatches"] = qty_mismatch
    report["status"] = "COMPARED_GRANULAR"

    critical = bool(orphans or phantom_locals or qty_mismatch)
    report["critical_mismatch"] = critical

    cnt = int(prev.get("count", 0))
    if critical:
        cnt += 1
    else:
        cnt = 0
    _save_json(_mismatch_counter_path(), {"count": cnt})
    report["mismatch_streak"] = cnt

    threshold = 3
    if cnt >= threshold and critical:
        runtime_safety.set_trading_freeze(
            f"Granular reconciliation failed ({cnt} cycles): {mismatches[:5]}",
            source="reconciliation",
        )
        report["freeze_triggered"] = True
        report["reconciliation_ok"] = False
        _save_json(_state_path(), report)
        return False, report

    ok = not critical
    report["reconciliation_ok"] = ok
    _save_json(_state_path(), report)
    return ok, report


def audit_and_recover_gtt_protection(client: Any) -> Tuple[bool, Dict[str, Any]]:
    """
    Verify every local OPEN F&O position has active broker-side SL protection.

    Missing/cancelled/failed GTT is recovered by placing a fresh SL GTT using
    the position's stored stop_loss_pct. If recovery fails, trading is frozen.
    """
    from brain.position_tracker import get_position_tracker
    from execution.risk_runtime import log_risk_audit

    tracker = get_position_tracker()
    report: Dict[str, Any] = {
        "checked_at": datetime.now(IST).isoformat(),
        "positions_checked": 0,
        "missing_gtt": [],
        "replaced_gtt": [],
        "bad_gtt": [],
        "errors": [],
    }

    if client is None:
        report["errors"].append("missing_client")
        return False, report

    try:
        from execution.lean_fo_executor import _place_protective_sl_gtt
    except Exception as ex:
        report["errors"].append(f"import_place_gtt_failed:{ex}")
        return False, report

    terminal_bad = {"CANCELLED", "EXPIRED", "FAILED"}

    for pos in tracker.get_open_positions():
        report["positions_checked"] += 1
        if not pos.instrument_key:
            report["errors"].append(f"{pos.decision_id}:missing_instrument_key")
            continue

        needs_replacement = False
        sl_id = pos.gtt_sl_order_id
        if not sl_id:
            needs_replacement = True
            report["missing_gtt"].append(pos.decision_id)
        else:
            try:
                info = client.gtt_rule_status(sl_id)
                if not info.get("ok"):
                    needs_replacement = True
                    report["bad_gtt"].append({"decision_id": pos.decision_id, "gtt_id": sl_id, "status": "unknown"})
                else:
                    statuses = {
                        str(rule.get("status") or "").upper()
                        for rule in info.get("rules", [])
                    }
                    if not statuses:
                        needs_replacement = True
                        report["bad_gtt"].append({
                            "decision_id": pos.decision_id,
                            "gtt_id": sl_id,
                            "status": "no_rules",
                        })
                    elif statuses & terminal_bad:
                        needs_replacement = True
                        report["bad_gtt"].append({
                            "decision_id": pos.decision_id,
                            "gtt_id": sl_id,
                            "status": sorted(statuses),
                        })
                    elif statuses & {"COMPLETED", "TRIGGERED"}:
                        # The exit manager will reconcile the triggered fill;
                        # do not place a second SL over a likely flat position.
                        continue
            except Exception as ex:
                needs_replacement = True
                report["bad_gtt"].append({
                    "decision_id": pos.decision_id,
                    "gtt_id": sl_id,
                    "error": str(ex),
                })

        if not needs_replacement:
            continue

        qty = int(pos.lots) * int(pos.lot_size)
        if qty <= 0 or pos.entry_price <= 0:
            report["errors"].append(f"{pos.decision_id}:invalid_qty_or_entry")
            continue
        try:
            tracker.clear_gtt_ids(pos.decision_id)
        except Exception:
            pass
        gtt = _place_protective_sl_gtt(
            client=client,
            decision_id=pos.decision_id,
            instrument_key=str(pos.instrument_key),
            qty=qty,
            avg_px=float(pos.entry_price),
            stop_loss_pct=float(pos.stop_loss_pct or 25.0),
        )
        if gtt.get("ok"):
            report["replaced_gtt"].append({
                "decision_id": pos.decision_id,
                "gtt_id": gtt.get("gtt_id"),
            })
            log_risk_audit("gtt_recovered", {
                "decision_id": pos.decision_id,
                "gtt_id": gtt.get("gtt_id"),
            })
        else:
            report["errors"].append(f"{pos.decision_id}:gtt_recovery_failed:{gtt.get('error')}")

    ok = len(report["errors"]) == 0
    report["ok"] = ok
    if not ok:
        runtime_safety.set_trading_freeze(
            f"GTT protection audit failed: {report['errors'][:5]}",
            source="reconciliation",
        )
        log_risk_audit("gtt_protection_audit_failed", report)
    return ok, report


def _recover_filled_entry_position(client: Any, intent: Dict[str, Any], status_resp: Dict[str, Any]) -> None:
    """
    Recreate local state for a BUY that filled before the process crashed.
    Also places/verifies missing SL GTT via the protection audit.
    """
    from brain.position_tracker import get_position_tracker, OpenPosition
    from memory.decision_log import get_decision_log
    from execution.risk_runtime import log_risk_audit

    decision_id = intent.get("decision_id") or intent.get("intent_id")
    if not decision_id:
        return

    tracker = get_position_tracker()
    if tracker.has_position(decision_id):
        return

    fill = {}
    try:
        fill = client.parse_order_details(status_resp)
    except Exception:
        fill = {}
    avg_px = float(fill.get("average_price") or 0)
    filled_qty = int(fill.get("filled_quantity") or intent.get("quantity") or 0)
    if avg_px <= 0 or filled_qty <= 0:
        log_risk_audit("startup_recover_fill_missing_price_or_qty", {
            "decision_id": decision_id,
            "avg_px": avg_px,
            "filled_qty": filled_qty,
        })
        return

    symbol = str(intent.get("symbol") or "").upper()
    instrument_key = str(intent.get("instrument_key") or "")
    strike = 0.0
    option_type = "CE"
    original_lots = 1
    try:
        decision = get_decision_log().get_decision(decision_id)
    except Exception:
        decision = None
    if decision:
        symbol = decision.symbol or symbol
        strike = float(decision.strike or 0)
        option_type = str(decision.option_type or option_type)
        original_lots = max(1, int(decision.lots or 1))

    lot_size = tracker.LOT_SIZES.get(symbol, 50)
    lots = max(1, filled_qty // lot_size) if lot_size > 0 else original_lots
    position = OpenPosition(
        decision_id=decision_id,
        symbol=symbol,
        strike=strike,
        option_type=option_type,
        entry_price=avg_px,
        entry_time=datetime.now(IST),
        lots=lots,
        lot_size=lot_size,
        instrument_key=instrument_key,
        highest_pnl_pct=0,
        status="OPEN",
        stop_loss_pct=25.0,
        target_pct=50.0,
    )
    tracker.add_position(position)
    log_risk_audit("startup_recovered_local_position", {
        "decision_id": decision_id,
        "instrument_key": instrument_key,
        "filled_qty": filled_qty,
        "avg_px": avg_px,
    })


def startup_reconciliation(client: Any = None) -> Dict[str, Any]:
    """
    Run on process start to recover from crashes.

    Steps:
      1. Recover pending order_intents (SUBMITTED but never confirmed)
      2. Broker truth check (orphan positions)
      3. Stale local positions from previous day

    Returns a report dict. May set trading freeze if problems are found.
    """
    from execution import order_tracker
    from execution.risk_runtime import log_risk_audit
    from brain.position_tracker import get_position_tracker

    report: Dict[str, Any] = {
        "timestamp": datetime.now(IST).isoformat(),
        "pending_intents_found": 0,
        "pending_intents_resolved": 0,
        "broker_check_ok": True,
        "stale_positions": 0,
        "freeze_set": False,
    }

    # ─── STEP 1: Recover pending intents ───
    pending = order_tracker.pending_intents()
    report["pending_intents_found"] = len(pending)

    if pending and client:
        for intent in pending:
            oid = intent.get("broker_order_id")
            intent_id = intent.get("intent_id", "")
            if not oid:
                order_tracker.update_intent_status(intent_id, "REJECTED_NO_OID")
                report["pending_intents_resolved"] += 1
                continue

            try:
                status_resp = client.get_order_status(oid)
                order_data = status_resp.get("data", {})
                if isinstance(order_data, list) and order_data:
                    order_data = order_data[0]
                elif isinstance(order_data, dict):
                    pass
                else:
                    order_data = {}

                broker_status = (order_data.get("status") or "").lower()
                filled_qty = int(order_data.get("filled_quantity") or 0)

                if broker_status in ("complete", "traded") and filled_qty > 0:
                    order_tracker.update_intent_status(
                        intent_id, "FILLED_ON_RECOVERY",
                        broker_order_id=oid,
                        broker_response=status_resp,
                    )
                    log_risk_audit("startup_intent_recovered_fill", {
                        "intent_id": intent_id, "order_id": oid, "filled_qty": filled_qty,
                    })
                    _recover_filled_entry_position(client, intent, status_resp)
                elif broker_status in ("cancelled", "rejected", "failed"):
                    order_tracker.update_intent_status(
                        intent_id, "REJECTED_ON_RECOVERY",
                        broker_response=status_resp,
                    )
                else:
                    # Still open or unknown — cancel it
                    try:
                        client.cancel_order(oid)
                    except Exception:
                        pass
                    order_tracker.update_intent_status(
                        intent_id, "CANCELLED_ON_RECOVERY",
                        broker_response=status_resp,
                    )
                report["pending_intents_resolved"] += 1
            except Exception as ex:
                log_risk_audit("startup_intent_recovery_error", {
                    "intent_id": intent_id, "error": str(ex),
                })

    elif pending and not client:
        log_risk_audit("startup_pending_intents_no_client", {
            "count": len(pending),
        })

    # ─── STEP 2: Broker truth check ───
    if client:
        try:
            ok, rec_report = reconcile_state(
                token_valid=True,
                fetch_broker=True,
                client=client,
            )
            report["broker_check_ok"] = ok
            orphans = rec_report.get("orphan_broker_positions", [])
            if orphans:
                now = datetime.now(IST)
                is_market = (now.weekday() < 5 and 9 <= now.hour < 16)
                if is_market:
                    runtime_safety.set_trading_freeze(
                        f"Startup: {len(orphans)} orphan broker position(s) detected. "
                        f"Manual review required before trading.",
                        source="startup_reconciliation",
                    )
                    report["freeze_set"] = True
                log_risk_audit("startup_orphan_positions", {"orphans": orphans})
            gtt_ok, gtt_report = audit_and_recover_gtt_protection(client)
            report["gtt_audit_ok"] = gtt_ok
            report["gtt_audit"] = gtt_report
            if not gtt_ok:
                report["freeze_set"] = True
        except Exception as ex:
            log_risk_audit("startup_broker_check_error", {"error": str(ex)})
            report["broker_check_ok"] = False

    # ─── STEP 3: Stale local positions ───
    tracker = get_position_tracker()
    open_positions = tracker.get_open_positions()
    today = datetime.now(IST).date()

    stale = []
    for pos in open_positions:
        if pos.entry_time:
            try:
                entry_date = pos.entry_time.date()
                if entry_date < today:
                    stale.append(pos)
            except Exception:
                stale.append(pos)

    report["stale_positions"] = len(stale)
    report["stale_auto_closed_paper"] = 0
    mode = runtime_safety.load_trading_mode()
    if stale:
        log_risk_audit("startup_stale_positions", {
            "count": len(stale),
            "decision_ids": [p.decision_id for p in stale],
        })
        if mode in (runtime_safety.TradingMode.PAPER, runtime_safety.TradingMode.SHADOW):
            for pos in stale:
                exit_px = float(pos.current_price or 0) or float(pos.entry_price)
                try:
                    cp = tracker.estimate_current_price(pos)
                    if cp and cp > 0:
                        exit_px = float(cp)
                except Exception:
                    pass
                tracker.close_position_record(
                    pos,
                    exit_px,
                    "AUTO_CLOSE_STALE_SESSION_PAPER",
                )
            report["stale_auto_closed_paper"] = len(stale)
            log_risk_audit("startup_stale_auto_closed_paper", {
                "count": len(stale),
                "decision_ids": [p.decision_id for p in stale],
            })
        else:
            runtime_safety.set_trading_freeze(
                f"Startup: {len(stale)} local position(s) from previous day still OPEN. "
                f"Likely broker MIS auto-squared. Manual confirmation required.",
                source="startup_reconciliation",
            )
            report["freeze_set"] = True

    return report
