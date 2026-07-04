"""
Database schema for the AI Trading Agent.
SQLite as the single source of truth between Agent and Dashboard.
"""

import os
from datetime import datetime
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Text,
    Boolean,
    Enum as SQLEnum,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()


class Trade(Base):
    """
    Records all trades (both paper and live).
    """
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), default="NSE")
    quantity = Column(Integer, nullable=False)
    side = Column(String(10), nullable=False)  # "BUY" or "SELL"
    price = Column(Float, nullable=False)
    order_type = Column(String(20), default="MARKET")  # MARKET, LIMIT
    product_type = Column(String(20), default="INTRADAY")  # INTRADAY, DELIVERY, etc.
    order_id = Column(String(100), nullable=True)  # Upstox order ID (null for paper trades)
    status = Column(String(20), default="EXECUTED")  # PENDING, EXECUTED, REJECTED, CANCELLED
    is_paper_trade = Column(Boolean, default=True)
    pnl = Column(Float, nullable=True)  # Realized P&L for closing trades
    notes = Column(Text, nullable=True)


class AgentLog(Base):
    """
    Logs the AI agent's reasoning and decision-making process.
    Critical for monitoring and debugging the agent's behavior.
    """
    __tablename__ = "agent_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    ai_reasoning = Column(Text, nullable=False)
    strategy_used = Column(String(100), nullable=True)
    action_taken = Column(String(50), nullable=True)  # TRADE, HOLD, ANALYSIS, etc.
    symbols_analyzed = Column(String(200), nullable=True)
    confidence_level = Column(Float, nullable=True)  # 0.0 to 1.0
    market_conditions = Column(Text, nullable=True)


class PortfolioSnapshot(Base):
    """
    Periodic snapshots of portfolio state.
    Used for tracking performance over time.
    """
    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    total_value = Column(Float, nullable=False)
    available_margin = Column(Float, nullable=False)
    used_margin = Column(Float, default=0.0)
    realized_pnl = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    num_open_positions = Column(Integer, default=0)
    positions_summary = Column(Text, nullable=True)  # JSON string of current positions


class TokenStore(Base):
    """
    Stores OAuth tokens for automated refresh.
    """
    __tablename__ = "token_store"

    id = Column(Integer, primary_key=True, autoincrement=True)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    token_type = Column(String(50), default="Bearer")
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# Database connection
_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        db_path = os.getenv("DATABASE_PATH", "trading_agent.db")
        _engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            connect_args={"check_same_thread": False}
        )
    return _engine


def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def init_database():
    """Initialize the database and create all tables."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    print("✓ Database initialized successfully")
    return engine


if __name__ == "__main__":
    init_database()
