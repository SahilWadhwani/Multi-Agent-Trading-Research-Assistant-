# QUANT-1 Architecture Documentation

## Overview

QUANT-1 is a multi-agent AI trading system for Indian stock markets (NSE/BSE). 
It uses a pipeline of specialized agents that each handle one aspect of the trading decision.

**Key Principle:** No AI hallucination. Every decision is backed by real data. 
When data is unavailable, agents return explicit "insufficient data" responses.

## Agent Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES                               │
│  Upstox API → Market Data → Historical OHLCV → Real-time Quotes     │
└────────────────────────────────┬────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────────┐
│                       TECHNICAL ANALYST                              │
│  Input:  Symbol, Exchange                                            │
│  Output: Indicators (RSI, MACD, Bollinger), Trend, Support/Resist   │
│  Rule:   Only reports what data shows. No speculation.               │
└────────────────────────────────┬────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────────┐
│                         TRADER AGENT                                 │
│  Input:  Technical Report, Available Capital                         │
│  Output: Trade Proposal (Action, Qty, SL, Target)                   │
│  Rule:   Position sizing based on risk parameters only.             │
└────────────────────────────────┬────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────────┐
│                        RISK MANAGER                                  │
│  Input:  Trade Proposal, Account Balance                             │
│  Output: Approval/Rejection with violations                          │
│  Rule:   IMMUTABLE guardrails. Cannot be bypassed.                   │
└────────────────────────────────┬────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      PORTFOLIO MANAGER                               │
│  Input:  Tech Report, Proposal, Risk Assessment                      │
│  Output: Final Decision (BUY/SELL/HOLD/REJECTED)                    │
│  Rule:   Synthesizes all inputs. Logs reasoning.                     │
└────────────────────────────────┬────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────────┐
│                         EXECUTION                                    │
│  Paper Mode: Log to database only                                    │
│  Live Mode:  Execute via Upstox API                                  │
└─────────────────────────────────────────────────────────────────────┘
```

## File Structure

```
trading-agent/
├── brain/
│   ├── __init__.py
│   └── orchestrator.py        # Main coordinator - TradingBrain class
│
├── agents/
│   ├── analysts/
│   │   └── technical_analyst.py   # RSI, MACD, Bollinger analysis
│   ├── traders/
│   │   └── trader.py              # Trade proposal generation
│   ├── risk/
│   │   └── risk_manager.py        # Guardrail enforcement
│   └── managers/
│       └── portfolio_manager.py   # Final decision maker
│
├── data_feeds/
│   ├── technical_indicators.py    # Indicator calculations
│   └── market_data.py             # Upstox data fetcher
│
├── mcp_server/
│   ├── upstox_client.py           # Upstox API wrapper
│   ├── guardrails.py              # IMMUTABLE risk rules
│   └── server.py                  # MCP server for tools
│
├── database/
│   ├── schema.py                  # SQLite models
│   └── operations.py              # CRUD operations
│
├── dashboard/
│   └── app.py                     # Streamlit monitoring
│
├── tests/
│   ├── test_phase1.py             # Unit tests (no API needed)
│   └── test_upstox_connection.py  # API connection tests
│
└── main.py                        # CLI entry point
```

## Immutable Guardrails

These rules are HARDCODED and CANNOT be changed by any agent or configuration:

```python
GUARDRAILS = {
    "max_position_percent": 20,      # Max 20% of capital per trade
    "max_daily_loss_percent": 5,     # Stop trading at 5% daily loss
    "max_daily_trades": 50,          # Max 50 trades per day
    "min_trade_value": 100,          # Minimum ₹100 per trade
    "max_trade_value": 10000,        # Maximum ₹10,000 per trade (adjustable)
    "blocked_actions": [
        "add_funds",
        "withdraw_funds",
        "bank_transfer",
        "modify_bank_account",
    ],
}
```

## No-Hallucination Rules

1. **Technical Analyst**: Only reports indicators that can be calculated from data.
   - If insufficient historical data → returns "Insufficient data for X indicator"
   - Never extrapolates or assumes patterns

2. **Trader Agent**: Only proposes trades based on explicit signals.
   - If confidence < 50% → HOLD
   - If no clear signal → HOLD
   - Position size strictly from risk formula, not "feeling"

3. **Risk Manager**: Binary validation only.
   - Trade passes ALL checks → APPROVED
   - Trade fails ANY check → REJECTED
   - No "maybe" or "with caution"

4. **Portfolio Manager**: Documents every decision factor.
   - Lists what signals led to decision
   - Explains why trade was approved/rejected
   - Logs full reasoning to database

## Data Sources

### Phase 1 (Current)
- Upstox API: Real-time quotes, historical OHLCV
- Technical Indicators: Calculated locally

### Phase 2 (Next)
- Indian News APIs: MoneyControl, Economic Times
- Sentiment: Social media analysis
- Macro: FII/DII flows, RBI data

## Testing Strategy

1. **Unit Tests** (`tests/test_phase1.py`)
   - Use mock data
   - No API connection needed
   - Tests all components independently

2. **Connection Tests** (`tests/test_upstox_connection.py`)
   - Requires authentication
   - Tests live API endpoints
   - Run after `python main.py --auth`

3. **Integration Tests** (Future)
   - Full pipeline with paper trading
   - Validates decisions match expected behavior

## Usage

```bash
# Authenticate with Upstox
python main.py --auth

# Check status (no auth needed for basic)
python main.py --status

# Analyze a stock
python main.py --analyze RELIANCE

# Get full trade decision
python main.py --decide TCS

# Scan watchlist
python main.py --scan

# Run tests
python -m tests.test_phase1
python -m tests.test_upstox_connection
```
