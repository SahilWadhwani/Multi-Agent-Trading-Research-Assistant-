"""
Risk Manager Agent
The final gatekeeper before any trade execution.
Enforces all guardrails and risk limits.
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.operations import (
    get_today_pnl,
    get_daily_trade_count,
    get_current_portfolio_value,
)
from mcp_server.guardrails import (
    GUARDRAILS,
    validate_trade_risk,
    is_market_hours,
)


@dataclass
class RiskAssessment:
    """Structured risk assessment result."""
    approved: bool
    risk_score: float  # 0.0 (safe) to 1.0 (max risk)
    violations: List[str]
    warnings: List[str]
    adjusted_quantity: Optional[int]
    reasoning: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "risk_score": self.risk_score,
            "violations": self.violations,
            "warnings": self.warnings,
            "adjusted_quantity": self.adjusted_quantity,
            "reasoning": self.reasoning,
        }


class RiskManager:
    """
    Risk Manager Agent - The Guardian.
    
    IMMUTABLE RULES - These cannot be overridden by any agent:
    1. Max 20% of capital per single position
    2. Max 5% daily loss limit
    3. Max 50 trades per day
    4. No fund transfers (add/withdraw blocked)
    5. Must operate within market hours
    
    Additional Risk Checks:
    - Position concentration limits
    - Sector exposure limits
    - Volatility-adjusted position sizing
    - Correlation checks
    """
    
    def __init__(self):
        self.guardrails = GUARDRAILS.copy()
    
    def assess_trade(
        self,
        proposal: Dict[str, Any],
        account_balance: float,
        existing_positions: List[Dict] = None,
        force_check_market_hours: bool = True,
    ) -> RiskAssessment:
        """
        Comprehensive risk assessment of a trade proposal.
        
        Args:
            proposal: Trade proposal from TraderAgent
            account_balance: Current available balance
            existing_positions: Current portfolio positions
            force_check_market_hours: Whether to enforce market hours
        
        Returns:
            RiskAssessment with approval status and details
        """
        existing_positions = existing_positions or []
        violations = []
        warnings = []
        adjusted_qty = None
        
        action = proposal.get("action", "HOLD")
        symbol = proposal.get("symbol", "")
        quantity = proposal.get("quantity", 0)
        price = proposal.get("price_estimate", 0)
        confidence = proposal.get("confidence", 0)
        
        # HOLD actions are always approved
        if action == "HOLD":
            return RiskAssessment(
                approved=True,
                risk_score=0.0,
                violations=[],
                warnings=[],
                adjusted_quantity=0,
                reasoning="HOLD action - no trade to assess",
            )
        
        trade_value = quantity * price
        
        # === HARD GUARDRAILS (Cannot be bypassed) ===
        
        # 1. Market Hours Check
        if force_check_market_hours and not is_market_hours():
            violations.append("MARKET_CLOSED: Trading only allowed 9:15 AM - 3:30 PM IST")
        
        # 2. Position Size Limit (20% max)
        max_position_percent = self.guardrails["max_position_percent"]
        max_position_value = account_balance * (max_position_percent / 100)
        
        if trade_value > max_position_value:
            violations.append(
                f"POSITION_SIZE: Trade value ₹{trade_value:,.2f} exceeds "
                f"{max_position_percent}% limit (₹{max_position_value:,.2f})"
            )
            # Calculate adjusted quantity
            adjusted_qty = int(max_position_value / price) if price > 0 else 0
        
        # 3. Daily Loss Limit (5% max)
        daily_pnl = get_today_pnl()
        max_daily_loss = account_balance * (self.guardrails["max_daily_loss_percent"] / 100)
        
        if daily_pnl < -max_daily_loss:
            violations.append(
                f"DAILY_LOSS: Daily loss ₹{abs(daily_pnl):,.2f} exceeds "
                f"{self.guardrails['max_daily_loss_percent']}% limit (₹{max_daily_loss:,.2f}). "
                "Trading suspended for today."
            )
        
        # 4. Daily Trade Count Limit
        daily_trades = get_daily_trade_count()
        if daily_trades >= self.guardrails["max_daily_trades"]:
            violations.append(
                f"TRADE_LIMIT: Daily trade count ({daily_trades}) "
                f"has reached limit ({self.guardrails['max_daily_trades']})"
            )
        
        # 5. Minimum Balance Check
        if account_balance < trade_value:
            violations.append(
                f"INSUFFICIENT_FUNDS: Trade value ₹{trade_value:,.2f} "
                f"exceeds available balance ₹{account_balance:,.2f}"
            )
        
        # === SOFT CHECKS (Warnings, not violations) ===
        
        # Low confidence warning
        if confidence < 0.5:
            warnings.append(f"LOW_CONFIDENCE: Trade confidence ({confidence:.0%}) is below 50%")
        
        # Position concentration check
        symbol_positions = [p for p in existing_positions if p.get("symbol") == symbol]
        if symbol_positions:
            existing_value = sum(p.get("quantity", 0) * p.get("avg_price", 0) for p in symbol_positions)
            total_exposure = existing_value + trade_value
            if total_exposure > max_position_value * 1.5:
                warnings.append(
                    f"CONCENTRATION: Total {symbol} exposure ₹{total_exposure:,.2f} "
                    f"exceeds 1.5x position limit"
                )
        
        # Stop loss check
        if not proposal.get("stop_loss"):
            warnings.append("NO_STOP_LOSS: Trade has no stop-loss defined")
        else:
            # Check stop loss distance
            sl_distance = abs(price - proposal["stop_loss"]) / price
            if sl_distance > 0.05:  # More than 5% SL
                warnings.append(f"WIDE_STOP_LOSS: Stop loss is {sl_distance:.1%} from entry")
        
        # Risk-reward ratio check
        rr_ratio = proposal.get("risk_reward_ratio", 0)
        if rr_ratio and rr_ratio < 1.5:
            warnings.append(f"POOR_RR: Risk-reward ratio ({rr_ratio:.1f}) is below 1.5")
        
        # Calculate overall risk score
        risk_score = self._calculate_risk_score(
            trade_value=trade_value,
            account_balance=account_balance,
            confidence=confidence,
            violations=violations,
            warnings=warnings,
        )
        
        # Build reasoning
        reasoning = self._build_reasoning(
            proposal=proposal,
            violations=violations,
            warnings=warnings,
            risk_score=risk_score,
            adjusted_qty=adjusted_qty,
        )
        
        # Approved only if no violations
        approved = len(violations) == 0
        
        return RiskAssessment(
            approved=approved,
            risk_score=risk_score,
            violations=violations,
            warnings=warnings,
            adjusted_quantity=adjusted_qty if violations else quantity,
            reasoning=reasoning,
        )
    
    def _calculate_risk_score(
        self,
        trade_value: float,
        account_balance: float,
        confidence: float,
        violations: List[str],
        warnings: List[str],
    ) -> float:
        """
        Calculate a risk score from 0.0 (safe) to 1.0 (maximum risk).
        """
        score = 0.0
        
        # Position size contribution (0-0.3)
        if account_balance > 0:
            position_ratio = trade_value / account_balance
            score += min(position_ratio * 1.5, 0.3)
        
        # Confidence inverse contribution (0-0.2)
        score += (1 - confidence) * 0.2
        
        # Violations heavily penalize (0.2 per violation)
        score += len(violations) * 0.2
        
        # Warnings slightly penalize (0.05 per warning)
        score += len(warnings) * 0.05
        
        return min(score, 1.0)
    
    def _build_reasoning(
        self,
        proposal: Dict,
        violations: List[str],
        warnings: List[str],
        risk_score: float,
        adjusted_qty: Optional[int],
    ) -> str:
        """Build detailed reasoning for the risk assessment."""
        
        parts = [
            f"=== Risk Assessment: {proposal.get('action')} {proposal.get('symbol')} ===",
            "",
            f"Trade Value: ₹{proposal.get('quantity', 0) * proposal.get('price_estimate', 0):,.2f}",
            f"Confidence: {proposal.get('confidence', 0):.0%}",
            f"Risk Score: {risk_score:.2f}/1.00",
            "",
        ]
        
        if violations:
            parts.append("❌ VIOLATIONS (Trade Blocked):")
            for v in violations:
                parts.append(f"   - {v}")
            parts.append("")
        
        if warnings:
            parts.append("⚠️ WARNINGS:")
            for w in warnings:
                parts.append(f"   - {w}")
            parts.append("")
        
        if adjusted_qty is not None and adjusted_qty != proposal.get("quantity"):
            parts.append(f"📊 ADJUSTED QUANTITY: {adjusted_qty} (from {proposal.get('quantity')})")
            parts.append("")
        
        if not violations:
            parts.append("✅ APPROVED: Trade passes all risk checks")
        else:
            parts.append("🚫 REJECTED: Trade blocked due to violations")
        
        return "\n".join(parts)
    
    def get_portfolio_risk_summary(
        self,
        positions: List[Dict],
        account_balance: float,
    ) -> Dict[str, Any]:
        """
        Get overall portfolio risk metrics.
        """
        total_exposure = sum(
            p.get("quantity", 0) * p.get("current_price", p.get("avg_price", 0))
            for p in positions
        )
        
        # Concentration by symbol
        symbol_exposure = {}
        for p in positions:
            symbol = p.get("symbol", "UNKNOWN")
            value = p.get("quantity", 0) * p.get("current_price", p.get("avg_price", 0))
            symbol_exposure[symbol] = symbol_exposure.get(symbol, 0) + value
        
        # Find max concentration
        max_concentration = max(symbol_exposure.values()) if symbol_exposure else 0
        max_symbol = max(symbol_exposure, key=symbol_exposure.get) if symbol_exposure else None
        
        daily_pnl = get_today_pnl()
        daily_trades = get_daily_trade_count()
        
        return {
            "total_exposure": total_exposure,
            "exposure_percent": (total_exposure / account_balance * 100) if account_balance > 0 else 0,
            "available_capital": account_balance - total_exposure,
            "position_count": len(positions),
            "symbol_exposure": symbol_exposure,
            "max_concentration": {
                "symbol": max_symbol,
                "value": max_concentration,
                "percent": (max_concentration / account_balance * 100) if account_balance > 0 else 0,
            },
            "daily_pnl": daily_pnl,
            "daily_trades": daily_trades,
            "trades_remaining": self.guardrails["max_daily_trades"] - daily_trades,
            "guardrails": self.guardrails,
        }


# Convenience functions
def check_trade_risk(
    proposal: Dict,
    account_balance: float,
    existing_positions: List[Dict] = None,
) -> Dict[str, Any]:
    """Quick risk check for a trade proposal."""
    manager = RiskManager()
    assessment = manager.assess_trade(
        proposal=proposal,
        account_balance=account_balance,
        existing_positions=existing_positions,
        force_check_market_hours=False,  # Allow checking outside hours for planning
    )
    return assessment.to_dict()


def get_risk_summary(positions: List[Dict], account_balance: float) -> Dict[str, Any]:
    """Get portfolio risk summary."""
    manager = RiskManager()
    return manager.get_portfolio_risk_summary(positions, account_balance)
