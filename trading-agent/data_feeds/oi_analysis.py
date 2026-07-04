"""
OI (Open Interest) Analysis Module.

Classifies OI patterns into standard institutional interpretations:
- LONG_BUILDUP: OI up + price up (new longs entering)
- SHORT_BUILDUP: OI up + price down (new shorts entering)
- LONG_UNWINDING: OI down + price down (longs exiting)
- SHORT_COVERING: OI down + price up (shorts exiting)

Also provides sticky-strike detection (institutional walls) and
OI concentration bias for directional conviction.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple


class OIBuildup(str, Enum):
    LONG_BUILDUP = "long_buildup"
    SHORT_BUILDUP = "short_buildup"
    LONG_UNWINDING = "long_unwinding"
    SHORT_COVERING = "short_covering"
    NEUTRAL = "neutral"


@dataclass
class OISnapshot:
    """Summarized OI analysis for a symbol's option chain."""
    total_call_oi: int = 0
    total_put_oi: int = 0
    pcr_oi: float = 0.0
    max_pain: float = 0.0
    call_oi_change: int = 0
    put_oi_change: int = 0
    oi_buildup: OIBuildup = OIBuildup.NEUTRAL
    sticky_call_strike: float = 0.0
    sticky_put_strike: float = 0.0
    oi_bias: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL
    top_call_oi_strikes: List[float] = field(default_factory=list)
    top_put_oi_strikes: List[float] = field(default_factory=list)


def analyze_oi_buildup(chain: Dict[str, Any], spot_change_pct: float = 0.0) -> OIBuildup:
    """
    Classify the aggregate OI change pattern.

    Standard interpretation:
      OI increasing + price up   = LONG_BUILDUP (bullish)
      OI increasing + price down = SHORT_BUILDUP (bearish)
      OI decreasing + price down = LONG_UNWINDING (weak bearish / neutral)
      OI decreasing + price up   = SHORT_COVERING (weak bullish)

    Args:
        chain: Option chain dict from fo_data_feed.get_option_chain()
        spot_change_pct: Spot price change % from open/prev close
    """
    calls = chain.get("calls", [])
    puts = chain.get("puts", [])

    total_oi_change = 0
    for leg in calls + puts:
        total_oi_change += int(leg.get("oi_change", 0) or 0)

    if abs(total_oi_change) < 500 and abs(spot_change_pct) < 0.1:
        return OIBuildup.NEUTRAL

    oi_increasing = total_oi_change > 0
    price_up = spot_change_pct > 0.05

    if oi_increasing and price_up:
        return OIBuildup.LONG_BUILDUP
    elif oi_increasing and not price_up:
        return OIBuildup.SHORT_BUILDUP
    elif not oi_increasing and not price_up:
        return OIBuildup.LONG_UNWINDING
    elif not oi_increasing and price_up:
        return OIBuildup.SHORT_COVERING

    return OIBuildup.NEUTRAL


def find_sticky_strikes(chain: Dict[str, Any], top_n: int = 3) -> Tuple[List[float], List[float]]:
    """
    Find strikes with highest OI — these act as institutional walls.

    Returns (top_call_oi_strikes, top_put_oi_strikes) sorted by OI descending.
    """
    calls = chain.get("calls", [])
    puts = chain.get("puts", [])

    call_by_oi = sorted(calls, key=lambda r: int(r.get("oi", 0) or 0), reverse=True)
    put_by_oi = sorted(puts, key=lambda r: int(r.get("oi", 0) or 0), reverse=True)

    top_calls = [float(r["strike"]) for r in call_by_oi[:top_n] if r.get("strike")]
    top_puts = [float(r["strike"]) for r in put_by_oi[:top_n] if r.get("strike")]

    return top_calls, top_puts


def oi_concentration_bias(
    chain: Dict[str, Any],
    spot: float,
    range_pct: float = 2.0,
) -> str:
    """
    Determine directional bias from OI concentration near spot.

    If call OI >> put OI near spot → resistance above → BEARISH bias
    If put OI >> call OI near spot → support below → BULLISH bias

    Args:
        chain: Option chain dict
        spot: Current spot price
        range_pct: Consider strikes within this % of spot
    """
    calls = chain.get("calls", [])
    puts = chain.get("puts", [])

    threshold = spot * (range_pct / 100)
    near_call_oi = 0
    near_put_oi = 0

    for c in calls:
        strike = float(c.get("strike", 0) or 0)
        if abs(strike - spot) <= threshold:
            near_call_oi += int(c.get("oi", 0) or 0)

    for p in puts:
        strike = float(p.get("strike", 0) or 0)
        if abs(strike - spot) <= threshold:
            near_put_oi += int(p.get("oi", 0) or 0)

    if near_call_oi == 0 and near_put_oi == 0:
        return "NEUTRAL"

    ratio = near_put_oi / max(near_call_oi, 1)

    if ratio > 1.5:
        return "BULLISH"  # Heavy put OI = strong support below
    elif ratio < 0.67:
        return "BEARISH"  # Heavy call OI = strong resistance above
    return "NEUTRAL"


def build_oi_snapshot(
    chain: Dict[str, Any],
    spot: float,
    spot_change_pct: float = 0.0,
) -> OISnapshot:
    """Build a complete OI analysis snapshot from a chain dict."""
    calls = chain.get("calls", [])
    puts = chain.get("puts", [])

    total_call_oi = sum(int(c.get("oi", 0) or 0) for c in calls)
    total_put_oi = sum(int(p.get("oi", 0) or 0) for p in puts)
    call_oi_change = sum(int(c.get("oi_change", 0) or 0) for c in calls)
    put_oi_change = sum(int(p.get("oi_change", 0) or 0) for p in puts)

    pcr = total_put_oi / max(total_call_oi, 1)
    max_pain = float(chain.get("max_pain", 0) or 0)

    buildup = analyze_oi_buildup(chain, spot_change_pct)
    top_calls, top_puts = find_sticky_strikes(chain)
    bias = oi_concentration_bias(chain, spot)

    return OISnapshot(
        total_call_oi=total_call_oi,
        total_put_oi=total_put_oi,
        pcr_oi=pcr,
        max_pain=max_pain,
        call_oi_change=call_oi_change,
        put_oi_change=put_oi_change,
        oi_buildup=buildup,
        sticky_call_strike=top_calls[0] if top_calls else 0.0,
        sticky_put_strike=top_puts[0] if top_puts else 0.0,
        oi_bias=bias,
        top_call_oi_strikes=top_calls,
        top_put_oi_strikes=top_puts,
    )
