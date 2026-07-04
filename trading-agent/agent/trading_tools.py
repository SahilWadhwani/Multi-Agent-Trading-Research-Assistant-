"""
Trading Tools for Claude/Cursor Integration.
These functions can be called directly by Claude via MCP or command line.
No external LLM API required - Claude IS the brain.
"""

import os
import sys
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

# Add parent path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.schema import init_database
from database.operations import (
    log_trade,
    log_agent_reasoning,
    log_portfolio_snapshot,
    get_recent_trades,
    get_recent_logs,
    get_all_holdings,
    calculate_todays_pnl,
    get_todays_trades,
    get_win_rate,
)
from mcp_server.upstox_client import get_upstox_client
from mcp_server.guardrails import get_guardrails, TradingGuardrails
from agent.market_hours import get_market_checker

load_dotenv()

# Initialize
init_database()

# Trading mode
TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()
IS_PAPER_TRADING = TRADING_MODE == "paper"


class TradingTools:
    """
    Trading toolkit for Claude/Cursor to use.
    All methods return structured data that Claude can analyze.
    """
    
    def __init__(self):
        self.upstox = None
        self.guardrails = get_guardrails()
        self.market_checker = get_market_checker()
    
    def _get_upstox(self):
        if self.upstox is None:
            self.upstox = get_upstox_client()
        return self.upstox
    
    # ============== MARKET DATA ==============
    
    def get_market_status(self) -> Dict[str, Any]:
        """Get current market status and trading hours info."""
        status = self.market_checker.get_status_display()
        status["trading_mode"] = "PAPER" if IS_PAPER_TRADING else "LIVE"
        status["guardrails"] = self.guardrails.get_status_report()
        return status
    
    def get_quote(self, symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """Get real-time quote for a symbol."""
        client = self._get_upstox()
        if not client.ensure_authenticated():
            return {"error": "Authentication required. Run: python main.py --auth"}
        
        try:
            return client.get_market_quote(symbol, exchange)
        except Exception as e:
            return {"error": str(e)}
    
    def get_historical_data(
        self, 
        symbol: str, 
        interval: str = "day",
        days: int = 30
    ) -> Dict[str, Any]:
        """Get historical OHLC data."""
        client = self._get_upstox()
        if not client.ensure_authenticated():
            return {"error": "Authentication required"}
        
        try:
            instrument_key = f"NSE_EQ|{symbol}"
            return client.get_historical_candles(instrument_key, interval)
        except Exception as e:
            return {"error": str(e)}
    
    # ============== ACCOUNT DATA ==============
    
    def get_balance(self) -> Dict[str, Any]:
        """Get account balance and available margin."""
        client = self._get_upstox()
        if not client.ensure_authenticated():
            return {"error": "Authentication required"}
        
        try:
            funds = client.get_funds_and_margin()
            
            # Update guardrails
            if funds.get("status") == "success" and funds.get("data"):
                data = funds["data"]
                equity = data.get("equity", data)
                available = float(equity.get("available_margin", 0))
                self.guardrails.update_context(available_margin=available)
            
            return {
                "funds": funds,
                "max_trade_value": self.guardrails.get_max_trade_value(),
                "guardrails": self.guardrails.get_status_report(),
            }
        except Exception as e:
            return {"error": str(e)}
    
    def get_positions(self) -> Dict[str, Any]:
        """Get current positions from Upstox and database."""
        client = self._get_upstox()
        
        result = {
            "paper_positions": get_all_holdings(),
            "is_paper_mode": IS_PAPER_TRADING,
        }
        
        if client.ensure_authenticated():
            try:
                result["upstox_positions"] = client.get_positions()
                result["upstox_holdings"] = client.get_holdings()
            except Exception as e:
                result["upstox_error"] = str(e)
        
        return result
    
    # ============== TRADING ==============
    
    def execute_trade(
        self,
        symbol: str,
        side: str,
        quantity: int,
        reasoning: str,
        product_type: str = "INTRADAY",
    ) -> Dict[str, Any]:
        """
        Execute a trade with guardrails.
        
        Args:
            symbol: Stock symbol (e.g., "RELIANCE", "TCS")
            side: "BUY" or "SELL"
            quantity: Number of shares
            reasoning: Why this trade (logged to database)
            product_type: "INTRADAY" or "DELIVERY"
        
        Returns:
            Trade result with status
        """
        # Check market hours (allow paper trades anytime)
        is_open, market_msg = TradingGuardrails.is_market_hours()
        if not is_open and not IS_PAPER_TRADING:
            return {
                "status": "REJECTED",
                "reason": f"Market closed: {market_msg}",
                "trade_executed": False,
            }
        
        # Log reasoning FIRST
        log_agent_reasoning(
            ai_reasoning=reasoning,
            action_taken="TRADE_ATTEMPT",
            symbols_analyzed=symbol,
        )
        
        # Get current price
        client = self._get_upstox()
        current_price = 100.0  # Default for paper
        
        if client.ensure_authenticated():
            try:
                quote = client.get_market_quote(symbol)
                if quote.get("status") == "success" and quote.get("data"):
                    price_data = list(quote["data"].values())[0]
                    current_price = float(price_data.get("last_price", 100))
            except:
                pass
        
        # Validate against guardrails
        validation = self.guardrails.validate_trade(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=current_price,
            product_type=product_type,
        )
        
        if not validation.is_valid:
            return {
                "status": "REJECTED",
                "reason": validation.message,
                "details": validation.details,
                "trade_executed": False,
                "guardrail_violation": True,
            }
        
        trade_value = quantity * current_price
        
        if IS_PAPER_TRADING:
            # Paper trade
            trade = log_trade(
                symbol=symbol,
                quantity=quantity,
                side=side.upper(),
                price=current_price,
                product_type=product_type,
                order_id=f"PAPER_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                status="EXECUTED",
                is_paper_trade=True,
                notes=reasoning[:500],
            )
            
            return {
                "status": "SUCCESS",
                "mode": "PAPER_TRADE",
                "trade_id": trade.id,
                "symbol": symbol,
                "side": side.upper(),
                "quantity": quantity,
                "price": current_price,
                "trade_value": trade_value,
                "message": "Paper trade logged. No real order placed.",
            }
        else:
            # Live trade
            try:
                product_code = "I" if product_type == "INTRADAY" else "D"
                result = client.place_order(
                    symbol=symbol,
                    exchange="NSE",
                    transaction_type=side.upper(),
                    quantity=quantity,
                    order_type="MARKET",
                    product=product_code,
                )
                
                if result.get("status") == "success":
                    order_id = result.get("data", {}).get("order_id")
                    trade = log_trade(
                        symbol=symbol,
                        quantity=quantity,
                        side=side.upper(),
                        price=current_price,
                        product_type=product_type,
                        order_id=order_id,
                        status="EXECUTED",
                        is_paper_trade=False,
                        notes=reasoning[:500],
                    )
                    return {
                        "status": "SUCCESS",
                        "mode": "LIVE_TRADE",
                        "trade_id": trade.id,
                        "order_id": order_id,
                        "symbol": symbol,
                        "side": side.upper(),
                        "quantity": quantity,
                        "price": current_price,
                        "trade_value": trade_value,
                    }
                else:
                    return {
                        "status": "FAILED",
                        "reason": result.get("message", "Order failed"),
                        "trade_executed": False,
                    }
            except Exception as e:
                return {"status": "ERROR", "reason": str(e), "trade_executed": False}
    
    # ============== ANALYSIS & LOGS ==============
    
    def log_analysis(
        self,
        reasoning: str,
        symbols: str = "",
        strategy: str = "",
        action: str = "ANALYSIS",
    ) -> Dict[str, Any]:
        """Log analysis without trading."""
        log = log_agent_reasoning(
            ai_reasoning=reasoning,
            action_taken=action,
            symbols_analyzed=symbols,
            strategy_used=strategy,
        )
        return {
            "status": "LOGGED",
            "log_id": log.id,
            "timestamp": log.timestamp.isoformat(),
        }
    
    def get_order_history(self, limit: int = 20) -> List[Dict]:
        """Get recent trades from database."""
        trades = get_recent_trades(limit)
        return [
            {
                "id": t.id,
                "timestamp": t.timestamp.isoformat(),
                "symbol": t.symbol,
                "side": t.side,
                "quantity": t.quantity,
                "price": t.price,
                "value": t.quantity * t.price,
                "status": t.status,
                "is_paper": t.is_paper_trade,
                "pnl": t.pnl,
            }
            for t in trades
        ]
    
    def get_daily_summary(self) -> Dict[str, Any]:
        """Get today's trading summary."""
        return {
            "todays_pnl": calculate_todays_pnl(),
            "todays_trades": len(get_todays_trades()),
            "win_rate": get_win_rate(),
            "holdings": get_all_holdings(),
            "market_status": self.market_checker.get_status_display(),
            "mode": "PAPER" if IS_PAPER_TRADING else "LIVE",
        }


# Singleton instance
_tools = None

def get_trading_tools() -> TradingTools:
    """Get the trading tools singleton."""
    global _tools
    if _tools is None:
        _tools = TradingTools()
    return _tools


# ============== CLI Interface for quick commands ==============

def cli():
    """Command-line interface for quick trading operations."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Trading Tools CLI")
    parser.add_argument("command", choices=[
        "status", "balance", "positions", "quote", "history", "summary"
    ])
    parser.add_argument("--symbol", "-s", help="Stock symbol")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    args = parser.parse_args()
    tools = get_trading_tools()
    
    if args.command == "status":
        result = tools.get_market_status()
    elif args.command == "balance":
        result = tools.get_balance()
    elif args.command == "positions":
        result = tools.get_positions()
    elif args.command == "quote":
        if not args.symbol:
            print("Error: --symbol required for quote")
            return
        result = tools.get_quote(args.symbol)
    elif args.command == "history":
        result = tools.get_order_history()
    elif args.command == "summary":
        result = tools.get_daily_summary()
    
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        for key, value in result.items() if isinstance(result, dict) else enumerate(result):
            print(f"{key}: {value}")


if __name__ == "__main__":
    cli()
