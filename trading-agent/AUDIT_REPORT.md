# Independent system audit — 2026-05-12

**Scope:** F&O-first trading agent (Upstox, LLM, regime, OI/VWAP, theta/ATR exits, reconciliation, startup recovery). **Not** a guarantee of profitability.

**Tests run:** `./venv/bin/python -m pytest tests/ -q` → **36 passed**, **2 failed** (pre-existing: `test_equity.py` import error, `test_upstox_connection.py` TypeError).

---

## Executive summary

| Dimension | Verdict |
|-----------|---------|
| **Shadow / paper learning loop** | Reasonable for experimentation; P&L not comparable to live. |
| **Directional single-leg live path** | Substantially hardened: transactional entry, GTT cancel verified before sell, fill checks, orphan handling, post-market ordering. |
| **“Elite” edge / real-time profits** | **Not established.** Stronger **process** (regime, OI/VWAP filters, dynamic exits) does not equal proven edge; no multi-year walk-forward, no live slippage/margin model. |
| **EXECUTE_SPREAD (Phase D)** | **Analysis-only today.** Scheduler and `run_agent` only call `maybe_execute_lean_fo_order` when `decision == "EXECUTE"`. **`EXECUTE_SPREAD` never reaches a broker executor**; no spread rows in exit manager. |
| **Spread risk path** | `_try_spread_strategy` **bypasses** `RiskGates` and **`_dual_model_execute_gate`** vs directional flow. |

**Readiness (opinion):** **Shadow:** good. **Micro-live (1-lot, supervised):** plausible for **single-leg** after you validate broker responses in production. **Full unattended live:** still **not** recommended until spread execution is either removed or fully implemented and gated.

---

## End-to-end traces (evidence-based)

### 1. Directional entry (`EXECUTE`)

- **`LeanFOBrain.analyze()`** → `_should_trade` (regime, IV, OI/VWAP divergence, consensus) → `_generate_signal` → **`RiskGates`** → **`_dual_model_execute_gate`** (Proxima) → `EXECUTE` / `BLOCKED`.
- **`maybe_execute_lean_fo_order`** ([`execution/lean_fo_executor.py`](execution/lean_fo_executor.py)): intent → BUY → **`PLACED` + `broker_order_id` persisted immediately after successful place** (fix applied 2026-05-12 for crash recovery) → wait for fill → local `OpenPosition` → SL GTT → GTT failure: live flatten / micro-live freeze.

### 2. Spread path (`EXECUTE_SPREAD`)

- **`_try_spread_strategy`** ([`brain/lean_fo_brain.py`](brain/lean_fo_brain.py)): runs when `_should_trade` is false **and** regime is `range_bound` / `low_vol_grind`. Builds `spread_signal`, sets `decision = "EXECUTE_SPREAD"`, logs scan — **no** `RiskGates`, **no** dual-model gate.
- **`run_fo_scan`** ([`scheduler.py`](scheduler.py) ~239–258): executes broker only if `decision == "EXECUTE"`. **`EXECUTE_SPREAD` is ignored** (falls through to print-only branch).
- **`scan_fo`** ([`run_agent.py`](run_agent.py) ~154–161): same — only `EXECUTE` triggers execution.
- **`exit_manager`:** no `spread` / second-leg handling; positions are single-leg `OpenPosition` shape.

**Conclusion:** Phase D is **signal generation + telemetry**, not **live spread trading**.

### 3. Exits

- **`check_and_exit_positions`** → `should_exit(pnl_pct, highest, hours_held, current_hour_ist, atr_pct)` with ATR from `get_spot_atr` ([`execution/exit_manager.py`](execution/exit_manager.py)).
- **GTT:** `_cancel_one_gtt` / `_handle_gtt_before_exit` block SELL when cancel cannot be verified ([`execution/exit_manager.py`](execution/exit_manager.py)).

### 4. Startup reconciliation

- **`startup_reconciliation`** ([`execution/reconciliation.py`](execution/reconciliation.py)): pending intents (via `pending_intents`: `SUBMITTED`, `PENDING`, `PLACED`), broker `reconcile_state`, stale open positions from prior calendar day → may freeze.
- **Gap (documented):** On **`FILLED_ON_RECOVERY`**, intent is updated but **no automatic `OpenPosition` + GTT** is created — operator must reconcile manually or rely on `reconcile_state` / orphans path.

### 5. Intent recovery fix (2026-05-12)

- **Issue:** Intent was `SUBMITTED` with `broker_order_id=None` until terminal `FILLED`; a crash after order placement but before fill persistence made startup treat rows as `REJECTED_NO_OID`.
- **Fix:** After successful `place_fo_order`, **`update_intent_status(..., "PLACED", broker_order_id=oid, ...)`** so `pending_intents()` can recover via `get_order_status`.

---

## Top 5 strengths

1. Explicit **trading modes** and **kill switch** / freeze patterns.
2. **Transactional** single-leg live entry ordering (intent → place → persist order id → fill → position → GTT).
3. **GTT cancel verification** before agent SELL (reduces double-sell class).
4. **Granular reconciliation** and orphan flatten attempts with fill awareness (when wired).
5. **Regime + OI/VWAP + dynamic exits** improve *decision hygiene* vs naïve fixed-% only.

## Top 5 blockers for “elite / unattended profit”

1. **`EXECUTE_SPREAD` not executed**; spread path skips risk + dual gate.
2. **No proof of edge** (out-of-sample, live costs, margin, assignment).
3. **Startup fill recovery** does not rebuild position + GTT automatically.
4. **Equity / connection tests failing** — hygiene signal for CI and imports.
5. **Operational dependency:** token, process uptime, laptop sleep — infrastructure not “exchange-grade.”

---

## Recommendations (priority)

1. **Either** wire `maybe_execute_spread_order` + spread position model + exits **or** stop emitting `EXECUTE_SPREAD` in live/micro-live until then.
2. Run **`RiskGates` + dual gate** on spread proposals if they remain.
3. Extend startup recovery: on confirmed fill from `PLACED` intent, create position + GTT or enqueue manual review with loud alert.
4. Fix **equity test import** and **Upstox connection test** for clean CI.
5. Paper/shadow: track **slippage** and **fees** assumptions before trusting P&L.

---

*Generated as an internal engineering audit. Not financial advice.*
