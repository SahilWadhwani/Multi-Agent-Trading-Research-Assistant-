# 🤖 AI Trading Agent

An autonomous AI trading agent for **Indian Markets** - trading **Stocks, ETFs, and Options** using the Upstox API, LLM-powered analysis, and disciplined risk management.

## What It Does

- **Trades ALL asset classes**: Stocks, ETFs, Index Options (NIFTY, BANKNIFTY)
- **Analyzes** technical data + news sentiment using LLMs (GPT-5.5/Gemini)
- **Validates** through hard risk gates (10 for F&O, standard for equity)
- **Learns** from every trade outcome
- **Runs 24/7** with scheduled market-hours scanning

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      LEAN F&O BRAIN                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    │
│   │  MARKET DATA │    │  LLM NEWS    │    │   SIGNAL     │    │
│   │  (Upstox)    │───▶│  ANALYSIS    │───▶│   TRACKER    │    │
│   │  PCR, IV, OI │    │  (Proxima)   │    │              │    │
│   └──────────────┘    └──────────────┘    └──────────────┘    │
│          │                   │                    │            │
│          ▼                   ▼                    ▼            │
│   ┌─────────────────────────────────────────────────────┐     │
│   │              TREND + SIGNAL STRENGTH                 │     │
│   │         (PCR < 0.9 = Bullish, > 1.1 = Bearish)      │     │
│   └─────────────────────────────────────────────────────┘     │
│                              │                                 │
│                              ▼                                 │
│   ┌─────────────────────────────────────────────────────┐     │
│   │              10 HARD RISK GATES                      │     │
│   │  • Capital limits    • Premium range                 │     │
│   │  • Daily loss cap    • IV threshold                  │     │
│   │  • Trading hours     • Stop-loss validation          │     │
│   └─────────────────────────────────────────────────────┘     │
│                              │                                 │
│              ┌───────────────┴───────────────┐                │
│              ▼                               ▼                │
│        ┌──────────┐                   ┌──────────┐           │
│        │ EXECUTE  │                   │ NO TRADE │           │
│        │ + LOG    │                   │ (logged) │           │
│        └──────────┘                   └──────────┘           │
│                                                               │
└─────────────────────────────────────────────────────────────────┘
```

## Features

### Trading
- **Index Options**: NIFTY, BANKNIFTY, FINNIFTY
- **Stocks**: Full NIFTY 50 + all NSE equities (2,451 available)
- **ETFs**: NIFTYBEES, BANKBEES, + 99 others
- **Strategy**: Options buying (CE/PE), equity swing trades
- **Capital**: Optimized for Rs 15-20k
- **Modes**: Paper (default) or Live

### Dynamic Symbol Universe
- **No hardcoded ISINs** - Fetches instrument master from Upstox
- **96,247 instruments** indexed and searchable
- **Auto-refresh** every 24 hours
- **Methods**: `get_nifty50()`, `get_all_equity()`, `get_etfs()`, `search()`

### Analysis
- **Technical**: PCR, Max Pain, IV, OI analysis
- **News**: LLM-powered sentiment analysis (GPT-5.5 via Proxima)
- **Greeks**: Delta, Gamma, Theta, Vega calculation

### Risk Management
- 10 hard-coded risk gates (no LLM in execution path)
- Max 70% capital per trade
- Daily loss limit: Rs 4,000
- Mandatory stop-loss (30-50%)
- Trading hours enforced

### Learning
- All scans logged (even rejections)
- Trade outcome tracking
- Daily calibration of thresholds
- Activity reports

## Quick Start

```bash
# 1. Setup
cd trading-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Configure .env
UPSTOX_API_KEY=your_api_key
UPSTOX_API_SECRET=your_secret
UPSTOX_REDIRECT_URI=http://127.0.0.1:5000/callback
TRADING_MODE=paper
# Optional production controls (see docs/INCIDENT_RUNBOOK.md)
# TRADING_ENABLED=true
# TRADING_KILL_SWITCH=0
# TRADING_NON_INTERACTIVE=0   # set 1 on servers to block browser OAuth
# MICRO_LIVE_MAX_ORDER_VALUE=5000
# MAX_OPEN_POSITIONS=4
# MAX_CONSECUTIVE_LOSSES=4
# EQUITY_LIVE_ENABLED=0       # must be 1 for live equity orders in micro_live/live

# 3. Authenticate with Upstox
python main.py --auth

# 4. Start Proxima (for LLM - if you have ChatGPT Plus)
# Download from: https://github.com/proxima/proxima
proxima start

# 5. Run single analysis
python -c "from brain.lean_fo_brain import analyze_fo; analyze_fo('NIFTY', 20000)"

# 6. Run scheduler (24/7)
python scheduler.py
```

## Production modes & rollout

| `TRADING_MODE` | Broker F&O orders | Notes |
|----------------|-------------------|--------|
| `paper` | No | Default; decisions + paper positions only |
| `shadow` | No | Live-like analysis; still no broker orders |
| `micro_live` | Yes (capped) | F&O MARKET BUY via `place_fo_order`; per-order notional cap |
| `live` | Yes | Same path without micro cap |

**Promotion gates (objective):** run `paper` until reconciliation + logs are clean, then `shadow`, then `micro_live` with small capital, then `live` only after measured expectancy and slippage review. See [docs/INCIDENT_RUNBOOK.md](docs/INCIDENT_RUNBOOK.md).

## Commands

### Quick Status
```bash
# Check everything at a glance
python quick_status.py
```

### Dashboard
```bash
# Start the web dashboard
streamlit run dashboard.py

# Opens: http://localhost:8501
```

### Run Tests
```bash
# Comprehensive test suite
python tests/comprehensive_test.py

# Equity-specific tests
python tests/test_equity.py
```

### Single Analysis
```bash
# Analyze NIFTY with Rs 20k capital
python -c "from brain.lean_fo_brain import analyze_fo; analyze_fo('NIFTY', 20000)"

# Analyze BANKNIFTY
python -c "from brain.lean_fo_brain import analyze_fo; analyze_fo('BANKNIFTY', 20000)"
```

### Autonomous Agent (24/7 Operation)
```bash
# Recommended - Standalone daemon with logging
python run_agent.py              # Run forever with file logging
python run_agent.py --once       # Single scan and exit
python run_agent.py --status     # Show current status

# Logs written to: logs/agent_YYYYMMDD.log
```

### Scheduler (Alternative)
```bash
python scheduler.py              # Run continuously (F&O + Equity, scans NIFTY 50)
python scheduler.py --once       # Single scan
python scheduler.py --test       # Test mode (no real trades)
python scheduler.py --universe nifty100  # Scan NIFTY 100 stocks
python scheduler.py --fo-only    # Only scan F&O (options)
python scheduler.py --equity-only  # Only scan stocks

# Run in background
nohup python scheduler.py > scheduler.log 2>&1 &
```

### Equity Analysis
```bash
# Analyze a single stock
python -c "
from brain.orchestrator import TradingBrain
brain = TradingBrain()
result = brain.analyze_and_decide('RELIANCE', available_capital=20000)
print(f'Decision: {result.get(\"final_decision\", {}).get(\"action\", \"HOLD\")}')
"
```

### Activity Report
```bash
# See what the agent has been doing
python -c "from brain.lean_fo_brain import activity_report; activity_report(7)"
```

### Backtesting
```bash
# Run backtest with gap reversal strategy
python -c "from backtesting.unbiased_backtest import run_backtest; run_backtest('NIFTY', 60, 'gap_reversal', 20000)"

# Available strategies: gap_momentum, gap_reversal, trend_continuation
```

### Authentication
```bash
python main.py --auth       # Get new Upstox token
python main.py --status     # Check connection status
```

## Project Structure

```
trading-agent/
├── brain/
│   ├── lean_fo_brain.py      # F&O trading brain
│   ├── orchestrator.py       # Equity trading brain
│   ├── position_tracker.py   # Real-time P&L tracking
│   ├── smart_exit.py         # Dynamic exit decisions
│   └── signal_tracker.py     # Logs all scans
│
├── backtesting/
│   ├── unbiased_backtest.py  # No lookahead bias
│   ├── simulator.py          # Trade simulation
│   └── historical_data.py    # Data management
│
├── data_feeds/
│   ├── fo_data_feed.py       # Option chain data
│   ├── instrument_master.py  # Dynamic symbol fetching
│   ├── options_greeks.py     # Greeks calculation
│   └── news_feed.py          # News fetching
│
├── memory/
│   ├── decision_log.py       # Trade logging
│   ├── reflection.py         # Learning from trades
│   └── calibrator.py         # Threshold adjustment
│
├── mcp_server/
│   ├── upstox_client.py      # Upstox API wrapper
│   └── guardrails.py         # Risk limits
│
├── execution/
│   ├── runtime_safety.py     # Modes, kill switch, fail-closed gates
│   ├── reconciliation.py     # Broker vs local integrity
│   ├── risk_runtime.py       # Daily loss / streak / exposure caps
│   ├── order_tracker.py      # Order intent audit DB
│   └── lean_fo_executor.py   # F&O live order wiring
│
├── llm/
│   └── client.py             # Proxima/Ollama integration
│
├── tests/
│   ├── comprehensive_test.py # Full test suite
│   └── test_equity.py        # Equity tests
│
├── dashboard.py              # Streamlit web dashboard
├── quick_status.py           # CLI status checker
├── run_agent.py              # Autonomous runner
├── scheduler.py              # Scan scheduler
│
├── database/
│   ├── schema.py             # SQLite tables
│   └── operations.py         # CRUD operations
│
├── main.py                   # CLI entry point
└── .env                      # Configuration
```

## Risk Gates (Cannot Be Bypassed)

| Gate | Rule |
|------|------|
| Time | Only 9:30 AM - 3:15 PM IST |
| Capital | Max 70% per trade |
| Trade Value | Max Rs 15,000 |
| Daily Loss | Max Rs 4,000 |
| Daily Trades | Max 8 |
| Premium | Rs 20 - Rs 250 range |
| IV | Max 30% for buying |
| Stop Loss | 25-50% mandatory |
| Risk:Reward | Min 1.2:1 |
| Confidence | Min 55% (50% in activity mode) |

## LLM Setup

The agent uses LLMs for news analysis. Options:

### Option 1: Proxima (Recommended)
Uses your existing ChatGPT Plus / Gemini Pro subscription.
```bash
# Install Proxima
# See: https://github.com/proxima/proxima

# Start before running agent
proxima start
```

### Option 2: Ollama (Free, Local)
```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model
ollama pull llama3

# Set in .env
LLM_BACKEND=ollama
```

## Upstox Token

**Upstox access tokens expire daily.** You need to refresh each morning:

```bash
python main.py --auth
# Follow the OAuth flow in browser
```

For automated refresh, you'll need to implement the refresh token flow (see Upstox API docs).

## Backtest Results (60 days, Rs 20k)

| Strategy | Return | Win Rate | Sharpe | Profit Factor |
|----------|--------|----------|--------|---------------|
| Gap Momentum | +12.2% | 45.0% | 0.26 | 1.04 |
| **Gap Reversal** | **+55.4%** | **52.0%** | **2.28** | **1.35** |
| Trend Continuation | +21.7% | 44.4% | 0.97 | 1.14 |

*Gap Reversal shows the strongest edge.*

## Philosophy

```
Most days = NO TRADE (discipline > activity)
LLM for analysis, NOT execution
Hard rules for risk (fast, deterministic)
Learn from every trade
```

The agent will only trade when:
- Clear directional signal (PCR, Max Pain)
- News doesn't conflict
- All 10 risk gates pass

## Troubleshooting

**Token expired:**
```bash
python main.py --auth
```

**No trades happening:**
```bash
# Check activity report
python -c "from brain.lean_fo_brain import activity_report; activity_report(7)"

# Common reasons:
# - Market sideways (PCR ~1.0)
# - News conflicts with trend
# - Outside market hours
```

**LLM not working:**
```bash
# Check if Proxima is running
curl http://localhost:3210/api/v1/models

# Or use Ollama fallback
ollama serve
```

## Safety Notes

- **Paper mode by default** - set `TRADING_MODE=live` for real trades
- All trades require reasoning
- Cannot exceed risk limits even manually
- All activity is logged and reviewable

---

**Built for disciplined, autonomous options trading.**
