"""
Reflection System - Learning from Past Decisions.

Uses LLM to analyze past trades and improve strategy.
Inspired by TradingAgents' reflection and memory system.
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.decision_log import (
    get_decision_log,
    DecisionLog,
    TradingDecision,
    DecisionOutcome,
)
from llm.client import get_llm_client, LLMClient


@dataclass
class ReflectionInsight:
    """An insight from reflection."""
    category: str  # pattern, mistake, success, strategy
    insight: str
    confidence: float
    supporting_trades: List[str]  # Decision IDs
    suggested_action: Optional[str] = None


class ReflectionEngine:
    """
    LLM-powered reflection on trading decisions.
    
    Analyzes:
    - What worked and what didn't
    - Patterns in winning vs losing trades
    - Strategy effectiveness
    - Market condition correlations
    """
    
    def __init__(self):
        self.decision_log = get_decision_log()
        self.llm = get_llm_client()
    
    def reflect_on_trade(self, decision_id: str) -> Dict[str, Any]:
        """
        Deep reflection on a single trade.
        
        Analyzes what went right/wrong and lessons learned.
        """
        decisions = self.decision_log.get_recent_decisions(limit=1)
        decision = None
        
        for d in decisions:
            if d.decision_id == decision_id:
                decision = d
                break
        
        if not decision:
            return {"error": f"Decision {decision_id} not found"}
        
        if decision.outcome == DecisionOutcome.PENDING:
            return {"error": "Trade still pending - cannot reflect yet"}
        
        # Build reflection prompt
        prompt = f"""Analyze this options trade and provide a brief reflection:

TRADE DETAILS:
- Symbol: {decision.symbol}
- Action: {decision.action}
- Strike: {decision.strike} {decision.option_type}
- Entry Price: Rs {decision.entry_price}
- Exit Price: Rs {decision.exit_price}
- P&L: Rs {decision.pnl:+.0f}
- Outcome: {decision.outcome.value}

MARKET CONTEXT AT ENTRY:
- Spot Price: {decision.spot_price}
- IV Level: {decision.iv_level}%
- PCR: {decision.pcr}
- Trend: {decision.trend}

REASONING AT ENTRY:
{decision.reasoning}

EXIT REASON: {decision.exit_reason or "End of day"}

Provide:
1. What went right in this trade?
2. What could have been done better?
3. Key lesson learned
4. Score (1-10) for decision quality

Be concise and specific. Focus on actionable insights."""

        result = self.llm.chat(
            prompt=prompt,
            task_type="analysis",
        )
        
        return {
            "decision_id": decision_id,
            "outcome": decision.outcome.value,
            "pnl": decision.pnl,
            "reflection": result.get("response", ""),
            "model_used": result.get("model_used", ""),
        }
    
    def daily_reflection(self, date: datetime = None) -> Dict[str, Any]:
        """
        End-of-day reflection on all trades.
        
        Summarizes the day's performance and key lessons.
        """
        if date is None:
            date = datetime.now()
        
        # Get today's decisions
        all_decisions = self.decision_log.get_recent_decisions(limit=50)
        today_decisions = [
            d for d in all_decisions
            if d.timestamp.date() == date.date()
            and d.outcome != DecisionOutcome.PENDING
        ]
        
        if not today_decisions:
            return {"message": "No completed trades today"}
        
        # Calculate stats
        total_pnl = sum(d.pnl for d in today_decisions)
        wins = len([d for d in today_decisions if d.pnl > 0])
        losses = len([d for d in today_decisions if d.pnl <= 0])
        
        # Build trades summary
        trades_summary = "\n".join([
            f"- {d.symbol} {d.strike}{d.option_type}: Rs {d.pnl:+.0f} ({d.outcome.value})"
            for d in today_decisions
        ])
        
        prompt = f"""Daily Trading Reflection for {date.strftime('%Y-%m-%d')}:

SUMMARY:
- Total Trades: {len(today_decisions)}
- Wins: {wins}, Losses: {losses}
- Win Rate: {wins/len(today_decisions)*100:.0f}%
- Total P&L: Rs {total_pnl:+.0f}

TRADES:
{trades_summary}

Provide:
1. Overall assessment of today's trading (2-3 sentences)
2. Best decision of the day and why
3. Worst decision of the day and why
4. One key improvement for tomorrow
5. Market insight observed today

Be specific and actionable."""

        result = self.llm.chat(
            prompt=prompt,
            task_type="analysis",
        )
        
        return {
            "date": date.strftime("%Y-%m-%d"),
            "total_trades": len(today_decisions),
            "wins": wins,
            "losses": losses,
            "total_pnl": total_pnl,
            "reflection": result.get("response", ""),
        }
    
    def weekly_reflection(self) -> Dict[str, Any]:
        """
        Weekly reflection with pattern analysis.
        """
        stats = self.decision_log.get_performance_stats(days=7)
        strategy_stats = self.decision_log.get_strategy_performance(days=7)
        
        # Get all decisions for context
        decisions = self.decision_log.get_recent_decisions(limit=100)
        week_ago = datetime.now() - timedelta(days=7)
        week_decisions = [d for d in decisions if d.timestamp > week_ago]
        
        # Group by day
        by_day = {}
        for d in week_decisions:
            day = d.timestamp.strftime("%A")
            if day not in by_day:
                by_day[day] = {"wins": 0, "losses": 0, "pnl": 0}
            if d.pnl > 0:
                by_day[day]["wins"] += 1
            elif d.pnl < 0:
                by_day[day]["losses"] += 1
            by_day[day]["pnl"] += d.pnl
        
        day_summary = "\n".join([
            f"- {day}: {s['wins']} wins, {s['losses']} losses, Rs {s['pnl']:+.0f}"
            for day, s in by_day.items()
        ])
        
        strategy_summary = "\n".join([
            f"- {name}: {s['total']} trades, {s['win_rate']:.0f}% win rate, Rs {s['pnl']:+.0f}"
            for name, s in strategy_stats.items()
        ])
        
        prompt = f"""Weekly Trading Reflection:

PERFORMANCE SUMMARY:
- Total Trades: {stats.get('total_trades', 0)}
- Win Rate: {stats.get('win_rate', 0):.1f}%
- Total P&L: Rs {stats.get('total_pnl', 0):+.0f}
- Avg Win: Rs {stats.get('avg_win', 0):+.0f}
- Avg Loss: Rs {stats.get('avg_loss', 0):+.0f}

BY DAY:
{day_summary}

BY STRATEGY:
{strategy_summary}

Provide:
1. Week summary (what kind of week was this?)
2. Best performing pattern/strategy and why
3. Worst performing pattern/strategy and why
4. Key patterns observed (time of day, market conditions, etc.)
5. Top 3 improvements for next week

Be analytical and specific."""

        result = self.llm.chat(
            prompt=prompt,
            task_type="analysis",
        )
        
        return {
            "period": "Last 7 days",
            "stats": stats,
            "strategy_stats": strategy_stats,
            "by_day": by_day,
            "reflection": result.get("response", ""),
        }
    
    def find_patterns(self, days: int = 30) -> List[ReflectionInsight]:
        """
        Find patterns in past trades.
        
        Looks for:
        - Winning patterns
        - Losing patterns
        - Time-based patterns
        - Condition-based patterns
        """
        decisions = self.decision_log.get_recent_decisions(limit=200)
        cutoff = datetime.now() - timedelta(days=days)
        decisions = [d for d in decisions if d.timestamp > cutoff and d.outcome != DecisionOutcome.PENDING]
        
        if len(decisions) < 10:
            return []
        
        insights = []
        
        # Pattern 1: Time of day
        morning_trades = [d for d in decisions if 9 <= d.timestamp.hour < 11]
        afternoon_trades = [d for d in decisions if 13 <= d.timestamp.hour < 16]
        
        if len(morning_trades) >= 5:
            morning_wins = len([d for d in morning_trades if d.pnl > 0])
            morning_wr = morning_wins / len(morning_trades) * 100
            
            if morning_wr > 60:
                insights.append(ReflectionInsight(
                    category="time_pattern",
                    insight=f"Morning trades (9-11 AM) have {morning_wr:.0f}% win rate - consider focusing here",
                    confidence=0.7,
                    supporting_trades=[d.decision_id for d in morning_trades[:3]],
                    suggested_action="Prioritize morning setups",
                ))
            elif morning_wr < 40:
                insights.append(ReflectionInsight(
                    category="time_pattern",
                    insight=f"Morning trades have low {morning_wr:.0f}% win rate - avoid early entries",
                    confidence=0.7,
                    supporting_trades=[d.decision_id for d in morning_trades[:3]],
                    suggested_action="Wait for midday confirmation",
                ))
        
        # Pattern 2: High confidence trades
        high_conf = [d for d in decisions if d.confidence >= 0.7]
        low_conf = [d for d in decisions if d.confidence < 0.5]
        
        if len(high_conf) >= 5:
            hc_wins = len([d for d in high_conf if d.pnl > 0])
            hc_wr = hc_wins / len(high_conf) * 100
            
            if hc_wr > 65:
                insights.append(ReflectionInsight(
                    category="confidence_pattern",
                    insight=f"High confidence (>70%) trades have {hc_wr:.0f}% win rate - trust the analysis",
                    confidence=0.8,
                    supporting_trades=[d.decision_id for d in high_conf[:3]],
                ))
        
        if len(low_conf) >= 5:
            lc_wins = len([d for d in low_conf if d.pnl > 0])
            lc_wr = lc_wins / len(low_conf) * 100
            
            if lc_wr < 40:
                insights.append(ReflectionInsight(
                    category="confidence_pattern",
                    insight=f"Low confidence (<50%) trades have poor {lc_wr:.0f}% win rate - avoid these",
                    confidence=0.8,
                    supporting_trades=[d.decision_id for d in low_conf[:3]],
                    suggested_action="Skip trades with confidence below 50%",
                ))
        
        # Pattern 3: Trend following vs counter-trend
        bullish_ce = [d for d in decisions if d.trend == "BULLISH" and d.option_type == "CE"]
        bearish_pe = [d for d in decisions if d.trend == "BEARISH" and d.option_type == "PE"]
        
        trend_following = bullish_ce + bearish_pe
        if len(trend_following) >= 5:
            tf_wins = len([d for d in trend_following if d.pnl > 0])
            tf_wr = tf_wins / len(trend_following) * 100
            
            insights.append(ReflectionInsight(
                category="strategy_pattern",
                insight=f"Trend-following trades (CE in bullish, PE in bearish) have {tf_wr:.0f}% win rate",
                confidence=0.75,
                supporting_trades=[d.decision_id for d in trend_following[:3]],
            ))
        
        return insights
    
    def get_pre_trade_context(
        self,
        symbol: str,
        trend: str,
        iv_level: float = None,
    ) -> str:
        """
        Get relevant historical context before making a trade.
        
        Returns summary of similar past situations.
        """
        similar = self.decision_log.get_similar_situations(
            symbol=symbol,
            trend=trend,
            iv_level=iv_level or 15,
            limit=5,
        )
        
        if not similar:
            return "No similar historical situations found."
        
        context_lines = []
        wins = 0
        losses = 0
        
        for d in similar:
            outcome = "WIN" if d.pnl > 0 else "LOSS"
            if d.pnl > 0:
                wins += 1
            else:
                losses += 1
            
            context_lines.append(
                f"- {d.timestamp.strftime('%Y-%m-%d')}: {d.action} {d.strike}{d.option_type} "
                f"-> {outcome} (Rs {d.pnl:+.0f})"
            )
        
        win_rate = wins / len(similar) * 100
        
        return f"""Historical context for {symbol} in {trend} trend:

Past similar trades ({len(similar)} found, {win_rate:.0f}% win rate):
{chr(10).join(context_lines)}

Summary: {'Favorable conditions historically' if win_rate > 50 else 'Historically challenging conditions'}"""


# Singleton
_reflection_engine = None

def get_reflection_engine() -> ReflectionEngine:
    """Get or create reflection engine singleton."""
    global _reflection_engine
    if _reflection_engine is None:
        _reflection_engine = ReflectionEngine()
    return _reflection_engine
