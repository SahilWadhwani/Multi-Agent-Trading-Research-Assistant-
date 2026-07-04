"""
Autonomous AI Trading Agent using LangChain.
This agent has a persona of a highly analytical quantitative trader.
"""

import os
import sys
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

# LangChain imports
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.tools import Tool, StructuredTool
from langchain.memory import ConversationBufferWindowMemory
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

# Add parent path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.market_hours import get_market_checker, MarketHoursChecker
from mcp_server.upstox_client import get_upstox_client
from mcp_server.guardrails import get_guardrails, TradingGuardrails
from database.operations import (
    log_trade,
    log_agent_reasoning,
    log_portfolio_snapshot,
    get_recent_trades,
    get_recent_logs,
    get_all_holdings,
    calculate_todays_pnl,
    get_todays_trades,
)
from database.schema import init_database

load_dotenv()

# Initialize database
init_database()

# Trading mode
TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()
IS_PAPER_TRADING = TRADING_MODE == "paper"


# ============== TOOL INPUT SCHEMAS ==============

class MarketDataInput(BaseModel):
    symbol: str = Field(description="Stock symbol (e.g., 'RELIANCE', 'TCS')")
    timeframe: str = Field(default="day", description="Timeframe: 1minute, 30minute, day, week, month")


class TradeInput(BaseModel):
    symbol: str = Field(description="Stock symbol to trade")
    side: str = Field(description="BUY or SELL")
    quantity: int = Field(description="Number of shares")
    product_type: str = Field(default="INTRADAY", description="INTRADAY or DELIVERY")
    reasoning: str = Field(description="Your analysis and reasoning for this trade")


class AnalysisInput(BaseModel):
    reasoning: str = Field(description="Your market analysis and observations")
    symbols_analyzed: str = Field(default="", description="Comma-separated list of symbols")
    strategy: str = Field(default="", description="Strategy being considered")


# ============== THE TRADING AGENT ==============

class TradingAgent:
    """
    Autonomous AI Trading Agent with a quantitative trader persona.
    """
    
    # Default to best available model, configurable via .env
    DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
    
    SYSTEM_PROMPT = """You are QUANT-1, an elite autonomous quantitative trading agent managing a live portfolio on the Indian stock market (NSE/BSE).

## YOUR IDENTITY
- You are a highly analytical, data-driven quantitative trader
- You make decisions based on technical analysis, market patterns, and statistical evidence
- You are risk-aware and always consider downside protection
- You document your reasoning thoroughly before every trade

## YOUR CAPABILITIES
You have access to these tools:
1. **get_market_data** - Fetch real-time quotes and historical OHLC data
2. **get_account_balance** - Check available funds and margin
3. **get_current_positions** - View open positions and holdings
4. **execute_trade** - Place trades (subject to guardrails)
5. **get_order_history** - Review recent trades
6. **get_trading_status** - Check market hours and guardrail limits
7. **log_analysis** - Document your analysis without trading

## CRITICAL GUARDRAILS (CANNOT BE BYPASSED)
- Maximum 20% of available margin per single trade
- Maximum 50 trades per day
- Trading paused if daily loss exceeds 5%
- You CANNOT add funds - only trade with what's available
- You CANNOT bypass these rules under any circumstances

## YOUR TRADING APPROACH
1. **ALWAYS check market status** before attempting trades
2. **ALWAYS check account balance** to know your limits
3. **ALWAYS log your reasoning** BEFORE executing any trade
4. **Use technical analysis**: Look for trends, support/resistance, volume patterns
5. **Manage risk**: Never over-leverage, use appropriate position sizing
6. **Be patient**: Wait for high-probability setups
7. **Cut losses early**: Don't let small losses become big ones

## CURRENT MODE: {'📝 PAPER TRADING' if IS_PAPER_TRADING else '🔴 LIVE TRADING'}
{'Trades are simulated and logged but NOT sent to the exchange.' if IS_PAPER_TRADING else 'CAUTION: Trades are REAL and will affect your actual portfolio.'}

## MARKET HOURS
- Trading: 9:15 AM - 3:30 PM IST (Monday-Friday)
- Outside these hours, focus on analysis and planning

When you receive a task, think through it step by step:
1. What is the current market status?
2. What are my available funds and positions?
3. What does the data tell me?
4. What is my thesis and confidence level?
5. What is the appropriate position size given the risk?
6. Document reasoning THEN execute (or decide to wait)

Be autonomous but responsible. Your goal is consistent, risk-adjusted returns - not gambling."""

    def __init__(self, model: str = None):
        self.model_name = model or self.DEFAULT_MODEL
        print(f"🤖 Initializing Trading Agent with model: {self.model_name}")
        self.market_checker = get_market_checker()
        self.guardrails = get_guardrails()
        self.upstox = None
        
        # Verify OpenAI API key
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY not set in environment")
        
        # Initialize LLM
        self.llm = ChatOpenAI(
            model=model,
            temperature=0.1,  # Low temperature for consistent, analytical responses
            max_tokens=4096,
        )
        
        # Initialize tools
        self.tools = self._create_tools()
        
        # Create agent
        self.agent = self._create_agent()
        
        # Memory for conversation context
        self.memory = ConversationBufferWindowMemory(
            k=10,
            memory_key="chat_history",
            return_messages=True
        )
        
        # Agent executor
        self.executor = AgentExecutor(
            agent=self.agent,
            tools=self.tools,
            memory=self.memory,
            verbose=True,
            max_iterations=15,
            handle_parsing_errors=True,
        )
    
    def _get_upstox(self):
        """Lazy load Upstox client."""
        if self.upstox is None:
            self.upstox = get_upstox_client()
        return self.upstox
    
    def _create_tools(self) -> List[Tool]:
        """Create LangChain tools for the agent."""
        
        tools = [
            StructuredTool.from_function(
                func=self._tool_get_market_data,
                name="get_market_data",
                description="Get market data for a symbol including current price, OHLC, and volume",
                args_schema=MarketDataInput,
            ),
            Tool(
                name="get_account_balance",
                func=self._tool_get_account_balance,
                description="Get current account balance, available margin, and guardrail limits. IMPORTANT: You can only trade with available funds.",
            ),
            Tool(
                name="get_current_positions",
                func=self._tool_get_current_positions,
                description="Get all current open positions and holdings",
            ),
            StructuredTool.from_function(
                func=self._tool_execute_trade,
                name="execute_trade",
                description=f"Execute a trade ({'PAPER MODE' if IS_PAPER_TRADING else 'LIVE MODE'}). Max 20% of margin per trade. MUST provide reasoning.",
                args_schema=TradeInput,
            ),
            Tool(
                name="get_order_history",
                func=self._tool_get_order_history,
                description="Get recent order history and trades",
            ),
            Tool(
                name="get_trading_status",
                func=self._tool_get_trading_status,
                description="Get current trading status including market hours, guardrail limits, and daily P&L",
            ),
            StructuredTool.from_function(
                func=self._tool_log_analysis,
                name="log_analysis",
                description="Log your market analysis and reasoning WITHOUT executing a trade. Use this to document your thinking.",
                args_schema=AnalysisInput,
            ),
        ]
        
        return tools
    
    def _create_agent(self):
        """Create the LangChain agent with the system prompt."""
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=self.SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
        
        return create_openai_functions_agent(self.llm, self.tools, prompt)
    
    # ============== TOOL IMPLEMENTATIONS ==============
    
    def _tool_get_market_data(self, symbol: str, timeframe: str = "day") -> str:
        """Get market data for a symbol."""
        try:
            client = self._get_upstox()
            if not client.ensure_authenticated():
                return json.dumps({"error": "Authentication required"})
            
            # Get quote
            quote = client.get_market_quote(symbol)
            
            # Get historical data
            instrument_key = f"NSE_EQ|{symbol}"
            historical = client.get_historical_candles(instrument_key, timeframe)
            
            return json.dumps({
                "symbol": symbol,
                "quote": quote,
                "historical_summary": {
                    "timeframe": timeframe,
                    "candles_count": len(historical.get("data", {}).get("candles", [])) if historical.get("data") else 0,
                },
                "data": historical
            }, indent=2, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    def _tool_get_account_balance(self, _: str = "") -> str:
        """Get account balance."""
        try:
            client = self._get_upstox()
            if not client.ensure_authenticated():
                return json.dumps({"error": "Authentication required"})
            
            funds = client.get_funds_and_margin()
            
            # Update guardrails
            if funds.get("status") == "success" and funds.get("data"):
                data = funds["data"]
                equity_data = data.get("equity", data)
                available = float(equity_data.get("available_margin", 0))
                self.guardrails.update_context(available_margin=available)
            
            return json.dumps({
                "funds": funds,
                "guardrails": self.guardrails.get_status_report(),
                "reminder": "You can ONLY trade with available funds. You CANNOT add more funds."
            }, indent=2, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    def _tool_get_current_positions(self, _: str = "") -> str:
        """Get current positions."""
        try:
            client = self._get_upstox()
            if not client.ensure_authenticated():
                return json.dumps({"error": "Authentication required"})
            
            positions = client.get_positions()
            holdings = client.get_holdings()
            db_holdings = get_all_holdings()
            
            return json.dumps({
                "upstox_positions": positions,
                "upstox_holdings": holdings,
                "paper_positions": db_holdings,
                "is_paper_mode": IS_PAPER_TRADING,
            }, indent=2, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    def _tool_execute_trade(
        self,
        symbol: str,
        side: str,
        quantity: int,
        product_type: str = "INTRADAY",
        reasoning: str = ""
    ) -> str:
        """Execute a trade with guardrails."""
        try:
            # Check market hours
            is_open, market_status = TradingGuardrails.is_market_hours()
            if not is_open and not IS_PAPER_TRADING:
                return json.dumps({
                    "status": "REJECTED",
                    "reason": f"Market is closed: {market_status}",
                    "trade_executed": False
                })
            
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
                product_type=product_type
            )
            
            if not validation.is_valid:
                return json.dumps({
                    "status": "REJECTED",
                    "reason": validation.message,
                    "details": validation.details,
                    "trade_executed": False,
                    "guardrail_violation": True
                }, indent=2)
            
            trade_value = quantity * current_price
            
            if IS_PAPER_TRADING:
                # Paper trade - log only
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
                
                return json.dumps({
                    "status": "SUCCESS",
                    "mode": "PAPER_TRADE",
                    "trade_id": trade.id,
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "price": current_price,
                    "trade_value": trade_value,
                    "message": "Paper trade logged. No real order placed.",
                }, indent=2)
            else:
                # Live trade
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
                    
                    return json.dumps({
                        "status": "SUCCESS",
                        "mode": "LIVE_TRADE",
                        "trade_id": trade.id,
                        "order_id": order_id,
                        "symbol": symbol,
                        "side": side,
                        "quantity": quantity,
                        "price": current_price,
                        "trade_value": trade_value,
                    }, indent=2)
                else:
                    return json.dumps({
                        "status": "FAILED",
                        "reason": result.get("message", "Order failed"),
                        "trade_executed": False,
                    }, indent=2)
                    
        except Exception as e:
            return json.dumps({"error": str(e), "trade_executed": False})
    
    def _tool_get_order_history(self, _: str = "") -> str:
        """Get order history."""
        try:
            trades = get_recent_trades(20)
            return json.dumps({
                "trades": [
                    {
                        "id": t.id,
                        "timestamp": t.timestamp.isoformat(),
                        "symbol": t.symbol,
                        "side": t.side,
                        "quantity": t.quantity,
                        "price": t.price,
                        "status": t.status,
                        "is_paper": t.is_paper_trade,
                    }
                    for t in trades
                ],
                "is_paper_mode": IS_PAPER_TRADING,
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    def _tool_get_trading_status(self, _: str = "") -> str:
        """Get trading status."""
        try:
            is_open, market_status = TradingGuardrails.is_market_hours()
            daily_pnl = calculate_todays_pnl()
            todays_trades = len(get_todays_trades())
            
            self.guardrails.update_context(daily_trades=todays_trades, daily_pnl=daily_pnl)
            
            return json.dumps({
                "trading_mode": "PAPER" if IS_PAPER_TRADING else "LIVE",
                "market_status": market_status,
                "market_is_open": is_open,
                "guardrails": self.guardrails.get_status_report(),
                "daily_pnl": daily_pnl,
                "todays_trade_count": todays_trades,
                "timestamp": datetime.now().isoformat(),
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    def _tool_log_analysis(
        self,
        reasoning: str,
        symbols_analyzed: str = "",
        strategy: str = ""
    ) -> str:
        """Log analysis without trading."""
        try:
            log = log_agent_reasoning(
                ai_reasoning=reasoning,
                action_taken="ANALYSIS",
                symbols_analyzed=symbols_analyzed,
                strategy_used=strategy,
            )
            
            return json.dumps({
                "status": "LOGGED",
                "log_id": log.id,
                "message": "Analysis logged successfully",
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    # ============== AGENT EXECUTION ==============
    
    def run(self, task: str) -> str:
        """
        Run the agent with a specific task.
        
        Args:
            task: The instruction/task for the agent
            
        Returns:
            Agent's response
        """
        try:
            result = self.executor.invoke({"input": task})
            return result.get("output", "No output")
        except Exception as e:
            return f"Agent error: {str(e)}"
    
    def run_autonomous_loop(
        self,
        interval_seconds: int = 300,
        max_iterations: Optional[int] = None
    ):
        """
        Run the agent in an autonomous loop.
        
        Args:
            interval_seconds: Seconds between iterations
            max_iterations: Maximum iterations (None for infinite)
        """
        iteration = 0
        
        print(f"\n{'='*60}")
        print(f"  STARTING AUTONOMOUS TRADING AGENT")
        print(f"  Mode: {'PAPER TRADING' if IS_PAPER_TRADING else 'LIVE TRADING'}")
        print(f"  Interval: {interval_seconds} seconds")
        print(f"{'='*60}\n")
        
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            
            # Check market status
            should_run, reason = self.market_checker.should_agent_run()
            
            if not should_run:
                print(f"\n[{datetime.now().isoformat()}] {reason}")
                print("Waiting for market hours...")
                time.sleep(interval_seconds)
                continue
            
            print(f"\n{'='*60}")
            print(f"  ITERATION {iteration} - {datetime.now().isoformat()}")
            print(f"{'='*60}")
            
            # Run agent with autonomous task
            task = """
            You are in autonomous trading mode. Perform your analysis cycle:
            
            1. Check the current trading status and market conditions
            2. Review your account balance and available margin
            3. Check current positions
            4. Analyze market opportunities for potential trades
            5. Either:
               - Execute a trade if you find a high-probability setup
               - Log your analysis if you decide to wait
               - Manage existing positions if needed
            
            Be analytical and document your reasoning. Remember the 20% position limit.
            """
            
            try:
                response = self.run(task)
                print(f"\nAgent Response:\n{response}")
            except Exception as e:
                print(f"\nAgent Error: {e}")
                log_agent_reasoning(
                    ai_reasoning=f"Error during autonomous cycle: {str(e)}",
                    action_taken="ERROR",
                )
            
            # Wait for next iteration
            print(f"\nSleeping for {interval_seconds} seconds...")
            time.sleep(interval_seconds)


def run_agent():
    """Entry point to run the trading agent."""
    agent = TradingAgent()
    
    # Run autonomous loop
    agent.run_autonomous_loop(
        interval_seconds=300,  # 5 minutes between cycles
        max_iterations=None     # Run indefinitely
    )


if __name__ == "__main__":
    run_agent()
