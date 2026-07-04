"""
MCP Server for the AI Trading Agent.
This is the ONLY interface the AI agent has to interact with Upstox.
All trades must go through this server and its guardrails.
"""

import os
import sys
import json
import asyncio
from datetime import datetime
from typing import Any, Optional
from dotenv import load_dotenv

# MCP imports
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Add parent path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.upstox_client import get_upstox_client, UpstoxClient
from mcp_server.guardrails import get_guardrails, TradingGuardrails, GuardrailViolation
from database.operations import (
    log_trade,
    log_agent_reasoning,
    log_portfolio_snapshot,
    get_recent_trades,
    get_all_holdings,
    calculate_todays_pnl,
)
from database.schema import init_database

load_dotenv()

# Initialize database
init_database()

# Get trading mode
TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()
IS_PAPER_TRADING = TRADING_MODE == "paper"

print(f"\n{'='*60}")
print(f"  TRADING MODE: {'📝 PAPER TRADING' if IS_PAPER_TRADING else '🔴 LIVE TRADING'}")
print(f"{'='*60}\n")


class TradingMCPServer:
    """
    MCP Server that exposes trading tools to the AI agent.
    All tools enforce guardrails and logging.
    """
    
    def __init__(self):
        self.server = Server("upstox-trading-agent")
        self.client: Optional[UpstoxClient] = None
        self.guardrails = get_guardrails()
        self._setup_tools()
    
    def _get_client(self) -> UpstoxClient:
        """Get or initialize the Upstox client."""
        if self.client is None:
            self.client = get_upstox_client()
        return self.client
    
    def _setup_tools(self):
        """Register all MCP tools."""
        
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="get_market_data",
                    description="Get market data for a symbol including current price, OHLC, and volume. Use this to analyze stocks before trading.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Stock symbol (e.g., 'RELIANCE', 'TCS', 'INFY')"
                            },
                            "timeframe": {
                                "type": "string",
                                "enum": ["1minute", "30minute", "day", "week", "month"],
                                "description": "Timeframe for historical data",
                                "default": "day"
                            },
                            "days": {
                                "type": "integer",
                                "description": "Number of days of historical data",
                                "default": 30
                            }
                        },
                        "required": ["symbol"]
                    }
                ),
                Tool(
                    name="get_account_balance",
                    description="Get current account balance, available margin, and fund details. IMPORTANT: The agent can only trade with available funds - it cannot add more funds.",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                Tool(
                    name="get_current_positions",
                    description="Get all current open positions and holdings.",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                Tool(
                    name="execute_trade",
                    description=f"Execute a trade (currently in {'PAPER' if IS_PAPER_TRADING else 'LIVE'} mode). GUARDRAILS ENFORCED: Max 20% of margin per trade. Must log reasoning BEFORE calling this.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Stock symbol to trade"
                            },
                            "side": {
                                "type": "string",
                                "enum": ["BUY", "SELL"],
                                "description": "Trade direction"
                            },
                            "quantity": {
                                "type": "integer",
                                "description": "Number of shares"
                            },
                            "product_type": {
                                "type": "string",
                                "enum": ["INTRADAY", "DELIVERY"],
                                "description": "INTRADAY for same-day, DELIVERY for holding",
                                "default": "INTRADAY"
                            },
                            "reasoning": {
                                "type": "string",
                                "description": "REQUIRED: Your analysis and reasoning for this trade"
                            }
                        },
                        "required": ["symbol", "side", "quantity", "reasoning"]
                    }
                ),
                Tool(
                    name="get_order_history",
                    description="Get today's order history and trade log.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of orders to return",
                                "default": 20
                            }
                        }
                    }
                ),
                Tool(
                    name="get_trading_status",
                    description="Get current trading status including market hours, guardrail limits, and daily stats.",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                Tool(
                    name="log_analysis",
                    description="Log your market analysis and reasoning WITHOUT executing a trade. Use this to document your thinking.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "reasoning": {
                                "type": "string",
                                "description": "Your analysis and market observations"
                            },
                            "symbols_analyzed": {
                                "type": "string",
                                "description": "Comma-separated list of symbols you analyzed"
                            },
                            "strategy": {
                                "type": "string",
                                "description": "Strategy being considered"
                            },
                            "action": {
                                "type": "string",
                                "enum": ["HOLD", "WATCH", "ANALYSIS"],
                                "description": "Action decision",
                                "default": "ANALYSIS"
                            }
                        },
                        "required": ["reasoning"]
                    }
                ),
            ]
        
        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            try:
                if name == "get_market_data":
                    result = await self._get_market_data(
                        arguments["symbol"],
                        arguments.get("timeframe", "day"),
                        arguments.get("days", 30)
                    )
                elif name == "get_account_balance":
                    result = await self._get_account_balance()
                elif name == "get_current_positions":
                    result = await self._get_current_positions()
                elif name == "execute_trade":
                    result = await self._execute_trade(
                        arguments["symbol"],
                        arguments["side"],
                        arguments["quantity"],
                        arguments.get("product_type", "INTRADAY"),
                        arguments["reasoning"]
                    )
                elif name == "get_order_history":
                    result = await self._get_order_history(arguments.get("limit", 20))
                elif name == "get_trading_status":
                    result = await self._get_trading_status()
                elif name == "log_analysis":
                    result = await self._log_analysis(
                        arguments["reasoning"],
                        arguments.get("symbols_analyzed"),
                        arguments.get("strategy"),
                        arguments.get("action", "ANALYSIS")
                    )
                else:
                    result = {"error": f"Unknown tool: {name}"}
                
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            
            except Exception as e:
                error_result = {"error": str(e), "tool": name}
                return [TextContent(type="text", text=json.dumps(error_result, indent=2))]
    
    async def _get_market_data(self, symbol: str, timeframe: str, days: int) -> dict:
        """Get market data for a symbol."""
        client = self._get_client()
        
        if not client.ensure_authenticated():
            return {"error": "Authentication required. Please run authentication flow."}
        
        try:
            # Get current quote
            quote = client.get_market_quote(symbol)
            
            # Get historical data
            instrument_key = f"NSE_EQ|{symbol}"
            historical = client.get_historical_candles(
                instrument_key=instrument_key,
                interval=timeframe,
            )
            
            return {
                "symbol": symbol,
                "quote": quote,
                "historical": historical,
                "timeframe": timeframe,
            }
        except Exception as e:
            return {"error": f"Failed to get market data: {str(e)}"}
    
    async def _get_account_balance(self) -> dict:
        """Get account balance and update guardrails."""
        client = self._get_client()
        
        if not client.ensure_authenticated():
            return {"error": "Authentication required"}
        
        try:
            funds = client.get_funds_and_margin()
            
            # Update guardrails with current margin
            if funds.get("status") == "success" and funds.get("data"):
                data = funds["data"]
                # Handle both equity and commodity segments
                equity_data = data.get("equity", data)
                available = float(equity_data.get("available_margin", 0))
                self.guardrails.update_context(available_margin=available)
            
            return {
                "funds": funds,
                "guardrails": self.guardrails.get_status_report(),
                "warning": "REMINDER: You can only trade with available funds. You CANNOT add more funds."
            }
        except Exception as e:
            return {"error": f"Failed to get balance: {str(e)}"}
    
    async def _get_current_positions(self) -> dict:
        """Get current positions."""
        client = self._get_client()
        
        if not client.ensure_authenticated():
            return {"error": "Authentication required"}
        
        try:
            positions = client.get_positions()
            holdings = client.get_holdings()
            
            # Also get positions from our database (for paper trades)
            db_holdings = get_all_holdings()
            
            return {
                "upstox_positions": positions,
                "upstox_holdings": holdings,
                "paper_positions": db_holdings,
                "is_paper_mode": IS_PAPER_TRADING,
            }
        except Exception as e:
            return {"error": f"Failed to get positions: {str(e)}"}
    
    async def _execute_trade(
        self,
        symbol: str,
        side: str,
        quantity: int,
        product_type: str,
        reasoning: str
    ) -> dict:
        """
        Execute a trade with guardrails.
        In paper mode, only logs to database.
        """
        client = self._get_client()
        
        # === STEP 1: Check market hours ===
        is_open, market_status = TradingGuardrails.is_market_hours()
        if not is_open and not IS_PAPER_TRADING:
            return {
                "status": "REJECTED",
                "reason": f"Market is closed: {market_status}",
                "trade_executed": False
            }
        
        # === STEP 2: Log reasoning FIRST (required) ===
        log_agent_reasoning(
            ai_reasoning=reasoning,
            action_taken="TRADE_ATTEMPT",
            symbols_analyzed=symbol,
            strategy_used=product_type,
        )
        
        # === STEP 3: Get current price for validation ===
        try:
            if client.ensure_authenticated():
                quote = client.get_market_quote(symbol)
                if quote.get("status") == "success" and quote.get("data"):
                    price_data = list(quote["data"].values())[0]
                    current_price = float(price_data.get("last_price", 0))
                else:
                    current_price = 100  # Fallback for paper trading
            else:
                current_price = 100  # Fallback
        except:
            current_price = 100  # Fallback for testing
        
        # === STEP 4: Validate against guardrails ===
        validation = self.guardrails.validate_trade(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=current_price,
            product_type=product_type
        )
        
        if not validation.is_valid:
            return {
                "status": "REJECTED",
                "reason": validation.message,
                "details": validation.details,
                "trade_executed": False,
                "guardrail_violation": True
            }
        
        # === STEP 5: Execute trade (or simulate) ===
        trade_value = quantity * current_price
        
        if IS_PAPER_TRADING:
            # Paper trading - log to database only
            trade = log_trade(
                symbol=symbol,
                quantity=quantity,
                side=side.upper(),
                price=current_price,
                exchange="NSE",
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
                "order_id": trade.order_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": current_price,
                "trade_value": trade_value,
                "trade_executed": True,
                "message": "Paper trade logged successfully. No real order placed.",
                "risk_level": validation.risk_level.value,
            }
        
        else:
            # LIVE TRADING
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
                    
                    # Log to database
                    trade = log_trade(
                        symbol=symbol,
                        quantity=quantity,
                        side=side.upper(),
                        price=current_price,
                        exchange="NSE",
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
                        "side": side,
                        "quantity": quantity,
                        "price": current_price,
                        "trade_value": trade_value,
                        "trade_executed": True,
                        "upstox_response": result,
                    }
                else:
                    return {
                        "status": "FAILED",
                        "reason": result.get("message", "Order placement failed"),
                        "upstox_response": result,
                        "trade_executed": False,
                    }
                    
            except Exception as e:
                return {
                    "status": "ERROR",
                    "reason": str(e),
                    "trade_executed": False,
                }
    
    async def _get_order_history(self, limit: int) -> dict:
        """Get order history from database and Upstox."""
        client = self._get_client()
        
        # Get from database
        db_trades = get_recent_trades(limit)
        
        # Get from Upstox if authenticated
        upstox_orders = None
        if client.access_token:
            try:
                upstox_orders = client.get_order_book()
            except:
                pass
        
        return {
            "database_trades": [
                {
                    "id": t.id,
                    "timestamp": t.timestamp.isoformat(),
                    "symbol": t.symbol,
                    "side": t.side,
                    "quantity": t.quantity,
                    "price": t.price,
                    "status": t.status,
                    "is_paper": t.is_paper_trade,
                    "order_id": t.order_id,
                }
                for t in db_trades
            ],
            "upstox_orders": upstox_orders,
            "is_paper_mode": IS_PAPER_TRADING,
        }
    
    async def _get_trading_status(self) -> dict:
        """Get comprehensive trading status."""
        is_open, market_status = TradingGuardrails.is_market_hours()
        daily_pnl = calculate_todays_pnl()
        
        return {
            "trading_mode": "PAPER" if IS_PAPER_TRADING else "LIVE",
            "market_status": market_status,
            "market_is_open": is_open,
            "guardrails": self.guardrails.get_status_report(),
            "daily_pnl": daily_pnl,
            "timestamp": datetime.now().isoformat(),
        }
    
    async def _log_analysis(
        self,
        reasoning: str,
        symbols_analyzed: Optional[str],
        strategy: Optional[str],
        action: str
    ) -> dict:
        """Log analysis without trading."""
        log = log_agent_reasoning(
            ai_reasoning=reasoning,
            action_taken=action,
            symbols_analyzed=symbols_analyzed,
            strategy_used=strategy,
        )
        
        return {
            "status": "LOGGED",
            "log_id": log.id,
            "timestamp": log.timestamp.isoformat(),
            "message": "Analysis logged successfully",
        }
    
    async def run(self):
        """Run the MCP server."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options()
            )


def main():
    """Entry point for the MCP server."""
    server = TradingMCPServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
