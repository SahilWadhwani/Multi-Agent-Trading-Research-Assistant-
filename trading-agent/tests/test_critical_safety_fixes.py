from datetime import datetime
from types import SimpleNamespace


def test_gatekeeper_accepts_fractional_confidence_scale():
    from brain.pre_trade_gatekeeper import PreTradeGatekeeper

    gk = PreTradeGatekeeper()
    assert gk._calibrate_probability(0.85, "trending_bullish", 24) > 0.55

    result = gk.validate_execution(
        signal={
            "direction": "BUY_CE",
            "llm_confidence": 0.85,
            "entry_premium": 100,
            "lots": 2,
        },
        market_data={
            "regime": "trending_bullish",
            "spot_price": 1000,
            "support": 950,
            "resistance": 1100,
            "hours_to_expiry": 24,
        },
    )
    assert result["status"] == "EXECUTE"
    assert result["win_probability"] > 0.55
    assert result["size"] == 2


def test_position_close_uses_net_pnl_after_costs(tmp_path, monkeypatch):
    from brain.position_tracker import OpenPosition, PositionTracker
    import brain.position_tracker as position_tracker_mod

    monkeypatch.setattr(
        position_tracker_mod,
        "get_upstox_client",
        lambda: SimpleNamespace(),
    )

    tracker = PositionTracker(db_path=str(tmp_path / "positions.db"))
    monkeypatch.setattr(tracker.decision_log, "update_outcome", lambda *a, **k: None)

    pos = OpenPosition(
        decision_id="D1",
        symbol="NIFTY",
        strike=24000,
        option_type="CE",
        entry_price=100,
        entry_time=datetime.now(),
        lots=1,
        lot_size=50,
        instrument_key="NSE_FO|TEST",
    )
    tracker.add_position(pos)
    rec = tracker.close_position_record(pos, exit_price=110, exit_reason="unit_test")

    assert rec["gross_pnl_rs"] == 500
    assert rec["costs_rs"] > 0
    assert rec["pnl_rs"] < rec["gross_pnl_rs"]


def test_exit_ticker_live_uses_canonical_safe_exit(monkeypatch):
    import execution.exit_ticker as ticker_mod
    import execution.exit_manager as exit_manager
    from execution.runtime_safety import TradingMode

    calls = []
    pos = SimpleNamespace(decision_id="D1")
    tracker = SimpleNamespace(get_open_positions=lambda: [pos])

    monkeypatch.setattr(ticker_mod, "load_trading_mode", lambda: TradingMode.LIVE)
    monkeypatch.setattr(ticker_mod, "get_position_tracker", lambda: tracker)

    import database.operations as db_ops
    import mcp_server.upstox_client as upstox

    monkeypatch.setattr(db_ops, "is_token_valid", lambda: True)
    monkeypatch.setattr(upstox, "get_upstox_client", lambda: SimpleNamespace(is_authenticated=lambda: True))

    def fake_safe_exit(**kwargs):
        calls.append(kwargs)
        return {"broker_order_id": "OID-1"}

    monkeypatch.setattr(exit_manager, "exit_position_via_broker_safely", fake_safe_exit)

    ticker = ticker_mod.ExitTicker()
    ticker.register_position(
        decision_id="D1",
        symbol="NIFTY",
        instrument_key="NSE_FO|TEST",
        entry_price=100,
        sl_pct=25,
        target_pct=30,
        qty=50,
        lot_size=50,
    )
    ticker._execute_exit_async(
        "D1",
        {
            "symbol": "NIFTY",
            "instrument_key": "NSE_FO|TEST",
            "entry_price": 100,
            "sl_price": 75,
            "target_price": 130,
            "qty": 50,
        },
        "SL_HIT",
        74,
    )

    assert len(calls) == 1
    assert calls[0]["pos"] is pos
    assert calls[0]["exit_reason"] == "sl_hit_exit_ticker"


def test_gtt_audit_replaces_missing_protection(monkeypatch):
    import execution.reconciliation as reconciliation
    import execution.lean_fo_executor as executor
    import brain.position_tracker as position_tracker

    pos = SimpleNamespace(
        decision_id="D1",
        instrument_key="NSE_FO|TEST",
        gtt_sl_order_id=None,
        lots=1,
        lot_size=50,
        entry_price=100,
        stop_loss_pct=25,
    )
    tracker = SimpleNamespace(
        get_open_positions=lambda: [pos],
        clear_gtt_ids=lambda decision_id: None,
    )
    placed = []

    monkeypatch.setattr(position_tracker, "get_position_tracker", lambda: tracker)

    def fake_place(**kwargs):
        placed.append(kwargs)
        return {"ok": True, "gtt_id": "GTT-1", "error": None}

    monkeypatch.setattr(executor, "_place_protective_sl_gtt", fake_place)

    ok, report = reconciliation.audit_and_recover_gtt_protection(client=SimpleNamespace())

    assert ok is True
    assert report["missing_gtt"] == ["D1"]
    assert placed[0]["qty"] == 50
    assert placed[0]["stop_loss_pct"] == 25


def test_duplicate_local_entry_is_blocked(monkeypatch):
    import execution.lean_fo_executor as executor

    pos = SimpleNamespace(instrument_key="NSE_FO|TEST")
    tracker = SimpleNamespace(get_open_positions=lambda: [pos])
    monkeypatch.setattr(executor, "get_position_tracker", lambda: tracker)

    assert executor._has_local_open_position("NSE_FO|TEST") is True
    assert executor._has_local_open_position("NSE_FO|OTHER") is False
