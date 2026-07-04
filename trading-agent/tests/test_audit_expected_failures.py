"""Executable audit findings.

These xfail tests encode safety requirements that the current implementation
does not meet yet. They should be flipped to normal tests as the fixes land.
"""

from types import SimpleNamespace

import pytest


@pytest.mark.xfail(reason="UpstoxClient currently uses SQLite token store, not UPSTOX_ACCESS_TOKEN env only")
def test_upstox_client_uses_access_token_from_env_only(monkeypatch):
    import mcp_server.upstox_client as upstox_client

    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "ENV_TOKEN_FOR_TEST")
    monkeypatch.setattr(upstox_client, "get_stored_token", lambda: None)
    monkeypatch.setattr(upstox_client, "is_token_valid", lambda: False)

    client = upstox_client.UpstoxClient()

    assert client.access_token == "ENV_TOKEN_FOR_TEST"


@pytest.mark.xfail(reason="SmartExitManager._fallback_decision references missing EXCELLENT_PROFIT")
def test_smart_exit_fallback_excellent_profit_does_not_raise():
    from brain.smart_exit import SmartExitManager

    decision = SmartExitManager()._fallback_decision(
        pnl_pct=55.0,
        highest_pnl_pct=55.0,
        spot_move_pct=1.0,
        option_type="CE",
    )

    assert decision.should_exit is True


@pytest.mark.xfail(reason="ExitTicker asks FODataFeed for methods that do not exist")
def test_exit_ticker_market_context_methods_exist():
    from data_feeds.fo_data_feed import FODataFeed

    assert hasattr(FODataFeed, "get_market_regime")
    assert hasattr(FODataFeed, "get_iv_level")


@pytest.mark.xfail(reason="Startup recovery defaults recovered live SL/target to 25/50 instead of original signal")
def test_recovered_entry_preserves_original_risk_params(monkeypatch):
    import execution.reconciliation as reconciliation

    captured = []

    tracker = SimpleNamespace(
        has_position=lambda decision_id: False,
        LOT_SIZES={"NIFTY": 65},
        add_position=lambda pos: captured.append(pos),
    )
    decision = SimpleNamespace(
        symbol="NIFTY",
        strike=24000,
        option_type="CE",
        lots=1,
        stop_loss_pct=20.0,
        target_pct=30.0,
    )
    client = SimpleNamespace(parse_order_details=lambda resp: {"average_price": 100.0, "filled_quantity": 65})

    monkeypatch.setattr(reconciliation, "get_position_tracker", lambda: tracker, raising=False)
    monkeypatch.setattr(
        reconciliation,
        "get_decision_log",
        lambda: SimpleNamespace(get_decision=lambda decision_id: decision),
        raising=False,
    )

    reconciliation._recover_filled_entry_position(
        client,
        {
            "decision_id": "D1",
            "symbol": "NIFTY",
            "instrument_key": "NSE_FO|TEST",
            "quantity": 65,
        },
        {"status": "success", "data": {"status": "complete"}},
    )

    assert captured[0].stop_loss_pct == 20.0
    assert captured[0].target_pct == 30.0
