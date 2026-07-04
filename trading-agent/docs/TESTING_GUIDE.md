# QUANT-1 Manual Testing Guide

## Before You Start

### Prerequisites Checklist
- [ ] Ollama running: `ollama serve`
- [ ] Model loaded: `ollama list` shows qwen2.5:7b-instruct
- [ ] Virtual env active: `source venv/bin/activate`
- [ ] Upstox segments reactivated (for live tests)

### Quick Status Check
```bash
cd /Users/sahil/Desktop/Tradibng/trading-agent
source venv/bin/activate
python main.py --status
```

---

## Test Level 1: Component Tests (No API Needed)

### Test 1.1: LLM Connection
```bash
python -c "
from llm.client import get_llm_client, check_llm_status
status = check_llm_status()
print(f'LLM Available: {status[\"available\"]}')
print(f'Model: {status.get(\"model\", \"N/A\")}')
"
```
**Expected:** LLM Available: True, Model: qwen2.5:7b-instruct

### Test 1.2: Technical Indicators
```bash
python -c "
from data_feeds.technical_indicators import TechnicalIndicators

# Test with sample data
prices = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 111, 110, 112, 114, 115]
rsi = TechnicalIndicators.calculate_rsi(prices, 14)
print(f'RSI calculated: {rsi[-1]:.2f}')

macd = TechnicalIndicators.calculate_macd(prices)
print(f'MACD: {macd[\"macd\"][-1]:.4f}')
print('✅ Technical indicators working')
"
```

### Test 1.3: News Feed (No API key needed)
```bash
python -c "
from data_feeds.news_feed import get_news_feed
feed = get_news_feed()
result = feed.fetch_stock_news('RELIANCE', max_results=5)
print(f'News fetched: {len(result.get(\"news\", []))} articles')
for n in result.get('news', [])[:3]:
    print(f'  - {n[\"title\"][:60]}...')
print('✅ News feed working')
"
```

### Test 1.4: LLM News Analysis
```bash
python -c "
from llm.client import get_llm_client
llm = get_llm_client()

news = [
    {'title': 'TCS reports record Q4 profit, beats all estimates', 'source': 'ET'},
    {'title': 'TCS wins \$500M deal from major US bank', 'source': 'MC'},
]
result = llm.analyze_news(news, 'TCS')
print(f'Sentiment: {result[\"sentiment\"]}')
print(f'Confidence: {result[\"confidence\"]}%')
print(f'Implication: {result[\"trading_implication\"]}')
print('✅ LLM analysis working')
"
```

### Test 1.5: Database Operations
```bash
python -c "
from database.operations import init_database, log_trade, get_todays_trades
init_database()

# Log a test trade
log_trade(
    symbol='TEST',
    quantity=10,
    side='BUY',
    price=100.0,
    order_id='test_001',
    status='PAPER_TRADE'
)
trades = get_todays_trades()
print(f'Trades logged today: {len(trades)}')
print('✅ Database working')
"
```

### Test 1.6: Guardrails
```bash
python -c "
from mcp_server.guardrails import validate_trade_risk, GUARDRAILS

print('Guardrails:')
for k, v in GUARDRAILS.items():
    print(f'  {k}: {v}')

# Test a valid trade
result = validate_trade_risk(
    symbol='TCS',
    side='BUY',
    quantity=10,
    price=3500,
    available_margin=500000,
    daily_trades=5,
    daily_pnl=0
)
print(f'Trade approved: {result.approved}')
print('✅ Guardrails working')
"
```

---

## Test Level 2: Agent Tests (Mock Mode)

### Test 2.1: Full Pipeline with Mock Data
```bash
python -m pytest tests/test_phase1.py tests/test_phase2.py -v
```
**Expected:** All tests pass (13/13)

### Test 2.2: Brain Orchestrator (Mock)
```bash
python -c "
from brain.orchestrator import TradingBrain

brain = TradingBrain(paper_mode=True)
status = brain.get_status()
print(f'Brain Status:')
print(f'  Paper Mode: {status[\"paper_mode\"]}')
print(f'  Market: {status[\"market_status\"]}')
print('✅ Brain initialized')
"
```

---

## Test Level 3: Live API Tests (Requires Upstox Auth)

### Test 3.1: Authenticate with Upstox
```bash
python main.py --auth
```
**Expected:** Browser opens, you login, callback received, token stored

### Test 3.2: Check Account Status
```bash
python main.py --status
```
**Expected:** Shows balance, positions, market status

### Test 3.3: Analyze a Real Stock
```bash
python main.py --analyze RELIANCE
```
**Expected:** 
- Technical indicators calculated
- News fetched and analyzed by LLM
- Sentiment aggregated
- Bias and confidence displayed

### Test 3.4: Full Decision Pipeline
```bash
python main.py --decide RELIANCE
```
**Expected:**
- [1/6] Technical Analysis
- [2/6] News Analysis (LLM)
- [3/6] Sentiment Aggregation (LLM)
- [4/6] Trade Proposal
- [5/6] Risk Assessment
- [6/6] Final Decision (LLM reasoning)

### Test 3.5: Watchlist Scan
```bash
python main.py --scan
```
**Expected:** Scans watchlist and shows opportunities

---

## Test Level 4: Paper Trading Simulation

### Test 4.1: Single Paper Trade
```bash
python main.py --trade -s RELIANCE --side BUY --qty 5 --reason "Manual test"
```
**Expected:** Trade logged to database (not sent to Upstox)

### Test 4.2: Verify Trade Logged
```bash
python -c "
from database.operations import get_todays_trades
trades = get_todays_trades()
for t in trades[-5:]:
    print(f'{t.timestamp} | {t.side} {t.quantity} {t.symbol} @ {t.price} | {t.status}')
"
```

---

## Test Level 5: Dashboard

### Test 5.1: Launch Dashboard
```bash
python main.py --dashboard
```
**Expected:** Streamlit opens at http://localhost:8501

### Test 5.2: Verify Dashboard Shows
- [ ] Current P&L
- [ ] Today's trades
- [ ] Agent reasoning log
- [ ] Holdings (if any)

---

## Profitability Verification Checklist

Before going live, verify these profit-maximizing features:

### Entry Logic
- [ ] Only enters on strong signals (confidence > 60%)
- [ ] Technical + Sentiment alignment required
- [ ] Volume confirmation (when available)
- [ ] Avoids entry in last 30 min of market

### Exit Logic  
- [ ] Stop-loss always set (based on ATR/support)
- [ ] Target always set (R:R ratio > 1.5)
- [ ] Trailing stop for winners (future enhancement)

### Risk Management
- [ ] Max 20% per position
- [ ] Max 5% daily loss
- [ ] Max 50 trades/day
- [ ] No bank transfers

### Intelligence
- [ ] LLM analyzes news context
- [ ] LLM aggregates sentiment
- [ ] LLM provides decision reasoning
- [ ] Falls back to rules if LLM fails

---

## Common Issues & Fixes

### "No segments active"
→ Reactivate trading segments in Upstox app/web

### "Token expired"  
→ Run `python main.py --auth` again

### "LLM not available"
→ Run `ollama serve` in another terminal

### "No news found"
→ Normal for small-cap stocks; system handles gracefully

---

## Automation Readiness Checklist

For OpenClaw / 24/7 operation:

- [ ] Ollama runs as service: `brew services start ollama`
- [ ] Agent can auto-recover from errors
- [ ] Dashboard accessible remotely
- [ ] Alerts configured (Phase 5)
- [ ] Daily token refresh works

---

## Next Steps After Testing

1. **If all tests pass:** Proceed to Phase 3 (F&O)
2. **If API tests fail:** Reactivate Upstox segments
3. **If LLM slow:** Consider smaller model or GPU

**Remember:** Paper trade for at least 1 week before going live!
