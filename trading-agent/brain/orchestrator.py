"""
Trading Brain Orchestrator
Coordinates all agents to make intelligent trading decisions.

This is the main entry point for the AI trading system.
Pipeline: Technical → News → Sentiment → Trader → Risk → Portfolio Manager
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.analysts.technical_analyst import TechnicalAnalyst
from agents.analysts.news_analyst import NewsAnalyst
from agents.analysts.sentiment_analyst import SentimentAnalyst
from agents.analysts.fo_analyst import FOAnalyst
from agents.traders.trader import TraderAgent
from agents.traders.fo_trader import FOTrader
from agents.risk.risk_manager import RiskManager
from agents.managers.portfolio_manager import PortfolioManager

from database.operations import (
    log_agent_reasoning,
    log_trade,
    get_current_holdings,
    get_current_portfolio_value,
    get_today_pnl,
)
from mcp_server.upstox_client import get_upstox_client
from mcp_server.guardrails import is_market_hours


class TradingBrain:
    """
    The Trading Brain - Orchestrates all agents for intelligent trading.
    
    Flow:
    1. Technical Analyst analyzes the stock
    2. Trader generates a trade proposal
    3. Risk Manager validates against guardrails
    4. Portfolio Manager makes the final decision
    
    This class is the main interface for Claude/Cursor to interact with.
    """
    
    def __init__(self, paper_mode: bool = True):
        """
        Initialize the Trading Brain.
        
        Args:
            paper_mode: If True, trades are simulated (logged but not executed)
        """
        self.paper_mode = paper_mode
        
        # Initialize all agents
        self.technical_analyst = TechnicalAnalyst()
        self.news_analyst = NewsAnalyst()
        self.sentiment_analyst = SentimentAnalyst()
        self.trader = TraderAgent()
        self.risk_manager = RiskManager()
        self.portfolio_manager = PortfolioManager()
        
        # F&O specific agents (Phase 3)
        self.fo_analyst = FOAnalyst()
        self.fo_trader = FOTrader()
        
        # Upstox client for live data and execution
        self.upstox_client = None
    
    def _get_upstox_client(self):
        """Lazy load Upstox client."""
        if self.upstox_client is None:
            self.upstox_client = get_upstox_client()
        return self.upstox_client
    
    def analyze_stock(self, symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """
        Perform comprehensive analysis on a stock.
        Returns technical analysis without making trade decisions.
        
        Use this to understand a stock before deciding to trade.
        """
        return self.technical_analyst.analyze(symbol, exchange)
    
    def get_account_info(self, require_auth: bool = True) -> Dict[str, Any]:
        """
        Get current account balance and portfolio info.
        
        Args:
            require_auth: If True, triggers auth flow if not authenticated.
                         If False, returns error without triggering auth.
        """
        client = self._get_upstox_client()
        
        # Check if already authenticated without triggering flow
        if not client.is_authenticated():
            if require_auth:
                if not client.ensure_authenticated():
                    return {
                        "error": "Authentication failed",
                        "authenticated": False,
                        "paper_mode": self.paper_mode,
                        "suggestion": "Run: python main.py --auth",
                    }
            else:
                return {
                    "error": "Not authenticated",
                    "authenticated": False,
                    "paper_mode": self.paper_mode,
                    "suggestion": "Run: python main.py --auth",
                }
        
        try:
            funds = client.get_funds_and_margin()
            positions = client.get_positions()
            holdings = client.get_holdings()
            
            available_balance = 0
            if funds.get("status") == "success" and funds.get("data"):
                equity_data = funds["data"].get("equity", {})
                available_balance = equity_data.get("available_margin", 0)
            
            return {
                "authenticated": True,
                "paper_mode": self.paper_mode,
                "available_balance": available_balance,
                "funds": funds.get("data", {}),
                "positions": positions.get("data", []),
                "holdings": holdings.get("data", []),
                "market_open": is_market_hours(),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            return {"error": str(e)}
    
    def analyze_and_decide(
        self,
        symbol: str,
        exchange: str = "NSE",
        product_type: str = "INTRADAY",
        available_capital: float = None,
    ) -> Dict[str, Any]:
        """
        Full analysis → proposal → risk check → decision pipeline.
        
        This is the main method for getting a trading decision.
        
        Args:
            symbol: Stock symbol (e.g., "RELIANCE", "TCS")
            exchange: NSE or BSE
            product_type: INTRADAY or DELIVERY
            available_capital: Override capital (uses account balance if None)
        
        Returns:
            Complete decision with all agent reports
        """
        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "exchange": exchange,
            "product_type": product_type,
            "market_open": is_market_hours(),
            "paper_mode": self.paper_mode,
        }
        
        # Step 1: Get account info
        if available_capital is None:
            account = self.get_account_info()
            if "error" in account:
                result["error"] = account["error"]
                result["stage"] = "ACCOUNT_INFO"
                return result
            available_capital = account.get("available_balance", 100000)  # Default for paper
            existing_positions = account.get("positions", [])
        else:
            existing_positions = []
        
        result["available_capital"] = available_capital
        
        # Step 2: Technical Analysis
        print(f"\n🔍 [1/6] Technical Analysis for {symbol}...")
        technical_report = self.technical_analyst.analyze(symbol, exchange)
        result["technical_analysis"] = technical_report
        
        if "error" in technical_report:
            result["error"] = technical_report["error"]
            result["stage"] = "TECHNICAL_ANALYSIS"
            return result
        
        print(f"   Bias: {technical_report.get('bias')} | Confidence: {technical_report.get('confidence', 0):.0%}")
        
        # Step 3: News Analysis
        print(f"\n📰 [2/6] News Analysis...")
        news_report = self.news_analyst.analyze(symbol, exchange)
        result["news_analysis"] = news_report
        
        news_bias = news_report.get("bias", "NEUTRAL")
        news_conf = news_report.get("confidence", 0)
        print(f"   News Bias: {news_bias} | Confidence: {news_conf:.0%}")
        
        # Step 4: Sentiment Analysis (combines technical + news)
        print(f"\n💭 [3/6] Sentiment Aggregation...")
        sentiment_report = self.sentiment_analyst.analyze(
            symbol=symbol,
            technical_report=technical_report,
            news_report=news_report,
        )
        result["sentiment_analysis"] = sentiment_report
        
        sentiment_bias = sentiment_report.get("bias", "NEUTRAL")
        sentiment_conf = sentiment_report.get("confidence", 0)
        print(f"   Overall Sentiment: {sentiment_bias} | Confidence: {sentiment_conf:.0%}")
        
        # Step 5: Generate Trade Proposal (now considers sentiment)
        print(f"\n📊 [4/6] Generating trade proposal...")
        
        # Combine technical and sentiment for enhanced proposal
        combined_confidence = (
            technical_report.get("confidence", 0) * 0.6 +
            sentiment_report.get("confidence", 0) * 0.4
        )
        
        # Create enhanced technical report with sentiment
        enhanced_report = technical_report.copy()
        enhanced_report["sentiment_bias"] = sentiment_bias
        enhanced_report["sentiment_confidence"] = sentiment_conf
        enhanced_report["combined_confidence"] = combined_confidence
        
        # Adjust confidence if sentiment disagrees with technical
        tech_bias = technical_report.get("bias", "NEUTRAL")
        if tech_bias != sentiment_bias and sentiment_conf > 0.3:
            enhanced_report["confidence"] = combined_confidence * 0.8  # Reduce confidence on disagreement
            print(f"   ⚠️ Tech ({tech_bias}) vs Sentiment ({sentiment_bias}) disagreement")
        
        trade_proposal = self.trader.generate_proposal(
            technical_report=enhanced_report,
            available_capital=available_capital,
            existing_positions=existing_positions,
            product_type=product_type,
        )
        result["trade_proposal"] = trade_proposal.to_dict()
        
        print(f"   Proposed: {trade_proposal.action} {trade_proposal.quantity} @ ₹{trade_proposal.price_estimate:,.2f}")
        
        # Step 6: Risk Assessment
        print(f"\n🛡️ [5/6] Risk Assessment...")
        risk_assessment = self.risk_manager.assess_trade(
            proposal=trade_proposal.to_dict(),
            account_balance=available_capital,
            existing_positions=existing_positions,
            force_check_market_hours=not self.paper_mode,
        )
        result["risk_assessment"] = risk_assessment.to_dict()
        
        print(f"   Risk Score: {risk_assessment.risk_score:.2f} | Approved: {risk_assessment.approved}")
        
        # Portfolio Context
        portfolio_context = self.risk_manager.get_portfolio_risk_summary(
            positions=existing_positions,
            account_balance=available_capital,
        )
        result["portfolio_context"] = portfolio_context
        
        # Step 7: Final Decision
        print(f"\n🎯 [6/6] Final Decision...")
        final_decision = self.portfolio_manager.make_decision(
            technical_report=enhanced_report,
            trade_proposal=trade_proposal.to_dict(),
            risk_assessment=risk_assessment.to_dict(),
            portfolio_context=portfolio_context,
        )
        result["final_decision"] = final_decision.to_dict()
        
        print(f"   DECISION: {final_decision.action}")
        print(f"   Execution Approved: {final_decision.execution_approved}")
        
        # Step 7: Execute if approved (and not in paper mode for live)
        if final_decision.execution_approved and final_decision.action in ["BUY", "SELL"]:
            if self.paper_mode:
                # Log paper trade
                log_trade(
                    symbol=symbol,
                    side=final_decision.action,
                    quantity=final_decision.quantity,
                    price=final_decision.price,
                    order_id=f"PAPER_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    status="SIMULATED",
                )
                result["execution"] = {
                    "status": "SIMULATED",
                    "message": "Paper trade logged to database",
                }
                print(f"\n📝 Paper trade logged: {final_decision.action} {final_decision.quantity} {symbol}")
            else:
                import os as _os

                if _os.getenv("EQUITY_LIVE_ENABLED", "").strip().lower() not in ("1", "true", "yes"):
                    result["execution"] = {
                        "status": "BLOCKED",
                        "message": "Equity live execution disabled (set EQUITY_LIVE_ENABLED=1 after operational review)",
                    }
                else:
                    from database.operations import is_token_valid
                    from execution import runtime_safety
                    from execution.reconciliation import reconcile_state
                    from execution.risk_runtime import evaluate_risk_runtime

                    risk_ok, risk_reason, _ = evaluate_risk_runtime()
                    tok = is_token_valid()
                    client = self._get_upstox_client()
                    fetch_b = tok and runtime_safety.load_trading_mode() in (
                        runtime_safety.TradingMode.MICRO_LIVE,
                        runtime_safety.TradingMode.LIVE,
                    )
                    rec_ok, rec_rep = reconcile_state(
                        token_valid=tok, fetch_broker=fetch_b, client=client if fetch_b else None
                    )
                    state, broker_ok = runtime_safety.evaluate_runtime_safety(
                        token_valid=tok,
                        reconciliation_ok=rec_ok,
                        risk_ok=risk_ok,
                        risk_lock_reason=risk_reason,
                    )
                    if not broker_ok:
                        result["execution"] = {
                            "status": "BLOCKED",
                            "message": "Runtime safety blocked order",
                            "safety": state.to_dict(),
                            "reconciliation": rec_rep,
                        }
                    elif not client.ensure_authenticated():
                        result["execution"] = {
                            "status": "BLOCKED",
                            "message": "Upstox authentication required",
                        }
                    else:
                        side = final_decision.action
                        qty = int(final_decision.quantity or 0)
                        if qty <= 0:
                            result["execution"] = {"status": "ERROR", "message": "Invalid quantity"}
                        else:
                            order_resp = client.place_order(
                                symbol=symbol,
                                exchange=exchange,
                                transaction_type=side,
                                quantity=qty,
                                order_type="MARKET",
                                product="I" if product_type == "INTRADAY" else "D",
                            )
                            oid = None
                            if isinstance(order_resp.get("data"), dict):
                                oid = order_resp["data"].get("order_id")
                            ok = order_resp.get("status") == "success"
                            log_trade(
                                symbol=symbol,
                                side=side,
                                quantity=qty,
                                price=float(final_decision.price or 0),
                                order_id=str(oid or "UNKNOWN"),
                                status="EXECUTED" if ok else "REJECTED",
                                is_paper_trade=False,
                                notes=str(order_resp)[:2000],
                            )
                            result["execution"] = {
                                "status": "SUBMITTED" if ok else "REJECTED",
                                "broker_response": order_resp,
                            }
        else:
            result["execution"] = {
                "status": "NO_ACTION",
                "message": f"Action: {final_decision.action}",
            }
        
        return result
    
    def scan_watchlist(
        self,
        symbols: List[str],
        exchange: str = "NSE",
        product_type: str = "INTRADAY",
    ) -> List[Dict[str, Any]]:
        """
        Scan multiple symbols and return opportunities.
        
        Args:
            symbols: List of stock symbols to scan
            exchange: NSE or BSE
            product_type: INTRADAY or DELIVERY
        
        Returns:
            List of opportunities sorted by confidence
        """
        opportunities = []
        
        for symbol in symbols:
            print(f"\nScanning {symbol}...")
            try:
                result = self.analyze_and_decide(
                    symbol=symbol,
                    exchange=exchange,
                    product_type=product_type,
                )
                
                decision = result.get("final_decision", {})
                if decision.get("action") in ["BUY", "SELL"]:
                    opportunities.append({
                        "symbol": symbol,
                        "action": decision.get("action"),
                        "confidence": decision.get("confidence", 0),
                        "price": decision.get("price", 0),
                        "quantity": decision.get("quantity", 0),
                        "reasoning": decision.get("reasoning", ""),
                    })
            except Exception as e:
                print(f"   Error scanning {symbol}: {e}")
        
        # Sort by confidence
        opportunities.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        
        return opportunities
    
    def get_status(self) -> Dict[str, Any]:
        """Get current brain status and summary (without triggering auth)."""
        # Get account info without triggering auth flow
        account = self.get_account_info(require_auth=False)
        today_pnl = get_today_pnl()
        holdings = get_current_holdings()
        
        return {
            "status": "ACTIVE" if is_market_hours() else "MARKET_CLOSED",
            "paper_mode": self.paper_mode,
            "authenticated": account.get("authenticated", False),
            "available_balance": account.get("available_balance", 0),
            "today_pnl": today_pnl,
            "holdings_count": len(holdings),
            "market_hours": {
                "open": is_market_hours(),
                "hours": "9:15 AM - 3:30 PM IST",
            },
            "timestamp": datetime.now().isoformat(),
        }
    
    def explain_decision(self, decision_result: Dict[str, Any]) -> str:
        """
        Generate a human-readable explanation of a decision.
        """
        parts = [
            "=" * 60,
            "TRADING DECISION EXPLANATION",
            "=" * 60,
            "",
        ]
        
        # Symbol and Action
        symbol = decision_result.get("symbol", "UNKNOWN")
        decision = decision_result.get("final_decision", {})
        action = decision.get("action", "UNKNOWN")
        
        parts.append(f"📈 {symbol} - {action}")
        parts.append("")
        
        # Technical Summary
        tech = decision_result.get("technical_analysis", {})
        parts.append("TECHNICAL ANALYSIS:")
        parts.append(f"  Bias: {tech.get('bias', 'N/A')}")
        parts.append(f"  Confidence: {tech.get('confidence', 0):.0%}")
        parts.append(f"  Summary: {tech.get('summary', 'N/A')}")
        parts.append("")
        
        # Trade Proposal
        proposal = decision_result.get("trade_proposal", {})
        parts.append("TRADE PROPOSAL:")
        parts.append(f"  Quantity: {proposal.get('quantity', 0)}")
        parts.append(f"  Price: ₹{proposal.get('price_estimate', 0):,.2f}")
        parts.append(f"  Stop Loss: ₹{proposal.get('stop_loss', 'N/A')}")
        parts.append(f"  Target: ₹{proposal.get('target', 'N/A')}")
        parts.append("")
        
        # Risk Assessment
        risk = decision_result.get("risk_assessment", {})
        parts.append("RISK ASSESSMENT:")
        parts.append(f"  Approved: {'✅ Yes' if risk.get('approved') else '❌ No'}")
        parts.append(f"  Risk Score: {risk.get('risk_score', 0):.2f}/1.00")
        if risk.get("violations"):
            parts.append("  Violations:")
            for v in risk["violations"]:
                parts.append(f"    - {v}")
        if risk.get("warnings"):
            parts.append("  Warnings:")
            for w in risk["warnings"]:
                parts.append(f"    - {w}")
        parts.append("")
        
        # Final Decision
        parts.append("FINAL DECISION:")
        parts.append(f"  Action: {action}")
        parts.append(f"  Execution: {'Approved' if decision.get('execution_approved') else 'Blocked'}")
        parts.append("")
        
        # Execution Status
        execution = decision_result.get("execution", {})
        parts.append(f"EXECUTION: {execution.get('status', 'N/A')} - {execution.get('message', '')}")
        
        parts.append("=" * 60)
        
        return "\n".join(parts)
    
    # ============== F&O TRADING METHODS (Phase 3) ==============
    
    def analyze_fo(self, symbol: str, expiry: str = None) -> Dict[str, Any]:
        """
        Perform F&O analysis on an underlying.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            expiry: Expiry date (default: nearest)
        
        Returns:
            Comprehensive F&O analysis report
        """
        return self.fo_analyst.analyze(symbol, expiry)
    
    def analyze_and_decide_fo(
        self,
        symbol: str,
        expiry: str = None,
        available_capital: float = None,
        risk_appetite: str = "MODERATE",
    ) -> Dict[str, Any]:
        """
        Full F&O analysis → strategy → decision pipeline.
        
        Args:
            symbol: Underlying (NIFTY, BANKNIFTY)
            expiry: Expiry date (default: nearest)
            available_capital: Trading capital
            risk_appetite: CONSERVATIVE, MODERATE, AGGRESSIVE
        
        Returns:
            Complete F&O decision with analysis and trade proposal
        """
        result = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol.upper(),
            "asset_type": "F&O",
            "market_open": is_market_hours(),
            "paper_mode": self.paper_mode,
        }
        
        # Step 1: Get account info
        if available_capital is None:
            account = self.get_account_info(require_auth=False)
            if "error" not in account:
                available_capital = account.get("available_balance", 500000)
                existing_positions = account.get("positions", [])
            else:
                available_capital = 500000  # Default for paper
                existing_positions = []
        else:
            existing_positions = []
        
        result["available_capital"] = available_capital
        
        # Step 2: F&O Analysis
        print(f"\n📊 [1/3] F&O Analysis for {symbol}...")
        fo_analysis = self.fo_analyst.analyze(symbol, expiry)
        result["fo_analysis"] = fo_analysis
        
        if "error" in fo_analysis:
            result["error"] = fo_analysis["error"]
            result["stage"] = "FO_ANALYSIS"
            return result
        
        print(f"   Spot: {fo_analysis.get('spot_price')} | Bias: {fo_analysis.get('bias')} | PCR: {fo_analysis.get('oi_analysis', {}).get('pcr_oi', 'N/A')}")
        
        # Step 3: Generate F&O Trade Proposal
        print(f"\n📈 [2/3] Generating F&O trade proposal...")
        fo_proposal = self.fo_trader.generate_proposal(
            fo_analysis=fo_analysis,
            available_capital=available_capital,
            existing_positions=existing_positions,
            risk_appetite=risk_appetite,
        )
        
        result["trade_proposal"] = {
            "strategy_name": fo_proposal.strategy_name,
            "strategy_type": fo_proposal.strategy_type,
            "legs": fo_proposal.legs,
            "total_lots": fo_proposal.total_lots,
            "lot_size": fo_proposal.lot_size,
            "net_premium": fo_proposal.net_premium,
            "max_profit": fo_proposal.max_profit,
            "max_loss": fo_proposal.max_loss,
            "margin_required": fo_proposal.margin_required,
            "breakeven_points": fo_proposal.breakeven_points,
            "risk_reward_ratio": fo_proposal.risk_reward_ratio,
            "probability_of_profit": fo_proposal.probability_of_profit,
            "action": fo_proposal.action,
            "confidence": fo_proposal.confidence,
            "reasoning": fo_proposal.reasoning,
            "expiry": fo_proposal.expiry,
        }
        
        print(f"   Strategy: {fo_proposal.strategy_name} | Action: {fo_proposal.action}")
        
        # Step 4: Risk Check (reuse existing risk manager with adaptations)
        print(f"\n🛡️ [3/3] Risk Assessment...")
        
        # Adapt for F&O risk check
        risk_check = {
            "approved": fo_proposal.action == "EXECUTE",
            "violations": [],
            "warnings": [],
        }
        
        # Check margin
        if fo_proposal.margin_required > available_capital * 0.2:
            risk_check["warnings"].append(f"Margin ({fo_proposal.margin_required:.0f}) is >20% of capital")
        
        # Check max loss
        if fo_proposal.max_loss != float('inf'):
            if fo_proposal.max_loss > available_capital * 0.05:
                risk_check["warnings"].append(f"Max loss ({fo_proposal.max_loss:.0f}) exceeds 5% of capital")
        else:
            risk_check["warnings"].append("Strategy has unlimited loss potential")
        
        result["risk_assessment"] = risk_check
        print(f"   Approved: {'✅' if risk_check['approved'] else '❌'} | Warnings: {len(risk_check['warnings'])}")
        
        # Final Decision
        result["final_decision"] = {
            "action": fo_proposal.action,
            "strategy": fo_proposal.strategy_name,
            "execution_approved": fo_proposal.action == "EXECUTE" and risk_check["approved"],
            "reasoning": fo_proposal.reasoning,
        }
        
        # Generate orders if approved
        if result["final_decision"]["execution_approved"]:
            result["orders"] = self.fo_trader.generate_orders(fo_proposal)
            
            # Execute in paper mode
            if self.paper_mode:
                result["execution"] = {
                    "status": "PAPER_TRADE",
                    "message": f"Paper trade logged: {fo_proposal.strategy_name}",
                    "orders": result["orders"],
                }
            else:
                result["execution"] = {
                    "status": "READY",
                    "message": "Ready for live execution. Call execute_fo_orders() to place.",
                    "orders": result["orders"],
                }
        else:
            result["execution"] = {
                "status": "NOT_EXECUTED",
                "message": f"Trade not executed: {fo_proposal.action}",
            }
        
        return result
    
    def explain_fo_decision(self, decision_result: Dict[str, Any]) -> str:
        """Generate human-readable explanation of F&O decision."""
        parts = [
            "=" * 60,
            "F&O TRADING DECISION",
            "=" * 60,
            "",
        ]
        
        symbol = decision_result.get("symbol", "UNKNOWN")
        fo_analysis = decision_result.get("fo_analysis", {})
        proposal = decision_result.get("trade_proposal", {})
        
        parts.append(f"📊 {symbol} OPTIONS")
        parts.append(f"Spot: {fo_analysis.get('spot_price', 'N/A')} | Expiry: {fo_analysis.get('expiry', 'N/A')}")
        parts.append("")
        
        # Market View
        parts.append("MARKET VIEW:")
        parts.append(f"  Bias: {fo_analysis.get('bias', 'N/A')} ({fo_analysis.get('confidence', 0)*100:.0f}%)")
        parts.append(f"  PCR: {fo_analysis.get('oi_analysis', {}).get('pcr_oi', 'N/A')}")
        parts.append(f"  IV Level: {fo_analysis.get('iv_analysis', {}).get('iv_level', 'N/A')}")
        parts.append(f"  Max Pain: {fo_analysis.get('support_resistance', {}).get('max_pain', 'N/A')}")
        parts.append("")
        
        # Strategy
        parts.append("STRATEGY:")
        parts.append(f"  {proposal.get('strategy_name', 'N/A')}")
        parts.append(f"  Lots: {proposal.get('total_lots', 0)} x {proposal.get('lot_size', 0)} = {proposal.get('total_lots', 0) * proposal.get('lot_size', 0)} qty")
        parts.append("")
        
        # Legs
        parts.append("LEGS:")
        for leg in proposal.get("legs", []):
            parts.append(f"  {leg.get('action')} {leg.get('strike')} {leg.get('option_type')} @ ₹{leg.get('premium', 0):.2f}")
        parts.append("")
        
        # Risk Profile
        parts.append("RISK PROFILE:")
        parts.append(f"  Net Premium: ₹{proposal.get('net_premium', 0):,.2f}")
        max_profit = proposal.get('max_profit', 0)
        max_loss = proposal.get('max_loss', 0)
        parts.append(f"  Max Profit: {'Unlimited' if max_profit == float('inf') else f'₹{max_profit:,.2f}'}")
        parts.append(f"  Max Loss: {'Unlimited' if max_loss == float('inf') else f'₹{max_loss:,.2f}'}")
        parts.append(f"  Margin: ₹{proposal.get('margin_required', 0):,.2f}")
        parts.append(f"  R:R Ratio: {proposal.get('risk_reward_ratio', 0):.2f}")
        if proposal.get('probability_of_profit'):
            parts.append(f"  POP: {proposal.get('probability_of_profit')*100:.0f}%")
        parts.append(f"  Breakeven: {proposal.get('breakeven_points', [])}")
        parts.append("")
        
        # Decision
        decision = decision_result.get("final_decision", {})
        parts.append("DECISION:")
        parts.append(f"  Action: {decision.get('action', 'N/A')}")
        parts.append(f"  Execute: {'✅ Yes' if decision.get('execution_approved') else '❌ No'}")
        parts.append(f"  Reasoning: {proposal.get('reasoning', 'N/A')}")
        parts.append("")
        
        parts.append("=" * 60)
        
        return "\n".join(parts)


# Convenience functions for direct use
def analyze_and_decide(
    symbol: str,
    exchange: str = "NSE",
    product_type: str = "INTRADAY",
    paper_mode: bool = True,
) -> Dict[str, Any]:
    """
    Quick analysis and decision for a symbol.
    This is the main function to call for trading decisions.
    """
    brain = TradingBrain(paper_mode=paper_mode)
    return brain.analyze_and_decide(symbol, exchange, product_type)


def scan_stocks(
    symbols: List[str],
    paper_mode: bool = True,
) -> List[Dict[str, Any]]:
    """Scan multiple stocks for opportunities."""
    brain = TradingBrain(paper_mode=paper_mode)
    return brain.scan_watchlist(symbols)


def get_brain_status(paper_mode: bool = True) -> Dict[str, Any]:
    """Get trading brain status."""
    brain = TradingBrain(paper_mode=paper_mode)
    return brain.get_status()


# F&O Convenience Functions (Phase 3)
def analyze_fo(
    symbol: str,
    expiry: str = None,
) -> Dict[str, Any]:
    """
    Quick F&O analysis for an underlying.
    """
    brain = TradingBrain(paper_mode=True)
    return brain.analyze_fo(symbol, expiry)


def analyze_and_decide_fo(
    symbol: str,
    expiry: str = None,
    paper_mode: bool = True,
    risk_appetite: str = "MODERATE",
) -> Dict[str, Any]:
    """
    Full F&O analysis and decision for an underlying.
    
    Args:
        symbol: NIFTY, BANKNIFTY, etc.
        expiry: Expiry date (default: nearest)
        paper_mode: If True, simulates trades
        risk_appetite: CONSERVATIVE, MODERATE, AGGRESSIVE
    """
    brain = TradingBrain(paper_mode=paper_mode)
    return brain.analyze_and_decide_fo(symbol, expiry, risk_appetite=risk_appetite)
