"""
COMPREHENSIVE TRADING AGENT AUDIT PROMPT

Use this prompt with Claude, GPT-4, or any AI model to get unbiased
feedback on your entire options trading system.

Instructions:
1. Copy this entire prompt
2. Provide the AI agent with access to your codebase (paste key files)
3. Ask the model to run this audit
4. Get detailed, actionable feedback

Key focus areas:
- Logic soundness
- Implementation correctness
- Edge case handling
- Production readiness
- Risk management integrity
- Execution flow reliability
"""

AUDIT_PROMPT = """
You are a Senior Quantitative Trading Systems Architect and Code Reviewer.
Your task is to audit an options trading agent that uses LLMs (ChatGPT) for
decision-making.

CRITICAL REQUIREMENT: Be brutally honest. If there are bugs, design flaws,
or risks, highlight them clearly. Do not sugarcoat. Assume this will trade
real money and losses are expensive.

═══════════════════════════════════════════════════════════════════════════════
AUDIT CHECKLIST (Review each section thoroughly)
═══════════════════════════════════════════════════════════════════════════════

## SECTION 1: SIGNAL GENERATION & CONFIDENCE CALIBRATION
────────────────────────────────────────────────────────────

Review the following:
1. How is the LLM asked to generate directional predictions?
   - Q: Does the prompt avoid leading language?
   - Q: Does it ask for probability ranges instead of binary yes/no?
   - Q: Is there a documented baseline accuracy expectation?

2. How is confidence calculated?
   - Q: Is LLM confidence being conflated with win probability?
   - Q: What's the actual win rate for trades with 85% LLM confidence?
   - Q: Is there backtested calibration or just gut feel?

3. Multi-timeframe validation?
   - Q: Does the signal check alignment across 5min, 15min, 1hr charts?
   - Q: Or is it single-timeframe only (fragile)?

VERDICT TEMPLATE:
✓ Signal generation is sound because: ___
⚠️  Warning: ___
❌ CRITICAL BUG: ___


## SECTION 2: MARKET CONTEXT & REGIME DETECTION
────────────────────────────────────────────────

Review:
1. Regime detection logic
   - Q: Does it correctly identify MEAN_REVERT, STRONG_TREND, CHOPPY, BREAKOUT?
   - Q: What's the confidence level on regime detection?
   - Q: Does it fail gracefully if regime detector is unavailable?

2. Support/Resistance boundary checks
   - Q: Are trades rejected when spot is <1% from support/resistance?
   - Q: Or are traders allowed to trade near boundaries (high reversal risk)?
   - Q: Is boundary logic symmetric (CE near resistance = skip, PE near support = skip)?

3. IV Regime handling
   - Q: High IV: Does it block buying or switch to selling premium?
   - Q: Low IV: Does it recognize options are cheap/edge is low?
   - Q: Is IV rank used or just raw IV level?

VERDICT TEMPLATE:
✓ Regime detection is solid because: ___
⚠️  Warning: ___
❌ CRITICAL BUG: ___


## SECTION 3: PRE-TRADE GATES (Risk Management)
────────────────────────────────────────────────

Review each gate:
1. Position Sizing Gate
   - Q: Does size scale down as expiry approaches?
   - Q: Is there a minimum size (avoid pennies)?
   - Q: Is there a maximum position value vs. capital?
   - Q: Is leverage capped?

2. Stop Loss Gate
   - Q: Is SL calculation static or dynamic (time-aware)?
   - Q: For options <4h to expiry: is SL wider (theta acceleration)?
   - Q: Is SL honored as a hard floor or just a guideline?

3. Daily Loss Cap
   - Q: Does the system track cumulative daily losses?
   - Q: Does it auto-freeze after -Rs 4,000 loss?
   - Q: Is this enforced pre-trade or post-trade?

4. Trade Frequency Cap
   - Q: Max 8 trades/day: Is this enforced?
   - Q: If limit hit at 8:05am: Are no more trades allowed until tomorrow?

5. Capital Preservation
   - Q: Max order value Rs 15,000: Enforced?
   - Q: If order exceeds this: Is it auto-reduced or rejected?

6. Probability Threshold
   - Q: Is there a minimum calibrated win rate (e.g., 55%)?
   - Q: Below threshold: Auto-reject?

VERDICT TEMPLATE:
✓ Risk gates are production-ready because: ___
⚠️  Warning: ___
❌ CRITICAL BUG: ___


## SECTION 4: BROKER ORDER EXECUTION
──────────────────────────────────────

Review the execution flow (BUY order placement):
1. Order placement
   - Q: Is durable intent logged BEFORE calling broker?
   - Q: If system crashes between intent log and broker call: Can it recover?
   - Q: Is order type MARKET or LIMIT? (MARKET = worse fills, LIMIT = no fill risk)

2. Fill confirmation
   - Q: How long does the system wait for fill confirmation?
   - Q: What's the timeout? (45 seconds is industry standard)
   - Q: If timeout: Does it cancel or leave hanging?

3. Partial fills
   - Q: If only 50% of quantity fills: Is the remainder canceled?
   - Q: Or does it try to average into the position?
   - Q: Is partial fill risk documented?

4. Broker authentication
   - Q: If Upstox token expires mid-trade: What happens?
   - Q: Is there automatic re-authentication or does it freeze?

5. Crash recovery
   - Q: If system crashes after fill but before position record: What happens?
   - Q: Is there a reconciliation process?

VERDICT TEMPLATE:
✓ Execution flow is bulletproof because: ___
⚠️  Warning: ___
❌ CRITICAL BUG: ___


## SECTION 5: POSITION PROTECTION (GTT & SL/TARGET MONITORING)
──────────────────────────────────────────────────────────────

Review protective mechanisms:
1. Broker-side SL (GTT - Good Till Triggered order)
   - Q: Is a GTT SELL placed immediately after position creation?
   - Q: If GTT fails: Does system immediately flatten or freeze?
   - Q: Is GTT status checked regularly?

2. Real-time SL/Target monitoring
   - Q: Is SL/target checked on EVERY tick or every 15 seconds?
   - Q: If checked every 15 seconds: Can price gap through SL?
   - Q: Is there exponential backoff if price stalls?

3. Terminal conditions
   - Q: What happens if price hits SL exactly at entry? (edge case)
   - Q: What happens if both SL and target are hit same second? (tie-break logic?)
   - Q: At 15:25 IST: Auto-exit if position still open? (5 min before close)

4. Orphaned positions
   - Q: If monitoring crashes: Are positions at broker left unprotected?
   - Q: Is there a watchdog process that monitors the monitor?

VERDICT TEMPLATE:
✓ Position protection is rock-solid because: ___
⚠️  Warning: ___
❌ CRITICAL BUG: ___


## SECTION 6: EXIT EXECUTION (SELL ORDER)
──────────────────────────────────────────

Review the exit/SELL flow:
1. Exit decision trigger
   - Q: What triggers an exit? (SL, target, time-based, manual?)
   - Q: Is exit decision logged BEFORE execution?

2. Order placement
   - Q: MARKET or LIMIT? (MARKET for guaranteed exit, LIMIT for better price)
   - Q: If MARKET: Do you accept worst-case slippage?

3. Partial exit handling
   - Q: If only 50% exits: What's the recovery logic?
   - Q: Is remaining position tracked separately?

4. Exit confirmation
   - Q: How is exit fill confirmed?
   - Q: If fill unconfirmed: Does it retry or leave position open?

5. P&L calculation
   - Q: Entry price vs. exit price: Is this calculated correctly?
   - Q: Are transaction costs deducted?
   - Q: Is P&L logged to decision_log?

VERDICT TEMPLATE:
✓ Exit execution is reliable because: ___
⚠️  Warning: ___
❌ CRITICAL BUG: ___


## SECTION 7: END-OF-DAY (EOD) HANDLING
────────────────────────────────────────

Review what happens as market close approaches:
1. EOD critical window (15:15 - 15:30 IST)
   - Q: Are new trades blocked after 15:20?
   - Q: At 15:20+: Are ALL open positions force-closed?
   - Q: Is there a 15-minute window to exit without SL?

2. MIS (Intraday) auto-square-off
   - Q: Are all positions MIS product type (intraday)?
   - Q: If not exited by 15:30: Does broker auto-square (take loss)?
   - Q: Is this loss acceptable or is it a disaster?

3. Holiday handling
   - Q: Does the system know which days are market holidays?
   - Q: If trading on a holiday: What happens?

VERDICT TEMPLATE:
✓ EOD handling is bulletproof because: ___
⚠️  Warning: ___
❌ CRITICAL BUG: ___


## SECTION 8: MONITORING & OBSERVABILITY
──────────────────────────────────────────

Review logging and debugging:
1. Decision logging
   - Q: Is every trade decision logged to decision_log.db?
   - Q: Is rejection reason logged (not just "BLOCKED")?
   - Q: Can you replay any trade from logs?

2. P&L tracking
   - Q: Is daily P&L calculated correctly?
   - Q: Are trades reconciled with broker at EOD?
   - Q: What if broker has 3 trades but system shows 2?

3. Error handling
   - Q: If broker API fails: Does system freeze or continue?
   - Q: If LLM is unavailable: Does system fall back to rules?
   - Q: Are all errors logged with full context?

4. Alerts
   - Q: Is there an alert when daily loss cap is hit?
   - Q: When a high-risk trade is blocked?
   - Q: When GTT placement fails?

VERDICT TEMPLATE:
✓ Observability is production-ready because: ___
⚠️  Warning: ___
❌ CRITICAL BUG: ___


## SECTION 9: EDGE CASES & FAILURE SCENARIOS
──────────────────────────────────────────────

Test each scenario (ask if code handles this):

SCENARIO A: Market Gap
├─ Spot jumps from 23400 to 23900 in 1 second
├─ PE position SL = 23100
├─ SL price never touched (gap right through it)
├─ Q: Does SL execute at market or is position orphaned?
└─ RISK: If orphaned → unlimited loss

SCENARIO B: Internet Disconnection
├─ System placed BUY order → got fill
├─ Internet dies before GTT placement
├─ Position is unprotected at broker
├─ Q: When internet returns: Does system auto-place GTT?
└─ RISK: Unprotected position overnight

SCENARIO C: Broker Auth Expires
├─ Upstox token valid at 10:00
├─ Expires at 14:00
├─ System tries to place SL GTT at 14:05
├─ Q: Does it auto-refresh token or freeze?
└─ RISK: SL not placed, position unprotected

SCENARIO D: LLM Hallucination
├─ LLM says "EXECUTE" with 95% confidence
├─ But signal is actually contradictory to all market data
├─ Q: Are there reality checks? (Regime, support/resistance, PCR)
└─ RISK: Trades on completely wrong signals

SCENARIO E: Partial Fill on Huge Gap
├─ Buy 5 lots: Only 1 lot fills
├─ System records 5-lot position
├─ SL is for 5 lots but only 1 is covered
├─ Q: Does system auto-cancel unfilled quantity?
└─ RISK: Position/GTT mismatch

SCENARIO F: Duplicate Order
├─ System places BUY
├─ No fill confirmation received (timeout)
├─ System retry-places BUY (now 2x order)
├─ Q: Is there idempotency check?
└─ RISK: Double position size

VERDICT TEMPLATE (for EACH scenario):
✓ Scenario X is handled correctly because: ___
⚠️  Warning on Scenario X: ___
❌ CRITICAL FAILURE on Scenario X: ___


## SECTION 10: BACKTESTING & VALIDATION
─────────────────────────────────────────

Ask about testing:
1. Has this system been backtested?
   - Q: On how many historical trades?
   - Q: What was the win rate?
   - Q: Was the backtest overfitted?

2. Paper trading
   - Q: Has it run in paper mode for >100 trades?
   - Q: What was the actual vs. expected win rate?
   - Q: Any major discrepancies?

3. Live micro testing
   - Q: Has it traded real money (small size)?
   - Q: For how long?
   - Q: What was the real win rate?

4. Known limitations
   - Q: Are there documented scenarios where the system fails?
   - Q: Is there a maximum drawdown threshold that triggers shutdown?

VERDICT TEMPLATE:
✓ Validation is sufficient for live trading because: ___
⚠️  Warning: Need more testing on ___
❌ CRITICAL: Cannot deploy to live yet because: ___


═══════════════════════════════════════════════════════════════════════════════
FINAL ASSESSMENT
═══════════════════════════════════════════════════════════════════════════════

After reviewing all 10 sections, provide:

1. CONFIDENCE SCORE (0-100)
   Is this system production-ready?
   
2. TOP 3 STRENGTHS
   What's working well?
   
3. TOP 5 RISKS/BUGS
   What could cause losses?
   
4. BLOCKERS FOR LIVE TRADING
   What must be fixed before going live?
   
5. RECOMMENDATIONS (Ranked by Impact)
   What should be done first?
   
6. ESTIMATED PROBABILITY OF PROFITABILITY
   Based on the logic and implementation:
   - Is it likely to make money?
   - What's the risk of catastrophic loss?
   - What's the realistic Sharpe ratio?

═══════════════════════════════════════════════════════════════════════════════
REPORTING FORMAT
═══════════════════════════════════════════════════════════════════════════════

For each section, use this exact format:

### [SECTION X]: [TITLE]

**Status**: ✓ PASS | ⚠️ WARNING | ❌ FAIL

**Findings**:
- Finding 1: Details
- Finding 2: Details

**Verdict**:
[1-2 sentence summary]

**Action Items** (if any):
1. [What to fix]
2. [Priority]
3. [Effort]

───────────────────────────────────────────────────────────────────────────────

At the end, provide an EXECUTIVE SUMMARY (2-3 paragraphs):
- Can this go live? Why/why not?
- What's the biggest risk?
- What would make you 90% confident?

═══════════════════════════════════════════════════════════════════════════════
"""

if __name__ == "__main__":
    print(AUDIT_PROMPT)
    print("\n" + "="*80)
    print("HOW TO USE THIS PROMPT")
    print("="*80)
    print("""
1. Copy the entire AUDIT_PROMPT text above

2. Paste it into an AI agent with access to your codebase:
   - Claude (claude.ai)
   - ChatGPT (openai.com)
   - Gemini (google.com)
   - Local LLM (llama.cpp, ollama)

3. Add this instruction:
   "Here is my options trading agent codebase. Review it using the 
    comprehensive audit checklist provided. Be brutally honest about 
    any bugs, design flaws, or production readiness issues."

4. Then provide these files:
   - brain/lean_fo_brain.py (main analysis engine)
   - brain/pre_trade_gatekeeper.py (risk gates)
   - execution/lean_fo_executor.py (order placement)
   - execution/exit_manager.py (position monitoring/exit)
   - execution/exit_ticker.py (real-time SL/target)
   - brain/position_tracker.py (position state)
   - brain/regime_detector.py (market regime)
   - data_feeds/fo_data_feed.py (market data)
   - memory/decision_log.py (trade logging)
   
5. Get the audit report

6. Fix critical issues
   
7. Re-run audit after fixes

═══════════════════════════════════════════════════════════════════════════════
TIPS FOR BEST RESULTS
═══════════════════════════════════════════════════════════════════════════════

1. Use a capable model (Claude 3+, GPT-4, or Gemini Ultra)
   - Cheaper models may miss subtle bugs

2. Provide complete file context
   - Don't summarize; let AI read full code

3. Ask follow-up questions
   - "Can you explain the risk in Scenario B more?"
   - "What's the line-by-line fix for that bug?"

4. Cross-check with multiple models
   - Run audit with Claude AND GPT-4
   - Compare their findings
   - If both flag same issue: It's definitely a problem

5. After fixes, re-run audit
   - Don't just fix one issue
   - Fix all critical issues together
   - Then re-run audit to verify

═══════════════════════════════════════════════════════════════════════════════
""")
