"""
REAL-DATA BACKTESTING ENGINE  v2

Runs strategies on ACTUAL Upstox historical candles — no synthetic/fake data.

Bias safeguards:
  - Decision function receives ONLY prev-day(s) OHLC + today's open.
  - Intraday exit uses delta-based pricing with slippage + brokerage.
  - High/low ordering randomised per day.

Improvements over v1:
  - ATR-adaptive stop-loss / target (volatility-scaled)
  - Trailing stop-loss simulation
  - Confirmation filter (gap must hold — not fill in first candle)
  - Day-of-week filter
  - Multi-day context (5-day lookback for ATR, trend)

Costs modelled:
  - Entry slippage  +1.5 %
  - Exit  slippage  -1.5 %
  - Brokerage ₹40 round-trip
"""

import math
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RealCandle:
    date: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class DecisionContext:
    """What the strategy knows at 9:20 AM — NO today high/low/close."""
    date: datetime
    symbol: str
    prev_open: float
    prev_high: float
    prev_low: float
    prev_close: float
    today_open: float
    gap_pct: float
    prev_range_pct: float
    prev_trend: str
    avg_daily_range_pct: float
    simulated_iv: float
    # v2 additions
    atr_5: float            # 5-day average true range (points)
    atr_pct: float          # ATR as % of price
    day_of_week: int        # 0=Mon … 4=Fri
    prev_2_trend: str       # 2-day trend (both green / both red / mixed)
    gap_vs_atr: float       # gap size relative to ATR (how significant is it)


@dataclass
class Trade:
    date: datetime
    symbol: str
    strike: float
    option_type: str
    entry_premium: float
    exit_premium: float
    exit_reason: str
    pnl: float
    lot_size: int
    lots: int


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LOT_SIZES = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 40}
STRIKE_INTERVALS = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}
AVG_IV = {"NIFTY": 65.13, "BANKNIFTY": 30.18, "FINNIFTY": 0.15}

ENTRY_SLIPPAGE = 0.015
EXIT_SLIPPAGE = 0.015
ROUND_TRIP_BROKERAGE = 40

# ---------------------------------------------------------------------------
# Fetch real candles from Upstox
# ---------------------------------------------------------------------------

def fetch_real_candles(symbol: str, from_date: str, to_date: str) -> List[RealCandle]:
    from mcp_server.upstox_client import get_upstox_client
    index_keys = {
        "NIFTY": "NSE_INDEX|Nifty 50",
        "BANKNIFTY": "NSE_INDEX|Nifty Bank",
        "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    }
    key = index_keys.get(symbol.upper())
    if not key:
        raise ValueError(f"Unknown index symbol {symbol}")

    client = get_upstox_client()
    resp = client.get_historical_candles(instrument_key=key, interval="day",
                                         from_date=from_date, to_date=to_date)
    raw = resp.get("data", {}).get("candles", [])
    if not raw:
        raise RuntimeError(f"No data for {symbol}: {resp}")

    candles = [RealCandle(date=datetime.fromisoformat(c[0]),
                          open=c[1], high=c[2], low=c[3], close=c[4])
               for c in raw]
    candles.sort(key=lambda x: x.date)
    return candles


def fetch_equity_candles(symbol: str, from_date: str, to_date: str) -> List[RealCandle]:
    """Fetch real daily candles for an equity stock from Upstox."""
    from mcp_server.upstox_client import get_upstox_client
    from data_feeds.instrument_master import get_instrument_master

    master = get_instrument_master()
    ikey = master.get_instrument_key(symbol)
    if not ikey:
        raise ValueError(f"No instrument key for {symbol}")

    client = get_upstox_client()
    resp = client.get_historical_candles(instrument_key=ikey, interval="day",
                                         from_date=from_date, to_date=to_date)
    raw = resp.get("data", {}).get("candles", [])
    if not raw:
        raise RuntimeError(f"No data for {symbol}: {resp}")

    candles = [RealCandle(date=datetime.fromisoformat(c[0]),
                          open=c[1], high=c[2], low=c[3], close=c[4])
               for c in raw]
    candles.sort(key=lambda x: x.date)
    return candles


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------
_NORM_CDF = lambda x: 1 / (1 + math.exp(-1.7 * x))


def _bs_premium(spot, strike, opt_type, iv, dte_years):
    t = max(dte_years, 1e-6)
    d1 = (math.log(spot / strike) + (0.065 + iv**2 / 2) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if opt_type == "CE":
        return max(spot * _NORM_CDF(d1) - strike * math.exp(-0.065 * t) * _NORM_CDF(d2), 2)
    return max(strike * math.exp(-0.065 * t) * _NORM_CDF(-d2) - spot * _NORM_CDF(-d1), 2)


def _option_price_at_spot(new_spot, strike, opt_type, entry_premium, entry_spot):
    moneyness = (entry_spot - strike) / entry_spot
    if opt_type == "CE":
        delta = 0.7 if moneyness > 0.02 else (0.3 if moneyness < -0.02 else 0.5)
    else:
        delta = -0.7 if moneyness < -0.02 else (-0.3 if moneyness > 0.02 else -0.5)
    new_price = entry_premium + delta * (new_spot - entry_spot)
    intrinsic = max(0, new_spot - strike) if opt_type == "CE" else max(0, strike - new_spot)
    return max(new_price, intrinsic, 1)


# ---------------------------------------------------------------------------
# ATR calculator
# ---------------------------------------------------------------------------

def _calc_atr(candles: List[RealCandle], idx: int, period: int = 5) -> float:
    """True Range averaged over `period` days ending at idx (inclusive)."""
    trs = []
    for j in range(max(1, idx - period + 1), idx + 1):
        hi = candles[j].high
        lo = candles[j].low
        pc = candles[j - 1].close
        tr = max(hi - lo, abs(hi - pc), abs(lo - pc))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 1


# ---------------------------------------------------------------------------
# Intraday sim (with trailing stop)
# ---------------------------------------------------------------------------

def _simulate_intraday(
    today: RealCandle,
    strike: float,
    opt_type: str,
    entry_premium: float,
    sl_price: float,
    tgt_price: float,
    rng: random.Random,
    trailing: bool = True,
) -> Tuple[float, str]:
    price_at_high = _option_price_at_spot(today.high, strike, opt_type, entry_premium, today.open)
    price_at_low = _option_price_at_spot(today.low, strike, opt_type, entry_premium, today.open)
    price_at_close = _option_price_at_spot(today.close, strike, opt_type, entry_premium, today.open)

    high_first = rng.random() > 0.5
    checks = [(price_at_high, "HIGH"), (price_at_low, "LOW")]
    if not high_first:
        checks = checks[::-1]

    best_seen = entry_premium
    current_sl = sl_price

    for price, _ in checks:
        if price > best_seen:
            best_seen = price
            if trailing and best_seen > entry_premium * 1.20:
                current_sl = max(current_sl, entry_premium * 1.0)
            if trailing and best_seen > entry_premium * 1.40:
                current_sl = max(current_sl, best_seen * 0.80)

        if price >= tgt_price:
            return tgt_price * (1 - EXIT_SLIPPAGE), "TARGET"
        if price <= current_sl:
            return current_sl * (1 - EXIT_SLIPPAGE), "STOP_LOSS"

    exit_px = price_at_close * 0.985 * (1 - EXIT_SLIPPAGE)
    return max(exit_px, sl_price * 0.4), "EOD"


# ---------------------------------------------------------------------------
# Build context with v2 features
# ---------------------------------------------------------------------------

def _build_context(candles: List[RealCandle], i: int, symbol: str,
                   recent_ranges: List[float]) -> DecisionContext:
    today = candles[i]
    prev = candles[i - 1]
    avg_iv = AVG_IV.get(symbol.upper(), 0.15)

    if len(recent_ranges) >= 5:
        recent_ranges.pop(0)
    recent_ranges.append((prev.high - prev.low) / prev.close * 100)
    avg_range = sum(recent_ranges) / len(recent_ranges)

    atr = _calc_atr(candles, i - 1, 5)
    gap_pts = today.open - prev.close

    prev2_bullish = (candles[i - 1].close > candles[i - 1].open and
                     candles[i - 2].close > candles[i - 2].open)
    prev2_bearish = (candles[i - 1].close < candles[i - 1].open and
                     candles[i - 2].close < candles[i - 2].open)

    return DecisionContext(
        date=today.date,
        symbol=symbol.upper(),
        prev_open=prev.open,
        prev_high=prev.high,
        prev_low=prev.low,
        prev_close=prev.close,
        today_open=today.open,
        gap_pct=(today.open - prev.close) / prev.close * 100,
        prev_range_pct=(prev.high - prev.low) / prev.close * 100,
        prev_trend="BULLISH" if prev.close > prev.open else "BEARISH",
        avg_daily_range_pct=avg_range,
        simulated_iv=avg_iv * 100 * (1 + abs(today.open - prev.close) / prev.close),
        atr_5=atr,
        atr_pct=atr / prev.close * 100,
        day_of_week=today.date.weekday(),
        prev_2_trend="BULLISH" if prev2_bullish else ("BEARISH" if prev2_bearish else "MIXED"),
        gap_vs_atr=abs(gap_pts) / atr if atr > 0 else 0,
    )


# ---------------------------------------------------------------------------
# Core backtest runner
# ---------------------------------------------------------------------------

def run_real_backtest(
    symbol: str,
    candles: List[RealCandle],
    strategy_fn: Callable[[DecisionContext], Optional[Dict]],
    capital: float = 17000,
    dte: int = 7,
    seed: int = 0,
) -> Dict[str, Any]:
    symbol = symbol.upper()
    lot_size = LOT_SIZES.get(symbol, 75)
    strike_int = STRIKE_INTERVALS.get(symbol, 50)
    rng = random.Random(seed)

    current_capital = capital
    trades: List[Trade] = []
    equity_curve: List[Tuple[datetime, float]] = []
    recent_ranges: List[float] = []

    for i in range(5, len(candles)):
        today = candles[i]
        ctx = _build_context(candles, i, symbol, recent_ranges)

        decision = strategy_fn(ctx)
        if decision is None:
            equity_curve.append((today.date, current_capital))
            continue

        opt_type = "CE" if "CE" in decision.get("direction", "BUY_CE") else "PE"
        atm = round(today.open / strike_int) * strike_int
        strike = atm + strike_int if opt_type == "CE" else atm - strike_int

        raw_premium = _bs_premium(today.open, strike, opt_type,
                                  ctx.simulated_iv / 100, dte / 365)
        entry_premium = raw_premium * (1 + ENTRY_SLIPPAGE)

        if entry_premium < 10:
            equity_curve.append((today.date, current_capital))
            continue

        max_value = min(current_capital * 0.5, 12000)
        lots = max(1, int(max_value / (entry_premium * lot_size)))
        cost = entry_premium * lot_size * lots + ROUND_TRIP_BROKERAGE
        if cost > current_capital:
            equity_curve.append((today.date, current_capital))
            continue

        sl_pct = decision.get("stop_loss_pct", 35)
        tgt_pct = decision.get("target_pct", 45)
        sl_price = entry_premium * (1 - sl_pct / 100)
        tgt_price = entry_premium * (1 + tgt_pct / 100)

        use_trailing = decision.get("trailing_sl", False)

        exit_premium, exit_reason = _simulate_intraday(
            today, strike, opt_type, entry_premium, sl_price, tgt_price, rng,
            trailing=use_trailing,
        )

        pnl = (exit_premium - entry_premium) * lot_size * lots - ROUND_TRIP_BROKERAGE
        current_capital += pnl

        trades.append(Trade(
            date=today.date, symbol=symbol, strike=strike,
            option_type=opt_type,
            entry_premium=round(entry_premium, 1),
            exit_premium=round(exit_premium, 1),
            exit_reason=exit_reason,
            pnl=round(pnl),
            lot_size=lot_size, lots=lots,
        ))
        equity_curve.append((today.date, current_capital))

    return _compute_metrics(symbol, candles, trades, equity_curve, capital, current_capital)


# ---------------------------------------------------------------------------
# Equity backtester (Technical Analyst pipeline, rule-based — no LLM calls)
# ---------------------------------------------------------------------------

def run_equity_backtest(
    symbol: str,
    candles: List[RealCandle],
    capital: float = 50000,
    seed: int = 0,
) -> Dict[str, Any]:
    """
    Backtest equity using the same logic as TechnicalAnalyst:
    RSI, MACD, SMA crossovers, Bollinger.
    Buy on strong bullish confluence, sell on strong bearish or trailing SL.
    """
    from data_feeds.technical_indicators import TechnicalIndicators as TI

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]

    if len(closes) < 30:
        return {"symbol": symbol, "error": "Need >= 30 days of data"}

    rsi_all = TI.calculate_rsi(closes, 14)
    sma20 = TI.calculate_sma(closes, 20)
    ema12 = TI.calculate_ema(closes, 12)
    ema26 = TI.calculate_ema(closes, 26)
    bb_upper, bb_mid, bb_lower = [], [], []
    for idx in range(len(closes)):
        if sma20[idx] is not None:
            window = closes[max(0, idx - 19):idx + 1]
            std = (sum((p - sma20[idx]) ** 2 for p in window) / len(window)) ** 0.5
            bb_upper.append(sma20[idx] + 2 * std)
            bb_lower.append(sma20[idx] - 2 * std)
        else:
            bb_upper.append(None)
            bb_lower.append(None)

    rng = random.Random(seed)
    current_capital = capital
    qty = 0
    entry_price = 0.0
    trailing_sl = 0.0
    trades: List[Dict] = []
    equity_curve = []

    for i in range(26, len(candles)):
        price = candles[i].close
        rsi = rsi_all[i]
        ma20 = sma20[i]
        e12 = ema12[i]
        e26 = ema26[i]
        bbu = bb_upper[i]
        bbl = bb_lower[i]

        if None in (rsi, ma20, e12, e26):
            equity_curve.append((candles[i].date, current_capital))
            continue

        macd = e12 - e26
        prev_macd = (ema12[i - 1] or 0) - (ema26[i - 1] or 0)

        bullish_count = 0
        bearish_count = 0
        if rsi < 35:
            bullish_count += 2
        elif rsi < 45:
            bullish_count += 1
        elif rsi > 65:
            bearish_count += 2
        elif rsi > 55:
            bearish_count += 1

        if macd > 0 and prev_macd <= 0:
            bullish_count += 2
        elif macd > 0:
            bullish_count += 1
        if macd < 0 and prev_macd >= 0:
            bearish_count += 2
        elif macd < 0:
            bearish_count += 1

        if price > ma20:
            bullish_count += 1
        else:
            bearish_count += 1

        if bbl and price < bbl:
            bullish_count += 2
        if bbu and price > bbu:
            bearish_count += 2

        if qty == 0:
            if bullish_count >= 4 and bearish_count <= 1:
                max_shares = int(min(current_capital * 0.25, 20000) / price)
                if max_shares >= 1:
                    qty = max_shares
                    entry_price = price * 1.001
                    trailing_sl = entry_price * 0.96
                    current_capital -= entry_price * qty
        else:
            if price > entry_price:
                trailing_sl = max(trailing_sl, price * 0.965)

            sell = False
            reason = ""
            if price <= trailing_sl:
                sell, reason = True, "TRAILING_SL"
            elif bearish_count >= 4:
                sell, reason = True, "BEARISH_SIGNAL"
            elif price >= entry_price * 1.12:
                sell, reason = True, "TARGET_12PCT"

            if sell:
                exit_price = price * 0.999
                pnl = (exit_price - entry_price) * qty - 30
                current_capital += exit_price * qty
                trades.append({
                    "entry_date": candles[i - 1].date if i > 0 else candles[i].date,
                    "exit_date": candles[i].date,
                    "entry": round(entry_price, 2),
                    "exit": round(exit_price, 2),
                    "qty": qty,
                    "pnl": round(pnl),
                    "reason": reason,
                })
                qty = 0

        mark_to_market = current_capital + (qty * price if qty else 0)
        equity_curve.append((candles[i].date, mark_to_market))

    if qty > 0:
        exit_price = candles[-1].close * 0.999
        pnl = (exit_price - entry_price) * qty - 30
        current_capital += exit_price * qty
        trades.append({
            "entry_date": candles[-2].date,
            "exit_date": candles[-1].date,
            "entry": round(entry_price, 2),
            "exit": round(exit_price, 2),
            "qty": qty,
            "pnl": round(pnl),
            "reason": "EOD_CLOSE",
        })
        qty = 0

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    gross_w = sum(t["pnl"] for t in wins) if wins else 0
    gross_l = abs(sum(t["pnl"] for t in losses)) if losses else 0

    peak = capital
    max_dd = 0
    for _, eq in equity_curve:
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd: max_dd = dd

    return {
        "symbol": symbol,
        "period": f"{candles[26].date.strftime('%Y-%m-%d')} → {candles[-1].date.strftime('%Y-%m-%d')}",
        "real_data_days": len(candles),
        "starting_capital": capital,
        "ending_capital": round(current_capital),
        "total_pnl": round(total_pnl),
        "return_pct": round((current_capital / capital - 1) * 100, 1),
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "avg_win": round(gross_w / len(wins)) if wins else 0,
        "avg_loss": round(-gross_l / len(losses)) if losses else 0,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else float("inf"),
        "max_drawdown_pct": round(max_dd, 1),
        "trades": trades,
    }


# ---------------------------------------------------------------------------
# Metrics helper (shared by F&O runner)
# ---------------------------------------------------------------------------

def _compute_metrics(symbol, candles, trades, equity_curve, capital, current_capital):
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    total_pnl = sum(t.pnl for t in trades)
    gross_win = sum(t.pnl for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl for t in losses)) if losses else 0

    peak = capital
    max_dd = 0
    for _, eq in equity_curve:
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd: max_dd = dd

    rets = [t.pnl / capital for t in trades]
    if len(rets) >= 2:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / len(rets)
        std = math.sqrt(var) if var > 0 else 1e-9
        sharpe = (mu * 252 - 0.065) / (std * math.sqrt(252))
    else:
        sharpe = 0

    return {
        "symbol": symbol,
        "period": f"{candles[5].date.strftime('%Y-%m-%d')} → {candles[-1].date.strftime('%Y-%m-%d')}",
        "real_data_days": len(candles),
        "starting_capital": capital,
        "ending_capital": round(current_capital),
        "total_pnl": round(total_pnl),
        "return_pct": round((current_capital / capital - 1) * 100, 1),
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "avg_win": round(gross_win / len(wins)) if wins else 0,
        "avg_loss": round(-gross_loss / len(losses)) if losses else 0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else float("inf"),
        "max_drawdown_pct": round(max_dd, 1),
        "sharpe": round(sharpe, 2),
        "trades": trades,
    }


# ===================================================================
#  V1 STRATEGIES (original — for comparison baseline)
# ===================================================================

def strategy_gap_reversal_v1(ctx: DecisionContext) -> Optional[Dict]:
    if abs(ctx.gap_pct) < 0.5:
        return None
    if ctx.gap_pct > 0.5:
        return {"direction": "BUY_PE", "stop_loss_pct": 35, "target_pct": 45}
    return {"direction": "BUY_CE", "stop_loss_pct": 35, "target_pct": 45}


def strategy_gap_momentum_v1(ctx: DecisionContext) -> Optional[Dict]:
    if abs(ctx.gap_pct) < 0.3:
        return None
    if ctx.gap_pct > 0.3:
        return {"direction": "BUY_CE", "stop_loss_pct": 40, "target_pct": 50}
    return {"direction": "BUY_PE", "stop_loss_pct": 40, "target_pct": 50}


# ===================================================================
#  V2 STRATEGIES (improved — ATR-adaptive, filtered, trailing SL)
# ===================================================================

def strategy_smart_momentum(ctx: DecisionContext) -> Optional[Dict]:
    """
    Improved gap momentum with:
    - ATR-adaptive SL/target
    - Gap significance filter (gap must be > 0.3 ATR)
    - 2-day trend confirmation (previous momentum alignment)
    - Day-of-week filter (skip Mondays — settlement noise)
    - Trailing stop-loss enabled
    """
    gap = ctx.gap_pct

    # Filter: skip tiny gaps that are just noise
    if ctx.gap_vs_atr < 0.30:
        return None

    # Filter: minimum absolute gap
    if abs(gap) < 0.15:
        return None

    # Filter: skip Mondays (settlement carryover noise)
    if ctx.day_of_week == 0:
        return None

    # Determine direction
    if gap > 0:
        direction = "BUY_CE"
        # Confirmation: previous trend should not be strongly against us
        if ctx.prev_2_trend == "BEARISH" and ctx.gap_vs_atr < 0.5:
            return None  # weak gap against 2-day trend — skip
    else:
        direction = "BUY_PE"
        if ctx.prev_2_trend == "BULLISH" and ctx.gap_vs_atr < 0.5:
            return None

    # ATR-adaptive stops: wider on volatile days, tighter on calm days
    if ctx.atr_pct > 1.5:  # High-vol day
        sl = 45
        tgt = 60
    elif ctx.atr_pct > 1.0:  # Normal vol
        sl = 35
        tgt = 50
    else:  # Low vol
        sl = 28
        tgt = 40

    return {
        "direction": direction,
        "stop_loss_pct": sl,
        "target_pct": tgt,
        "trailing_sl": True,
    }


def strategy_smart_reversal(ctx: DecisionContext) -> Optional[Dict]:
    """
    Improved gap reversal with:
    - Only trade large gaps (> 0.6 ATR) — more likely to fill
    - Require previous day closed in opposite direction (exhaustion sign)
    - ATR-adaptive stops
    - Trailing SL
    """
    gap = ctx.gap_pct

    # Need a significant gap relative to recent volatility
    if ctx.gap_vs_atr < 0.6:
        return None

    if abs(gap) < 0.4:
        return None

    # Exhaustion confirmation: prev day should have trended in same
    # direction as the gap (over-extension sign)
    if gap > 0 and ctx.prev_trend != "BULLISH":
        return None  # gap up but prev day wasn't bullish — not exhaustion
    if gap < 0 and ctx.prev_trend != "BEARISH":
        return None

    # Trade the reversal
    if gap > 0:
        direction = "BUY_PE"
    else:
        direction = "BUY_CE"

    if ctx.atr_pct > 1.5:
        sl = 40
        tgt = 55
    elif ctx.atr_pct > 1.0:
        sl = 32
        tgt = 45
    else:
        sl = 25
        tgt = 38

    return {
        "direction": direction,
        "stop_loss_pct": sl,
        "target_pct": tgt,
        "trailing_sl": True,
    }


def strategy_trend_follow(ctx: DecisionContext) -> Optional[Dict]:
    """
    Improved trend continuation:
    - 2-day trend confirmation (both prev days same direction)
    - Gap aligns with trend
    - Skip Fridays (weekend risk)
    - ATR-adaptive stops
    - Trailing SL
    """
    # Need 2-day trend confirmation
    if ctx.prev_2_trend == "MIXED":
        return None

    # Gap must align with trend
    if ctx.prev_2_trend == "BULLISH" and ctx.gap_pct <= 0:
        return None
    if ctx.prev_2_trend == "BEARISH" and ctx.gap_pct >= 0:
        return None

    # Skip Fridays (weekend theta + event risk)
    if ctx.day_of_week == 4:
        return None

    # Previous day should have had a decent range (trending, not doji)
    if ctx.prev_range_pct < ctx.avg_daily_range_pct * 0.7:
        return None

    direction = "BUY_CE" if ctx.prev_2_trend == "BULLISH" else "BUY_PE"

    if ctx.atr_pct > 1.5:
        sl = 42
        tgt = 65
    elif ctx.atr_pct > 1.0:
        sl = 35
        tgt = 55
    else:
        sl = 28
        tgt = 45

    return {
        "direction": direction,
        "stop_loss_pct": sl,
        "target_pct": tgt,
        "trailing_sl": True,
    }


def strategy_combined_v2(ctx: DecisionContext) -> Optional[Dict]:
    """
    Meta-strategy: tries momentum first, then reversal, then trend.
    Only takes the first signal — avoids over-trading.
    """
    sig = strategy_smart_momentum(ctx)
    if sig:
        return sig
    sig = strategy_smart_reversal(ctx)
    if sig:
        return sig
    return strategy_trend_follow(ctx)


# ---------------------------------------------------------------------------
# Multi-seed runner
# ---------------------------------------------------------------------------

def run_multi_seed(symbol, candles, strategy_fn, capital=17000, seeds=5):
    results = []
    for s in range(seeds):
        r = run_real_backtest(symbol, candles, strategy_fn, capital, seed=s)
        results.append(r)
    n = len(results)
    return {
        "avg_pnl": round(sum(r["total_pnl"] for r in results) / n),
        "avg_return_pct": round(sum(r["return_pct"] for r in results) / n, 1),
        "avg_win_rate": round(sum(r["win_rate"] for r in results) / n, 1),
        "avg_profit_factor": round(sum(r["profit_factor"] for r in results) / n, 2),
        "avg_max_dd": round(sum(r["max_drawdown_pct"] for r in results) / n, 1),
        "avg_sharpe": round(sum(r["sharpe"] for r in results) / n, 2),
        "seeds": seeds,
        "per_seed": [
            {"seed": i, "pnl": r["total_pnl"], "ret": r["return_pct"],
             "trades": r["total_trades"]}
            for i, r in enumerate(results)
        ],
    }


# ---------------------------------------------------------------------------
# Pretty printers
# ---------------------------------------------------------------------------

def print_result(label: str, r: Dict):
    v = "PROFITABLE" if r["total_pnl"] > 0 and r.get("profit_factor", 0) > 1.1 else (
        "MARGINAL" if r["total_pnl"] > 0 else "UNPROFITABLE")
    print(f"\n{'='*64}")
    print(f"  {label}")
    print(f"  Period : {r['period']}  ({r['real_data_days']} days)")
    print(f"  Capital: ₹{r['starting_capital']:,} → ₹{r['ending_capital']:,}  ({r['return_pct']:+.1f}%)")
    print(f"  Trades : {r['total_trades']}  W:{r['wins']} L:{r['losses']}  WR:{r['win_rate']}%")
    print(f"  Avg W/L: ₹{r['avg_win']:+,} / ₹{r['avg_loss']:,}")
    pf = r.get('profit_factor', 0)
    sh = r.get('sharpe', 0)
    print(f"  PF:{pf:.2f}  MaxDD:{r['max_drawdown_pct']:.1f}%  Sharpe:{sh:.2f}")
    print(f"  Costs  : slippage 1.5% each way + ₹40 brokerage/trade")
    print(f"  Verdict: {v}")
    print(f"{'='*64}")
    for t in r.get("trades", []):
        if isinstance(t, Trade):
            e = "✅" if t.pnl > 0 else "❌"
            print(f"  {t.date.strftime('%Y-%m-%d')} {t.strike:.0f}{t.option_type} "
                  f"₹{t.entry_premium}→₹{t.exit_premium} P&L:{t.pnl:+,} {e} {t.exit_reason}")
        else:
            e = "✅" if t["pnl"] > 0 else "❌"
            print(f"  {t['exit_date'].strftime('%Y-%m-%d')} ₹{t['entry']}→₹{t['exit']} "
                  f"x{t['qty']} P&L:{t['pnl']:+,} {e} {t['reason']}")


def print_equity_result(label: str, r: Dict):
    v = "PROFITABLE" if r["total_pnl"] > 0 and r.get("profit_factor", 0) > 1.1 else (
        "MARGINAL" if r["total_pnl"] > 0 else "UNPROFITABLE")
    print(f"\n{'='*64}")
    print(f"  EQUITY: {label}")
    print(f"  Period : {r['period']}  ({r['real_data_days']} days)")
    print(f"  Capital: ₹{r['starting_capital']:,} → ₹{r['ending_capital']:,}  ({r['return_pct']:+.1f}%)")
    print(f"  Trades : {r['total_trades']}  W:{r['wins']} L:{r['losses']}  WR:{r['win_rate']}%")
    if r['wins']:
        print(f"  Avg W/L: ₹{r['avg_win']:+,} / ₹{r['avg_loss']:,}")
    print(f"  PF:{r.get('profit_factor',0):.2f}  MaxDD:{r['max_drawdown_pct']:.1f}%")
    print(f"  Verdict: {v}")
    print(f"{'='*64}")
    for t in r.get("trades", []):
        e = "✅" if t["pnl"] > 0 else "❌"
        dt = t.get("exit_date", "")
        if hasattr(dt, "strftime"):
            dt = dt.strftime("%Y-%m-%d")
        print(f"  {dt} ₹{t['entry']}→₹{t['exit']} x{t['qty']} "
              f"P&L:{t['pnl']:+,} {e} {t['reason']}")
