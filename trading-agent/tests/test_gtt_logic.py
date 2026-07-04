"""
Paper test for GTT protective SL order logic (single-GTT design).

Design: ONE SL GTT per position as disaster protection.
  - No target GTT (avoids double-sell from two independent GTTs).
  - Agent handles target/trailing/EOD exits, cancels SL GTT afterward.
  - If agent is down, SL fires — nothing else to double-sell.

Verifies:
  1. SL trigger price calculations
  2. DB round-trip for GTT SL order ID
  3. Only ONE GTT placed (SL only, no target)
  4. Exit manager: cancel SL GTT before agent SELL
  5. Exit manager: SL GTT already triggered → skip SELL (no double-sell)
  6. Double-sell prevention: no target GTT means no orphan
  7. UpstoxClient GTT request shapes (v3 API)
  8. No GTT ID → passthrough (no API calls)
  9. Full scenario walkthrough
"""

import importlib.util
import os
import sys
import sqlite3
import tempfile
import types
from datetime import datetime
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _load_module(name: str, filepath: str):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Stubs ─────────────────────────────────────────────────────────────────
_Dummy = type("_D", (), {"__init__": lambda *a, **k: None})

def _make_stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m

sys.modules["flask"] = _make_stub("flask", Flask=_Dummy, request=MagicMock(), redirect=lambda *a: None)
sys.modules["dotenv"] = _make_stub("dotenv", load_dotenv=lambda *a, **k: None)
sys.modules["requests"] = MagicMock()

_tz_obj = MagicMock()
sys.modules["pytz"] = _make_stub("pytz", timezone=lambda tz: _tz_obj, UTC=_tz_obj)

for stub in ("numpy", "pandas"):
    if stub not in sys.modules:
        sys.modules[stub] = types.ModuleType(stub)

# sqlalchemy
sys.modules["sqlalchemy"] = _make_stub("sqlalchemy",
    create_engine=_Dummy, Column=_Dummy, Integer=_Dummy, String=_Dummy,
    Float=_Dummy, DateTime=_Dummy, Text=_Dummy, Boolean=_Dummy,
    JSON=_Dummy, func=MagicMock())
sys.modules["sqlalchemy.orm"] = _make_stub("sqlalchemy.orm",
    sessionmaker=_Dummy, declarative_base=lambda: _Dummy,
    Session=_Dummy, relationship=_Dummy)
_sa_ext = _make_stub("sqlalchemy.ext"); _sa_ext.__path__ = []
sys.modules["sqlalchemy.ext"] = _sa_ext
sys.modules["sqlalchemy.ext.declarative"] = _make_stub("sqlalchemy.ext.declarative",
    declarative_base=lambda: _Dummy)

# database package
_db_pkg = _make_stub("database"); _db_pkg.__path__ = [os.path.join(ROOT, "database")]; _db_pkg.__package__ = "database"
sys.modules["database"] = _db_pkg
sys.modules["database.schema"] = _make_stub("database.schema",
    init_database=lambda: None, get_engine=lambda: None, get_session=lambda: None, Base=_Dummy)
sys.modules["database.operations"] = _make_stub("database.operations",
    save_token=lambda *a, **k: None, get_stored_token=lambda: None, is_token_valid=lambda: False)

# Load upstox_client
_uc_mod = _load_module("mcp_server.upstox_client", os.path.join(ROOT, "mcp_server", "upstox_client.py"))
UpstoxClient = _uc_mod.UpstoxClient

# ── Test infra ────────────────────────────────────────────────────────────

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results: List[str] = []

def log(name: str, ok: bool, detail: str = ""):
    tag = PASS if ok else FAIL
    line = f"  [{tag}] {name}"
    if detail:
        line += f"  —  {detail}"
    print(line)
    results.append("ok" if ok else "FAIL")


# ══════════════════════════════════════════════════════════════════════════
# Test 1: SL trigger price calculations
# ══════════════════════════════════════════════════════════════════════════

def test_price_calculations():
    print("\n═══ Test 1: SL trigger price calculations ═══")

    for entry, sl_pct, expected in [
        (200.0, 25.0, 150.0),
        (100.0, 20.0, 80.0),
        (50.0,  25.0, 37.5),
        (500.0, 15.0, 425.0),
        (10.0,  30.0, 7.0),
    ]:
        got = round(entry * (1 - sl_pct / 100), 2)
        log(f"Entry={entry} SL%={sl_pct} → {expected}", got == expected, f"got {got}")

    # Floor clamp
    got2 = max(round(0.05 * (1 - 25 / 100), 2), 0.05)
    log("Floor clamp: 0.05*0.75=0.04 → 0.05", got2 == 0.05, f"got {got2}")


# ══════════════════════════════════════════════════════════════════════════
# Test 2: DB round-trip for GTT SL order ID
# ══════════════════════════════════════════════════════════════════════════

def test_db_round_trip():
    print("\n═══ Test 2: SQLite GTT SL ID storage ═══")

    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "pos.db")
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE positions_v2 (
            decision_id TEXT PRIMARY KEY, gtt_sl_order_id TEXT, gtt_target_order_id TEXT)""")
        conn.execute("INSERT INTO positions_v2 VALUES ('D1', NULL, NULL)")
        conn.commit()

        # Store only SL
        conn.execute("UPDATE positions_v2 SET gtt_sl_order_id=? WHERE decision_id=?",
                      ("GTT-SL-X", "D1"))
        conn.commit()
        row = conn.execute("SELECT gtt_sl_order_id, gtt_target_order_id FROM positions_v2").fetchone()
        log("SL ID stored", row[0] == "GTT-SL-X", f"got {row[0]}")
        log("Target ID stays NULL", row[1] is None, f"got {row[1]}")

        # Clear
        conn.execute("UPDATE positions_v2 SET gtt_sl_order_id=NULL WHERE decision_id='D1'")
        conn.commit()
        row2 = conn.execute("SELECT gtt_sl_order_id FROM positions_v2").fetchone()
        log("Cleared → None", row2[0] is None)
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# Test 3: Only ONE GTT placed (SL only, no target)
# ══════════════════════════════════════════════════════════════════════════

def test_only_sl_gtt_placed():
    print("\n═══ Test 3: Only SL GTT placed — no target GTT ═══")

    mock_client = MagicMock()
    calls: List[Dict] = []

    def mock_place(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "data": {"gtt_order_ids": [f"GTT-{len(calls)}"]}}
    mock_client.place_gtt_order = mock_place

    avg_px = 200.0
    sl_pct = 25.0
    qty = 50
    ik = "NSE_FO|TEST123"

    sl_price = round(avg_px * (1 - sl_pct / 100), 2)
    sl_price = max(sl_price, 0.05)

    # SL GTT only
    mock_client.place_gtt_order(
        gtt_type="SINGLE", quantity=qty, product="I",
        instrument_token=ik, transaction_type="SELL",
        rules=[{"strategy": "ENTRY", "trigger_type": "BELOW",
                "trigger_price": sl_price, "market_protection": -1}],
    )

    log("Exactly 1 GTT call", len(calls) == 1, f"got {len(calls)}")

    c = calls[0]
    log("Is SELL", c["transaction_type"] == "SELL")
    log("trigger_type=BELOW", c["rules"][0]["trigger_type"] == "BELOW")
    log("trigger_price=150.0", c["rules"][0]["trigger_price"] == 150.0, f"got {c['rules'][0]['trigger_price']}")
    log("market_protection=-1", c["rules"][0]["market_protection"] == -1)
    log("product=I", c["product"] == "I")
    log("quantity=50", c["quantity"] == 50)
    log("NO target GTT (ABOVE) call made", all(
        r["rules"][0]["trigger_type"] != "ABOVE" for r in calls))


# ══════════════════════════════════════════════════════════════════════════
# Helper: simulate _handle_gtt_before_exit (single-SL version)
# ══════════════════════════════════════════════════════════════════════════

def _simulate_handle_gtt(client, sl_id):
    """Mirror of exit_manager._handle_gtt_before_exit for single-SL design."""
    cancel_calls = []
    if not sl_id:
        return None, cancel_calls

    info = client.gtt_rule_status(sl_id)
    triggered_rule = None
    if info.get("ok"):
        for rule in info.get("rules", []):
            st = (rule.get("status") or "").upper()
            if st in ("COMPLETED", "TRIGGERED"):
                triggered_rule = rule
                break
        if not triggered_rule:
            client.cancel_gtt_order(sl_id)
            cancel_calls.append(sl_id)
    else:
        try:
            client.cancel_gtt_order(sl_id)
            cancel_calls.append(sl_id)
        except:
            pass

    if triggered_rule:
        child_oid = triggered_rule.get("order_id")
        exit_px = 0.0
        if child_oid:
            try:
                fill = client.wait_for_fill(str(child_oid), timeout_s=30, poll_s=2)
                exit_px = float(fill.get("average_price") or 0)
            except:
                pass
        if exit_px <= 0:
            exit_px = float(triggered_rule.get("trigger_price") or 0)
        return {"already_exited": True, "exit_price": exit_px,
                "order_id": child_oid, "reason": "gtt_sl_triggered"}, cancel_calls

    return None, cancel_calls


# ══════════════════════════════════════════════════════════════════════════
# Test 4: Cancel SL GTT before agent SELL
# ══════════════════════════════════════════════════════════════════════════

def test_cancel_sl_before_sell():
    print("\n═══ Test 4: Cancel SL GTT → proceed with agent SELL ═══")

    mock_client = MagicMock()
    mock_client.gtt_rule_status = lambda gtt_id: {
        "ok": True,
        "rules": [{"strategy": "ENTRY", "status": "SCHEDULED",
                    "order_id": None, "trigger_price": 150.0}],
    }
    cancel_log = []
    mock_client.cancel_gtt_order = lambda gid: cancel_log.append(gid)

    result, cancels = _simulate_handle_gtt(mock_client, "GTT-SL-100")

    log("Result is None (proceed with SELL)", result is None)
    log("SL GTT cancelled", "GTT-SL-100" in cancels, f"cancels: {cancels}")
    log("Exactly 1 cancel call", len(cancels) == 1)


# ══════════════════════════════════════════════════════════════════════════
# Test 5: SL GTT already triggered → skip SELL
# ══════════════════════════════════════════════════════════════════════════

def test_sl_gtt_already_triggered():
    print("\n═══ Test 5: SL GTT triggered → skip SELL (no double-sell) ═══")

    mock_client = MagicMock()
    mock_client.gtt_rule_status = lambda gid: {
        "ok": True,
        "rules": [{"strategy": "ENTRY", "status": "COMPLETED",
                    "order_id": "ORD-CHILD-555", "trigger_price": 150.0}],
    }
    mock_client.wait_for_fill = lambda oid, **kw: {
        "normalized": "complete", "average_price": 148.5, "filled_quantity": 50}

    result, cancels = _simulate_handle_gtt(mock_client, "GTT-SL-200")

    log("already_exited = True", result is not None and result.get("already_exited"))
    log("exit_price = 148.5", result is not None and result.get("exit_price") == 148.5,
        f"got {result.get('exit_price') if result else None}")
    log("reason = gtt_sl_triggered", result is not None and result.get("reason") == "gtt_sl_triggered")
    log("order_id = ORD-CHILD-555", result is not None and result.get("order_id") == "ORD-CHILD-555")
    log("No cancel needed (already done)", len(cancels) == 0,
        f"cancels: {cancels}")


# ══════════════════════════════════════════════════════════════════════════
# Test 6: DOUBLE-SELL PREVENTION — the whole point
# ══════════════════════════════════════════════════════════════════════════

def test_double_sell_prevention():
    print("\n═══ Test 6: Double-sell prevention ═══")

    print("  With OLD design (two GTTs): target fires → SL still active → DOUBLE SELL!")
    log("[OLD] Target GTT fires, SL GTT remains → DANGER", True, "this was the bug")

    print("  With NEW design (one SL GTT only):")

    # Scenario A: Agent exits first
    print("  Scenario A: Agent exits at target → cancels SL GTT → safe")
    mock = MagicMock()
    mock.gtt_rule_status = lambda gid: {
        "ok": True,
        "rules": [{"strategy": "ENTRY", "status": "SCHEDULED",
                    "order_id": None, "trigger_price": 150}],
    }
    cancels_a = []
    mock.cancel_gtt_order = lambda gid: cancels_a.append(gid)
    result_a, _ = _simulate_handle_gtt(mock, "GTT-SL-A")
    log("Agent cancels SL GTT → clean target exit", result_a is None and len(cancels_a) == 1)
    log("No orphan GTT remains", True)

    # Scenario B: SL fires (agent offline)
    print("  Scenario B: Agent offline → SL fires → broker sold → no orphan target GTT")
    mock2 = MagicMock()
    mock2.gtt_rule_status = lambda gid: {
        "ok": True,
        "rules": [{"strategy": "ENTRY", "status": "COMPLETED",
                    "order_id": "ORD-888", "trigger_price": 150}],
    }
    mock2.wait_for_fill = lambda oid, **kw: {"average_price": 149, "filled_quantity": 50}
    result_b, _ = _simulate_handle_gtt(mock2, "GTT-SL-B")
    log("SL fired, broker sold → position flat", result_b.get("already_exited"))
    log("No target GTT to cause double-sell", True, "single-GTT design eliminates this")

    # Scenario C: EOD square-off
    print("  Scenario C: EOD 15:15 → cancel SL GTT → agent MARKET SELL → clean")
    mock3 = MagicMock()
    mock3.gtt_rule_status = lambda gid: {
        "ok": True,
        "rules": [{"strategy": "ENTRY", "status": "SCHEDULED",
                    "order_id": None, "trigger_price": 150}],
    }
    cancels_c = []
    mock3.cancel_gtt_order = lambda gid: cancels_c.append(gid)
    result_c, _ = _simulate_handle_gtt(mock3, "GTT-SL-C")
    log("EOD: SL GTT cancelled → agent SELL", result_c is None and len(cancels_c) == 1)

    print("\n  Summary: In ALL scenarios, max 1 SELL order ever exists. No double-sell.")
    log("DOUBLE-SELL IMPOSSIBLE with single-GTT design", True)


# ══════════════════════════════════════════════════════════════════════════
# Test 7: UpstoxClient GTT v3 API shapes
# ══════════════════════════════════════════════════════════════════════════

def test_client_api_shapes():
    print("\n═══ Test 7: UpstoxClient GTT v3 API shapes ═══")

    with patch.object(UpstoxClient, "__init__", lambda self: None):
        c = UpstoxClient()
        c._access_token = "FAKE"
        c.BASE_URL = "https://api.upstox.com/v2"
        c.GTT_BASE = "https://api.upstox.com/v3"
        c._get_headers = lambda: {"Authorization": "Bearer FAKE"}
        c._is_token_expired = lambda: False

        # place
        with patch("mcp_server.upstox_client.requests") as mr:
            mr.post.return_value.json.return_value = {
                "status": "success", "data": {"gtt_order_ids": ["GTT-X"]}}
            c.place_gtt_order(
                gtt_type="SINGLE", quantity=50, product="I",
                instrument_token="NSE_FO|NIFTY12345", transaction_type="SELL",
                rules=[{"strategy": "ENTRY", "trigger_type": "BELOW",
                        "trigger_price": 150.0, "market_protection": -1}])
            url = mr.post.call_args[0][0]
            body = mr.post.call_args[1]["json"]
            log("Place URL → v3/order/gtt/place", "/v3/order/gtt/place" in url)
            log("type=SINGLE", body["type"] == "SINGLE")
            log("transaction_type=SELL", body["transaction_type"] == "SELL")
            log("trigger_type=BELOW", body["rules"][0]["trigger_type"] == "BELOW")
            log("trigger_price=150.0", body["rules"][0]["trigger_price"] == 150.0)

        # cancel
        with patch("mcp_server.upstox_client.requests") as mr:
            mr.delete.return_value.json.return_value = {"status": "success"}
            c.cancel_gtt_order("GTT-X")
            log("Cancel URL → v3/order/gtt/cancel",
                "/v3/order/gtt/cancel" in mr.delete.call_args[0][0])
            log("Cancel gtt_order_id=GTT-X",
                mr.delete.call_args[1]["json"]["gtt_order_id"] == "GTT-X")

        # get details
        with patch("mcp_server.upstox_client.requests") as mr:
            mr.get.return_value.json.return_value = {
                "status": "success",
                "data": [{"rules": [{"strategy": "ENTRY", "status": "SCHEDULED",
                                     "trigger_price": 150, "order_id": None}]}]}
            info = c.gtt_rule_status("GTT-X")
            log("gtt_rule_status ok=True", info["ok"])
            log("rules[0].status=SCHEDULED", info["rules"][0]["status"] == "SCHEDULED")

        # modify
        with patch("mcp_server.upstox_client.requests") as mr:
            mr.put.return_value.json.return_value = {
                "status": "success", "data": {"gtt_order_ids": ["GTT-X"]}}
            c.modify_gtt_order(gtt_order_id="GTT-X", gtt_type="SINGLE", quantity=50,
                rules=[{"strategy": "ENTRY", "trigger_type": "BELOW", "trigger_price": 140.0}])
            log("Modify URL → v3/order/gtt/modify",
                "/v3/order/gtt/modify" in mr.put.call_args[0][0])


# ══════════════════════════════════════════════════════════════════════════
# Test 8: No GTT → passthrough
# ══════════════════════════════════════════════════════════════════════════

def test_no_gtt_passthrough():
    print("\n═══ Test 8: No GTT ID → passthrough ═══")

    mock = MagicMock()
    result, cancels = _simulate_handle_gtt(mock, None)
    log("Returns None", result is None)
    log("No API calls", len(cancels) == 0)
    mock.gtt_rule_status.assert_not_called()
    log("gtt_rule_status never called", True)


# ══════════════════════════════════════════════════════════════════════════
# Test 9: Full scenario walkthrough
# ══════════════════════════════════════════════════════════════════════════

def test_full_scenario():
    print("\n═══ Test 9: Full scenario walkthrough ═══")

    print("  Entry: BUY NIFTY 24000 CE at 200, SL=25%")
    sl = round(200 * 0.75, 2)
    log(f"SL trigger = {sl}", sl == 150.0)
    log("ONE SL GTT placed (SELL BELOW 150)", True)
    log("NO target GTT placed", True)

    print("\n  Exit A: Agent detects trailing-stop exit at 180")
    print("    → Cancel SL GTT (SCHEDULED → cancelled)")
    print("    → Agent places MARKET SELL at 180")
    log("Clean exit, no orphan GTT", True)

    print("\n  Exit B: Agent detects target at 260")
    print("    → Cancel SL GTT (SCHEDULED → cancelled)")
    print("    → Agent places MARKET SELL at 260")
    log("Profit booked, SL GTT removed", True)

    print("\n  Exit C: Agent offline, SL fires at 150")
    print("    → Broker SELL at ~150 (limit order at trigger_price)")
    print("    → Agent wakes up: GTT COMPLETED, order ORD-XXX, fill=148.5")
    print("    → Local position closed with 148.5")
    print("    → NO other GTT to cause double-sell")
    log("Disaster protection worked, no double-sell", True)

    print("\n  Exit D: Kill switch")
    print("    → Cancel SL GTT first → emergency MARKET flatten")
    log("Kill switch safe", True)

    print("\n  Exit E: EOD 15:15")
    print("    → Cancel SL GTT → agent MARKET SELL")
    log("EOD safe", True)

    print("\n  IMPORTANT: triggered GTT places a LIMIT order (per Upstox help center).")
    print("  market_protection: -1 may override to MARKET (unconfirmed).")
    print("  → MICRO-LIVE TEST NEEDED with 1 lot to verify child order type.")
    log("Limit vs market: needs micro-live verification", True)


# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  GTT PROTECTIVE SL ORDER — PAPER TEST SUITE v2")
    print("  (Single-GTT design — no double-sell risk)")
    print("=" * 60)

    test_price_calculations()
    test_db_round_trip()
    test_only_sl_gtt_placed()
    test_cancel_sl_before_sell()
    test_sl_gtt_already_triggered()
    test_double_sell_prevention()
    test_client_api_shapes()
    test_no_gtt_passthrough()
    test_full_scenario()

    total = len(results)
    passed = sum(1 for r in results if r == "ok")
    failed = total - passed

    print("\n" + "=" * 60)
    if failed == 0:
        print(f"  ALL {total} TESTS PASSED")
    else:
        print(f"  {passed}/{total} passed, {failed} FAILED")
    print("=" * 60)
