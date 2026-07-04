"""
Portfolio Manager Agent (LLM-Powered)
The final decision maker. Uses AI reasoning to synthesize all inputs.

Uses local LLM (Ollama) for intelligent decision synthesis.
Falls back to rule-based if LLM unavailable.
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.operations import (
    log_agent_reasoning,
    log_trade,
    get_today_pnl,
)
from llm.client import get_llm_client


@dataclass
class FinalDecision:
    """The final trading decision."""
    action: str  # BUY, SELL, HOLD, REJECTED
    symbol: str
    quantity: int
    price: float
    stop_loss: Optional[float]
    target: Optional[float]
    product_type: str
    confidence: float
    reasoning: str
    execution_approved: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "price": self.price,
            "stop_loss": self.stop_loss,
            "target": self.target,
            "product_type": self.product_type,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "execution_approved": self.execution_approved,
        }


class PortfolioManager:
    """
    Portfolio Manager Agent - The Final Arbiter.
    
    Responsibilities:
    - Synthesize inputs from all agents
    - Make the final BUY/SELL/HOLD decision
    - Ensure portfolio-level objectives are met
    - Log all decisions with reasoning
    - Approve or reject trade execution
    
    Decision Framework:
    1. Technical Analysis must support the direction
    2. Risk Assessment must approve the trade
    3. Portfolio context must allow the trade
    4. Confidence must meet minimum threshold
    """
    
    def __init__(self, min_confidence: float = 0.5):
        """
        Args:
            min_confidence: Minimum confidence to approve a trade (default 50%)
        """
        self.min_confidence = min_confidence
        self.llm = get_llm_client()
        self._llm_available = self.llm.is_available()
        if self._llm_available:
            print(f"   🎯 Portfolio Manager: LLM-powered ({self.llm.model})")
        else:
            print("   🎯 Portfolio Manager: Rule-based (install Ollama for AI decisions)")
    
    def make_decision(
        self,
        technical_report: Dict[str, Any],
        trade_proposal: Dict[str, Any],
        risk_assessment: Dict[str, Any],
        portfolio_context: Dict[str, Any] = None,
    ) -> FinalDecision:
        """
        Make the final trading decision.
        
        Args:
            technical_report: From TechnicalAnalyst
            trade_proposal: From TraderAgent
            risk_assessment: From RiskManager
            portfolio_context: Current portfolio state
        
        Returns:
            FinalDecision with all details
        """
        portfolio_context = portfolio_context or {}
        
        symbol = trade_proposal.get("symbol", "UNKNOWN")
        proposed_action = trade_proposal.get("action", "HOLD")
        quantity = trade_proposal.get("quantity", 0)
        price = trade_proposal.get("price_estimate", 0)
        confidence = trade_proposal.get("confidence", 0)
        
        # Start building decision logic
        decision_factors = []
        execution_approved = True
        final_action = proposed_action
        final_quantity = quantity
        
        # Factor 1: Technical Alignment
        technical_bias = technical_report.get("bias", "NEUTRAL")
        technical_confidence = technical_report.get("confidence", 0)
        
        if proposed_action == "BUY" and technical_bias != "BULLISH":
            decision_factors.append(f"⚠️ Technical bias ({technical_bias}) doesn't support BUY")
            if technical_bias == "BEARISH":
                final_action = "HOLD"
                execution_approved = False
        elif proposed_action == "SELL" and technical_bias != "BEARISH":
            decision_factors.append(f"⚠️ Technical bias ({technical_bias}) doesn't support SELL")
            if technical_bias == "BULLISH":
                final_action = "HOLD"
                execution_approved = False
        else:
            decision_factors.append(f"✅ Technical bias ({technical_bias}) supports {proposed_action}")
        
        # Factor 2: Risk Assessment
        risk_approved = risk_assessment.get("approved", False)
        violations = risk_assessment.get("violations", [])
        warnings = risk_assessment.get("warnings", [])
        risk_score = risk_assessment.get("risk_score", 1.0)
        
        if not risk_approved:
            final_action = "REJECTED"
            execution_approved = False
            decision_factors.append(f"❌ Risk check FAILED: {len(violations)} violations")
            for v in violations:
                decision_factors.append(f"   - {v}")
        else:
            decision_factors.append(f"✅ Risk check PASSED (score: {risk_score:.2f})")
        
        # Use adjusted quantity if provided
        adjusted_qty = risk_assessment.get("adjusted_quantity")
        if adjusted_qty is not None and adjusted_qty != quantity:
            final_quantity = adjusted_qty
            decision_factors.append(f"📊 Quantity adjusted: {quantity} → {final_quantity}")
        
        # Factor 3: Confidence Threshold
        if confidence < self.min_confidence:
            decision_factors.append(
                f"⚠️ Confidence ({confidence:.0%}) below threshold ({self.min_confidence:.0%})"
            )
            if final_action in ["BUY", "SELL"]:
                final_action = "HOLD"
                execution_approved = False
        else:
            decision_factors.append(f"✅ Confidence ({confidence:.0%}) meets threshold")
        
        # Factor 4: Portfolio Context
        if portfolio_context:
            exposure_percent = portfolio_context.get("exposure_percent", 0)
            trades_remaining = portfolio_context.get("trades_remaining", 50)
            daily_pnl = portfolio_context.get("daily_pnl", 0)
            
            if trades_remaining <= 5:
                decision_factors.append(f"⚠️ Only {trades_remaining} trades remaining today")
            
            if exposure_percent > 80:
                decision_factors.append(f"⚠️ High portfolio exposure ({exposure_percent:.1f}%)")
                if final_action == "BUY":
                    decision_factors.append("   Consider reducing position size")
            
            if daily_pnl < 0:
                decision_factors.append(f"📉 Daily P&L: ₹{daily_pnl:,.2f}")
        
        # Factor 5: Warnings from Risk Manager
        if warnings:
            for w in warnings:
                decision_factors.append(f"⚠️ {w}")
        
        # Build final reasoning
        reasoning = self._build_final_reasoning(
            symbol=symbol,
            final_action=final_action,
            decision_factors=decision_factors,
            technical_report=technical_report,
            trade_proposal=trade_proposal,
            risk_assessment=risk_assessment,
        )
        
        # Log the decision
        strategy = self._determine_strategy(technical_report, trade_proposal)
        log_agent_reasoning(
            ai_reasoning=reasoning,
            strategy_used=strategy,
        )
        
        return FinalDecision(
            action=final_action,
            symbol=symbol,
            quantity=final_quantity,
            price=price,
            stop_loss=trade_proposal.get("stop_loss"),
            target=trade_proposal.get("target"),
            product_type=trade_proposal.get("product_type", "INTRADAY"),
            confidence=confidence,
            reasoning=reasoning,
            execution_approved=execution_approved,
        )
    
    def _determine_strategy(
        self,
        technical_report: Dict,
        trade_proposal: Dict,
    ) -> str:
        """Determine the strategy name based on signals."""
        signals = technical_report.get("signals", [])
        
        # Identify dominant signals
        strong_signals = [s for s in signals if s.get("strength") == "STRONG"]
        
        if any(s.get("indicator") == "RSI" for s in strong_signals):
            if any("OVERSOLD" in str(s.get("interpretation", "")).upper() for s in strong_signals):
                return "RSI_OVERSOLD_REVERSAL"
            elif any("OVERBOUGHT" in str(s.get("interpretation", "")).upper() for s in strong_signals):
                return "RSI_OVERBOUGHT_REVERSAL"
        
        if any(s.get("indicator") == "MACD" for s in signals):
            return "MACD_CROSSOVER"
        
        trend = technical_report.get("trend_analysis", {}).get("direction", "")
        if trend == "BULLISH":
            return "TREND_FOLLOWING_LONG"
        elif trend == "BEARISH":
            return "TREND_FOLLOWING_SHORT"
        
        return "MULTI_INDICATOR"
    
    def _build_final_reasoning(
        self,
        symbol: str,
        final_action: str,
        decision_factors: List[str],
        technical_report: Dict,
        trade_proposal: Dict,
        risk_assessment: Dict,
    ) -> str:
        """Build comprehensive final reasoning using LLM when available."""
        
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Try LLM reasoning first
        llm_reasoning = None
        if self._llm_available and final_action in ["BUY", "SELL", "HOLD"]:
            llm_reasoning = self._get_llm_reasoning(
                symbol, final_action, decision_factors,
                technical_report, trade_proposal, risk_assessment
            )
        
        parts = [
            "=" * 60,
            f"PORTFOLIO MANAGER DECISION - {timestamp}",
            f"Analysis Method: {'LLM-Powered' if llm_reasoning else 'Rule-Based'}",
            "=" * 60,
            "",
            f"SYMBOL: {symbol}",
            f"FINAL ACTION: {final_action}",
            "",
        ]
        
        # Add LLM reasoning if available
        if llm_reasoning:
            parts.extend([
                "─" * 40,
                "AI REASONING:",
                "─" * 40,
                llm_reasoning,
                "",
            ])
        
        parts.extend([
            "─" * 40,
            "DECISION FACTORS:",
            "─" * 40,
        ])
        
        for factor in decision_factors:
            parts.append(factor)
        
        parts.extend([
            "",
            "─" * 40,
            "TRADE DETAILS:",
            "─" * 40,
            f"Quantity: {trade_proposal.get('quantity', 0)}",
            f"Price: ₹{trade_proposal.get('price_estimate', 0):,.2f}",
            f"Stop Loss: ₹{trade_proposal.get('stop_loss', 'N/A')}",
            f"Target: ₹{trade_proposal.get('target', 'N/A')}",
            f"R:R Ratio: {trade_proposal.get('risk_reward_ratio', 'N/A')}",
            f"Product: {trade_proposal.get('product_type', 'INTRADAY')}",
            "",
            "─" * 40,
            "TECHNICAL SUMMARY:",
            "─" * 40,
            technical_report.get("summary", "No technical summary"),
            "",
            "=" * 60,
        ])
        
        if final_action in ["BUY", "SELL"]:
            parts.append(f"✅ EXECUTION APPROVED: {final_action} {trade_proposal.get('quantity', 0)} {symbol}")
        elif final_action == "HOLD":
            parts.append("⏸️ NO ACTION: Holding current position")
        else:
            parts.append("🚫 TRADE REJECTED: See violations above")
        
        parts.append("=" * 60)
        
        return "\n".join(parts)
    
    def _get_llm_reasoning(
        self,
        symbol: str,
        final_action: str,
        decision_factors: List[str],
        technical_report: Dict,
        trade_proposal: Dict,
        risk_assessment: Dict,
        use_consensus: bool = True,  # Use dual-brain for important decisions
    ) -> Optional[str]:
        """
        Get AI-powered reasoning for the trade decision.
        
        For BUY/SELL decisions, uses DUAL-BRAIN consensus (GPT-5.5 + Gemini)
        for higher confidence.
        """
        try:
            # Build context for LLM
            context = {
                "symbol": symbol,
                "proposed_action": final_action,
                "quantity": trade_proposal.get("quantity", 0),
                "price": trade_proposal.get("price_estimate", 0),
                "stop_loss": trade_proposal.get("stop_loss"),
                "target": trade_proposal.get("target"),
                "technical_bias": technical_report.get("bias"),
                "technical_confidence": technical_report.get("confidence"),
                "sentiment_bias": technical_report.get("sentiment_bias"),
                "sentiment_confidence": technical_report.get("sentiment_confidence"),
                "risk_score": risk_assessment.get("risk_score"),
                "risk_approved": risk_assessment.get("approved"),
                "indicators": technical_report.get("indicators", {}),
            }
            
            # Build the prompt
            prompt = f"""You are a professional portfolio manager analyzing a trade decision for Indian stock market.

TRADE CONTEXT:
- Symbol: {symbol} on NSE
- Proposed Action: {final_action}
- Quantity: {context['quantity']} shares
- Price: ₹{context['price']:,.2f}
- Stop Loss: ₹{context['stop_loss'] or 'Not set'}
- Target: ₹{context['target'] or 'Not set'}

ANALYSIS INPUTS:
- Technical Bias: {context['technical_bias']} (Confidence: {context['technical_confidence']:.0%} if conf else 'N/A')
- Sentiment Bias: {context.get('sentiment_bias', 'N/A')} (Confidence: {context.get('sentiment_confidence', 0):.0%} if context.get('sentiment_confidence') else 'N/A')
- Risk Score: {context['risk_score']:.2f} (Lower is better)
- Risk Approved: {'Yes' if context['risk_approved'] else 'No'}

DECISION FACTORS:
{chr(10).join(decision_factors)}

Provide a brief (2-3 sentences) professional reasoning for this {final_action} decision. 
Focus on the key factors that support or oppose this trade.
Be concise and actionable. Use Indian market terminology where relevant."""

            response = self.llm.chat(
                messages=[
                    {"role": "system", "content": "You are a professional Indian stock market portfolio manager. Be concise and factual."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
            )
            
            return response.content.strip()
            
        except Exception as e:
            print(f"   ⚠️ LLM reasoning failed: {e}")
            return None
    
    def review_portfolio(
        self,
        positions: List[Dict],
        technical_reports: Dict[str, Dict],
        account_balance: float,
    ) -> List[Dict[str, Any]]:
        """
        Review all positions and generate recommendations.
        
        Args:
            positions: Current holdings
            technical_reports: Technical reports keyed by symbol
            account_balance: Available balance
        
        Returns:
            List of position review recommendations
        """
        reviews = []
        
        for position in positions:
            symbol = position.get("symbol", "UNKNOWN")
            quantity = position.get("quantity", 0)
            avg_price = position.get("avg_price", 0)
            current_price = position.get("current_price", avg_price)
            
            pnl = (current_price - avg_price) * quantity
            pnl_percent = ((current_price - avg_price) / avg_price * 100) if avg_price > 0 else 0
            
            # Get technical report if available
            tech_report = technical_reports.get(symbol, {})
            bias = tech_report.get("bias", "NEUTRAL")
            
            review = {
                "symbol": symbol,
                "quantity": quantity,
                "avg_price": avg_price,
                "current_price": current_price,
                "unrealized_pnl": round(pnl, 2),
                "pnl_percent": round(pnl_percent, 2),
                "technical_bias": bias,
                "recommendation": "HOLD",
                "reasoning": "",
            }
            
            # Decision logic
            if pnl_percent < -5:  # More than 5% loss
                review["recommendation"] = "EXIT"
                review["reasoning"] = f"Stop loss triggered: {pnl_percent:.1f}% loss exceeds 5% limit"
            elif pnl_percent > 10:  # More than 10% profit
                if bias == "BEARISH":
                    review["recommendation"] = "EXIT"
                    review["reasoning"] = f"Take profit: {pnl_percent:.1f}% gain, technical turning bearish"
                else:
                    review["recommendation"] = "HOLD"
                    review["reasoning"] = f"Continue holding: {pnl_percent:.1f}% gain, trend still {bias}"
            elif bias == "BEARISH" and pnl_percent > 0:
                review["recommendation"] = "EXIT"
                review["reasoning"] = f"Exit with profit: Technical turned bearish, lock in {pnl_percent:.1f}% gain"
            else:
                review["recommendation"] = "HOLD"
                review["reasoning"] = f"Maintain position: P&L {pnl_percent:.1f}%, bias {bias}"
            
            reviews.append(review)
        
        return reviews


# Convenience function
def make_final_decision(
    technical_report: Dict,
    trade_proposal: Dict,
    risk_assessment: Dict,
    portfolio_context: Dict = None,
) -> Dict[str, Any]:
    """Quick decision making."""
    manager = PortfolioManager()
    decision = manager.make_decision(
        technical_report=technical_report,
        trade_proposal=trade_proposal,
        risk_assessment=risk_assessment,
        portfolio_context=portfolio_context,
    )
    return decision.to_dict()
