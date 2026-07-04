"""
Order intent lifecycle (local) for reconciliation with Upstox.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")


def _db_path() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "data_cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "order_intents.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_order_intents_db() -> None:
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS order_intents (
            intent_id TEXT PRIMARY KEY,
            decision_id TEXT,
            symbol TEXT,
            instrument_key TEXT,
            transaction_type TEXT,
            quantity INTEGER,
            product TEXT,
            mode TEXT,
            status TEXT,
            broker_order_id TEXT,
            broker_response TEXT,
            error TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def log_intent(
    *,
    decision_id: Optional[str],
    symbol: str,
    instrument_key: str,
    transaction_type: str,
    quantity: int,
    product: str,
    mode: str,
    status: str = "SUBMITTED",
    broker_order_id: Optional[str] = None,
    broker_response: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> str:
    init_order_intents_db()
    intent_id = str(uuid.uuid4())
    now = datetime.now(IST).isoformat()
    conn = _conn()
    conn.execute(
        """
        INSERT INTO order_intents (
            intent_id, decision_id, symbol, instrument_key, transaction_type,
            quantity, product, mode, status, broker_order_id, broker_response, error,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intent_id,
            decision_id,
            symbol,
            instrument_key,
            transaction_type,
            quantity,
            product,
            mode,
            status,
            broker_order_id,
            json.dumps(broker_response) if broker_response is not None else None,
            error,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return intent_id


def update_intent_status(
    intent_id: str,
    status: str,
    broker_order_id: Optional[str] = None,
    broker_response: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    init_order_intents_db()
    now = datetime.now(IST).isoformat()
    conn = _conn()
    conn.execute(
        """
        UPDATE order_intents SET
            status = ?,
            broker_order_id = COALESCE(?, broker_order_id),
            broker_response = COALESCE(?, broker_response),
            error = COALESCE(?, error),
            updated_at = ?
        WHERE intent_id = ?
        """,
        (
            status,
            broker_order_id,
            json.dumps(broker_response) if broker_response is not None else None,
            error,
            now,
            intent_id,
        ),
    )
    conn.commit()
    conn.close()


def recent_intents(limit: int = 50) -> List[Dict[str, Any]]:
    init_order_intents_db()
    conn = _conn()
    cur = conn.execute(
        "SELECT * FROM order_intents ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def pending_intents() -> List[Dict[str, Any]]:
    init_order_intents_db()
    conn = _conn()
    cur = conn.execute(
        """
        SELECT * FROM order_intents
        WHERE status IN ('SUBMITTED', 'PENDING', 'PLACED')
        ORDER BY created_at DESC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows
