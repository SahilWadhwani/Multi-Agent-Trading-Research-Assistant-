#!/usr/bin/env python3
"""
Phase 1 Test Suite for QUANT-1 Trading Agent

Tests all components WITHOUT requiring live Upstox connection.
Uses mock data to verify the brain pipeline works correctly.

Run with: python -m tests.test_phase1
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List

# Add parent path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Test results tracking
test_results = {
    "passed": 0,
    "failed": 0,
    "errors": [],
}


def log_test(name: str, passed: bool, message: str = ""):
    """Log test result."""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if not passed and message:
        print(f"         {message}")
    
    if passed:
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
        test_results["errors"].append(f"{name}: {message}")


def generate_mock_price_data(days: int = 100) -> Dict[str, List[float]]:
    """Generate realistic mock OHLCV data for testing."""
    import random
    
    base_price = 1500.0  # Starting price
    prices = []
    highs = []
    lows = []
    volumes = []
    
    for i in range(days):
        # Random walk with slight upward bias
        change = random.uniform(-2, 2.5)
        base_price = max(100, base_price + change)
        
        close = round(base_price, 2)
        high = round(close + random.uniform(1, 5), 2)
        low = round(close - random.uniform(1, 5), 2)
        volume = random.randint(500000, 5000000)
        
        prices.append(close)
        highs.append(high)
        lows.append(low)
        volumes.append(volume)
    
    return {
        "closes": prices,
        "highs": highs,
        "lows": lows,
        "volumes": volumes,
    }


def test_technical_indicators():
    """Test the technical indicators engine."""
    print("\n" + "=" * 60)
    print("TEST: Technical Indicators Engine")
    print("=" * 60)
    
    try:
        from data_feeds.technical_indicators import TechnicalIndicators
        log_test("Import TechnicalIndicators", True)
    except Exception as e:
        log_test("Import TechnicalIndicators", False, str(e))
        return
    
    # Generate mock data
    data = generate_mock_price_data(100)
    closes = data["closes"]
    highs = data["highs"]
    lows = data["lows"]
    volumes = data["volumes"]
    
    # Test RSI
    try:
        rsi = TechnicalIndicators.calculate_rsi(closes)
        has_values = any(v is not None for v in rsi)
        last_rsi = rsi[-1]
        valid_range = last_rsi is None or (0 <= last_rsi <= 100)
        log_test("RSI Calculation", has_values and valid_range, 
                 f"RSI={last_rsi:.2f}" if last_rsi else "No RSI values")
    except Exception as e:
        log_test("RSI Calculation", False, str(e))
    
    # Test MACD
    try:
        macd = TechnicalIndicators.calculate_macd(closes)
        has_macd = "macd" in macd and "signal" in macd and "histogram" in macd
        log_test("MACD Calculation", has_macd)
    except Exception as e:
        log_test("MACD Calculation", False, str(e))
    
    # Test SMA
    try:
        sma_20 = TechnicalIndicators.calculate_sma(closes, 20)
        sma_50 = TechnicalIndicators.calculate_sma(closes, 50)
        has_sma = sma_20[-1] is not None and sma_50[-1] is not None
        log_test("SMA Calculation", has_sma, 
                 f"SMA20={sma_20[-1]:.2f}, SMA50={sma_50[-1]:.2f}" if has_sma else "")
    except Exception as e:
        log_test("SMA Calculation", False, str(e))
    
    # Test EMA
    try:
        ema_12 = TechnicalIndicators.calculate_ema(closes, 12)
        has_ema = ema_12[-1] is not None
        log_test("EMA Calculation", has_ema)
    except Exception as e:
        log_test("EMA Calculation", False, str(e))
    
    # Test Bollinger Bands
    try:
        bollinger = TechnicalIndicators.calculate_bollinger_bands(closes)
        has_bands = all(k in bollinger for k in ["upper", "middle", "lower"])
        valid_bands = bollinger["upper"][-1] > bollinger["middle"][-1] > bollinger["lower"][-1]
        log_test("Bollinger Bands", has_bands and valid_bands)
    except Exception as e:
        log_test("Bollinger Bands", False, str(e))
    
    # Test ATR
    try:
        atr = TechnicalIndicators.calculate_atr(highs, lows, closes)
        has_atr = any(v is not None for v in atr)
        log_test("ATR Calculation", has_atr)
    except Exception as e:
        log_test("ATR Calculation", False, str(e))
    
    # Test Trend Detection
    try:
        trend = TechnicalIndicators.detect_trend(closes)
        valid_trend = trend in ["BULLISH", "BEARISH", "NEUTRAL"]
        log_test("Trend Detection", valid_trend, f"Trend={trend}")
    except Exception as e:
        log_test("Trend Detection", False, str(e))
    
    # Test Signal Generation
    try:
        signals = TechnicalIndicators.generate_signals(closes, highs, lows, volumes)
        has_keys = all(k in signals for k in ["current_price", "indicators", "analysis"])
        log_test("Signal Generation", has_keys)
    except Exception as e:
        log_test("Signal Generation", False, str(e))


def test_trader_agent():
    """Test the Trader Agent."""
    print("\n" + "=" * 60)
    print("TEST: Trader Agent")
    print("=" * 60)
    
    try:
        from agents.traders.trader import TraderAgent, TradeProposal
        log_test("Import TraderAgent", True)
    except Exception as e:
        log_test("Import TraderAgent", False, str(e))
        return
    
    # Create mock technical report
    mock_tech_report = {
        "symbol": "TEST",
        "exchange": "NSE",
        "quote": {"ltp": 1500.0},
        "bias": "BULLISH",
        "confidence": 0.75,
        "signals": [
            {"indicator": "RSI", "action": "BUY", "strength": "STRONG"},
            {"indicator": "MACD", "action": "BUY", "strength": "MEDIUM"},
        ],
        "support_resistance": {
            "nearest_support": {"level": 1450, "type": "SMA 50"},
            "nearest_resistance": {"level": 1550, "type": "Recent High"},
        },
        "summary": "Test technical report",
    }
    
    # Test proposal generation
    try:
        trader = TraderAgent()
        proposal = trader.generate_proposal(
            technical_report=mock_tech_report,
            available_capital=100000,
            existing_positions=[],
            product_type="INTRADAY",
        )
        
        valid_proposal = (
            isinstance(proposal, TradeProposal) and
            proposal.action in ["BUY", "SELL", "HOLD"] and
            proposal.quantity >= 0 and
            proposal.price_estimate > 0
        )
        log_test("Generate Trade Proposal", valid_proposal, 
                 f"Action={proposal.action}, Qty={proposal.quantity}")
    except Exception as e:
        log_test("Generate Trade Proposal", False, str(e))
    
    # Test HOLD scenario (low confidence)
    try:
        low_conf_report = mock_tech_report.copy()
        low_conf_report["confidence"] = 0.3
        low_conf_report["bias"] = "NEUTRAL"
        
        proposal = trader.generate_proposal(
            technical_report=low_conf_report,
            available_capital=100000,
        )
        is_hold = proposal.action == "HOLD"
        log_test("Low Confidence = HOLD", is_hold, f"Action={proposal.action}")
    except Exception as e:
        log_test("Low Confidence = HOLD", False, str(e))


def test_risk_manager():
    """Test the Risk Manager."""
    print("\n" + "=" * 60)
    print("TEST: Risk Manager")
    print("=" * 60)
    
    try:
        from agents.risk.risk_manager import RiskManager, RiskAssessment
        log_test("Import RiskManager", True)
    except Exception as e:
        log_test("Import RiskManager", False, str(e))
        return
    
    risk_manager = RiskManager()
    
    # Test valid trade
    try:
        valid_proposal = {
            "action": "BUY",
            "symbol": "TEST",
            "quantity": 10,
            "price_estimate": 1500,
            "confidence": 0.7,
            "stop_loss": 1470,
            "target": 1560,
            "risk_reward_ratio": 2.0,
        }
        
        assessment = risk_manager.assess_trade(
            proposal=valid_proposal,
            account_balance=100000,
            force_check_market_hours=False,  # Skip market hours for testing
        )
        
        # Trade value = 10 * 1500 = 15000 = 15% of 100000 (under 20%)
        log_test("Valid Trade Approved", assessment.approved, 
                 f"Risk Score={assessment.risk_score:.2f}")
    except Exception as e:
        log_test("Valid Trade Approved", False, str(e))
    
    # Test position size violation
    try:
        oversized_proposal = {
            "action": "BUY",
            "symbol": "TEST",
            "quantity": 100,
            "price_estimate": 1500,
            "confidence": 0.7,
        }
        
        assessment = risk_manager.assess_trade(
            proposal=oversized_proposal,
            account_balance=100000,  # 100 * 1500 = 150000 > 20% of 100000
            force_check_market_hours=False,
        )
        
        rejected = not assessment.approved
        has_violation = len(assessment.violations) > 0
        log_test("Oversized Trade Rejected", rejected and has_violation,
                 f"Violations: {len(assessment.violations)}")
    except Exception as e:
        log_test("Oversized Trade Rejected", False, str(e))
    
    # Test HOLD always approved
    try:
        hold_proposal = {"action": "HOLD", "symbol": "TEST"}
        assessment = risk_manager.assess_trade(
            proposal=hold_proposal,
            account_balance=100000,
            force_check_market_hours=False,
        )
        log_test("HOLD Always Approved", assessment.approved)
    except Exception as e:
        log_test("HOLD Always Approved", False, str(e))


def test_portfolio_manager():
    """Test the Portfolio Manager."""
    print("\n" + "=" * 60)
    print("TEST: Portfolio Manager")
    print("=" * 60)
    
    try:
        from agents.managers.portfolio_manager import PortfolioManager, FinalDecision
        log_test("Import PortfolioManager", True)
    except Exception as e:
        log_test("Import PortfolioManager", False, str(e))
        return
    
    pm = PortfolioManager()
    
    # Test decision making
    try:
        tech_report = {
            "symbol": "TEST",
            "bias": "BULLISH",
            "confidence": 0.75,
            "signals": [],
            "summary": "Test",
        }
        
        trade_proposal = {
            "action": "BUY",
            "symbol": "TEST",
            "quantity": 10,
            "price_estimate": 1500,
            "confidence": 0.75,
            "stop_loss": 1470,
            "target": 1560,
            "product_type": "INTRADAY",
        }
        
        risk_assessment = {
            "approved": True,
            "violations": [],
            "warnings": [],
            "risk_score": 0.3,
            "adjusted_quantity": 10,
        }
        
        decision = pm.make_decision(
            technical_report=tech_report,
            trade_proposal=trade_proposal,
            risk_assessment=risk_assessment,
        )
        
        valid_decision = (
            isinstance(decision, FinalDecision) and
            decision.action in ["BUY", "SELL", "HOLD", "REJECTED"]
        )
        log_test("Make Decision", valid_decision, 
                 f"Action={decision.action}, Approved={decision.execution_approved}")
    except Exception as e:
        log_test("Make Decision", False, str(e))
    
    # Test rejection on risk violation
    try:
        failed_risk = {
            "approved": False,
            "violations": ["Position too large"],
            "warnings": [],
            "risk_score": 0.9,
        }
        
        decision = pm.make_decision(
            technical_report=tech_report,
            trade_proposal=trade_proposal,
            risk_assessment=failed_risk,
        )
        
        is_rejected = decision.action == "REJECTED"
        log_test("Risk Violation = REJECTED", is_rejected, f"Action={decision.action}")
    except Exception as e:
        log_test("Risk Violation = REJECTED", False, str(e))


def test_brain_orchestrator_mock():
    """Test the Brain Orchestrator with mock data."""
    print("\n" + "=" * 60)
    print("TEST: Brain Orchestrator (Mock Mode)")
    print("=" * 60)
    
    try:
        from brain.orchestrator import TradingBrain
        log_test("Import TradingBrain", True)
    except Exception as e:
        log_test("Import TradingBrain", False, str(e))
        return
    
    # Test instantiation
    try:
        brain = TradingBrain(paper_mode=True)
        log_test("Create TradingBrain", True)
    except Exception as e:
        log_test("Create TradingBrain", False, str(e))
        return
    
    # Test status
    try:
        status = brain.get_status()
        has_keys = all(k in status for k in ["status", "paper_mode", "timestamp"])
        log_test("Get Status", has_keys, f"Status={status.get('status')}")
    except Exception as e:
        log_test("Get Status", False, str(e))


def test_guardrails():
    """Test the guardrails are immutable and enforced."""
    print("\n" + "=" * 60)
    print("TEST: Guardrails (IMMUTABLE)")
    print("=" * 60)
    
    try:
        from mcp_server.guardrails import (
            GUARDRAILS,
            TradingGuardrails,
            validate_trade_risk,
            is_market_hours,
        )
        log_test("Import Guardrails", True)
    except Exception as e:
        log_test("Import Guardrails", False, str(e))
        return
    
    # Verify guardrail values
    try:
        assert GUARDRAILS["max_position_percent"] == 20
        assert GUARDRAILS["max_daily_loss_percent"] == 5
        assert GUARDRAILS["max_daily_trades"] == 50
        assert "add_funds" in GUARDRAILS["blocked_actions"]
        assert "withdraw_funds" in GUARDRAILS["blocked_actions"]
        log_test("Guardrail Values Correct", True)
    except AssertionError as e:
        log_test("Guardrail Values Correct", False, str(e))
    
    # Test market hours function exists
    try:
        result = is_market_hours()
        is_bool = isinstance(result, bool)
        log_test("is_market_hours() Returns Bool", is_bool)
    except Exception as e:
        log_test("is_market_hours() Returns Bool", False, str(e))
    
    # Test validation
    try:
        result = validate_trade_risk(
            symbol="TEST",
            side="BUY",
            quantity=10,
            price=100,
            available_margin=10000,
        )
        has_is_valid = hasattr(result, 'is_valid')
        log_test("validate_trade_risk() Works", has_is_valid)
    except Exception as e:
        log_test("validate_trade_risk() Works", False, str(e))


def test_database_operations():
    """Test database operations."""
    print("\n" + "=" * 60)
    print("TEST: Database Operations")
    print("=" * 60)
    
    try:
        from database.operations import (
            log_trade,
            get_today_pnl,
            get_daily_trade_count,
            get_current_holdings,
            log_agent_reasoning,
        )
        from database.schema import init_database
        log_test("Import Database Operations", True)
    except Exception as e:
        log_test("Import Database Operations", False, str(e))
        return
    
    # Initialize database
    try:
        init_database()
        log_test("Initialize Database", True)
    except Exception as e:
        log_test("Initialize Database", False, str(e))
        return
    
    # Test logging
    try:
        trade = log_trade(
            symbol="TEST",
            side="BUY",
            quantity=10,
            price=100,
            is_paper_trade=True,
            status="TEST",
        )
        has_id = trade.id is not None
        log_test("Log Trade", has_id)
    except Exception as e:
        log_test("Log Trade", False, str(e))
    
    # Test agent log
    try:
        log = log_agent_reasoning(
            ai_reasoning="Test reasoning",
            strategy_used="TEST_STRATEGY",
        )
        has_id = log.id is not None
        log_test("Log Agent Reasoning", has_id)
    except Exception as e:
        log_test("Log Agent Reasoning", False, str(e))
    
    # Test queries
    try:
        pnl = get_today_pnl()
        count = get_daily_trade_count()
        holdings = get_current_holdings()
        
        valid = isinstance(pnl, (int, float)) and isinstance(count, int) and isinstance(holdings, dict)
        log_test("Query Functions Work", valid)
    except Exception as e:
        log_test("Query Functions Work", False, str(e))


def run_all_tests():
    """Run all Phase 1 tests."""
    print("\n" + "=" * 60)
    print("   QUANT-1 PHASE 1 TEST SUITE")
    print("   " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)
    
    test_technical_indicators()
    test_trader_agent()
    test_risk_manager()
    test_portfolio_manager()
    test_brain_orchestrator_mock()
    test_guardrails()
    test_database_operations()
    
    # Summary
    print("\n" + "=" * 60)
    print("   TEST SUMMARY")
    print("=" * 60)
    print(f"   ✅ Passed: {test_results['passed']}")
    print(f"   ❌ Failed: {test_results['failed']}")
    print(f"   Total:   {test_results['passed'] + test_results['failed']}")
    
    if test_results["errors"]:
        print("\n   Errors:")
        for err in test_results["errors"]:
            print(f"   - {err}")
    
    print("=" * 60)
    
    return test_results["failed"] == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
