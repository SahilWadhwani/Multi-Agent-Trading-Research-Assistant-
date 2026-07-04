"""
IMMEDIATE ACTION PLAN - Next 7 Days

This is your roadmap to prove the system works before going live.
Follow this exactly, don't skip steps.
"""

PLAN = """
╔════════════════════════════════════════════════════════════════════════════════╗
║                    7-DAY VALIDATION PLAN (DO THIS NOW)                         ║
╚════════════════════════════════════════════════════════════════════════════════╝

DAY 1: BACKTEST ON HISTORICAL DATA
═════════════════════════════════════

GOAL: Prove your LLM signal generation has a positive edge on historical data

STEPS:
  1. Identify historical dates to test:
     - Pick 20 different market days from past 3 months
     - Mix of volatile days and calm days
     - Mix of up-trending and down-trending markets
  
  2. For each day:
     a. Get historical price data + Greeks (use your fo_data_feed)
     b. Run signal generation for NIFTY PE at 10:00 AM IST
     c. Simulate entry at 10:00 AM spot price
     d. Run real_data_backtest.py to simulate outcome
     e. Record: Entry signal, confidence, actual win/loss, predicted vs actual
  
  3. Collect metrics:
     - Total trades: X
     - Winning trades: Y
     - Win rate: Y/X
     - Average win: +Z%
     - Average loss: -W%
     - Profit factor: (avg_win × Y) / (avg_loss × (X-Y))
  
  4. Compare to calibration model:
     - Your model predicts win_rate = 55%
     - Actual backtest win_rate = ?
     - If within 5%: ✓ Good
     - If 10%+ difference: ❌ Recalibrate needed
  
  5. Calculate Sharpe ratio:
     - If >0.5: System has edge
     - If <0.2: System is breakeven/negative
  
TIME: 3-4 hours
OUTPUT: backtest_results.json containing all metrics
PASS CRITERIA:
  ✓ Win rate >= 52% (profitable)
  ✓ Profit factor >= 1.3
  ✓ Sharpe ratio >= 0.5
  ✓ Calibration error <= 5%

IF FAILS:
  → STOP everything
  → Identify why edge is negative
  → Redesign signal generation
  → Retry backtest
  → Don't proceed to live until backtest passes

═════════════════════════════════════════════════════════════════════════════════

DAY 2-3: PAPER TRADING (50+ TRADES)
═══════════════════════════════════════

GOAL: Simulate live trading on paper to catch system bugs and confirm edge

STEPS:
  1. Set mode to PAPER in config
  2. Set trade size to normal (Rs 15,000)
  3. Run system for full market day (9:15 AM - 3:30 PM IST)
  4. Let it naturally generate trades
  5. Monitor for:
     - Trades get generated? Y/N
     - Trades pass gatekeeper? Y/N
     - Entries are executed? Y/N
     - GTT SL/Target placed? Y/N
     - Exits happen correctly? Y/N
  
  6. After each trade, record:
     - Entry price, SL, target
     - Exit reason (SL, target, manual)
     - Exit price
     - Calculated P&L vs system P&L (should match)
  
  7. Run for 2+ days until you have 50+ trades
  
TIME: 2 market days
OUTPUT: trades_log.csv with 50+ rows
PASS CRITERIA:
  ✓ 0 system crashes
  ✓ 100% of trades exit (no orphaned positions)
  ✓ P&L calculated matches expected
  ✓ Actual win rate >= calibrated win rate (within 5%)
  ✓ No emergency broker calls needed

IF FAILS:
  → Identify what failed
  → Fix the bug
  → Restart paper trading
  → Don't proceed to live until clean 50 trades

═════════════════════════════════════════════════════════════════════════════════

DAY 4-5: MICRO-LIVE TRADING (50+ TRADES @ Rs 500-1k each)
═══════════════════════════════════════════════════════════

GOAL: Verify broker integration works and your system works in real market

STEPS:
  1. Set mode to MICRO_LIVE
  2. Set trade size to Rs 500-1,000 (small money to learn)
  3. Set stop-loss to -Rs 500 (hard limit per trade)
  4. Set daily loss cap to -Rs 2,500 (hard stop)
  
  5. RUN DURING MARKET HOURS ONLY
     - Start at 9:30 AM IST
     - Supervise until first trade
     - After 3 trades execute successfully: Can leave for 15 mins
     - Check back every hour
     - Close by 3:00 PM
  
  6. For EACH trade, immediately after placement:
     - Check Upstox app: Is the position showing in "Positions"?
     - Check GTT: Is SL order placed? (Check → Orders → GTT)
     - Check system log: Is position recorded?
  
  7. For EACH exit:
     - Did exit happen at SL or target?
     - Check Upstox app: Is position closed?
     - Check P&L: Does realized P&L match system calculation?
  
  8. Monitor for unexpected issues:
     - Auth token expires? (shouldn't happen in 6 hours)
     - Broker API errors? (note the error code)
     - Price feed disconnects? (should auto-reconnect)
     - GTT fails to place? (position unprotected → exit immediately)
  
TIME: 2 market days
OUTPUT: live_trade_log.csv with 50+ micro trades
BUDGET: Rs 2,500-5,000 (to learn by doing)
PASS CRITERIA:
  ✓ 0 system crashes
  ✓ 0 positions orphaned (all closed normally)
  ✓ 0 broker API errors (or handled gracefully)
  ✓ 100% GTT placements successful
  ✓ Actual P&L = calculated P&L (within ±1%)
  ✓ No manual intervention needed (system handled everything)

IF FAILS:
  → Document the failure
  → Fix the bug in code
  → Restart micro-live
  → Don't scale up until 50 clean trades

═════════════════════════════════════════════════════════════════════════════════

DAY 6: VERIFY EVERYTHING
═════════════════════════

GOAL: Consolidate results, verify no gotchas before scaling

STEPS:
  1. Compare backtest win_rate vs paper win_rate vs live micro win_rate
     - Should all be similar (within 5%)
     - If live micro is 20% worse: There's a problem
  
  2. Check P&L consistency:
     - System says: Total P&L = +Rs X
     - Broker says: Account changed by +Rs Y
     - Should match (within ±Rs 500 for commissions)
     - If difference > Rs 1,000: Investigate
  
  3. Verify all edge cases worked:
     - ✓ Partial fills handled?
     - ✓ Missed SL? (Should have caught it)
     - ✓ Manual exit? (Position properly closed)
     - ✓ Auth didn't expire? (If it did, restart needed)
  
  4. Check database integrity:
     - decision_log.db: All trades recorded?
     - positions.db: All positions closed?
     - order_intents.db: All intents fulfilled?
  
TIME: 1-2 hours
OUTPUT: verification_checklist.md (all checkmarks = green light)

═════════════════════════════════════════════════════════════════════════════════

DAY 7: GO/NO-GO DECISION
═════════════════════════

DECISION MATRIX:

If ALL the following are true:
  ✓ Backtest win_rate >= 52%
  ✓ Paper trading: 50+ clean trades, 0 crashes
  ✓ Micro-live: 50+ clean trades, 0 crashes
  ✓ Actual win_rate >= calibrated win_rate (within 5%)
  ✓ P&L calculations match broker
  ✓ All edge cases handled
  ✓ No broker errors

→ DECISION: GO LIVE (carefully)

If ANY of the following are true:
  ❌ Backtest win_rate < 50%
  ❌ Paper trading: Crashes/orphaned positions/math errors
  ❌ Micro-live: Broker errors/GTT failures
  ❌ Actual win_rate much worse than calibrated
  ❌ P&L doesn't match
  ❌ Edge cases exposed bugs

→ DECISION: STOP
   - Fix issues
   - Retest
   - Don't scale until fixed

═════════════════════════════════════════════════════════════════════════════════

IF GO LIVE: PHASE-IN PLAN
════════════════════════

Only if you passed all criteria above:

WEEK 1: 10% of target capital
  - Rs 10,000 trade size
  - Max 5 trades/day
  - Daily loss cap: -Rs 1,000

WEEK 2-3: 30% of target capital
  - Rs 30,000 trade size (if capital is Rs 100k)
  - Max 8 trades/day
  - Daily loss cap: -Rs 2,000

WEEK 4+: 100% of target capital
  - Rs 100,000+ trade size (or whatever is safe)
  - Normal trading
  - Daily loss cap: -Rs 4,000

NEVER:
  ✗ Risk more than 2% per trade
  ✗ Violate daily loss cap
  ✗ Override system decisions
  ✗ Trade on new markets without testing
  ✗ Change parameters without backtest

═════════════════════════════════════════════════════════════════════════════════

WHAT IF SOMETHING GOES WRONG DURING TESTING?
══════════════════════════════════════════════

Scenario: Backtest shows negative win rate
→ FIX: Review signal generation logic
  - Is LLM predicting correct direction?
  - Is confidence calibration wrong?
  - Is regime detection blocking good trades?
  → Retune parameters, re-backtest

Scenario: Paper trading crashes
→ FIX: Review error logs
  - What line of code crashed?
  - Is it a broker API issue or system issue?
  - Add defensive checks
  → Retry paper trading

Scenario: Micro-live trade gets stuck (position won't close)
→ FIX: IMMEDIATELY execute manual SELL on Upstox app
  - Don't wait for system to fix itself
  - Better to take 1% loss than 10% loss
  → Document what went wrong
  → Add guard against it
  → Restart micro-live

Scenario: GTT placement fails for a trade
→ FIX: IMMEDIATELY place manual SL order on Upstox
  - Don't leave position unprotected
  - As soon as position is safe, stop trading
  → Debug why GTT failed
  → Restart system
  → Don't resume until GTT works

═════════════════════════════════════════════════════════════════════════════════

IMPORTANT MINDSET NOTES
═══════════════════════

✓ GOOD MINDSET:
  - "I'm testing carefully to avoid losing money"
  - "If something fails, I'll fix it before trying again"
  - "Paper trading is free; I'm getting valuable data"
  - "Micro-live is learning by doing with small stakes"
  - "Losing Rs 2,500 to validate the system is worth it"
  - "I won't go live until I'm confident"

✗ BAD MINDSET:
  - "Let me skip paper trading and go straight to live"
  - "If it fails, I'll debug live (risking capital)"
  - "I've been coding for weeks, it should just work"
  - "The backtest showed profit, so live should too"
  - "I don't have time for testing, let me just deploy"
  - "FOMO: I need to trade now, not wait 7 days"

REMEMBER: 7 days of validation will save you from Rs 50,000+ losses.
That's a 100x return on the time investment.

═════════════════════════════════════════════════════════════════════════════════

YOUR ACTUAL NEXT STEPS (DO THESE TODAY)
══════════════════════════════════════════

[ ] 1. Create backtest environment
     - Set dates for historical testing
     - Prepare Greeks simulation
     - Run first 5 historical days

[ ] 2. Collect backtest results
     - Calculate win rate
     - Compare to calibration model
     - Document findings

[ ] 3. Set up paper trading mode
     - Change config to PAPER
     - Confirm no real trades will execute
     - Test entry/exit one time

[ ] 4. Schedule paper trading runs
     - 2+ market days
     - Full trading hours
     - Collect 50+ trades minimum

[ ] 5. Prepare micro-live setup
     - Have Upstox app open during trading
     - Know how to manually exit if needed
     - Have budget allocated (Rs 2,500-5,000)

[ ] 6. Set calendar reminders
     - Day 1: Start backtest
     - Day 2-3: Paper trading
     - Day 4-5: Micro-live
     - Day 6: Verify
     - Day 7: Decision

[ ] 7. Document everything
     - Create spreadsheet tracking results
     - Screenshot each trade
     - Note any issues
     - Build case file for "did system work?"

═════════════════════════════════════════════════════════════════════════════════

Ready? Start with backtest today.
Come back after 7 days with data.
Then we'll decide: GO or NO-GO for live trading.

Good luck! 🚀
"""

print(PLAN)
