#!/usr/bin/env python3
"""
TRADING AGENT DASHBOARD

Real-time Streamlit dashboard showing:
- Agent status and market conditions
- Open positions with P&L
- Trade history and performance
- Signal activity
- Risk metrics

Run: streamlit run dashboard.py
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta
import pytz

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Page config
st.set_page_config(
    page_title="Trading Agent Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

IST = pytz.timezone('Asia/Kolkata')


def get_market_status():
    """Get current market status."""
    now = datetime.now(IST)
    
    if now.weekday() >= 5:
        return "🔴 CLOSED (Weekend)", False
    
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    if now < market_open:
        return f"🟡 Pre-Market (opens in {(market_open - now).seconds // 60} mins)", False
    elif now > market_close:
        return "🔴 CLOSED", False
    else:
        return "🟢 OPEN", True


def load_signal_tracker_data(days=7):
    """Load data from signal tracker."""
    db_path = os.path.join(os.path.dirname(__file__), "data_cache", "signal_tracker.db")
    if not os.path.exists(db_path):
        return None
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    
    df = pd.read_sql_query(f"""
        SELECT * FROM scans 
        WHERE timestamp > '{cutoff}'
        ORDER BY timestamp DESC
    """, conn)
    
    conn.close()
    return df


def load_decision_log(days=30):
    """Load trade decisions."""
    db_path = os.path.join(os.path.dirname(__file__), "data_cache", "decisions.db")
    if not os.path.exists(db_path):
        return None
    
    conn = sqlite3.connect(db_path)
    
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    
    df = pd.read_sql_query(f"""
        SELECT * FROM decisions 
        WHERE timestamp > '{cutoff}'
        ORDER BY timestamp DESC
    """, conn)
    
    conn.close()
    return df


def load_positions():
    """Load open positions."""
    # Try new table first
    db_path = os.path.join(os.path.dirname(__file__), "data_cache", "positions.db")
    if not os.path.exists(db_path):
        return None
    
    conn = sqlite3.connect(db_path)
    
    try:
        df = pd.read_sql_query("SELECT * FROM positions ORDER BY entry_time DESC", conn)
    except:
        df = pd.read_sql_query("SELECT * FROM positions_v2 ORDER BY entry_time DESC", conn)
    
    conn.close()
    return df


def load_runtime_safety():
    """Load runtime_safety.json written by execution/runtime_safety.py."""
    path = os.path.join(os.path.dirname(__file__), "data_cache", "runtime_safety.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_reconciliation_state():
    path = os.path.join(os.path.dirname(__file__), "data_cache", "reconciliation_state.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_trading_freeze():
    path = os.path.join(os.path.dirname(__file__), "data_cache", "trading_freeze.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_agent_health():
    path = os.path.join(os.path.dirname(__file__), "data_cache", "agent_health.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _parse_timestamp_series(s: pd.Series) -> pd.Series:
    """
    Parse timestamps from SQLite / logs. One column may mix naive ISO and tz-aware
    strings; vectorized to_datetime(..., format="mixed") raises on mixed offsets, so
    we parse per value and normalize to IST for consistent .dt.date / charts.
    """
    def _one(x) -> pd.Timestamp:
        if x is None:
            return pd.NaT
        try:
            if isinstance(x, float) and pd.isna(x):
                return pd.NaT
        except (ValueError, TypeError):
            pass
        if isinstance(x, str) and not x.strip():
            return pd.NaT
        ts = pd.to_datetime(x, errors="coerce", format="mixed")
        if pd.isna(ts):
            return pd.NaT
        if ts.tzinfo is None:
            return ts.tz_localize(IST)
        return ts.tz_convert(IST)

    return s.map(_one)


def load_supervisor_alert_tail(max_lines: int = 5):
    path = os.path.join(os.path.dirname(__file__), "data_cache", "agent_supervisor_alert.txt")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [ln.strip() for ln in lines[-max_lines:] if ln.strip()]
    except OSError:
        return []


def last_scan_timestamp():
    """Most recent F&O scan from signal_tracker (if available)."""
    db_path = os.path.join(os.path.dirname(__file__), "data_cache", "signal_tracker.db")
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT MAX(timestamp) FROM scans WHERE symbol IN ('NIFTY','BANKNIFTY')"
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except sqlite3.Error:
        return None


def load_order_intents_df():
    """Recent broker order intents (SQLite)."""
    path = os.path.join(os.path.dirname(__file__), "data_cache", "order_intents.db")
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path)
        df = pd.read_sql_query(
            "SELECT * FROM order_intents ORDER BY created_at DESC LIMIT 50", conn
        )
        conn.close()
        return df
    except Exception:
        return None


def load_calibration():
    """Load calibration data."""
    config_path = os.path.join(os.path.dirname(__file__), "data_cache", "calibration.json")
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            return json.load(f)
    return {}


# =============================================================================
# SIDEBAR
# =============================================================================

with st.sidebar:
    st.title("🤖 Trading Agent")
    
    # Market status
    market_status, is_open = get_market_status()
    st.metric("Market", market_status)
    
    # Current time
    now = datetime.now(IST)
    st.metric("IST Time", now.strftime("%H:%M:%S"))
    
    st.divider()
    
    # Quick stats
    st.subheader("Quick Stats")
    
    signals_df = load_signal_tracker_data(days=1)
    if signals_df is not None and len(signals_df) > 0:
        total_scans = len(signals_df)
        execute_count = len(signals_df[signals_df['final_decision'] == 'EXECUTE'])
        signal_rate = execute_count / total_scans * 100 if total_scans > 0 else 0
        
        col1, col2 = st.columns(2)
        col1.metric("Scans Today", total_scans)
        col2.metric("Signals", execute_count)
        st.metric("Signal Rate", f"{signal_rate:.1f}%")
    else:
        st.info("No scan data yet")
    
    st.divider()
    
    # Refresh
    if st.button("🔄 Refresh", use_container_width=True):
        st.rerun()
    
    # Auto refresh
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)
    if auto_refresh:
        import time
        time.sleep(30)
        st.rerun()

    st.divider()
    st.subheader("Agent health")
    health = load_agent_health()
    last_scan = last_scan_timestamp()
    if last_scan:
        st.caption(f"Last F&O scan row: {last_scan}")
    if health.get("updated_at"):
        try:
            hb = datetime.fromisoformat(health["updated_at"])
            if hb.tzinfo is None:
                hb = IST.localize(hb)
            age_m = (datetime.now(IST) - hb.astimezone(IST)).total_seconds() / 60.0
            st.metric("Heartbeat age (min)", f"{age_m:.1f}")
            st.caption(f"PID {health.get('pid', '?')} — {health.get('note', '')}")
            _, mopen = get_market_status()
            if mopen and age_m > 15:
                st.error("Agent may be dead: no heartbeat > 15 min during market hours")
            elif age_m > 15:
                st.warning("Heartbeat stale (>15 min) — ok if market closed")
        except ValueError:
            st.write(health)
    else:
        st.info("No agent_health.json (run_agent / scheduler / supervised wrapper)")
    alerts = load_supervisor_alert_tail()
    if alerts:
        st.warning("Supervisor alerts (latest):\n" + "\n".join(alerts[-3:]))


# =============================================================================
# MAIN CONTENT
# =============================================================================

st.title("📈 AI Trading Agent Dashboard")

# Top metrics row
col1, col2, col3, col4 = st.columns(4)

positions_df = load_positions()
decisions_df = load_decision_log(days=7)

# Calculate metrics
if positions_df is not None and len(positions_df) > 0:
    open_positions = len(positions_df[positions_df['status'] == 'OPEN'])
    open_pnl = positions_df[positions_df['status'] == 'OPEN']['current_pnl_rs'].sum() if 'current_pnl_rs' in positions_df.columns else 0
    
    closed = positions_df[positions_df['status'] != 'OPEN']
    realized_pnl = closed['current_pnl_rs'].sum() if len(closed) > 0 and 'current_pnl_rs' in closed.columns else 0
    
    col1.metric("Open Positions", open_positions)
    col2.metric("Open P&L", f"₹{open_pnl:+,.0f}", delta_color="normal")
    col3.metric("Realized P&L", f"₹{realized_pnl:+,.0f}", delta_color="normal")
else:
    col1.metric("Open Positions", 0)
    col2.metric("Open P&L", "₹0")
    col3.metric("Realized P&L", "₹0")

if decisions_df is not None and len(decisions_df) > 0:
    total_trades = len(decisions_df[decisions_df['decision_type'] == 'trade_entry'])
    col4.metric("Total Trades (7d)", total_trades)
else:
    col4.metric("Total Trades (7d)", 0)

st.divider()

# =============================================================================
# TABS
# =============================================================================

tab0, tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["Live safety", "Positions", "Signals", "Trade history", "Performance", "Settings"]
)

# -----------------------------------------------------------------------------
# TAB 0: Live safety
# -----------------------------------------------------------------------------
with tab0:
    st.subheader("Runtime safety & broker reconciliation")
    rs = load_runtime_safety()
    if rs:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Mode", rs.get("mode", "—"))
        c2.metric("Broker orders", "ON" if rs.get("broker_orders_allowed") else "OFF")
        c3.metric("Token OK", "yes" if rs.get("token_valid") else "no")
        c4.metric("Reconciliation", "OK" if rs.get("reconciliation_ok") else "check")
        if rs.get("reasons_blocked"):
            st.error("Blocked: " + "; ".join(rs["reasons_blocked"]))
        st.json(rs)
    else:
        st.info("No runtime_safety.json yet — start `python run_agent.py` or `python scheduler.py` once.")

    fz = load_trading_freeze()
    if fz.get("frozen"):
        st.warning(f"TRADING FROZEN: {fz.get('reason')} ({fz.get('at')})")
    rec = load_reconciliation_state()
    if rec:
        st.subheader("Last reconciliation report")
        st.json(rec)

    st.subheader("Recent order intents")
    odf = load_order_intents_df()
    if odf is not None and len(odf) > 0:
        st.dataframe(odf, use_container_width=True, hide_index=True)
    else:
        st.caption("No order intents logged yet.")

    try:
        from mcp_server.upstox_client import get_upstox_client

        c = get_upstox_client()
        st.subheader("Token (Upstox)")
        st.json(c.get_token_expiry_summary())
    except Exception as e:
        st.caption(f"Token summary unavailable: {e}")

# -----------------------------------------------------------------------------
# TAB 1: Positions
# -----------------------------------------------------------------------------
with tab1:
    st.subheader("Open Positions")
    
    if positions_df is not None and len(positions_df) > 0:
        open_pos = positions_df[positions_df['status'] == 'OPEN'].copy()
        
        if len(open_pos) > 0:
            # Format for display
            display_cols = ['symbol', 'strike', 'option_type', 'entry_price', 'current_price', 'current_pnl_pct', 'current_pnl_rs', 'status']
            available_cols = [c for c in display_cols if c in open_pos.columns]
            
            # Add color coding
            def color_pnl(val):
                if isinstance(val, (int, float)):
                    color = 'green' if val > 0 else 'red' if val < 0 else 'gray'
                    return f'color: {color}'
                return ''
            
            st.dataframe(
                open_pos[available_cols],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "current_pnl_pct": st.column_config.NumberColumn("P&L %", format="%.1f%%"),
                    "current_pnl_rs": st.column_config.NumberColumn("P&L ₹", format="₹%.0f"),
                    "entry_price": st.column_config.NumberColumn("Entry", format="₹%.1f"),
                    "current_price": st.column_config.NumberColumn("Current", format="₹%.1f"),
                }
            )
        else:
            st.info("No open positions")
        
        # Closed positions
        st.subheader("Recently Closed")
        closed_pos = positions_df[positions_df['status'] != 'OPEN'].head(10)
        
        if len(closed_pos) > 0:
            st.dataframe(closed_pos, use_container_width=True, hide_index=True)
        else:
            st.info("No closed positions yet")
    else:
        st.info("No position data available")

# -----------------------------------------------------------------------------
# TAB 2: Signals
# -----------------------------------------------------------------------------
with tab2:
    st.subheader("Signal Activity")
    
    signals_df = load_signal_tracker_data(days=7)
    
    if signals_df is not None and len(signals_df) > 0:
        # Decision breakdown chart
        col1, col2 = st.columns(2)
        
        with col1:
            decision_counts = signals_df['final_decision'].value_counts()
            fig = px.pie(
                values=decision_counts.values,
                names=decision_counts.index,
                title="Decision Breakdown",
                color_discrete_sequence=['#28a745', '#ffc107', '#dc3545']
            )
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            # Rejection reasons
            if 'rejection_reason' in signals_df.columns:
                rejections = signals_df[signals_df['rejection_reason'].notna()]['rejection_reason'].value_counts().head(5)
                if len(rejections) > 0:
                    rej_df = pd.DataFrame({'reason': rejections.index, 'count': rejections.values})
                    fig = px.bar(
                        rej_df,
                        x='count',
                        y='reason',
                        orientation='h',
                        title="Top Rejection Reasons",
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No rejections yet")
        
        # Daily activity
        st.subheader("Daily Scan Activity")
        signals_df['date'] = _parse_timestamp_series(signals_df['timestamp']).dt.date
        daily = signals_df.groupby(['date', 'final_decision']).size().unstack(fill_value=0)
        
        if len(daily) > 0:
            daily_reset = daily.reset_index()
            daily_melted = daily_reset.melt(id_vars='date', var_name='decision', value_name='count')
            fig = px.bar(
                daily_melted,
                x='date',
                y='count',
                color='decision',
                barmode='stack',
                title="Scans by Day",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No daily activity data")
        
        # Recent scans table
        st.subheader("Recent Scans")
        recent_cols = [
            'timestamp',
            'symbol',
            'trend',
            'signal_strength',
            'final_decision',
            'rejection_reason',
            'blocked_by_gate',
        ]
        recent = signals_df.head(20)[[c for c in recent_cols if c in signals_df.columns]]
        st.dataframe(recent, use_container_width=True, hide_index=True)
    else:
        st.info("No signal data available")

# -----------------------------------------------------------------------------
# TAB 3: Trade History
# -----------------------------------------------------------------------------
with tab3:
    st.subheader("Trade History")
    
    if decisions_df is not None and len(decisions_df) > 0:
        # Filter to trade entries
        trades = decisions_df[decisions_df['decision_type'] == 'trade_entry'].copy()
        
        if len(trades) > 0:
            # Summary metrics
            col1, col2, col3 = st.columns(3)
            
            wins = len(trades[trades['pnl'] > 0]) if 'pnl' in trades.columns else 0
            losses = len(trades[trades['pnl'] < 0]) if 'pnl' in trades.columns else 0
            total_pnl = trades['pnl'].sum() if 'pnl' in trades.columns else 0
            
            col1.metric("Wins", wins)
            col2.metric("Losses", losses)
            col3.metric("Total P&L", f"₹{total_pnl:+,.0f}")
            
            # P&L over time
            if 'pnl' in trades.columns and 'timestamp' in trades.columns:
                trades['date'] = _parse_timestamp_series(trades['timestamp']).dt.date
                daily_pnl = trades.groupby('date')['pnl'].sum().cumsum()
                
                fig = px.line(
                    x=daily_pnl.index,
                    y=daily_pnl.values,
                    title="Cumulative P&L",
                    labels={'x': 'Date', 'y': 'P&L (₹)'}
                )
                fig.add_hline(y=0, line_dash="dash", line_color="gray")
                st.plotly_chart(fig, use_container_width=True)
            
            # Trade table
            display_cols = ['timestamp', 'symbol', 'action', 'strike', 'option_type', 'entry_price', 'outcome', 'pnl']
            available_cols = [c for c in display_cols if c in trades.columns]
            st.dataframe(trades[available_cols].head(50), use_container_width=True, hide_index=True)
        else:
            st.info("No trades recorded yet")
    else:
        st.info("No trade data available")

# -----------------------------------------------------------------------------
# TAB 4: Performance
# -----------------------------------------------------------------------------
with tab4:
    st.subheader("Performance Analytics")
    
    if decisions_df is not None and len(decisions_df) > 0:
        trades = decisions_df[decisions_df['decision_type'] == 'trade_entry'].copy()
        
        if len(trades) > 0 and 'pnl' in trades.columns:
            col1, col2 = st.columns(2)
            
            with col1:
                # Win rate by symbol
                if 'symbol' in trades.columns:
                    symbol_stats = trades.groupby('symbol').agg({
                        'pnl': ['count', 'sum', lambda x: (x > 0).sum() / len(x) * 100]
                    }).round(1)
                    symbol_stats.columns = ['Trades', 'P&L', 'Win Rate %']
                    st.write("**Performance by Symbol**")
                    st.dataframe(symbol_stats)
            
            with col2:
                # Win rate by trend
                if 'trend' in trades.columns:
                    trend_stats = trades.groupby('trend').agg({
                        'pnl': ['count', 'sum']
                    }).round(1)
                    trend_stats.columns = ['Trades', 'P&L']
                    st.write("**Performance by Trend**")
                    st.dataframe(trend_stats)
            
            # P&L distribution
            fig = px.histogram(
                trades,
                x='pnl',
                nbins=20,
                title="P&L Distribution",
                labels={'pnl': 'P&L (₹)', 'count': 'Frequency'}
            )
            fig.add_vline(x=0, line_dash="dash", line_color="red")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Not enough trade data for analytics")
    else:
        st.info("No performance data available")
    
    # Calibration data
    st.subheader("Agent Calibration")
    calibration = load_calibration()
    
    if calibration:
        cal_df = pd.DataFrame([
            {
                'Symbol': symbol,
                'Min Confidence': data.get('min_confidence', 0.55),
                'Min Signal': data.get('min_signal_strength', 0.55),
                'Max IV': data.get('max_iv_for_buying', 30),
                'Trades': data.get('total_trades', 0),
                'P&L': data.get('total_pnl', 0),
            }
            for symbol, data in calibration.items()
        ])
        st.dataframe(cal_df, use_container_width=True, hide_index=True)
    else:
        st.info("No calibration data yet")

# -----------------------------------------------------------------------------
# TAB 5: Settings
# -----------------------------------------------------------------------------
with tab5:
    st.subheader("Agent Configuration")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**Risk Guardrails (Options)**")
        st.json({
            "Max Position %": "70%",
            "Max Trade Value": "₹15,000",
            "Max Daily Loss": "₹4,000",
            "Stop Loss": "30%",
            "Intraday Only": True,
            "Exit Time": "3:10 PM IST",
        })
    
    with col2:
        st.write("**Trading Hours**")
        st.json({
            "Market Open": "9:15 AM IST",
            "Market Close": "3:30 PM IST",
            "No Trade First": "15 mins",
            "No Trade Last": "20 mins",
            "Expiry Day Exit": "2:30 PM IST",
        })
    
    st.divider()
    
    st.write("**Symbols Being Tracked**")
    st.write("F&O: NIFTY, BANKNIFTY")
    st.write("Equity: NIFTY 50 stocks (dynamic from Upstox)")
    
    st.divider()
    
    st.write("**Smart Exit Logic**")
    st.code("""
INTRADAY EXIT at 3:10 PM → Forced (no overnight holding)
DOWN 30%               → STOP_LOSS (hard limit)
UP 50%+                → EXCELLENT_PROFIT (exit)
UP 35%+                → GOOD_PROFIT (exit)
UP 25%+                → FIRST_TARGET (partial exit)
Peak - 10%             → TRAILING_STOP (protect gains)
Otherwise              → HOLD
    """)


# =============================================================================
# FOOTER
# =============================================================================

st.divider()
st.caption(f"Last updated: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')} | Trading Agent v2.0")
