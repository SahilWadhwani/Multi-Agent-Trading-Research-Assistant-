"""
Structured JSON schemas for LLM outputs (Pro Trading Agent upgrade).

All prompts should ask models to return ONLY valid JSON matching these shapes.
parse_json_response() extracts JSON from raw text (direct JSON, markdown fences, or first object).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


# --- Schema documentation strings (embed in system prompts) ---

NEWS_ANALYSIS_JSON_SCHEMA = """{
  "bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "confidence": <number 0-100>,
  "key_factors": [<string>, ...],
  "catalyst": <true|false>,
  "summary": <string>,
  "trading_implication": <string>,
  "risks": [<string>, ...]
}"""

FO_ANALYSIS_JSON_SCHEMA = """{
  "bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "confidence": <number 0-100>,
  "regime_agreement": <true|false>,
  "iv_view": <string>,
  "oi_interpretation": <string>,
  "recommended_strategy": <string>,
  "summary": <string>
}"""

REGIME_JSON_SCHEMA = """{
  "regime": "trending_bullish" | "trending_bearish" | "range_bound" | "high_vol_breakout" | "low_vol_grind" | "expiry_day",
  "session_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "key_levels": {"support": <number|null>, "resistance": <number|null>},
  "confidence": <number 0-1>,
  "reasoning": <string>
}"""

DAY_PLAN_JSON_SCHEMA = """{
  "regime": "trending_bullish" | "trending_bearish" | "range_bound" | "high_vol_breakout" | "low_vol_grind" | "expiry_day",
  "session_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "key_levels": {"support": <number|null>, "resistance": <number|null>},
  "strategy_preference": <string>,
  "avoid_list": [<string>, ...],
  "notes": <string>
}"""

FINAL_DECISION_JSON_SCHEMA = """{
  "decision": "EXECUTE" | "NO_TRADE",
  "confidence": <number 0-100>,
  "reasoning": <string>
}"""


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if "```" in t:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return t


def parse_json_response(raw: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Parse JSON object from LLM output.

    Returns (dict, None) on success, or (None, error_message) on failure.
    """
    if not raw or not isinstance(raw, str):
        return None, "empty"
    if raw.strip().upper().startswith("ERROR:"):
        return None, raw.strip()[:200]

    text = _strip_code_fence(raw)
    text = text.strip()

    # Direct parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj, None
        return None, "not_a_dict"
    except json.JSONDecodeError:
        pass

    # First {...} span (greedy-safe: find outer braces)
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start : i + 1]
                    try:
                        obj = json.loads(chunk)
                        if isinstance(obj, dict):
                            return obj, None
                    except json.JSONDecodeError:
                        break
                    break

    return None, "json_parse_failed"


def normalize_news_from_json(d: Dict[str, Any], symbol: str, raw: str) -> Dict[str, Any]:
    """Map parsed JSON to analyze_news return shape."""
    bias = str(d.get("bias", "NEUTRAL")).upper()
    if bias not in ("BULLISH", "BEARISH", "NEUTRAL", "VERY_BULLISH", "VERY_BEARISH"):
        bias = "NEUTRAL"
    conf = d.get("confidence", 50)
    try:
        conf = int(float(conf))
    except (TypeError, ValueError):
        conf = 50
    conf = min(100, max(0, conf))
    factors = d.get("key_factors") or []
    if not isinstance(factors, list):
        factors = []
    risks = d.get("risks") or []
    if not isinstance(risks, list):
        risks = []
    return {
        "symbol": symbol,
        "sentiment": bias,
        "confidence": conf,
        "key_factors": [str(x) for x in factors[:8]],
        "trading_implication": str(d.get("trading_implication", d.get("summary", "")))[:500],
        "risks": [str(x) for x in risks[:5]],
        "raw_response": raw,
        "llm_used": True,
        "catalyst": bool(d.get("catalyst", False)),
    }


def normalize_fo_from_json(d: Dict[str, Any], raw: str, model: str = "structured_json") -> Dict[str, Any]:
    """Map parsed JSON to FO analyst llm_analysis shape."""
    bias = str(d.get("bias", "NEUTRAL")).upper()
    if bias not in ("BULLISH", "BEARISH", "NEUTRAL"):
        bias = "NEUTRAL"
    try:
        c = float(d.get("confidence", 50))
        conf01 = min(1.0, max(0.0, c / 100.0))
    except (TypeError, ValueError):
        conf01 = 0.5
    return {
        "bias": bias,
        "analysis": raw,
        "model": model,
        "confidence": conf01,
        "regime_agreement": bool(d.get("regime_agreement", True)),
        "iv_view": str(d.get("iv_view", "")),
        "oi_interpretation": str(d.get("oi_interpretation", "")),
        "recommended_strategy": str(d.get("recommended_strategy", "")),
        "summary": str(d.get("summary", "")),
    }
