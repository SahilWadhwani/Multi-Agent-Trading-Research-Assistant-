"""
CRITICAL BUGS TO FIX BEFORE VALIDATION STARTS

These issues MUST be fixed before Day 1 backtest.
If not fixed, testing will fail and you'll waste time.
"""

ISSUES = """
╔════════════════════════════════════════════════════════════════════════════════╗
║                    MUST-FIX ISSUES (Before Testing)                            ║
╚════════════════════════════════════════════════════════════════════════════════╝

PRIORITY 1: CRITICAL (Testing is impossible without these)
═════════════════════════════════════════════════════════════════════════════════

❌ ISSUE #1: Exit Ticker Thread Crash on Startup
   File: execution/exit_ticker.py
   Problem: If exit_ticker is enabled but WebSocket isn't ready → crash
   Test: What happens if you call enable_exit_ticker() before price_hub is ready?
   Status: UNKNOWN - Could crash scheduler on startup
   
   FIX:
   [ ] Add defensive check in enable():
       if not self.price_hub:
           logger.warning("Price hub not ready; exit ticker deferred")
           return False
   [ ] Test: Start system, check logs, verify no crash
   
   IMPACT: If this crashes, entire scheduler stops. Validation fails.

───────────────────────────────────────────────────────────────────────────────

❌ ISSUE #2: Partial Fill GTT Mismatch
   File: execution/lean_fo_executor.py
   Problem: If order partially fills (e.g., 25 of 50 lots), GTT still targets 50 lots
   Example: Order 50 lots, only 20 fill → Position is 20 lots, but GTT tries to SL 50
   Status: GTT will REJECT (target size > position size)
   
   FIX:
   [ ] After fill confirmation, verify qty == requested_qty
   [ ] If qty < requested_qty (partial fill):
       - Log warning: "PARTIAL FILL: Requested X, got Y"
       - Set position size to actual filled qty
       - Recalculate GTT SL/target for actual size
       - If qty is too small (< 1 lot): Immediately flatten
   
   IMPACT: Partial fill = unprotected position = potential disaster

───────────────────────────────────────────────────────────────────────────────

❌ ISSUE #3: Transaction Costs Missing from P&L
   File: execution/exit_manager.py or wherever P&L is calculated
   Problem: You calculate P&L as (exit_price - entry_price) × qty
            But ignore Upstox commissions (~0.02% + GST)
   Example: Rs 50,000 trade @ 0.02% = Rs 20 commission
            For Rs 5,000 trade @ 0.02% = Rs 2 commission
            Small trades lose 0.4% just to commission!
   
   FIX:
   [ ] Find P&L calculation code
   [ ] Add transaction cost:
       commission = trade_value × 0.0002  # 0.02% Upstox rate
       gst = commission × 0.18  # 18% GST
       total_cost = commission + gst
       
       adjusted_pnl = realized_pnl - total_cost
   
   [ ] Update decision_log to show net P&L (after costs)
   
   IMPACT: Without this, you think you're profitable but actually break even

───────────────────────────────────────────────────────────────────────────────

❌ ISSUE #4: Auth Token Expiry During Live Trading
   File: llm/client.py (or wherever Proxima auth is handled)
   Problem: Gemini/ChatGPT tokens expire after 24h
            If token expires mid-trading day → LLM calls fail
            System should handle gracefully, but doesn't
   
   Test: What happens if Proxima token expires during a trade?
         Does system exit order still work?
         Or does it block because "LLM unavailable"?
   
   FIX NEEDED:
   [ ] Review llm/client.py for token refresh logic
   [ ] Test: Let system run for >24h, verify token is refreshed
   [ ] If refresh fails: System should NOT block order execution
       - Should use fallback (execute with -1.5% penalty, not BLOCK)
   
   IMPACT: If not handled, live trading could freeze during the day

───────────────────────────────────────────────────────────────────────────────

❌ ISSUE #5: Internet Disconnection = Unprotected Position
   File: execution/lean_fo_executor.py (or wherever GTT is placed)
   Problem: Sequence is:
            1. Place MARKET BUY order
            2. Wait for fill
            3. Place GTT SL order
            
            If internet dies between step 2-3:
            - Position exists (BUY filled)
            - GTT not placed (no SL protection)
            - System doesn't know position is unprotected
            
   Status: This is a REAL SCENARIO in live trading
   
   FIX NEEDED:
   [ ] After GTT placement, add GTT status confirmation:
       - Query broker: Is GTT actually placed?
       - Compare: Expected GTT vs actual GTT on broker
       - If missing: Log ALERT and immediately re-place
   
   [ ] On scheduler restart:
       - Query broker: Any open positions?
       - For each position: Is GTT placed?
       - If position without GTT: Alert + immediately place SL
   
   [ ] Add monitoring:
       - Every hour: Verify all positions have GTT
       - If GTT missing: Alert (don't assume it's fine)
   
   IMPACT: Without this, you could wake up to an unprotected position that lost 20%

═════════════════════════════════════════════════════════════════════════════════

PRIORITY 2: HIGH (Testing won't fail, but results will be wrong)
═════════════════════════════════════════════════════════════════════════════════

⚠️  ISSUE #6: Regime Detection Accuracy Unknown
   File: brain/regime_detector.py
   Problem: Code detects regime (MEAN_REVERT, STRONG_TREND, CHOPPY)
            But: Accuracy unknown. Could be 90% accurate or 60%
   Status: Not validated
   
   FIX NEEDED:
   [ ] Test regime detection on 30 historical days
   [ ] For each day:
       - Run regime detector at 10:00 AM
       - Record: Detected regime
       - Compare vs actual market behavior during that day
       - Did MEAN_REVERT signal actually reverse? (yes/no)
       - Did STRONG_TREND signal actually trend? (yes/no)
   [ ] Calculate accuracy percentage
   [ ] If <80% accurate: Recalibrate parameters

───────────────────────────────────────────────────────────────────────────────

⚠️  ISSUE #7: Gatekeeper SL Formula Not Validated
   File: brain/pre_trade_gatekeeper.py (line ~285)
   Problem: SL calculation uses sqrt(hours/24) scaling factor
            But: Is this too aggressive? Too conservative?
   Example: 
     - At 1h to expiry: factor = 5.0
     - At 4h to expiry: factor = 2.5
     - At 12h to expiry: factor = 1.4
     
     Is 5.0x SL width reasonable? Or will it trigger on noise?
   
   Status: Not backtested
   
   FIX NEEDED:
   [ ] Backtest with current SL formula on 100 trades
   [ ] Measure: How many trades SL triggers but would have recovered?
   [ ] If >30% false SL triggers: Loosen formula (divide factor by 2)
   [ ] If <5% false triggers: Can tighten (multiply factor by 1.5)

───────────────────────────────────────────────────────────────────────────────

⚠️  ISSUE #8: Confidence Calibration Not Proven
   File: brain/pre_trade_gatekeeper.py (line ~200)
   Problem: Code calibrates LLM confidence to win_probability
            Example: 95% confidence → 58% win probability
            But: Is this calibration curve actually correct?
   Status: Based on theory, not historical validation
   
   FIX NEEDED:
   [ ] After 100 paper trades:
       - Group trades by predicted win_probability (50-55%, 55-60%, 60-65%, 65-70%)
       - Calculate actual win rate for each group
       - Compare vs predicted
       - If curve is wrong: Adjust calibration formula

───────────────────────────────────────────────────────────────────────────────

═════════════════════════════════════════════════════════════════════════════════

PRIORITY 3: MEDIUM (Nice to have, but not blocking)
═════════════════════════════════════════════════════════════════════════════════

🟡 ISSUE #9: No Real-Time Dashboard
   File: dashboard/app.py (or missing?)
   Problem: You can't see current positions in real-time
            Have to manually query logs to know what's happening
            In live trading, this is dangerous (slow incident response)
   
   FIX NEEDED:
   [ ] Build simple web dashboard showing:
       - Current positions (symbol, entry price, current P&L)
       - Current day stats (# trades, win %, total P&L)
       - Last trade details
       - Alerts (SL hit, target hit, error, etc.)
   
   Effort: 2-3 hours with FastAPI + simple HTML

───────────────────────────────────────────────────────────────────────────────

🟡 ISSUE #10: No Alert System
   Problem: Important events happen silently
            - Trade executed: No notification
            - SL hit: No notification
            - Error occurred: No notification
   
   FIX NEEDED:
   [ ] Add email/Telegram alerts for:
       - Trade entered (show entry price, SL, target)
       - SL hit (show loss amount)
       - Target hit (show profit amount)
       - Error (show error message)
   
   Effort: 1 hour with Telegram bot

───────────────────────────────────────────────────────────────────────────────

═════════════════════════════════════════════════════════════════════════════════

YOUR IMMEDIATE CHECKLIST (DO BEFORE DAY 1)
═════════════════════════════════════════════

[ ] PRIORITY 1: Fix Exit Ticker Crash Risk
    - Add defensive checks in exit_ticker.enable()
    - Test that scheduler starts without crash
    - Time: 30 minutes

[ ] PRIORITY 1: Fix Partial Fill GTT Mismatch
    - Add partial fill handling in lean_fo_executor.py
    - Verify GTT size matches actual position size
    - Time: 1 hour

[ ] PRIORITY 1: Add Transaction Costs
    - Find P&L calculation code
    - Add commission + GST calculation
    - Update decision_log with net P&L
    - Time: 30 minutes

[ ] PRIORITY 1: Validate Auth Token Expiry
    - Review llm/client.py for token refresh
    - Test token expiry handling
    - Ensure graceful fallback (don't block orders)
    - Time: 1 hour

[ ] PRIORITY 1: Test Internet Disconnection
    - Verify GTT placement after internet outage
    - Add recovery logic to scheduler
    - Test position reconciliation on startup
    - Time: 1-2 hours

TOTAL TIME: 4-5 hours

These are CRITICAL. If you skip them, testing results will be misleading.
Better to spend 5 hours now than lose Rs 10,000 on validation because of a bug.

═════════════════════════════════════════════════════════════════════════════════

After fixing these issues, you're ready to start Day 1 backtest.

Questions?
- Check HONEST_READINESS_ASSESSMENT.md for detailed analysis
- Check 7_DAY_VALIDATION_PLAN.md for step-by-step validation plan
"""

print(ISSUES)
