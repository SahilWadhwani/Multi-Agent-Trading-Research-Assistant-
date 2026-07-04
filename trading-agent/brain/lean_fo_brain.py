"""
LEAN F&O TRADING BRAIN

A streamlined, fast options trading system.
No debates. No overhead. Just signals, gates, and execution.

Based on research:
- TradingAgents architecture
- Volatility Risk Premium strategies
- Indian market specifics (NIFTY, BANKNIFTY, FINNIFTY)

Design principles:
1. LLM for analysis (news, context) - NOT in execution path
2. Hard rules for risk gates - fast, deterministic
3. Learn from every trade - calibrate thresholds
4. Most days = NO TRADE (discipline)
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import pytz

IST = pytz.timezone("Asia/Kolkata")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.client import get_llm_client, LLMBackend
from llm.schemas import parse_json_response
from data_feeds.fo_data_feed import get_fo_data_feed
from data_feeds.options_greeks import get_greeks_calculator
from memory.decision_log import get_decision_log, TradingDecision, DecisionType, DecisionOutcome
from brain.signal_tracker import get_signal_tracker, ScanRecord, generate_scan_id


class Trend(Enum):
    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"


class IVRegime(Enum):
    LOW = "low"          # IV < 12% - options cheap, good for buying
    NORMAL = "normal"    # IV 12-18%
    ELEVATED = "elevated"  # IV 18-25%
    HIGH = "high"        # IV > 25% - options expensive, avoid buying


@dataclass
class MarketContext:
    """Current market state - computed once per analysis."""
    timestamp: datetime
    symbol: str
    spot_price: float
    
    # Trend
    trend: Trend
    trend_strength: float  # 0-1
    
    # F&O data
    iv_level: float
    iv_regime: IVRegime
    pcr: float
    max_pain: float
    
    # Support/Resistance from OI
    immediate_support: float
    immediate_resistance: float
    
    # News context (from LLM)
    news_sentiment: str  # BULLISH, BEARISH, NEUTRAL
    news_summary: str
    has_catalyst: bool
    
    # Signal quality
    signal_strength: float  # 0-1, combined score
    
    # Data quality flag
    used_fallback_data: bool = False

    # Regime upgrade (Phase A): India VIX + day plan + detector
    regime: str = "unknown"
    regime_confidence: float = 0.0
    regime_reasoning: str = ""
    vix: float = 0.0
    vix_change_pct: float = 0.0
    day_plan: Optional[Dict[str, Any]] = None

    # Phase F: OI analysis + VWAP overlays
    oi_buildup: str = "neutral"
    oi_bias: str = "NEUTRAL"
    sticky_call_strike: float = 0.0
    sticky_put_strike: float = 0.0
    vwap: float = 0.0
    spot_vs_vwap: str = "unknown"


@dataclass 
class TradeSignal:
    """A trading signal ready for risk gates."""
    symbol: str
    direction: str  # BUY_CE or BUY_PE
    strike: float
    option_type: str  # CE or PE
    expiry: str
    
    # Entry details
    entry_premium: float
    lot_size: int
    lots: int
    
    # Risk management (MANDATORY)
    stop_loss_pct: float  # e.g., 40 means exit if down 40%
    target_pct: float     # e.g., 50 means book profit at +50%
    
    # Signal metadata
    confidence: float
    reasoning: str
    context: MarketContext
    # Broker instrument (from option chain when available)
    instrument_key: Optional[str] = None


@dataclass
class TradeDecision:
    """Final decision after risk gates."""
    signal: TradeSignal
    approved: bool
    gate_results: Dict[str, bool]
    rejection_reason: Optional[str]
    
    # Execution details (if approved)
    order_value: float = 0
    max_loss: float = 0
    risk_reward: float = 0


class RiskGates:
    """
    Hard risk gates - NO LLM, pure rules.
    All gates must pass. Any failure = NO TRADE.
    """
    
    # Capital limits
    MAX_POSITION_PCT = 70       # Max 70% of capital per trade
    MAX_TRADE_VALUE = 15000     # Max Rs 15,000 per trade
    MAX_DAILY_LOSS = 4000       # Stop if down Rs 4,000
    MAX_DAILY_TRADES = 8        # Max 8 trades per day
    
    # Premium limits
    MIN_PREMIUM = 20            # Don't buy < Rs 20 (too cheap = too risky)
    MAX_PREMIUM = 250           # Don't buy > Rs 250 (too expensive)
    
    # Time rules
    NO_TRADE_FIRST_MINS = 15    # Skip first 15 mins
    NO_TRADE_LAST_MINS = 15     # Skip last 15 mins
    EXPIRY_CUTOFF_MINS = 60     # No trades within 60 mins of expiry close
    
    # IV rules
    MAX_IV_FOR_BUYING = 30      # Don't buy when IV > 30% (too expensive)
    
    # Mandatory risk management
    MIN_STOP_LOSS_PCT = 25      # At least 25% stop loss
    MAX_STOP_LOSS_PCT = 50      # At most 50% stop loss
    MIN_RISK_REWARD = 1.2       # At least 1.2:1 reward:risk
    MIN_CONFIDENCE = 0.70       # Directional trades only when confidence is strong
    
    @classmethod
    def check_all(
        cls,
        signal: TradeSignal,
        available_capital: float,
        daily_loss_so_far: float,
        daily_trades_so_far: int,
        is_expiry_day: bool = False,
    ) -> Tuple[bool, Dict[str, bool], Optional[str]]:
        """
        Run all risk gates.
        
        Returns:
            (all_passed, gate_results, rejection_reason)
        """
        gates = {}
        rejection = None
        
        order_value = signal.entry_premium * signal.lot_size * signal.lots
        max_loss = order_value * (signal.stop_loss_pct / 100)
        
        # Gate 1: Trading time
        gates["time_allowed"] = cls._check_trading_time(is_expiry_day)
        if not gates["time_allowed"]:
            rejection = "Outside trading window"
        
        # Gate 2: Capital check
        gates["capital_ok"] = order_value <= available_capital * (cls.MAX_POSITION_PCT / 100)
        if not gates["capital_ok"] and not rejection:
            rejection = f"Order value {order_value:.0f} exceeds position limit"
        
        # Gate 3: Trade value limit
        gates["value_ok"] = order_value <= cls.MAX_TRADE_VALUE
        if not gates["value_ok"] and not rejection:
            rejection = f"Order value {order_value:.0f} exceeds max {cls.MAX_TRADE_VALUE}"
        
        # Gate 4: Daily loss limit
        gates["daily_loss_ok"] = (daily_loss_so_far + max_loss) <= cls.MAX_DAILY_LOSS
        if not gates["daily_loss_ok"] and not rejection:
            rejection = f"Would exceed daily loss limit (current: {daily_loss_so_far:.0f})"
        
        # Gate 5: Daily trade count
        gates["trade_count_ok"] = daily_trades_so_far < cls.MAX_DAILY_TRADES
        if not gates["trade_count_ok"] and not rejection:
            rejection = f"Daily trade limit reached ({daily_trades_so_far})"
        
        # Gate 6: Premium range
        gates["premium_ok"] = cls.MIN_PREMIUM <= signal.entry_premium <= cls.MAX_PREMIUM
        if not gates["premium_ok"] and not rejection:
            rejection = f"Premium {signal.entry_premium:.0f} outside range [{cls.MIN_PREMIUM}, {cls.MAX_PREMIUM}]"
        
        # Gate 7: IV check (don't buy expensive options)
        iv = signal.context.iv_level if signal.context else 15
        gates["iv_ok"] = iv <= cls.MAX_IV_FOR_BUYING
        if not gates["iv_ok"] and not rejection:
            rejection = f"IV {iv:.1f}% too high for buying"
        
        # Gate 8: Stop loss valid
        gates["sl_ok"] = cls.MIN_STOP_LOSS_PCT <= signal.stop_loss_pct <= cls.MAX_STOP_LOSS_PCT
        if not gates["sl_ok"] and not rejection:
            rejection = f"Stop loss {signal.stop_loss_pct}% invalid"
        
        # Gate 9: Risk-reward check
        rr = signal.target_pct / signal.stop_loss_pct if signal.stop_loss_pct > 0 else 0
        gates["rr_ok"] = rr >= cls.MIN_RISK_REWARD
        if not gates["rr_ok"] and not rejection:
            rejection = f"Risk-reward {rr:.2f} below minimum {cls.MIN_RISK_REWARD}"
        
        # Gate 10: Signal confidence
        gates["confidence_ok"] = signal.confidence >= cls.MIN_CONFIDENCE
        if not gates["confidence_ok"] and not rejection:
            rejection = f"Confidence {signal.confidence:.0%} too low"
        
        all_passed = all(gates.values())
        
        return all_passed, gates, rejection
    
    @classmethod
    def _check_trading_time(cls, is_expiry_day: bool = False) -> bool:
        """Check if current time is allowed for trading."""
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        
        # Weekend check
        if now.weekday() >= 5:
            return False
        
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        
        # Market hours check
        if now < market_open or now > market_close:
            return False
        
        # First N minutes
        if now < market_open + timedelta(minutes=cls.NO_TRADE_FIRST_MINS):
            return False
        
        # Last N minutes (or earlier on expiry)
        cutoff_mins = cls.EXPIRY_CUTOFF_MINS if is_expiry_day else cls.NO_TRADE_LAST_MINS
        if now > market_close - timedelta(minutes=cutoff_mins):
            return False
        
        return True


class LeanFOBrain:
    """
    The streamlined options trading brain.
    
    Flow (v2 — LLM-enhanced):
    1. Pre-scan: run FOAnalyst + NewsAnalyst once, cache the consensus (async-friendly)
    2. Get market context (trend, IV, PCR) — fast, rule-based
    3. Filter: LLM consensus must agree with rule-based direction
    4. Generate signal (if conditions align)
    5. Risk gates (hard rules, no LLM)
    6. Execute or reject
    7. Log for learning
    
    The LLM is a *gate* (can veto bad trades) but never in the hot execution path.
    """
    
    # Strike selection
    STRIKE_INTERVALS = {
        "NIFTY": 50,
        "BANKNIFTY": 100,
        "FINNIFTY": 50,
    }
    
    LOT_SIZES = {
        "NIFTY": 65,      # Updated May 2026 (verified via Upstox API)
        "BANKNIFTY": 30,  # Updated May 2026 (was 15)
        "FINNIFTY": 40,
    }
    
    # Activity mode: surface inactivity, but do not lower directional standards.
    ACTIVITY_MODE_DAYS = 3  # Trigger after 3 days without a trade
    DIRECTIONAL_SIGNAL_THRESHOLD = 0.70
    ACTIVITY_MODE_SIGNAL_THRESHOLD = DIRECTIONAL_SIGNAL_THRESHOLD
    
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.llm = get_llm_client()
        self.fo_feed = get_fo_data_feed()
        self.greeks_calc = get_greeks_calculator()
        self.decision_log = get_decision_log()
        self.signal_tracker = get_signal_tracker()
        
        # Session state
        self._news_cache: Dict[str, Dict] = {}
        self._daily_trades = 0
        self._daily_loss = 0.0
        self._last_reset_date = None
        self._activity_mode = False
        
        # LLM agent consensus cache (populated once per scan cycle)
        self._agent_consensus_cache: Dict[str, Dict] = {}

        # Learning: past-trade context cache (refreshed once per scan cycle, ~1ms)
        self._learning_context_cache: Dict[str, str] = {}
        self._calibrator = None
        try:
            from memory.calibrator import get_calibrator
            self._calibrator = get_calibrator()
        except Exception:
            pass
        
        # Check if we should enter activity mode
        days_since = self.signal_tracker.days_since_last_trade()
        if days_since >= self.ACTIVITY_MODE_DAYS or days_since == -1:
            self._activity_mode = True
        
        print(f"LeanFOBrain initialized (paper_mode={paper_mode})")
        if self.llm.is_available():
            print(f"   LLM: {self.llm.model}")
        else:
            print("   LLM: Unavailable - will use rule-based fallback")
        
        if self._activity_mode:
            print(f"   ACTIVITY MODE: Thresholds relaxed (no trades in {days_since} days)")
    
    # ------------------------------------------------------------------
    # Learning from past trades (pure SQLite reads, <5ms, no LLM)
    # ------------------------------------------------------------------
    def _get_learning_context(self, symbol: str, context) -> Optional[str]:
        """
        Get historical trade context for this symbol+conditions.
        Pure SQLite read — no LLM, no network. Cached per scan cycle.
        Returns a short text string injected into agent prompts.
        """
        cache_key = f"{symbol}_{getattr(context, 'trend', '')}"
        if cache_key in self._learning_context_cache:
            return self._learning_context_cache[cache_key]

        try:
            similar = self.decision_log.get_similar_situations(
                symbol=symbol,
                trend=context.trend.value if hasattr(context.trend, 'value') else str(context.trend),
                iv_level=context.iv_level or 15,
                limit=5,
            )
            if not similar:
                self._learning_context_cache[cache_key] = ""
                return ""

            wins = sum(1 for d in similar if d.pnl and d.pnl > 0)
            losses = len(similar) - wins
            total_pnl = sum(d.pnl or 0 for d in similar)
            win_rate = (wins / len(similar) * 100) if similar else 0

            lines = []
            for d in similar[:3]:
                outcome = "WIN" if (d.pnl and d.pnl > 0) else "LOSS"
                lines.append(
                    f"  {d.timestamp.strftime('%m/%d')} {d.action} {int(d.strike or 0)}{d.option_type}: "
                    f"{outcome} Rs {d.pnl:+,.0f} (exit: {d.exit_reason or '?'})"
                )

            ctx = (
                f"Past similar setups ({symbol}, {context.trend.value}, IV~{context.iv_level:.0f}%): "
                f"{len(similar)} trades, {win_rate:.0f}% win rate, Rs {total_pnl:+,.0f}\n"
                + "\n".join(lines)
            )
            self._learning_context_cache[cache_key] = ctx
            print(f"   Learning: {len(similar)} similar past trades ({win_rate:.0f}% WR)")
            return ctx

        except Exception:
            self._learning_context_cache[cache_key] = ""
            return ""

    def _get_calibrated_params(self, symbol: str) -> Dict:
        """Get calibrated thresholds for this symbol (JSON file read, ~0ms)."""
        if self._calibrator:
            try:
                return self._calibrator.get_trading_parameters(symbol)
            except Exception:
                pass
        return {}

    # ------------------------------------------------------------------
    # LLM multi-agent consensus (runs ONCE per scan, result is cached)
    # ------------------------------------------------------------------
    def prefetch_agent_consensus(self, symbol: str) -> Dict[str, Any]:
        """
        Run FOAnalyst + NewsAnalyst in advance and cache their verdict.
        Call this at the START of each scan cycle — before analyze().
        This keeps the hot path (analyze → signal → risk gates) fast.
        
        Returns cached consensus dict with keys:
          fo_bias, news_bias, combined_bias, confidence, summary
        """
        symbol = symbol.upper()
        if symbol in self._agent_consensus_cache:
            return self._agent_consensus_cache[symbol]
        
        consensus: Dict[str, Any] = {
            "fo_bias": "NEUTRAL",
            "news_bias": "NEUTRAL",
            "combined_bias": "NEUTRAL",
            "confidence": 0.5,
            "summary": "",
            "used_llm": False,
        }
        
        try:
            from agents.analysts.fo_analyst import get_fo_analyst
            fo_analyst = get_fo_analyst()
            fo_report = fo_analyst.analyze(symbol)
            consensus["fo_bias"] = fo_report.get("bias", "NEUTRAL")
            consensus["fo_confidence"] = fo_report.get("confidence", 0.5)
            consensus["fo_summary"] = fo_report.get("summary", "")
            if fo_report.get("llm_analysis"):
                consensus["used_llm"] = True
        except Exception as e:
            print(f"   FOAnalyst skipped: {e}")
        
        try:
            from agents.analysts.news_analyst import NewsAnalyst as NA
            news_analyst = NA()
            news_report = news_analyst.analyze(symbol, "NSE")
            consensus["news_bias"] = news_report.get("bias", "NEUTRAL")
            consensus["news_confidence"] = news_report.get("confidence", 0.0)
            if news_report.get("analysis_method") == "LLM":
                consensus["used_llm"] = True
        except Exception as e:
            print(f"   NewsAnalyst skipped: {e}")
        
        # Combine: if both agree, high confidence. If disagree, NEUTRAL.
        fb = consensus["fo_bias"]
        nb = consensus["news_bias"]
        if fb == nb and fb != "NEUTRAL":
            consensus["combined_bias"] = fb
            consensus["confidence"] = min(
                0.5 + consensus.get("fo_confidence", 0.5) * 0.3
                    + consensus.get("news_confidence", 0.0) * 0.2,
                0.95,
            )
        elif fb != "NEUTRAL" and nb == "NEUTRAL":
            consensus["combined_bias"] = fb
            consensus["confidence"] = consensus.get("fo_confidence", 0.5) * 0.8
        elif nb != "NEUTRAL" and fb == "NEUTRAL":
            consensus["combined_bias"] = nb
            consensus["confidence"] = consensus.get("news_confidence", 0.0) * 0.7
        else:
            consensus["combined_bias"] = "NEUTRAL"
            consensus["confidence"] = 0.4
        
        consensus["summary"] = (
            f"FO={fb} News={nb} → Combined={consensus['combined_bias']} "
            f"({consensus['confidence']:.0%})"
        )

        # Inject performance stats from past trades (pure DB read, <5ms)
        try:
            stats = self.decision_log.get_performance_stats(days=14, symbol=symbol)
            if stats.get("total_trades", 0) >= 3:
                consensus["past_performance"] = stats
                wr = stats.get("win_rate", 0)
                pnl = stats.get("total_pnl", 0)
                consensus["summary"] += f" | History: {wr:.0f}%WR, Rs{pnl:+,.0f}"
                if wr < 40 and stats["total_trades"] >= 5:
                    consensus["confidence"] = max(consensus["confidence"] - 0.1, 0.2)
        except Exception:
            pass
        
        self._agent_consensus_cache[symbol] = consensus
        return consensus
    
    def _reset_daily_counters(self):
        """Reset daily counters if new day, then hydrate from DB."""
        today = datetime.now(IST).date()
        
        if self._last_reset_date != today:
            self._daily_trades = 0
            self._daily_loss = 0.0
            self._last_reset_date = today
            self._agent_consensus_cache = {}
            self._learning_context_cache = {}
            self._news_cache = {}
            # Hydrate from today's actual positions to survive restarts
            self._hydrate_daily_counters_from_db(today)

    def _hydrate_daily_counters_from_db(self, today):
        """Load today's trade count and realized loss from positions DB."""
        try:
            import sqlite3
            db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data_cache", "positions.db"
            )
            if not os.path.exists(db_path):
                return
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            today_str = today.isoformat()
            # Count all positions entered today
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM positions_v2 WHERE entry_time LIKE ?",
                (f"{today_str}%",)
            ).fetchone()
            if row:
                self._daily_trades = row["cnt"]
            # Sum realized losses from today's closed losing trades
            loss_row = conn.execute(
                "SELECT COALESCE(SUM(current_pnl_rs), 0) as total_loss FROM positions_v2 "
                "WHERE exit_time LIKE ? AND current_pnl_rs < 0",
                (f"{today_str}%",)
            ).fetchone()
            if loss_row:
                self._daily_loss = abs(loss_row["total_loss"])
            conn.close()
        except Exception:
            pass
    
    def analyze(self, symbol: str, available_capital: float = 20000) -> Dict[str, Any]:
        """
        Full analysis pipeline.
        
        Returns analysis with signal (if any) and decision.
        """
        self._reset_daily_counters()
        symbol = symbol.upper()
        scan_id = generate_scan_id()
        
        result = {
            "timestamp": datetime.now(IST).isoformat(),
            "symbol": symbol,
            "paper_mode": self.paper_mode,
            "available_capital": available_capital,
            "activity_mode": self._activity_mode,
        }
        
        print(f"\n{'='*60}")
        print(f"LEAN F&O ANALYSIS: {symbol}")
        print(f"Capital: Rs {available_capital:,.0f}")
        if self._activity_mode:
            print(f"⚠️  ACTIVITY MODE ACTIVE (relaxed thresholds)")
        print(f"{'='*60}")
        
        # Step 1: Get market context
        print("\n[1/4] Market Context...")
        context = self._get_market_context(symbol)
        
        if not context:
            result["error"] = "Could not fetch market data"
            result["decision"] = "NO_TRADE"
            return result
        
        result["context"] = {
            "spot": context.spot_price,
            "trend": context.trend.value,
            "iv": context.iv_level,
            "iv_regime": context.iv_regime.value,
            "pcr": context.pcr,
            "max_pain": context.max_pain,
            "support": context.immediate_support,
            "resistance": context.immediate_resistance,
            "news_sentiment": context.news_sentiment,
            "has_catalyst": context.has_catalyst,
            "used_fallback": context.used_fallback_data,
            "regime": context.regime,
            "regime_confidence": context.regime_confidence,
            "vix": context.vix,
            "vix_change_pct": context.vix_change_pct,
            "oi_buildup": context.oi_buildup,
            "oi_bias": context.oi_bias,
            "vwap": context.vwap,
            "spot_vs_vwap": context.spot_vs_vwap,
        }
        
        print(f"   Spot: {context.spot_price:.2f}")
        print(f"   Trend: {context.trend.value} (strength: {context.trend_strength:.0%})")
        print(f"   IV: {context.iv_level:.1f}% ({context.iv_regime.value})")
        print(f"   PCR: {context.pcr:.2f}")
        print(f"   Regime: {context.regime} | India VIX: {context.vix:.2f}")
        print(f"   OI: {context.oi_buildup} | Bias: {context.oi_bias} | VWAP: spot {context.spot_vs_vwap}")
        print(f"   News: {context.news_sentiment}")
        if context.used_fallback_data:
            print(f"   ⚠️  USING FALLBACK DATA (API unavailable)")
        
        # Step 1b: Agent consensus (if pre-fetched)
        consensus = self._agent_consensus_cache.get(symbol)
        if consensus:
            result["agent_consensus"] = consensus
            print(f"   Agent Consensus: {consensus.get('summary', 'N/A')}")

        # Step 1c: Learning from past trades (pure SQLite, <5ms)
        learning_ctx = self._get_learning_context(symbol, context)
        if learning_ctx:
            result["learning_context"] = learning_ctx
        
        # Step 2: Should we trade today?
        print("\n[2/4] Trade Decision...")
        should_trade, trade_reason = self._should_trade(context)
        result["should_trade"] = should_trade
        result["trade_reason"] = trade_reason
        
        # Prepare scan record (will be updated as we go)
        scan_record = ScanRecord(
            scan_id=scan_id,
            timestamp=datetime.now(IST),
            symbol=symbol,
            spot_price=context.spot_price,
            trend=context.trend.value,
            trend_strength=context.trend_strength,
            iv_level=context.iv_level,
            iv_regime=context.iv_regime.value,
            pcr=context.pcr,
            news_sentiment=context.news_sentiment,
            signal_strength=context.signal_strength,
            should_trade=should_trade,
            rejection_reason=trade_reason if not should_trade else None,
            used_fallback_data=context.used_fallback_data,
        )
        
        if not should_trade:
            print(f"   NO TRADE: {trade_reason}")
            result["decision"] = "NO_TRADE"
            result["reasoning"] = trade_reason
            
            # Log scan
            scan_record.final_decision = "NO_TRADE"
            self.signal_tracker.log_scan(scan_record)
            
            return result
        
        print(f"   SIGNAL DETECTED: {trade_reason}")
        
        # Step 3: Generate signal
        print("\n[3/4] Signal Generation...")
        signal = self._generate_signal(context, available_capital)
        
        if not signal:
            result["decision"] = "NO_TRADE"
            result["reasoning"] = "Could not generate valid signal"
            
            scan_record.final_decision = "NO_TRADE"
            scan_record.rejection_reason = "Could not generate valid signal"
            self.signal_tracker.log_scan(scan_record)
            
            return result
        
        result["signal"] = {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "strike": signal.strike,
            "option_type": signal.option_type,
            "expiry": signal.expiry,
            "instrument_key": signal.instrument_key,
            "premium": signal.entry_premium,
            "lots": signal.lots,
            "lot_size": signal.lot_size,
            "stop_loss": signal.stop_loss_pct,
            "target": signal.target_pct,
            "confidence": signal.confidence,
        }
        
        # Update scan record with signal info
        scan_record.signal_direction = signal.direction
        scan_record.signal_strike = signal.strike
        scan_record.signal_premium = signal.entry_premium
        
        print(f"   {signal.direction}: {signal.strike} {signal.option_type}")
        print(f"   Premium: Rs {signal.entry_premium:.1f} x {signal.lots} lots")
        print(f"   SL: {signal.stop_loss_pct}% | Target: {signal.target_pct}%")
        print(f"   Confidence: {signal.confidence:.0%}")
        
        # Step 4: Risk gates
        print("\n[4/4] Risk Gates...")
        decision = self._apply_risk_gates(signal, available_capital)
        
        result["gate_results"] = decision.gate_results
        result["decision"] = "EXECUTE" if decision.approved else "BLOCKED"
        
        if decision.approved:
            print(f"   ALL GATES PASSED")
            print(f"   Order Value: Rs {decision.order_value:,.0f}")
            print(f"   Max Loss: Rs {decision.max_loss:,.0f}")
            print(f"   Risk:Reward = 1:{decision.risk_reward:.1f}")

            # ─── NEW: Pre-Trade Gatekeeper (Gemini-reviewed institutional gateway) ───
            print("\n[4.1/4] Pre-Trade Gatekeeper (All 8 Fixes)...")
            from brain.pre_trade_gatekeeper import get_pre_trade_gatekeeper
            
            gatekeeper = get_pre_trade_gatekeeper()
            gk_result = gatekeeper.validate_execution(
                signal={
                    "direction": signal.direction,
                    "llm_confidence": signal.confidence,
                    "entry_premium": signal.entry_premium,
                    "lots": signal.lots,
                    "lot_size": signal.lot_size,
                },
                market_data={
                    "regime": context.regime,
                    "spot_price": context.spot_price,
                    "support": context.immediate_support,
                    "resistance": context.immediate_resistance,
                    "hours_to_expiry": self._estimate_hours_to_expiry(signal.expiry),
                    "iv_regime": context.iv_regime.value,
                }
            )
            
            result["gatekeeper_result"] = gk_result
            
            if gk_result["status"] != "EXECUTE":
                print(f"   GATEKEEPER: BLOCKED")
                print(f"   Reason: {gk_result['reason']}")
                decision.approved = False
                decision.rejection_reason = gk_result["reason"]
                result["decision"] = "BLOCKED"
                result["rejection_reason"] = gk_result["reason"]
                scan_record.final_decision = "BLOCKED"
                scan_record.blocked_by_gate = "gatekeeper"
                scan_record.rejection_reason = gk_result["reason"]
                self.signal_tracker.log_scan(scan_record)
                print(f"\n{'='*60}")
                print(f"DECISION: {result['decision']}")
                print(f"{'='*60}\n")
                return result
            
            print(f"   GATEKEEPER: PASSED")
            print(f"   Calibrated Win Rate: {gk_result['win_probability']:.1%}")
            print(f"   Position Size: {gk_result['size']} lots")
            print(f"   Smart SL: {gk_result['stop_loss_pct']:.1f}% ({gk_result['stop_loss_price']:.1f})")
            print(f"   Reason: {gk_result['calibrated_reason']}")

            # Gatekeeper outputs must be execution inputs, not advisory text.
            approved_lots = int(gk_result.get("size") or 0)
            if approved_lots <= 0:
                decision.approved = False
                decision.rejection_reason = "gatekeeper_zero_size"
                result["decision"] = "BLOCKED"
                result["rejection_reason"] = decision.rejection_reason
                return result
            if approved_lots < signal.lots:
                signal.lots = approved_lots
                result["signal"]["lots"] = signal.lots
            smart_sl_pct = float(gk_result.get("stop_loss_pct") or signal.stop_loss_pct)
            if smart_sl_pct > 0:
                signal.stop_loss_pct = max(
                    RiskGates.MIN_STOP_LOSS_PCT,
                    min(RiskGates.MAX_STOP_LOSS_PCT, smart_sl_pct),
                )
                result["signal"]["stop_loss"] = signal.stop_loss_pct

            # Recompute order risk after gatekeeper sizing/SL changes.
            decision.order_value = signal.entry_premium * signal.lot_size * signal.lots
            decision.max_loss = decision.order_value * (signal.stop_loss_pct / 100)
            decision.risk_reward = signal.target_pct / signal.stop_loss_pct
            
            # ─── NEW: FIX #5 - Multi-Signal Consensus Check ───
            print("\n[4.2/4] Multi-Signal Consensus (FIX #5)...")
            consensus_ok, consensus_reason = self._check_multi_signal_consensus(signal, context)
            result["consensus_check"] = consensus_reason
            
            if not consensus_ok:
                print(f"   CONSENSUS CHECK: BLOCKED — {consensus_reason}")
                decision.approved = False
                decision.rejection_reason = consensus_reason
                result["decision"] = "BLOCKED"
                result["rejection_reason"] = consensus_reason
                scan_record.final_decision = "BLOCKED"
                scan_record.blocked_by_gate = "consensus_check"
                scan_record.rejection_reason = consensus_reason
                self.signal_tracker.log_scan(scan_record)
                print(f"\n{'='*60}")
                print(f"DECISION: {result['decision']}")
                print(f"{'='*60}\n")
                return result
            
            # ─── All early filters passed, proceed to LLM gate ───
            consensus = self._agent_consensus_cache.get(symbol)
            ok_dm, dm_reason = self._llm_execute_gate(signal, context, consensus)
            result["llm_execute_gate"] = dm_reason

            if not ok_dm:
                print(f"   GPT GATE: BLOCKED — {dm_reason}")
                decision.approved = False
                decision.rejection_reason = dm_reason
                result["decision"] = "BLOCKED"
                result["rejection_reason"] = dm_reason
                scan_record.final_decision = "BLOCKED"
                scan_record.blocked_by_gate = "gpt_gate"
                scan_record.rejection_reason = dm_reason
            else:
                result["order"] = {
                    "value": decision.order_value,
                    "max_loss": decision.max_loss,
                    "risk_reward": decision.risk_reward,
                }

                decision_id = self._log_decision(signal, decision)
                self._daily_trades += 1
                if decision_id:
                    result["decision_id"] = decision_id

                scan_record.final_decision = "EXECUTE"

        else:
            failed_gates = [g for g, v in decision.gate_results.items() if not v]
            print(f"   BLOCKED: {decision.rejection_reason}")
            print(f"   Failed gates: {failed_gates}")
            result["rejection_reason"] = decision.rejection_reason
            
            scan_record.final_decision = "BLOCKED"
            scan_record.rejection_reason = decision.rejection_reason
            scan_record.blocked_by_gate = ", ".join(failed_gates)
        
        # Log scan
        self.signal_tracker.log_scan(scan_record)
        
        print(f"\n{'='*60}")
        print(f"DECISION: {result['decision']}")
        print(f"{'='*60}\n")
        
        return result
    
    def _get_market_context(self, symbol: str) -> Optional[MarketContext]:
        """Build complete market context."""
        try:
            # Get F&O data
            fo_data = self.fo_feed.get_option_chain(symbol)
            
            if not fo_data or "error" in fo_data:
                # Use fallback for testing
                return self._get_fallback_context(symbol)
            
            spot = fo_data.get("spot_price", 0)
            if spot <= 0:
                return self._get_fallback_context(symbol)
            
            # Extract analysis data (in "summary" key from fo_data_feed)
            summary = fo_data.get("summary", {})
            
            # Get ATM IV from calls/puts around ATM strike
            atm_strike = fo_data.get("atm_strike", spot)
            calls = fo_data.get("calls", [])
            puts = fo_data.get("puts", [])
            atm_iv = 15  # default
            for c in calls:
                if c.get("strike") == atm_strike and c.get("iv"):
                    atm_iv = c["iv"] * 100  # Convert to percentage
                    break
            if atm_iv == 15:  # Still default, try puts
                for p in puts:
                    if p.get("strike") == atm_strike and p.get("iv"):
                        atm_iv = p["iv"] * 100
                        break
            
            # Determine IV regime
            if atm_iv < 12:
                iv_regime = IVRegime.LOW
            elif atm_iv < 18:
                iv_regime = IVRegime.NORMAL
            elif atm_iv < 25:
                iv_regime = IVRegime.ELEVATED
            else:
                iv_regime = IVRegime.HIGH
            
            # Get PCR and max pain from summary
            pcr = summary.get("pcr_oi", 1.0) or 1.0
            max_pain = summary.get("max_pain", spot) or spot
            
            # Determine trend from PCR and price vs max pain
            trend, strength = self._determine_trend(spot, max_pain, pcr)
            
            # Get support/resistance from highest OI strikes
            highest_put_strike = summary.get("highest_oi_put_strike", spot * 0.99)
            highest_call_strike = summary.get("highest_oi_call_strike", spot * 1.01)
            support = highest_put_strike if highest_put_strike else spot * 0.99
            resistance = highest_call_strike if highest_call_strike else spot * 1.01
            
            # Get news context (cached per session)
            news = self._get_news_context(symbol)
            
            # Calculate signal strength
            signal_strength = self._calculate_signal_strength(
                trend, strength, iv_regime, pcr, news
            )
            
            # Phase F: OI analysis + VWAP
            oi_buildup_str = "neutral"
            oi_bias_str = "NEUTRAL"
            sticky_call = resistance
            sticky_put = support
            try:
                from data_feeds.oi_analysis import build_oi_snapshot
                spot_data = self.fo_feed.get_spot_price(symbol)
                spot_chg = float(spot_data.get("change_percent", 0) or 0)
                oi_snap = build_oi_snapshot(fo_data, spot, spot_chg)
                oi_buildup_str = oi_snap.oi_buildup.value
                oi_bias_str = oi_snap.oi_bias
                if oi_snap.sticky_call_strike > 0:
                    sticky_call = oi_snap.sticky_call_strike
                if oi_snap.sticky_put_strike > 0:
                    sticky_put = oi_snap.sticky_put_strike
            except Exception:
                pass

            vwap_val = 0.0
            spot_vs_vwap_str = "unknown"
            try:
                vwap_data = self.fo_feed.get_intraday_vwap(symbol)
                if not vwap_data.get("error"):
                    vwap_val = vwap_data["vwap"]
                    spot_vs_vwap_str = vwap_data["spot_vs_vwap"]
            except Exception:
                pass

            ctx = MarketContext(
                timestamp=datetime.now(IST),
                symbol=symbol,
                spot_price=spot,
                trend=trend,
                trend_strength=strength,
                iv_level=atm_iv,
                iv_regime=iv_regime,
                pcr=pcr,
                max_pain=max_pain,
                immediate_support=sticky_put if sticky_put > 0 else support,
                immediate_resistance=sticky_call if sticky_call > 0 else resistance,
                news_sentiment=news.get("sentiment", "NEUTRAL"),
                news_summary=news.get("summary", ""),
                has_catalyst=news.get("has_catalyst", False),
                signal_strength=signal_strength,
                used_fallback_data=False,
                oi_buildup=oi_buildup_str,
                oi_bias=oi_bias_str,
                sticky_call_strike=sticky_call,
                sticky_put_strike=sticky_put,
                vwap=vwap_val,
                spot_vs_vwap=spot_vs_vwap_str,
            )
            self._enrich_context_with_regime(symbol, ctx)
            return ctx

        except Exception as e:
            print(f"   Error getting context: {e}")
            return self._get_fallback_context(symbol)
    
    def _get_fallback_context(self, symbol: str) -> MarketContext:
        """Fallback context when API fails - uses LLM news to determine direction."""
        base_prices = {"NIFTY": 24000, "BANKNIFTY": 51000, "FINNIFTY": 22000}
        spot = base_prices.get(symbol, 24000)
        
        # Even with fallback data, try to get news for direction
        news = self._get_news_context(symbol)
        
        # In fallback mode, derive trend from news sentiment
        # (since we don't have real PCR/max pain data)
        if news.get("sentiment") == "BULLISH":
            trend = Trend.BULLISH
            trend_strength = 0.65
        elif news.get("sentiment") == "BEARISH":
            trend = Trend.BEARISH
            trend_strength = 0.65
        else:
            trend = Trend.NEUTRAL
            trend_strength = 0.5
        
        # Calculate signal strength (will be lower due to fallback)
        signal_strength = 0.5
        if trend != Trend.NEUTRAL:
            signal_strength = 0.55  # Just enough to potentially trade in activity mode
        
        fb = MarketContext(
            timestamp=datetime.now(IST),
            symbol=symbol,
            spot_price=spot,
            trend=trend,
            trend_strength=trend_strength,
            iv_level=15,
            iv_regime=IVRegime.NORMAL,
            pcr=1.0,
            max_pain=spot,
            immediate_support=spot * 0.99,
            immediate_resistance=spot * 1.01,
            news_sentiment=news.get("sentiment", "NEUTRAL"),
            news_summary=news.get("summary", "No news data available"),
            has_catalyst=news.get("has_catalyst", False),
            signal_strength=signal_strength,
            used_fallback_data=True,
        )
        self._enrich_context_with_regime(symbol, fb)
        return fb

    def _enrich_context_with_regime(self, symbol: str, context: MarketContext) -> None:
        """Attach VIX, regime snapshot, and optional day plan to context (mutates)."""
        try:
            from brain.regime_detector import get_regime_detector, load_day_plan_for_date

            snap = get_regime_detector().detect(symbol, context, self.fo_feed, self.llm)
            context.vix = snap.vix
            context.vix_change_pct = snap.vix_change_pct
            context.regime = snap.regime
            context.regime_confidence = snap.confidence
            context.regime_reasoning = snap.reasoning
            today = datetime.now(IST).strftime("%Y-%m-%d")
            context.day_plan = load_day_plan_for_date(today)
        except Exception as ex:
            print(f"   Regime enrich skipped: {ex}")

    def _check_regime_suitability(
        self,
        signal: TradeSignal,
        context: MarketContext,
    ) -> Tuple[bool, str]:
        """
        FIX #1: Regime Detection
        
        Check if current market regime is suitable for directional trades.
        Different regimes have different risk profiles:
        
        - STRONG_TREND: Momentum trades work, but naked directional bets risky
        - MEAN_REVERT: Directional bets LOSE because of reversals to support/resistance
        - CHOPPY/SIDEWAYS: LLM directional signals unreliable
        - BREAKOUT: Can work but requires extra caution
        
        Returns (approved, reason).
        """
        regime = context.regime.lower() if context.regime else "unknown"
        regime_conf = context.regime_confidence or 0.0
        
        print(f"\n   [Regime Filter] Regime: {regime} (conf: {regime_conf:.0%})")
        
        # Skip if regime detection confidence too low
        if regime_conf < 0.5:
            return True, f"regime_confidence_too_low_{regime_conf:.0%}_skip_check"
        
        # MEAN_REVERT: Very dangerous for directional bets
        if "mean" in regime or "revert" in regime:
            print(f"      ❌ MEAN_REVERT regime: Reversals expected to opposite of signal")
            return False, f"mean_revert_regime_high_reversal_risk"
        
        # CHOPPY/SIDEWAYS: Direction unreliable
        if "choppy" in regime or "sideways" in regime:
            print(f"      ❌ CHOPPY regime: Directional signal unreliable")
            return False, f"choppy_regime_low_edge"
        
        # STRONG_TREND: OK but with caution
        if "strong_trend" in regime:
            # In strong trend, but verify trend aligns with signal direction
            signal_bearish = "PE" in signal.direction  # BUY_PE is bearish bet
            signal_bullish = "CE" in signal.direction  # BUY_CE is bullish bet
            
            trend_bearish = signal.context.trend in [Trend.BEARISH, Trend.STRONG_BEARISH]
            trend_bullish = signal.context.trend in [Trend.BULLISH, Trend.STRONG_BULLISH]
            
            if (signal_bearish and not trend_bearish) or (signal_bullish and not trend_bullish):
                print(f"      ⚠️  STRONG_TREND but signal direction OPPOSITE to trend")
                return False, f"strong_trend_signal_opposed_to_trend"
            
            print(f"      ✓ STRONG_TREND with aligned direction")
            return True, f"strong_trend_aligned_direction"
        
        # BREAKOUT: Can work
        if "breakout" in regime:
            print(f"      ✓ BREAKOUT regime OK for directional bets")
            return True, f"breakout_regime_suitable"
        
        # Default: unknown regime, allow
        print(f"      ✓ Regime {regime} (unknown type) - allowing")
        return True, f"unknown_regime_allowing"
    
    def _check_multi_signal_consensus(
        self,
        signal: TradeSignal,
        context: MarketContext,
    ) -> Tuple[bool, str]:
        """
        FIX #5: Multi-Signal Confirmation
        
        Require MULTIPLE independent signals to align with the directional bet.
        Single LLM signal is unreliable; need consensus from 3+ sources:
        
        1. Technical trend (from PCR/max pain)
        2. News sentiment
        3. OI bias (Call/Put Open Interest ratio)
        4. VWAP alignment
        5. Entry point quality (distance from support/resistance)
        
        Returns (approved, reason).
        """
        signal_direction = signal.direction  # "BUY_PE" or "BUY_CE"
        signal_bullish = "CE" in signal_direction
        signal_bearish = "PE" in signal_direction
        
        consensus_votes = []
        reasons = []
        
        print(f"\n   [Multi-Signal Consensus] Checking alignment for {signal_direction}...")
        
        # Signal 1: Technical trend (from PCR + max pain)
        trend_bullish = context.trend in [Trend.BULLISH, Trend.STRONG_BULLISH]
        trend_bearish = context.trend in [Trend.BEARISH, Trend.STRONG_BEARISH]
        
        if (signal_bullish and trend_bullish) or (signal_bearish and trend_bearish):
            consensus_votes.append(True)
            reasons.append(f"✓ Trend aligned ({context.trend.value})")
        elif context.trend == Trend.NEUTRAL:
            # Neutral trend is ambiguous, count as 0 (neither for nor against)
            reasons.append(f"~ Trend neutral (no signal)")
        else:
            consensus_votes.append(False)
            reasons.append(f"✗ Trend opposed ({context.trend.value})")
        
        # Signal 2: News sentiment
        news_bullish = context.news_sentiment == "BULLISH"
        news_bearish = context.news_sentiment == "BEARISH"
        
        if (signal_bullish and news_bullish) or (signal_bearish and news_bearish):
            consensus_votes.append(True)
            reasons.append(f"✓ News aligned ({context.news_sentiment})")
        elif context.news_sentiment == "NEUTRAL":
            reasons.append(f"~ News neutral")
        else:
            consensus_votes.append(False)
            reasons.append(f"✗ News opposed ({context.news_sentiment})")
        
        # Signal 3: OI bias (Put/Call OI ratio)
        # oi_bias: "BULLISH" means more call OI (bullish), "BEARISH" means more put OI
        oi_bullish = "bullish" in context.oi_bias.lower()
        oi_bearish = "bearish" in context.oi_bias.lower()
        
        if (signal_bullish and oi_bullish) or (signal_bearish and oi_bearish):
            consensus_votes.append(True)
            reasons.append(f"✓ OI bias aligned ({context.oi_bias})")
        elif context.oi_bias.upper() == "NEUTRAL":
            reasons.append(f"~ OI bias neutral")
        else:
            consensus_votes.append(False)
            reasons.append(f"✗ OI bias opposed ({context.oi_bias})")
        
        # Signal 4: VWAP alignment (spot vs VWAP tells us momentum)
        vwap_above = "above" in context.spot_vs_vwap.lower()
        vwap_below = "below" in context.spot_vs_vwap.lower()
        
        if (signal_bullish and vwap_above) or (signal_bearish and vwap_below):
            consensus_votes.append(True)
            reasons.append(f"✓ VWAP aligned (spot {context.spot_vs_vwap})")
        elif "unknown" in context.spot_vs_vwap.lower():
            reasons.append(f"~ VWAP unknown")
        else:
            consensus_votes.append(False)
            reasons.append(f"✗ VWAP opposed (spot {context.spot_vs_vwap})")
        
        # Signal 5: PCR (Put/Call Ratio) - independent check
        pcr_bullish = context.pcr < 0.9   # Low PCR = bullish
        pcr_bearish = context.pcr > 1.1   # High PCR = bearish
        
        if (signal_bullish and pcr_bullish) or (signal_bearish and pcr_bearish):
            consensus_votes.append(True)
            reasons.append(f"✓ PCR aligned ({context.pcr:.2f})")
        elif 0.9 <= context.pcr <= 1.1:
            reasons.append(f"~ PCR neutral ({context.pcr:.2f})")
        else:
            consensus_votes.append(False)
            reasons.append(f"✗ PCR opposed ({context.pcr:.2f})")
        
        # Print all signals
        for reason in reasons:
            print(f"      {reason}")
        
        # Decision: Need MAJORITY consensus
        votes_for = sum(1 for v in consensus_votes if v)
        votes_against = sum(1 for v in consensus_votes if not v)
        total_votes = votes_for + votes_against
        
        print(f"      Score: {votes_for} for, {votes_against} against (out of {total_votes} signals)")
        
        if total_votes == 0:
            # No strong signals either way
            print(f"      ⚠️  No clear consensus (too many neutral signals)")
            return False, f"no_consensus_too_many_neutrals"
        
        # Require 60% majority (3/5, 2/3, etc)
        vote_pct = votes_for / total_votes
        if vote_pct >= 0.6:
            print(f"      ✓ CONSENSUS: {vote_pct:.0%} signals aligned")
            return True, f"consensus_achieved_{votes_for}_of_{total_votes}"
        else:
            print(f"      ❌ CONSENSUS FAILED: Only {vote_pct:.0%} signals aligned (need 60%+)")
            return False, f"no_consensus_{votes_for}_of_{total_votes}_signals"

    def _llm_execute_gate(
        self,
        signal: TradeSignal,
        context: MarketContext,
        consensus: Optional[Dict[str, Any]],
    ) -> Tuple[bool, str]:
        """
        Final AI gate: ChatGPT-only veto after deterministic risk gates pass.

        Gemini is deliberately not execution-critical because browser-backed
        Proxima providers can return auth pages, prose, or non-JSON responses.
        If ChatGPT returns explicit JSON NO_TRADE, block. If ChatGPT is
        unavailable or returns malformed output, do not block a risk-approved
        trade on infrastructure noise.

        Returns (allowed, reason).
        """
        if self.llm.backend != LLMBackend.PROXIMA:
            return True, "gpt_gate_skipped_non_proxima"
        try:
            import json as _json

            cons_txt = _json.dumps(consensus or {}, default=str)[:4000]
            user = f"""You are the final risk gate for one F&O intraday BUY.

Proposed trade (JSON):
{_json.dumps({
    "symbol": signal.symbol,
    "direction": signal.direction,
    "strike": signal.strike,
    "option_type": signal.option_type,
    "expiry": signal.expiry,
    "entry_premium": signal.entry_premium,
    "lots": signal.lots,
    "lot_size": signal.lot_size,
    "stop_loss_pct": signal.stop_loss_pct,
    "target_pct": signal.target_pct,
    "confidence": signal.confidence,
    "reasoning": signal.reasoning,
}, default=str)}

Market context:
- spot: {context.spot_price:.2f}
- trend: {context.trend.value}
- iv_level: {context.iv_level:.2f}
- regime: {context.regime}
- vix: {context.vix:.2f}
- news_sentiment: {context.news_sentiment}
- signal_strength: {context.signal_strength:.2f}

Prefetch agent consensus (may be empty):
{cons_txt}

Output ONLY JSON per system instructions (decision EXECUTE or NO_TRADE)."""
            print("   🧠 Consulting ChatGPT final gate...")
            response = self.llm.chat(
                [{"role": "user", "content": user}],
                task_type="final_decision",
                model_override="chatgpt",
            )

            parsed, parse_err = parse_json_response(response.content)
            if not parsed:
                msg = str(parse_err or response.content or "empty_response")[:180]
                print(f"      ⚠️ ChatGPT gate parse failed ({msg})")
                if self._live_mode_requires_fail_closed():
                    return False, f"gpt_gate_unavailable_fail_closed:{msg}"
                return True, f"gpt_gate_unavailable_fail_open:{msg}"

            gpt_decision = str(parsed.get("decision", "NO_TRADE")).upper().strip()
            gpt_reason = str(parsed.get("reasoning", "")).strip()
            if gpt_decision == "EXECUTE":
                return True, f"gpt_gate_execute:{gpt_reason or 'approved'}"
            if gpt_decision == "NO_TRADE":
                return False, f"gpt_gate_no_trade:{gpt_reason or 'ChatGPT veto'}"

            if self._live_mode_requires_fail_closed():
                return False, f"gpt_gate_unknown_decision_fail_closed:{gpt_decision}"
            return True, f"gpt_gate_unknown_decision_fail_open:{gpt_decision}"
        except Exception as ex:
            if self._live_mode_requires_fail_closed():
                return False, f"gpt_gate_error_fail_closed:{ex}"
            return True, f"gpt_gate_error_fail_open:{ex}"

    def _live_mode_requires_fail_closed(self) -> bool:
        """In real broker modes, final LLM gate infrastructure errors block."""
        try:
            from execution import runtime_safety

            return runtime_safety.load_trading_mode() in (
                runtime_safety.TradingMode.MICRO_LIVE,
                runtime_safety.TradingMode.LIVE,
            )
        except Exception:
            return True
    
    def _determine_trend(self, spot: float, max_pain: float, pcr: float) -> Tuple[Trend, float]:
        """Determine trend from market data."""
        # PCR interpretation:
        # PCR < 0.7: Bullish (more calls being bought)
        # PCR 0.7-1.0: Neutral to slightly bullish
        # PCR 1.0-1.3: Neutral to slightly bearish
        # PCR > 1.3: Bearish (more puts being bought)
        
        # Max pain interpretation:
        # Spot > Max Pain: Bullish pressure
        # Spot < Max Pain: Bearish pressure
        
        pain_diff_pct = (spot - max_pain) / max_pain * 100
        
        score = 0
        
        # PCR contribution
        if pcr < 0.7:
            score += 2
        elif pcr < 0.9:
            score += 1
        elif pcr > 1.3:
            score -= 2
        elif pcr > 1.1:
            score -= 1
        
        # Max pain contribution
        if pain_diff_pct > 1:
            score += 1
        elif pain_diff_pct < -1:
            score -= 1
        
        # Map score to trend
        if score >= 3:
            return Trend.STRONG_BULLISH, 0.9
        elif score >= 1:
            return Trend.BULLISH, 0.7
        elif score <= -3:
            return Trend.STRONG_BEARISH, 0.9
        elif score <= -1:
            return Trend.BEARISH, 0.7
        else:
            return Trend.NEUTRAL, 0.5
    
    def _get_news_context(self, symbol: str) -> Dict[str, Any]:
        """Get news context using LLM (cached per session)."""
        if symbol in self._news_cache:
            return self._news_cache[symbol]
        
        if not self.llm.is_available():
            return {"sentiment": "NEUTRAL", "summary": "", "has_catalyst": False}
        
        try:
            prompt = f"""Analyze today's market news for Indian index {symbol}.

Consider:
- RBI monetary policy
- FII/DII activity
- Global market cues (US, Europe, Asia)
- Any major economic events

In 2-3 sentences, summarize:
1. Overall sentiment for {symbol} today (BULLISH/BEARISH/NEUTRAL)
2. Any immediate catalysts that could move the market
3. Key risk to watch

Format:
SENTIMENT: [BULLISH/BEARISH/NEUTRAL]
CATALYST: [YES/NO]
SUMMARY: [2-3 sentences]"""

            response = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                task_type="news_analysis",
            )
            
            content = response.content.upper() if response.content else ""
            
            sentiment = "NEUTRAL"
            if "BULLISH" in content and "BEARISH" not in content:
                sentiment = "BULLISH"
            elif "BEARISH" in content:
                sentiment = "BEARISH"
            
            has_catalyst = "CATALYST: YES" in content
            
            result = {
                "sentiment": sentiment,
                "summary": response.content or "",
                "has_catalyst": has_catalyst,
            }
            
            self._news_cache[symbol] = result
            return result
            
        except Exception as e:
            print(f"   News analysis error: {e}")
            return {"sentiment": "NEUTRAL", "summary": "", "has_catalyst": False}
    
    def _calculate_signal_strength(
        self,
        trend: Trend,
        trend_strength: float,
        iv_regime: IVRegime,
        pcr: float,
        news: Dict,
    ) -> float:
        """Calculate overall signal strength (0-1)."""
        score = 0.5  # Base
        
        # Trend contribution (0.2 max)
        if trend in [Trend.STRONG_BULLISH, Trend.STRONG_BEARISH]:
            score += 0.15
        elif trend in [Trend.BULLISH, Trend.BEARISH]:
            score += 0.1
        
        # IV regime contribution (0.15 max)
        # LOW IV = good for buying (cheap options)
        if iv_regime == IVRegime.LOW:
            score += 0.15
        elif iv_regime == IVRegime.NORMAL:
            score += 0.1
        elif iv_regime == IVRegime.HIGH:
            score -= 0.1  # Penalty for expensive options
        
        # News alignment (0.15 max)
        news_sentiment = news.get("sentiment", "NEUTRAL")
        if trend in [Trend.BULLISH, Trend.STRONG_BULLISH] and news_sentiment == "BULLISH":
            score += 0.15  # Alignment bonus
        elif trend in [Trend.BEARISH, Trend.STRONG_BEARISH] and news_sentiment == "BEARISH":
            score += 0.15
        elif news_sentiment != "NEUTRAL" and news.get("has_catalyst"):
            score += 0.1  # Catalyst bonus
        
        return min(max(score, 0.1), 0.95)
    
    def _should_trade(self, context: MarketContext) -> Tuple[bool, str]:
        """Determine if we should trade based on context + agent consensus + calibrated thresholds."""
        signal_threshold = (
            self.ACTIVITY_MODE_SIGNAL_THRESHOLD
            if self._activity_mode
            else self.DIRECTIONAL_SIGNAL_THRESHOLD
        )

        # Apply calibrated thresholds if available (learned from past trades)
        cal = self._get_calibrated_params(context.symbol)
        if cal and cal.get("total_trades_in_period", 0) >= 5:
            signal_threshold = max(signal_threshold, cal.get("min_signal_strength", 0.55))
            max_iv = cal.get("max_iv_for_buying", 30.0)
        else:
            max_iv = 30.0

        from brain.regime_detector import MarketRegime

        if context.regime in (MarketRegime.RANGE_BOUND.value, MarketRegime.LOW_VOL_GRIND.value):
            return False, (
                f"Regime {context.regime}: no directional trade; waiting for a confirmed trend"
            )

        if context.regime == MarketRegime.EXPIRY_DAY.value:
            return False, "Expiry-day regime — skip new directional entries"

        if context.regime == MarketRegime.HIGH_VOL_BREAKOUT.value and context.vix > 18:
            signal_threshold = max(signal_threshold, 0.62)
        
        # Rule 1: Need directional trend
        if context.trend == Trend.NEUTRAL:
            return False, "Market is sideways - no clear direction"
        
        # Rule 2: IV should not be too high (calibrated per symbol)
        if context.iv_regime == IVRegime.HIGH or (context.iv_level and context.iv_level > max_iv):
            return False, f"IV too high ({context.iv_level:.1f}%) - options expensive"
        
        # Rule 3: Need minimum signal strength (calibrated from past performance)
        if context.signal_strength < signal_threshold:
            return False, f"Signal too weak ({context.signal_strength:.0%})"
        
        # Rule 4: Trend and news should not conflict strongly
        news = context.news_sentiment
        if context.trend in [Trend.BULLISH, Trend.STRONG_BULLISH] and news == "BEARISH":
            if not context.has_catalyst:
                return False, "Trend bullish but news bearish - conflicting signals"
        
        if context.trend in [Trend.BEARISH, Trend.STRONG_BEARISH] and news == "BULLISH":
            if not context.has_catalyst:
                return False, "Trend bearish but news bullish - conflicting signals"
        
        direction = "BULLISH" if context.trend in [Trend.BULLISH, Trend.STRONG_BULLISH] else "BEARISH"
        
        # Rule 5 (v2): Agent consensus check — if LLM agents were run,
        # they must not strongly disagree with the rule-based direction.
        consensus = self._agent_consensus_cache.get(context.symbol)
        if consensus and consensus.get("used_llm"):
            cb = consensus.get("combined_bias", "NEUTRAL")
            if cb != "NEUTRAL" and cb != direction:
                return False, (
                    f"Agent consensus ({cb}) disagrees with rules ({direction}) "
                    f"— {consensus.get('summary', '')}"
                )
            if cb == direction:
                # Boost: agents agree → bump signal strength
                context.signal_strength = min(context.signal_strength + 0.10, 0.95)

        # Rule 6 (Phase F): OI buildup + VWAP confirmation
        from data_feeds.oi_analysis import OIBuildup
        oi = context.oi_buildup
        vwap_pos = context.spot_vs_vwap

        oi_contradicts = False
        vwap_contradicts = False

        if direction == "BULLISH":
            if oi in (OIBuildup.SHORT_BUILDUP.value, OIBuildup.LONG_UNWINDING.value):
                oi_contradicts = True
            if vwap_pos == "below":
                vwap_contradicts = True
        else:
            if oi in (OIBuildup.LONG_BUILDUP.value, OIBuildup.SHORT_COVERING.value):
                oi_contradicts = True
            if vwap_pos == "above":
                vwap_contradicts = True

        if oi_contradicts and vwap_contradicts:
            return False, (
                f"Double divergence: OI={oi} and VWAP={vwap_pos} both contradict "
                f"{direction} trend — skipping"
            )
        if oi_contradicts:
            context.signal_strength = max(context.signal_strength - 0.08, 0.1)
        if vwap_contradicts:
            context.signal_strength = max(context.signal_strength - 0.05, 0.1)

        # Re-check threshold after OI/VWAP penalties
        if context.signal_strength < signal_threshold:
            return False, (
                f"Signal weakened by OI/VWAP divergence ({context.signal_strength:.0%} "
                f"< {signal_threshold:.0%})"
            )

        return True, f"{direction} signal with {context.signal_strength:.0%} strength"
    
    def _generate_signal(self, context: MarketContext, capital: float) -> Optional[TradeSignal]:
        """Generate a trading signal from context."""
        symbol = context.symbol
        spot = context.spot_price
        
        # Determine direction
        if context.trend in [Trend.BULLISH, Trend.STRONG_BULLISH]:
            direction = "BUY_CE"
            option_type = "CE"
            # For calls, buy slightly OTM for better leverage
            strike_offset = 1 if context.trend == Trend.STRONG_BULLISH else 0
        else:
            direction = "BUY_PE"
            option_type = "PE"
            # For puts, buy slightly OTM
            strike_offset = -1 if context.trend == Trend.STRONG_BEARISH else 0
        
        # Calculate strike (OI-informed: avoid strikes near heavy resistance/support walls)
        interval = self.STRIKE_INTERVALS.get(symbol, 50)
        atm_strike = round(spot / interval) * interval
        strike = atm_strike + (strike_offset * interval)

        # Phase F: Use sticky strikes to validate or shift strike
        if option_type == "CE" and context.sticky_call_strike > 0:
            wall = context.sticky_call_strike
            # If our target strike equals or exceeds the resistance wall, stay ATM
            if strike >= wall and wall > atm_strike:
                strike = atm_strike
        elif option_type == "PE" and context.sticky_put_strike > 0:
            wall = context.sticky_put_strike
            # If our target strike equals or is below the support wall, stay ATM
            if strike <= wall and wall < atm_strike:
                strike = atm_strike

        # Get lot size
        lot_size = self.LOT_SIZES.get(symbol, 50)
        
        # Estimate premium (using Greeks calculator or fallback)
        premium = self._estimate_premium(symbol, spot, strike, option_type, context.iv_level)
        
        if premium <= 0:
            return None
        
        # Set stop loss and target based on IV regime
        # REALISTIC TARGETS for intraday options buying
        if context.iv_regime == IVRegime.LOW:
            # Low IV = options are cheap, can hold longer
            stop_loss = 25
            target = 35
        elif context.iv_regime == IVRegime.NORMAL:
            # Normal IV = standard risk management
            stop_loss = 25
            target = 30
        else:
            # High IV = options are expensive, but generated signals must still
            # satisfy the hard risk gate minimum stop and reward/risk rules.
            stop_loss = RiskGates.MIN_STOP_LOSS_PCT
            target = stop_loss * RiskGates.MIN_RISK_REWARD
        
        # Get expiry
        try:
            expiry = self.fo_feed.get_nearest_expiry(symbol)
        except Exception:
            # Fallback: assume Thursday expiry
            ist = pytz.timezone('Asia/Kolkata')
            now = datetime.now(ist)
            days_until_thursday = (3 - now.weekday()) % 7
            expiry_date = now + timedelta(days=days_until_thursday)
            expiry = expiry_date.strftime("%Y-%m-%d")

        instrument_key: Optional[str] = None
        chain = self.fo_feed.get_option_chain(symbol, expiry)
        if chain and "error" not in chain:
            rows = chain.get("calls", []) if option_type == "CE" else chain.get("puts", [])
            for r in rows:
                try:
                    if abs(float(r.get("strike", 0)) - float(strike)) < 0.51:
                        ltp = float(r.get("ltp") or 0)
                        if ltp > 0:
                            premium = ltp
                        instrument_key = r.get("instrument_key")
                        break
                except (TypeError, ValueError):
                    continue
        
        # Lot sizing AFTER live premium from chain (audit fix)
        max_order_value = min(capital * 0.5, 12000)
        lots = max(1, int(max_order_value / (premium * lot_size)))
        
        return TradeSignal(
            symbol=symbol,
            direction=direction,
            strike=strike,
            option_type=option_type,
            expiry=expiry,
            instrument_key=instrument_key,
            entry_premium=premium,
            lot_size=lot_size,
            lots=lots,
            stop_loss_pct=stop_loss,
            target_pct=target,
            confidence=context.signal_strength,
            reasoning=f"{direction} based on {context.trend.value} trend, IV={context.iv_level:.1f}%, PCR={context.pcr:.2f}",
            context=context,
        )
    
    def _estimate_premium(
        self,
        symbol: str,
        spot: float,
        strike: float,
        option_type: str,
        iv: float,
    ) -> float:
        """Estimate option premium using Black-Scholes."""
        try:
            from data_feeds.options_greeks import OptionType as OT
            
            # Assume 7 days to expiry for estimation
            time_to_expiry = 7 / 365
            
            greeks = self.greeks_calc.calculate_greeks(
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                volatility=iv / 100,
                option_type=OT.CALL if option_type == "CE" else OT.PUT,
            )
            
            if greeks.theoretical_price and greeks.theoretical_price > 0:
                return round(greeks.theoretical_price, 1)
            
        except Exception as e:
            pass
        
        # Fallback: rough estimation
        moneyness = (spot - strike) / spot * 100
        
        if option_type == "CE":
            intrinsic = max(0, spot - strike)
            if moneyness > 0:  # ITM
                return intrinsic + spot * 0.005
            else:  # OTM
                return max(20, spot * 0.004 * (1 + abs(moneyness) / 10))
        else:
            intrinsic = max(0, strike - spot)
            if moneyness < 0:  # ITM
                return intrinsic + spot * 0.005
            else:  # OTM
                return max(20, spot * 0.004 * (1 + abs(moneyness) / 10))
    
    def _estimate_hours_to_expiry(self, expiry_date_str: str) -> float:
        """
        Calculate hours remaining until option expiry.
        
        Args:
            expiry_date_str: Format "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS"
        
        Returns:
            Hours remaining until 15:30 IST (market close) on expiry date
        """
        try:
            from datetime import datetime, time
            
            # Parse expiry date
            if " " in expiry_date_str:
                expiry_dt = datetime.fromisoformat(expiry_date_str)
            else:
                expiry_dt = datetime.strptime(expiry_date_str, "%Y-%m-%d")
            
            # Market close time on expiry: 15:30 IST
            expiry_close = expiry_dt.replace(hour=15, minute=30, second=0, microsecond=0)
            expiry_close = pytz.timezone("Asia/Kolkata").localize(expiry_close)
            
            # Current time
            now = datetime.now(IST)
            
            # Calculate remaining hours
            time_delta = expiry_close - now
            hours_remaining = time_delta.total_seconds() / 3600
            
            return max(0.1, hours_remaining)  # Minimum 0.1 hours to avoid division errors
        
        except Exception as e:
            logger.warning(f"Could not parse expiry date {expiry_date_str}: {e}")
            return 24  # Default to 24 hours if parsing fails
    
    def _apply_risk_gates(self, signal: TradeSignal, capital: float) -> TradeDecision:
        """Apply all risk gates to signal."""
        # Re-hydrate counters from DB (catches losses from exit thread)
        self._hydrate_daily_counters_from_db(datetime.now(IST).date())
        approved, gates, rejection = RiskGates.check_all(
            signal=signal,
            available_capital=capital,
            daily_loss_so_far=self._daily_loss,
            daily_trades_so_far=self._daily_trades,
        )
        
        order_value = signal.entry_premium * signal.lot_size * signal.lots
        max_loss = order_value * (signal.stop_loss_pct / 100)
        rr = signal.target_pct / signal.stop_loss_pct
        
        return TradeDecision(
            signal=signal,
            approved=approved,
            gate_results=gates,
            rejection_reason=rejection,
            order_value=order_value,
            max_loss=max_loss,
            risk_reward=rr,
        )
    
    def _log_decision(self, signal: TradeSignal, decision: TradeDecision) -> Optional[str]:
        """Log decision for learning. Returns decision_id or None."""
        try:
            td = TradingDecision(
                decision_id=self.decision_log.generate_decision_id(),
                timestamp=datetime.now(IST),
                decision_type=DecisionType.TRADE_ENTRY,
                symbol=signal.symbol,
                action=signal.direction,
                strike=signal.strike,
                option_type=signal.option_type,
                lots=signal.lots,
                entry_price=signal.entry_premium,
                reasoning=signal.reasoning,
                confidence=signal.confidence,
                spot_price=signal.context.spot_price if signal.context else 0,
                iv_level=signal.context.iv_level if signal.context else None,
                pcr=signal.context.pcr if signal.context else None,
                trend=signal.context.trend.value if signal.context else None,
                fo_signal=signal.instrument_key or "",
                strategy_name="lean_fo",
                model_used=self.llm.model if self.llm.is_available() else "rule_based",
            )
            
            return self.decision_log.log_decision(td)
            
        except Exception as e:
            print(f"   Warning: Could not log decision: {e}")
            return None
    
    def get_activity_report(self, days: int = 7) -> Dict[str, Any]:
        """Get activity report from signal tracker."""
        return self.signal_tracker.get_activity_report(days)
    
    def print_activity_report(self, days: int = 7):
        """Print activity report."""
        self.signal_tracker.print_report(days)


# Convenience functions
def analyze_fo(symbol: str, capital: float = 20000, paper_mode: bool = True) -> Dict[str, Any]:
    """Quick F&O analysis."""
    brain = LeanFOBrain(paper_mode=paper_mode)
    return brain.analyze(symbol, capital)


def activity_report(days: int = 7):
    """Print activity report showing all scans and why they passed/failed."""
    from brain.signal_tracker import get_signal_tracker
    tracker = get_signal_tracker()
    tracker.print_report(days)


# Test
if __name__ == "__main__":
    result = analyze_fo("NIFTY", 20000)
    print(f"\nFinal Decision: {result.get('decision')}")
    
    print("\n--- Activity Report ---")
    activity_report(7)
