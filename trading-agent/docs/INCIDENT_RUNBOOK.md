# Incident & recovery runbook

## Trading freeze (`data_cache/trading_freeze.json`)

**Symptom:** Agent logs `Trading freeze` or dashboard shows frozen state.

**Common causes:**
- Reconciliation mismatch streak (local open positions vs broker F&O positions) reached threshold.
- Manual freeze via `TRADING_KILL_SWITCH=1` or `TRADING_ENABLED=false`.

**Recovery:**
1. Inspect `data_cache/reconciliation_state.json` and broker positions in Upstox app.
2. Resolve drift (close ghost positions in app or fix local DB after confirming broker truth).
3. Delete `data_cache/trading_freeze.json` **only after** root cause is fixed.
4. Optionally reset `data_cache/reconciliation_mismatch_count.json` to `{"count": 0}`.

## Token failure

**Symptom:** `Token expired`, `not_authenticated`, Upstox API errors mentioning invalid token.

**Recovery:**
1. Run `python main.py --auth` (or your documented auth entrypoint) before market open.
2. For servers without a browser, keep `TRADING_NON_INTERACTIVE=1` and refresh token out-of-band, then deploy the stored token DB.

## Repeated order rejects

**Symptom:** `order_intents` rows with `REJECTED`, broker_response errors.

**Recovery:**
1. Check `instrument_key` resolution (option chain must include `instrument_key` from Upstox).
2. Verify margin, product type (`I` intraday vs `D` NRML), and segment enablement on the Upstox account.

## Daily loss / risk lockout

**Symptom:** `risk_lock_reason` in runtime safety state; `risk_audit.logl` entries.

**Recovery:**
1. Review `data_cache/risk_audit.logl` and decision log PnL for the session.
2. Wait for next IST session after limits reset, or adjust env caps (`MAX_OPEN_POSITIONS`, `MAX_CONSECUTIVE_LOSSES`) only if you accept higher risk.

## Equity live path

Equity broker orders run only when **all** hold:
- `TRADING_MODE` is `micro_live` or `live`
- `EQUITY_LIVE_ENABLED=1`
- Runtime safety + reconciliation + risk checks pass
- `TradingBrain(paper_mode=False)`

Otherwise execution remains blocked or simulated.
