"""
Market regime detection and pre-market day plan (Pro Trading Agent upgrade).

Rule-based regime from India VIX, recent returns, expiry proximity, and trend.
Optional LLM JSON refinement. Pre-market briefing cached per IST calendar day.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional, TYPE_CHECKING

import pytz

IST = pytz.timezone("Asia/Kolkata")

if TYPE_CHECKING:
    from brain.lean_fo_brain import MarketContext


class MarketRegime(str, Enum):
    TRENDING_BULLISH = "trending_bullish"
    TRENDING_BEARISH = "trending_bearish"
    RANGE_BOUND = "range_bound"
    HIGH_VOL_BREAKOUT = "high_vol_breakout"
    LOW_VOL_GRIND = "low_vol_grind"
    EXPIRY_DAY = "expiry_day"
    UNKNOWN = "unknown"


@dataclass
class RegimeSnapshot:
    regime: str = MarketRegime.UNKNOWN.value
    confidence: float = 0.5
    reasoning: str = ""
    vix: float = 0.0
    vix_change_pct: float = 0.0
    rule_regime: str = MarketRegime.UNKNOWN.value
    five_day_return_pct: float = 0.0
    days_to_expiry: int = 7


def _data_cache_dir() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "data_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _day_plan_path(ist_date: str) -> str:
    return os.path.join(_data_cache_dir(), f"day_plan_{ist_date}.json")


def load_day_plan_for_date(ist_date: str) -> Optional[Dict[str, Any]]:
    path = _day_plan_path(ist_date)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


class RegimeDetector:
    """Detects intraday regime; generates pre-market briefing once per day."""

    def __init__(self) -> None:
        self._last_briefing_date: Optional[str] = None

    def _fetch_five_day_return(self, fo_feed: Any, symbol: str) -> float:
        """Approximate last-5-session return % from daily candles."""
        try:
            ik = fo_feed.INDEX_KEYS.get(symbol.upper())
            if not ik:
                return 0.0
            to_d = datetime.now(IST).strftime("%Y-%m-%d")
            from_d = (datetime.now(IST) - timedelta(days=14)).strftime("%Y-%m-%d")
            resp = fo_feed.client.get_historical_candles(
                instrument_key=ik, interval="day", from_date=from_d, to_date=to_d
            )
            raw = (resp or {}).get("data", {}).get("candles") or []
            if len(raw) < 2:
                return 0.0
            raw_sorted = sorted(raw, key=lambda c: str(c[0]))
            closes = [float(c[4]) for c in raw_sorted if len(c) > 4]
            if len(closes) < 2:
                return 0.0
            last = closes[-1]
            first = closes[max(0, len(closes) - 6)]
            if first <= 0:
                return 0.0
            return (last / first - 1.0) * 100.0
        except Exception:
            return 0.0

    def _rule_based_regime(
        self,
        *,
        vix: float,
        ret5: float,
        days_to_expiry: int,
        trend_name: str,
    ) -> tuple[str, str]:
        """Returns (regime_value, reasoning)."""
        if days_to_expiry <= 0:
            return MarketRegime.EXPIRY_DAY.value, "Expiry day (0 DTE or same-day expiry)"
        if vix >= 18 and abs(ret5) > 1.2:
            return MarketRegime.HIGH_VOL_BREAKOUT.value, f"VIX={vix:.1f} elevated and 5d move {ret5:+.1f}%"
        if vix > 0 and vix < 13 and abs(ret5) < 0.8:
            return MarketRegime.LOW_VOL_GRIND.value, f"VIX={vix:.1f} low, tight 5d range {ret5:+.1f}%"
        if abs(ret5) < 1.2 and 12 <= vix <= 18:
            return MarketRegime.RANGE_BOUND.value, f"Sideways 5d ({ret5:+.1f}%) with mid VIX {vix:.1f}"
        if trend_name in ("strong_bullish", "bullish"):
            return MarketRegime.TRENDING_BULLISH.value, f"Trend {trend_name}, VIX={vix:.1f}"
        if trend_name in ("strong_bearish", "bearish"):
            return MarketRegime.TRENDING_BEARISH.value, f"Trend {trend_name}, VIX={vix:.1f}"
        return MarketRegime.RANGE_BOUND.value, f"Default range; trend={trend_name}, VIX={vix:.1f}"

    def detect(
        self,
        symbol: str,
        context: "MarketContext",
        fo_feed: Any,
        llm: Any,
    ) -> RegimeSnapshot:
        """Full regime snapshot for symbol given current MarketContext."""
        vix_data = fo_feed.get_india_vix()
        vix = float(vix_data.get("vix") or 0)
        vix_ch = float(vix_data.get("vix_change_pct") or 0)
        if vix <= 0:
            vix = 15.0

        exp = fo_feed.get_nearest_expiry(symbol)
        days_te = fo_feed._days_to_expiry(exp) if exp else 7

        ret5 = self._fetch_five_day_return(fo_feed, symbol)
        trend_name = context.trend.value if hasattr(context.trend, "value") else str(context.trend)

        rule_reg, reason = self._rule_based_regime(
            vix=vix,
            ret5=ret5,
            days_to_expiry=days_te,
            trend_name=trend_name,
        )

        final_reg = rule_reg
        conf = 0.55
        reasoning = reason

        today = datetime.now(IST).strftime("%Y-%m-%d")
        day_plan = load_day_plan_for_date(today)
        if day_plan and isinstance(day_plan.get("regime"), str):
            dr = day_plan["regime"]
            valid = {e.value for e in MarketRegime}
            if dr in valid and dr != MarketRegime.UNKNOWN.value:
                final_reg = dr
                conf = 0.62
                notes = str(day_plan.get("notes", ""))[:120]
                reasoning = f"Day plan + rules: {dr} ({notes})"

        if llm is not None and getattr(llm, "is_available", lambda: False)():
            try:
                from llm.schemas import REGIME_JSON_SCHEMA, parse_json_response

                prompt = f"""Given:
- symbol: {symbol}
- rule_based_regime: {rule_reg}
- India_VIX: {vix:.2f} (change_pct: {vix_ch:.2f})
- five_day_index_return_pct: {ret5:.2f}
- days_to_expiry: {days_te}
- context_trend: {trend_name}
- spot: {context.spot_price:.2f}
- pcr_oi: {context.pcr:.3f}

Respond with ONLY valid JSON matching:
{REGIME_JSON_SCHEMA}
If unsure, set regime to the same as rule_based_regime and confidence <= 0.5."""
                resp = llm.chat(
                    [{"role": "user", "content": prompt}],
                    task_type="market_reasoning",
                    temperature=0.2,
                    max_tokens=400,
                )
                if not resp.content.startswith("ERROR:"):
                    parsed, _ = parse_json_response(resp.content)
                    if parsed:
                        lr = str(parsed.get("regime", "")).strip().lower()
                        valid = {e.value for e in MarketRegime}
                        lc = float(parsed.get("confidence", 0) or 0)
                        if lr in valid and lc >= 0.55:
                            final_reg = lr
                            conf = min(0.9, max(conf, lc))
                            reasoning = str(parsed.get("reasoning", reasoning))[:500]
            except Exception:
                pass

        return RegimeSnapshot(
            regime=final_reg,
            confidence=conf,
            reasoning=reasoning[:800],
            vix=vix,
            vix_change_pct=vix_ch,
            rule_regime=rule_reg,
            five_day_return_pct=ret5,
            days_to_expiry=days_te,
        )

    def generate_pre_market_briefing(self, fo_feed: Any, llm: Any) -> Optional[Dict[str, Any]]:
        """
        Run once pre-open (e.g. 9:10 IST). Writes day_plan JSON for today.
        Returns day_plan dict or None if LLM unavailable / failed.
        """
        today = datetime.now(IST).strftime("%Y-%m-%d")
        if self._last_briefing_date == today and load_day_plan_for_date(today):
            return load_day_plan_for_date(today)

        if llm is None or not llm.is_available():
            return None

        vix_data = fo_feed.get_india_vix()
        vix = float(vix_data.get("vix") or 15)
        chain: Dict[str, Any] = {}
        try:
            chain = fo_feed.get_option_chain("NIFTY") or {}
        except Exception:
            pass
        summary = chain.get("summary", {}) if isinstance(chain, dict) else {}
        spot = chain.get("spot_price", 0)

        yday_pnl = ""
        try:
            from memory.decision_log import get_decision_log

            stats = get_decision_log().get_performance_stats(days=2, symbol="NIFTY")
            yday_pnl = (
                f"recent_stats: trades={stats.get('total_trades', 0)} "
                f"win_rate={stats.get('win_rate', 0):.0f}% pnl={stats.get('total_pnl', 0):.0f}"
            )
        except Exception:
            pass

        from llm.schemas import DAY_PLAN_JSON_SCHEMA, parse_json_response

        prompt = f"""Pre-market briefing for Indian markets ({today} IST).
India VIX: {vix:.2f} (change_pct {vix_data.get('vix_change_pct', 0)})
NIFTY spot (if available): {spot}
OI summary: pcr_oi={summary.get('pcr_oi', 'n/a')}, max_pain={summary.get('max_pain', 'n/a')}
{yday_pnl}

Output ONLY valid JSON matching:
{DAY_PLAN_JSON_SCHEMA}
strategy_preference: one line (e.g. directional CE/PE vs avoid vs sell premium — high level only)."""

        try:
            resp = llm.chat(
                [{"role": "user", "content": prompt}],
                task_type="market_reasoning",
                temperature=0.35,
                max_tokens=600,
            )
            if resp.content.startswith("ERROR:"):
                return None
            parsed, _ = parse_json_response(resp.content)
            if not parsed:
                return None
            parsed["generated_at"] = datetime.now(IST).isoformat()
            path = _day_plan_path(today)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2)
            self._last_briefing_date = today
            return parsed
        except Exception:
            return None


_detector: Optional[RegimeDetector] = None


def get_regime_detector() -> RegimeDetector:
    global _detector
    if _detector is None:
        _detector = RegimeDetector()
    return _detector
