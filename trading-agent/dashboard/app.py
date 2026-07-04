"""
Real-time Streamlit Dashboard for the AI Trading Agent.
Monitors trades, holdings, P&L, and agent reasoning.
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv

# Add parent path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.schema import init_database
from database.operations import (
    get_recent_trades,
    get_recent_logs,
    get_latest_portfolio,
    get_todays_trades,
    get_all_holdings,
    calculate_todays_pnl,
    get_win_rate,
    get_portfolio_history,
)
from agent.market_hours import get_market_checker

load_dotenv()

# Initialize database
init_database()

# Page configuration
st.set_page_config(
    page_title="AI Trading Agent Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS with improved visibility
st.markdown("""
<style>
    /* Main theme - Bright & Clean */
    .stApp {
        background-color: #f8f9fa;
    }
    
    h1, h2, h3, h4, h5, h6 {
        color: #1a1a1a !important;
        font-weight: 700 !important;
    }
    
    /* Text visibility */
    p, label, span, div {
        color: #2d2d2d !important;
    }
    
    /* Metric cards - Bright with borders */
    .metric-card {
        background: linear-gradient(135deg, #ffffff 0%, #f0f2f5 100%);
        border: 2px solid #007bff;
        border-radius: 12px;
        padding: 20px;
        margin: 10px 0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    
    .metric-value {
        font-size: 36px;
        font-weight: bold;
        color: #007bff;
    }
    
    .metric-label {
        font-size: 14px;
        color: #555555;
        text-transform: uppercase;
        letter-spacing: 1px;
        font-weight: 600;
    }
    
    .positive { color: #28a745 !important; font-weight: bold; }
    .negative { color: #dc3545 !important; font-weight: bold; }
    
    /* Agent log styling */
    .agent-log {
        background: #ffffff;
        border-left: 5px solid #007bff;
        border: 1px solid #dee2e6;
        padding: 15px;
        margin: 10px 0;
        border-radius: 6px;
        font-family: 'Monaco', 'Courier New', monospace;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    
    .agent-log-timestamp {
        color: #666666;
        font-size: 12px;
        font-weight: 600;
    }
    
    .agent-log-action {
        color: #007bff;
        font-weight: bold;
        font-size: 13px;
    }
    
    /* Status indicators */
    .status-open { 
        background: #28a745; 
        color: white;
        padding: 6px 14px;
        border-radius: 20px;
        font-weight: bold;
        display: inline-block;
    }
    
    .status-closed { 
        background: #dc3545; 
        color: white;
        padding: 6px 14px;
        border-radius: 20px;
        font-weight: bold;
        display: inline-block;
    }
    
    /* Subheader visibility */
    .stSubheader {
        color: #1a1a1a !important;
        font-weight: 700 !important;
    }
    
    /* Caption clarity */
    .stCaption {
        color: #555555 !important;
        font-size: 13px !important;
    }
    
    /* Divider */
    hr {
        border: 1px solid #dee2e6;
    }
    
    /* Info/Warning boxes */
    .stAlert {
        border-radius: 8px;
    }
    
    /* Table styling */
    .dataframe {
        background: #ffffff !important;
        color: #2d2d2d !important;
    }
    
    /* Scrolling log container */
    .log-container {
        max-height: 500px;
        overflow-y: auto;
        padding: 12px;
        background: #ffffff;
        border: 2px solid #dee2e6;
        border-radius: 8px;
    }
</style>
""", unsafe_allow_html=True)


def get_trading_mode():
    """Get current trading mode from environment."""
    mode = os.getenv("TRADING_MODE", "paper").lower()
    return "📝 PAPER" if mode == "paper" else "🔴 LIVE"


def render_header():
    """Render the dashboard header."""
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        st.title("🤖 AI Trading Agent")
        st.caption("Autonomous Quantitative Trading System")
    
    with col2:
        mode = get_trading_mode()
        st.markdown(f"### Mode: {mode}")
    
    with col3:
        market_checker = get_market_checker()
        status = market_checker.get_status_display()
        if status["is_trading_hours"]:
            st.markdown('<span class="status-open">MARKET OPEN</span>', unsafe_allow_html=True)
        else:
            st.markdown(f'<span class="status-closed">MARKET CLOSED</span>', unsafe_allow_html=True)
        st.caption(status["message"])


def render_agent_status():
    """Render the current agent status and state."""
    st.subheader("⚡ Agent Status")
    
    market_checker = get_market_checker()
    status = market_checker.get_status_display()
    
    # Display agent state with color coding
    state_col, time_col = st.columns(2)
    
    with state_col:
        state_text = status.get("status", "Unknown")
        if "waiting" in state_text.lower():
            st.warning(f"🟡 {state_text}")
        elif "trading" in state_text.lower() or "active" in state_text.lower():
            st.success(f"🟢 {state_text}")
        elif "closed" in state_text.lower():
            st.info(f"⚪ {state_text}")
        else:
            st.info(f"🔵 {state_text}")
    
    with time_col:
        st.info(f"🕐 {status['current_time_ist']}")
    
    # Display latest log entry to show recent activity
    latest_logs = get_recent_logs(1)
    if latest_logs:
        latest = latest_logs[0]
        activity = f"Last Activity: {latest.timestamp.strftime('%H:%M:%S')} - {latest.action_taken or 'ANALYZING'}"
        st.caption(activity)


def render_metrics():
    """Render the key metrics cards."""
    col1, col2, col3, col4 = st.columns(4)
    
    # Today's P&L
    todays_pnl = calculate_todays_pnl()
    pnl_class = "positive" if todays_pnl >= 0 else "negative"
    pnl_sign = "+" if todays_pnl >= 0 else ""
    
    with col1:
        st.metric(
            label="Today's P&L",
            value=f"₹{todays_pnl:,.2f}",
            delta=f"{pnl_sign}{todays_pnl:,.2f}",
            delta_color="normal" if todays_pnl >= 0 else "inverse"
        )
    
    # Available Funds
    portfolio = get_latest_portfolio()
    available_margin = portfolio.available_margin if portfolio else 0
    
    with col2:
        st.metric(
            label="Available Margin",
            value=f"₹{available_margin:,.2f}",
        )
    
    # Win Rate
    win_data = get_win_rate()
    
    with col3:
        st.metric(
            label="Win Rate",
            value=f"{win_data['win_rate']:.1f}%",
            delta=f"{win_data['winning_trades']}/{win_data['total_trades']} trades"
        )
    
    # Today's Trades
    todays_trades = get_todays_trades()
    
    with col4:
        st.metric(
            label="Today's Trades",
            value=len(todays_trades),
            delta="50 max"
        )


def render_agent_logs():
    """Render the live agent logs panel using Streamlit components."""
    st.subheader("🧠 Agent Thinking (Live)")
    
    logs = get_recent_logs(20)
    
    if not logs:
        st.info("No agent logs yet. The agent will log its reasoning here.")
        return
    
    # Render logs using Streamlit components (not raw HTML)
    for log in logs:
        timestamp = log.timestamp.strftime("%H:%M:%S")
        action = log.action_taken or "THINKING"
        strategy = f" | Strategy: {log.strategy_used}" if log.strategy_used else ""
        symbols = f" | Symbols: {log.symbols_analyzed}" if log.symbols_analyzed else ""
        confidence = f" | Confidence: {log.confidence_level:.0%}" if log.confidence_level else ""
        
        # Use columns for better layout
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption(f"🕐 {timestamp} • **[{action}]**{strategy}{symbols}{confidence}")
            st.markdown(f"> {log.ai_reasoning[:300]}{'...' if len(log.ai_reasoning) > 300 else ''}")
        with col2:
            if log.confidence_level:
                st.metric("Confidence", f"{log.confidence_level:.0%}")


def render_positions():
    """Render current positions/holdings."""
    st.subheader("📊 Current Holdings")
    
    holdings = get_all_holdings()
    
    if not holdings:
        st.info("No current holdings")
        return
    
    # Convert to dataframe
    df = pd.DataFrame([
        {
            "Symbol": symbol,
            "Quantity": data["quantity"],
            "Avg Price": f"₹{data['avg_price']:,.2f}",
            "Side": data["side"],
            "Value": f"₹{data['quantity'] * data['avg_price']:,.2f}"
        }
        for symbol, data in holdings.items()
    ])
    
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )


def render_order_history():
    """Render recent order history."""
    st.subheader("📋 Recent Orders")
    
    trades = get_recent_trades(20)
    
    if not trades:
        st.info("No trades yet")
        return
    
    # Convert to dataframe
    df = pd.DataFrame([
        {
            "Time": t.timestamp.strftime("%Y-%m-%d %H:%M"),
            "Symbol": t.symbol,
            "Side": t.side,
            "Qty": t.quantity,
            "Price": f"₹{t.price:,.2f}",
            "Value": f"₹{t.quantity * t.price:,.2f}",
            "Status": t.status,
            "Mode": "📝 Paper" if t.is_paper_trade else "🔴 Live",
            "P&L": f"₹{t.pnl:,.2f}" if t.pnl else "-",
        }
        for t in trades
    ])
    
    # Color code the side column
    def highlight_side(val):
        if val == "BUY":
            return "background-color: rgba(72, 187, 120, 0.3)"
        elif val == "SELL":
            return "background-color: rgba(252, 129, 129, 0.3)"
        return ""
    
    st.dataframe(
        df.style.map(highlight_side, subset=["Side"]),
        use_container_width=True,
        hide_index=True,
    )


def render_portfolio_chart():
    """Render portfolio value over time chart."""
    st.subheader("📈 Portfolio Performance")
    
    history = get_portfolio_history(7)
    
    if not history or len(history) < 2:
        st.info("Not enough data for chart. Portfolio snapshots will appear here.")
        return
    
    df = pd.DataFrame([
        {
            "timestamp": h.timestamp,
            "total_value": h.total_value,
            "realized_pnl": h.realized_pnl,
        }
        for h in history
    ])
    
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=df["total_value"],
        mode="lines+markers",
        name="Portfolio Value",
        line=dict(color="#4299e1", width=2),
        fill="tozeroy",
        fillcolor="rgba(66, 153, 225, 0.1)"
    ))
    
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0"),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.1)",
            title=""
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.1)",
            title="Value (₹)"
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        height=300,
    )
    
    st.plotly_chart(fig, use_container_width=True)


def render_guardrails_status():
    """Render guardrails status panel."""
    st.subheader("🛡️ Risk Guardrails")
    
    from mcp_server.guardrails import get_guardrails
    guardrails = get_guardrails()
    status = guardrails.get_status_report()
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**Position Limits**")
        st.progress(0.2, text=f"Max per trade: {status['limits']['max_position_percent']}%")
        
        trades_used = status["daily_trades_used"]
        trades_max = status["limits"]["max_daily_trades"]
        st.progress(trades_used / trades_max, text=f"Trades: {trades_used}/{trades_max}")
    
    with col2:
        st.markdown("**Value Limits**")
        st.write(f"• Min trade: ₹{status['limits']['min_trade_value']:,}")
        st.write(f"• Max trade: ₹{status['limits']['max_trade_value']:,}")
        st.write(f"• Max daily loss: {status['limits']['max_daily_loss_percent']}%")


def render_sidebar():
    """Render sidebar with controls."""
    with st.sidebar:
        st.header("⚙️ Controls")
        
        # Auto-refresh toggle
        auto_refresh = st.checkbox("Auto-refresh (10s)", value=True)
        
        if auto_refresh:
            time.sleep(0.1)
            st.rerun()
        
        st.divider()
        
        # Market status
        st.header("🕐 Market Status")
        market_checker = get_market_checker()
        status = market_checker.get_status_display()
        
        st.write(f"**Time:** {status['current_time_ist']}")
        st.write(f"**Status:** {status['status']}")
        st.write(f"**Trading Day:** {'Yes' if status['is_trading_day'] else 'No'}")
        
        st.divider()
        
        # Quick stats
        st.header("📊 Quick Stats")
        trades = get_recent_trades(100)
        
        if trades:
            buy_count = sum(1 for t in trades if t.side == "BUY")
            sell_count = sum(1 for t in trades if t.side == "SELL")
            
            st.write(f"**Total Trades:** {len(trades)}")
            st.write(f"**Buys:** {buy_count}")
            st.write(f"**Sells:** {sell_count}")
            
            # Most traded symbol
            from collections import Counter
            symbols = [t.symbol for t in trades]
            if symbols:
                most_traded = Counter(symbols).most_common(1)[0]
                st.write(f"**Most Traded:** {most_traded[0]} ({most_traded[1]}x)")


def main():
    """Main dashboard function."""
    # Render header
    render_header()
    
    st.divider()
    
    # Agent status and activity
    render_agent_status()
    
    st.divider()
    
    # Render key metrics
    render_metrics()
    
    st.divider()
    
    # Main content area
    col1, col2 = st.columns([3, 2])
    
    with col1:
        render_agent_logs()
        st.divider()
        render_order_history()
    
    with col2:
        render_positions()
        st.divider()
        render_guardrails_status()
        st.divider()
        render_portfolio_chart()
    
    # Sidebar
    render_sidebar()


if __name__ == "__main__":
    main()
