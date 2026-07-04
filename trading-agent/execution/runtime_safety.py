"""
Runtime safety: explicit trading modes, kill switch, fail-closed preflight.

Environment:
  TRADING_MODE          paper | shadow | micro_live | live (default: paper)
  TRADING_ENABLED       true | false (default: true; set false to freeze)
  TRADING_KILL_SWITCH   if set to 1/true, blocks all broker orders
  TRADING_NON_INTERACTIVE  if 1, live modes refuse browser OAuth (fail-closed)
  MICRO_LIVE_MAX_ORDER_VALUE  rupee cap per order in micro_live (default 5000)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import pytz

IST = pytz.timezone("Asia/Kolkata")


class TradingMode(str, Enum):
    PAPER = "paper"
    SHADOW = "shadow"
    MICRO_LIVE = "micro_live"
    LIVE = "live"


def _data_cache_dir() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "data_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _safety_state_path() -> str:
    return os.path.join(_data_cache_dir(), "runtime_safety.json")


def _freeze_state_path() -> str:
    return os.path.join(_data_cache_dir(), "trading_freeze.json")


def _parse_bool(v: Optional[str], default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def kill_switch_active() -> bool:
    return _parse_bool(os.getenv("TRADING_KILL_SWITCH"), False)


def trading_enabled_flag() -> bool:
    return _parse_bool(os.getenv("TRADING_ENABLED"), True)


def non_interactive() -> bool:
    return _parse_bool(os.getenv("TRADING_NON_INTERACTIVE"), False)


def load_trading_mode() -> TradingMode:
    raw = (os.getenv("TRADING_MODE") or "paper").strip().lower()
    try:
        return TradingMode(raw)
    except ValueError:
        return TradingMode.PAPER


def micro_live_max_order_value() -> float:
    try:
        return float(os.getenv("MICRO_LIVE_MAX_ORDER_VALUE", "5000"))
    except ValueError:
        return 5000.0


@dataclass
class RuntimeSafetyState:
    mode: str
    trading_enabled: bool
    kill_switch: bool
    non_interactive: bool
    token_valid: bool
    reconciliation_ok: bool
    risk_ok: bool
    risk_lock_reason: str
    broker_orders_allowed: bool
    reasons_blocked: List[str]
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def read_trading_freeze() -> Optional[Dict[str, Any]]:
    path = _freeze_state_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def set_trading_freeze(reason: str, source: str = "system") -> None:
    payload = {
        "frozen": True,
        "reason": reason,
        "source": source,
        "at": datetime.now(IST).isoformat(),
    }
    with open(_freeze_state_path(), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def clear_trading_freeze() -> None:
    path = _freeze_state_path()
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def is_frozen() -> bool:
    data = read_trading_freeze()
    return bool(data and data.get("frozen"))


def persist_safety_state(state: RuntimeSafetyState) -> None:
    with open(_safety_state_path(), "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, indent=2)


def evaluate_runtime_safety(
    *,
    token_valid: bool,
    reconciliation_ok: bool,
    risk_ok: bool,
    risk_lock_reason: str = "",
) -> Tuple[RuntimeSafetyState, bool]:
    """
    Returns (state, broker_orders_allowed).
    """
    mode = load_trading_mode()
    reasons: List[str] = []

    if not trading_enabled_flag():
        reasons.append("TRADING_ENABLED is false")
    if kill_switch_active():
        reasons.append("TRADING_KILL_SWITCH is active")
    if is_frozen():
        fr = read_trading_freeze() or {}
        reasons.append(f"Trading freeze: {fr.get('reason', 'unknown')}")
    if mode in (TradingMode.MICRO_LIVE, TradingMode.LIVE) and not token_valid:
        reasons.append("Invalid or missing Upstox token")
    if mode in (TradingMode.MICRO_LIVE, TradingMode.LIVE) and non_interactive():
        # Non-interactive still allowed if token already valid
        if not token_valid:
            reasons.append("TRADING_NON_INTERACTIVE: cannot authenticate without valid token")
    if not reconciliation_ok:
        reasons.append("Broker reconciliation not OK")
    if not risk_ok:
        reasons.append(risk_lock_reason or "Risk lockout")

    broker_allowed = mode in (TradingMode.MICRO_LIVE, TradingMode.LIVE) and len(reasons) == 0

    state = RuntimeSafetyState(
        mode=mode.value,
        trading_enabled=trading_enabled_flag(),
        kill_switch=kill_switch_active(),
        non_interactive=non_interactive(),
        token_valid=token_valid,
        reconciliation_ok=reconciliation_ok,
        risk_ok=risk_ok,
        risk_lock_reason=risk_lock_reason or "",
        broker_orders_allowed=broker_allowed,
        reasons_blocked=reasons,
        updated_at=datetime.now(IST).isoformat(),
    )
    persist_safety_state(state)
    return state, broker_allowed


def preflight_for_live_modes() -> Dict[str, Any]:
    """Startup summary for logs / dashboard."""
    from database.operations import is_token_valid

    mode = load_trading_mode()
    tok = is_token_valid()
    frozen = is_frozen()
    return {
        "trading_mode": mode.value,
        "token_valid": tok,
        "trading_enabled": trading_enabled_flag(),
        "kill_switch": kill_switch_active(),
        "frozen": frozen,
        "freeze_detail": read_trading_freeze(),
        "non_interactive": non_interactive(),
    }


def emergency_flatten() -> Dict[str, Any]:
    """
    Close all open F&O positions at the broker (used with TRADING_KILL_SWITCH & manual recovery).
    """
    import importlib

    em = importlib.import_module("execution.exit_manager")
    return em.emergency_flatten_open_fo_positions()
