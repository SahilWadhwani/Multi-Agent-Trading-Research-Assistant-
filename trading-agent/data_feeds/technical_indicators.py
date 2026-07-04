"""
Technical Indicators Calculator for Indian Markets.
Calculates RSI, MACD, Moving Averages, Bollinger Bands, etc.
"""

import numpy as np
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta


class TechnicalIndicators:
    """
    Calculate technical indicators from OHLCV data.
    All methods are static and work with price lists.
    """
    
    @staticmethod
    def calculate_sma(prices: List[float], period: int) -> List[Optional[float]]:
        """Simple Moving Average."""
        result = [None] * len(prices)
        for i in range(period - 1, len(prices)):
            result[i] = sum(prices[i - period + 1:i + 1]) / period
        return result
    
    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> List[Optional[float]]:
        """Exponential Moving Average."""
        result = [None] * len(prices)
        if len(prices) < period:
            return result
        
        multiplier = 2 / (period + 1)
        # First EMA is SMA
        result[period - 1] = sum(prices[:period]) / period
        
        for i in range(period, len(prices)):
            result[i] = (prices[i] - result[i - 1]) * multiplier + result[i - 1]
        
        return result
    
    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> List[Optional[float]]:
        """
        Relative Strength Index.
        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss
        """
        result = [None] * len(prices)
        if len(prices) < period + 1:
            return result
        
        gains = []
        losses = []
        
        for i in range(1, len(prices)):
            change = prices[i] - prices[i - 1]
            gains.append(max(0, change))
            losses.append(max(0, -change))
        
        # First RSI calculation using SMA
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        if avg_loss == 0:
            result[period] = 100
        else:
            rs = avg_gain / avg_loss
            result[period] = 100 - (100 / (1 + rs))
        
        # Subsequent RSI using smoothed averages
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            
            if avg_loss == 0:
                result[i + 1] = 100
            else:
                rs = avg_gain / avg_loss
                result[i + 1] = 100 - (100 / (1 + rs))
        
        return result
    
    @staticmethod
    def calculate_macd(
        prices: List[float],
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9
    ) -> Dict[str, List[Optional[float]]]:
        """
        MACD (Moving Average Convergence Divergence).
        Returns MACD line, Signal line, and Histogram.
        """
        fast_ema = TechnicalIndicators.calculate_ema(prices, fast_period)
        slow_ema = TechnicalIndicators.calculate_ema(prices, slow_period)
        
        # MACD Line = Fast EMA - Slow EMA
        macd_line = [None] * len(prices)
        for i in range(len(prices)):
            if fast_ema[i] is not None and slow_ema[i] is not None:
                macd_line[i] = fast_ema[i] - slow_ema[i]
        
        # Signal Line = 9-period EMA of MACD Line
        valid_macd = [m for m in macd_line if m is not None]
        signal_ema = TechnicalIndicators.calculate_ema(valid_macd, signal_period)
        
        signal_line = [None] * len(prices)
        macd_idx = 0
        for i in range(len(prices)):
            if macd_line[i] is not None:
                if macd_idx < len(signal_ema):
                    signal_line[i] = signal_ema[macd_idx]
                macd_idx += 1
        
        # Histogram = MACD Line - Signal Line
        histogram = [None] * len(prices)
        for i in range(len(prices)):
            if macd_line[i] is not None and signal_line[i] is not None:
                histogram[i] = macd_line[i] - signal_line[i]
        
        return {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram,
        }
    
    @staticmethod
    def calculate_bollinger_bands(
        prices: List[float],
        period: int = 20,
        std_dev: float = 2.0
    ) -> Dict[str, List[Optional[float]]]:
        """
        Bollinger Bands.
        Middle = SMA
        Upper = SMA + (std_dev * standard deviation)
        Lower = SMA - (std_dev * standard deviation)
        """
        middle = TechnicalIndicators.calculate_sma(prices, period)
        upper = [None] * len(prices)
        lower = [None] * len(prices)
        
        for i in range(period - 1, len(prices)):
            window = prices[i - period + 1:i + 1]
            std = np.std(window)
            upper[i] = middle[i] + (std_dev * std)
            lower[i] = middle[i] - (std_dev * std)
        
        return {
            "middle": middle,
            "upper": upper,
            "lower": lower,
        }
    
    @staticmethod
    def calculate_atr(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14
    ) -> List[Optional[float]]:
        """
        Average True Range - measures volatility.
        """
        result = [None] * len(closes)
        if len(closes) < period + 1:
            return result
        
        true_ranges = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            true_ranges.append(tr)
        
        # First ATR is simple average
        result[period] = sum(true_ranges[:period]) / period
        
        # Subsequent ATR using smoothing
        for i in range(period, len(true_ranges)):
            result[i + 1] = (result[i] * (period - 1) + true_ranges[i]) / period
        
        return result
    
    @staticmethod
    def calculate_vwap(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float]
    ) -> List[Optional[float]]:
        """
        Volume Weighted Average Price.
        """
        result = [None] * len(closes)
        cumulative_tp_vol = 0
        cumulative_vol = 0
        
        for i in range(len(closes)):
            typical_price = (highs[i] + lows[i] + closes[i]) / 3
            cumulative_tp_vol += typical_price * volumes[i]
            cumulative_vol += volumes[i]
            
            if cumulative_vol > 0:
                result[i] = cumulative_tp_vol / cumulative_vol
        
        return result
    
    @staticmethod
    def detect_trend(prices: List[float], short_period: int = 10, long_period: int = 50) -> str:
        """
        Detect trend using moving average crossover.
        Returns: "BULLISH", "BEARISH", or "NEUTRAL"
        """
        if len(prices) < long_period:
            return "NEUTRAL"
        
        short_ma = TechnicalIndicators.calculate_sma(prices, short_period)
        long_ma = TechnicalIndicators.calculate_sma(prices, long_period)
        
        current_short = short_ma[-1]
        current_long = long_ma[-1]
        
        if current_short is None or current_long is None:
            return "NEUTRAL"
        
        # Check recent crossover
        prev_short = short_ma[-2] if len(short_ma) > 1 else current_short
        prev_long = long_ma[-2] if len(long_ma) > 1 else current_long
        
        if current_short > current_long and prev_short <= prev_long:
            return "BULLISH"  # Golden cross
        elif current_short < current_long and prev_short >= prev_long:
            return "BEARISH"  # Death cross
        elif current_short > current_long:
            return "BULLISH"
        elif current_short < current_long:
            return "BEARISH"
        else:
            return "NEUTRAL"
    
    @staticmethod
    def generate_signals(
        prices: List[float],
        highs: List[float],
        lows: List[float],
        volumes: List[float]
    ) -> Dict[str, Any]:
        """
        Generate comprehensive technical signals.
        Returns a dictionary with all indicators and signals.
        """
        closes = prices
        
        # Calculate all indicators
        rsi = TechnicalIndicators.calculate_rsi(closes)
        macd = TechnicalIndicators.calculate_macd(closes)
        bollinger = TechnicalIndicators.calculate_bollinger_bands(closes)
        atr = TechnicalIndicators.calculate_atr(highs, lows, closes)
        
        sma_20 = TechnicalIndicators.calculate_sma(closes, 20)
        sma_50 = TechnicalIndicators.calculate_sma(closes, 50)
        sma_200 = TechnicalIndicators.calculate_sma(closes, 200)
        ema_12 = TechnicalIndicators.calculate_ema(closes, 12)
        
        # Current values
        current_price = closes[-1] if closes else None
        current_rsi = rsi[-1] if rsi else None
        current_macd = macd["macd"][-1] if macd["macd"] else None
        current_macd_signal = macd["signal"][-1] if macd["signal"] else None
        current_macd_hist = macd["histogram"][-1] if macd["histogram"] else None
        
        # Generate signals
        signals = {
            "current_price": current_price,
            "indicators": {
                "rsi": current_rsi,
                "macd": current_macd,
                "macd_signal": current_macd_signal,
                "macd_histogram": current_macd_hist,
                "sma_20": sma_20[-1] if sma_20 else None,
                "sma_50": sma_50[-1] if sma_50 else None,
                "sma_200": sma_200[-1] if sma_200 else None,
                "bollinger_upper": bollinger["upper"][-1] if bollinger["upper"] else None,
                "bollinger_middle": bollinger["middle"][-1] if bollinger["middle"] else None,
                "bollinger_lower": bollinger["lower"][-1] if bollinger["lower"] else None,
                "atr": atr[-1] if atr else None,
            },
            "analysis": {},
        }
        
        # RSI Analysis
        if current_rsi is not None:
            if current_rsi > 70:
                signals["analysis"]["rsi"] = "OVERBOUGHT - Potential sell signal"
            elif current_rsi < 30:
                signals["analysis"]["rsi"] = "OVERSOLD - Potential buy signal"
            elif current_rsi > 50:
                signals["analysis"]["rsi"] = "BULLISH momentum"
            else:
                signals["analysis"]["rsi"] = "BEARISH momentum"
        
        # MACD Analysis
        if current_macd is not None and current_macd_signal is not None:
            if current_macd > current_macd_signal:
                signals["analysis"]["macd"] = "BULLISH crossover"
            else:
                signals["analysis"]["macd"] = "BEARISH crossover"
        
        # Bollinger Band Analysis
        if current_price and bollinger["upper"][-1] and bollinger["lower"][-1]:
            if current_price > bollinger["upper"][-1]:
                signals["analysis"]["bollinger"] = "ABOVE upper band - Overbought"
            elif current_price < bollinger["lower"][-1]:
                signals["analysis"]["bollinger"] = "BELOW lower band - Oversold"
            else:
                signals["analysis"]["bollinger"] = "Within bands - Normal range"
        
        # Trend Analysis
        signals["analysis"]["trend"] = TechnicalIndicators.detect_trend(closes)
        
        # Price vs Moving Averages
        if current_price and sma_50[-1]:
            if current_price > sma_50[-1]:
                signals["analysis"]["price_vs_sma50"] = "ABOVE 50 SMA - Bullish"
            else:
                signals["analysis"]["price_vs_sma50"] = "BELOW 50 SMA - Bearish"
        
        return signals
