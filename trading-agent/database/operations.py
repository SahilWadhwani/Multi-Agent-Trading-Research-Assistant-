"""
Database CRUD operations for the AI Trading Agent.
"""

import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from .schema import (
    get_session,
    Trade,
    AgentLog,
    PortfolioSnapshot,
    TokenStore,
)


# ============== TRADE OPERATIONS ==============

def log_trade(
    symbol: str,
    quantity: int,
    side: str,
    price: float,
    exchange: str = "NSE",
    order_type: str = "MARKET",
    product_type: str = "INTRADAY",
    order_id: Optional[str] = None,
    status: str = "EXECUTED",
    is_paper_trade: bool = True,
    pnl: Optional[float] = None,
    notes: Optional[str] = None,
) -> Trade:
    """Log a trade to the database."""
    session = get_session()
    try:
        trade = Trade(
            timestamp=datetime.utcnow(),
            symbol=symbol,
            exchange=exchange,
            quantity=quantity,
            side=side.upper(),
            price=price,
            order_type=order_type,
            product_type=product_type,
            order_id=order_id,
            status=status,
            is_paper_trade=is_paper_trade,
            pnl=pnl,
            notes=notes,
        )
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return trade
    finally:
        session.close()


def get_recent_trades(limit: int = 50) -> List[Trade]:
    """Get the most recent trades."""
    session = get_session()
    try:
        return session.query(Trade).order_by(desc(Trade.timestamp)).limit(limit).all()
    finally:
        session.close()


def get_todays_trades() -> List[Trade]:
    """Get all trades from today (IST timezone aware)."""
    session = get_session()
    try:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            session.query(Trade)
            .filter(Trade.timestamp >= today_start)
            .order_by(desc(Trade.timestamp))
            .all()
        )
    finally:
        session.close()


def get_trades_by_symbol(symbol: str, limit: int = 20) -> List[Trade]:
    """Get trades for a specific symbol."""
    session = get_session()
    try:
        return (
            session.query(Trade)
            .filter(Trade.symbol == symbol)
            .order_by(desc(Trade.timestamp))
            .limit(limit)
            .all()
        )
    finally:
        session.close()


def calculate_todays_pnl() -> float:
    """Calculate today's realized P&L."""
    session = get_session()
    try:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = (
            session.query(func.sum(Trade.pnl))
            .filter(Trade.timestamp >= today_start)
            .filter(Trade.pnl.isnot(None))
            .scalar()
        )
        return result or 0.0
    finally:
        session.close()


def get_win_rate() -> Dict[str, Any]:
    """Calculate win rate from all trades."""
    session = get_session()
    try:
        trades_with_pnl = session.query(Trade).filter(Trade.pnl.isnot(None)).all()
        if not trades_with_pnl:
            return {"win_rate": 0.0, "total_trades": 0, "winning_trades": 0}
        
        winning = sum(1 for t in trades_with_pnl if t.pnl > 0)
        total = len(trades_with_pnl)
        
        return {
            "win_rate": (winning / total) * 100 if total > 0 else 0.0,
            "total_trades": total,
            "winning_trades": winning,
        }
    finally:
        session.close()


# ============== AGENT LOG OPERATIONS ==============

def log_agent_reasoning(
    ai_reasoning: str,
    strategy_used: Optional[str] = None,
    action_taken: Optional[str] = None,
    symbols_analyzed: Optional[str] = None,
    confidence_level: Optional[float] = None,
    market_conditions: Optional[str] = None,
) -> AgentLog:
    """Log the agent's reasoning and decisions."""
    session = get_session()
    try:
        log = AgentLog(
            timestamp=datetime.utcnow(),
            ai_reasoning=ai_reasoning,
            strategy_used=strategy_used,
            action_taken=action_taken,
            symbols_analyzed=symbols_analyzed,
            confidence_level=confidence_level,
            market_conditions=market_conditions,
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        return log
    finally:
        session.close()


def get_recent_logs(limit: int = 30) -> List[AgentLog]:
    """Get the most recent agent logs."""
    session = get_session()
    try:
        return (
            session.query(AgentLog)
            .order_by(desc(AgentLog.timestamp))
            .limit(limit)
            .all()
        )
    finally:
        session.close()


# ============== PORTFOLIO SNAPSHOT OPERATIONS ==============

def log_portfolio_snapshot(
    total_value: float,
    available_margin: float,
    used_margin: float = 0.0,
    realized_pnl: float = 0.0,
    unrealized_pnl: float = 0.0,
    num_open_positions: int = 0,
    positions_summary: Optional[Dict] = None,
) -> PortfolioSnapshot:
    """Log a portfolio snapshot."""
    session = get_session()
    try:
        snapshot = PortfolioSnapshot(
            timestamp=datetime.utcnow(),
            total_value=total_value,
            available_margin=available_margin,
            used_margin=used_margin,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            num_open_positions=num_open_positions,
            positions_summary=json.dumps(positions_summary) if positions_summary else None,
        )
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return snapshot
    finally:
        session.close()


def get_latest_portfolio() -> Optional[PortfolioSnapshot]:
    """Get the most recent portfolio snapshot."""
    session = get_session()
    try:
        return (
            session.query(PortfolioSnapshot)
            .order_by(desc(PortfolioSnapshot.timestamp))
            .first()
        )
    finally:
        session.close()


def get_portfolio_history(days: int = 7) -> List[PortfolioSnapshot]:
    """Get portfolio snapshots for the last N days."""
    session = get_session()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days)
        return (
            session.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.timestamp >= cutoff)
            .order_by(PortfolioSnapshot.timestamp)
            .all()
        )
    finally:
        session.close()


# ============== HOLDINGS (Derived from trades) ==============

def get_all_holdings() -> Dict[str, Dict[str, Any]]:
    """
    Calculate current holdings from trade history.
    Returns a dict of symbol -> {quantity, avg_price, side}
    """
    session = get_session()
    try:
        trades = (
            session.query(Trade)
            .filter(Trade.status == "EXECUTED")
            .order_by(Trade.timestamp)
            .all()
        )
        
        holdings = {}
        for trade in trades:
            symbol = trade.symbol
            if symbol not in holdings:
                holdings[symbol] = {"quantity": 0, "total_cost": 0.0}
            
            if trade.side == "BUY":
                holdings[symbol]["quantity"] += trade.quantity
                holdings[symbol]["total_cost"] += trade.quantity * trade.price
            else:  # SELL
                holdings[symbol]["quantity"] -= trade.quantity
                holdings[symbol]["total_cost"] -= trade.quantity * trade.price
        
        # Calculate average price and filter out zero holdings
        result = {}
        for symbol, data in holdings.items():
            if data["quantity"] != 0:
                avg_price = abs(data["total_cost"] / data["quantity"]) if data["quantity"] != 0 else 0
                result[symbol] = {
                    "quantity": abs(data["quantity"]),
                    "avg_price": round(avg_price, 2),
                    "side": "LONG" if data["quantity"] > 0 else "SHORT",
                }
        
        return result
    finally:
        session.close()


# ============== TOKEN OPERATIONS ==============

def save_token(
    access_token: str,
    refresh_token: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> TokenStore:
    """Save or update the OAuth token."""
    session = get_session()
    try:
        # Delete old tokens
        session.query(TokenStore).delete()
        
        token = TokenStore(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            created_at=datetime.utcnow(),
        )
        session.add(token)
        session.commit()
        session.refresh(token)
        return token
    finally:
        session.close()


def get_stored_token() -> Optional[TokenStore]:
    """Get the stored OAuth token."""
    session = get_session()
    try:
        return session.query(TokenStore).order_by(desc(TokenStore.created_at)).first()
    finally:
        session.close()


def is_token_valid() -> bool:
    """Check if the stored token is still valid."""
    token = get_stored_token()
    if not token:
        return False
    if token.expires_at:
        # Parse the expires_at string to naive datetime and compare with naive utcnow
        try:
            expires_dt = datetime.fromisoformat(str(token.expires_at).split('.')[0])
            if expires_dt < datetime.utcnow():
                return False
        except (ValueError, AttributeError):
            return False
    return True


# ============== CONVENIENCE ALIASES ==============

def get_today_pnl() -> float:
    """Alias for calculate_todays_pnl()."""
    return calculate_todays_pnl()


def get_current_holdings() -> Dict[str, Dict[str, Any]]:
    """Alias for get_all_holdings()."""
    return get_all_holdings()


def get_daily_trade_count() -> int:
    """Get the number of trades executed today."""
    trades = get_todays_trades()
    return len(trades)


def get_current_portfolio_value() -> float:
    """
    Get the current portfolio value from latest snapshot.
    Returns 0 if no snapshot exists.
    """
    snapshot = get_latest_portfolio()
    if snapshot:
        return snapshot.total_value
    return 0.0
