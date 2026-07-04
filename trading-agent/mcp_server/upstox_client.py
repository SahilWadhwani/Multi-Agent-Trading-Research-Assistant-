"""
Upstox API Client with automated OAuth flow.
Handles token generation and refresh (tokens expire daily at 3:30 AM IST).
"""

import os
import time
import json
import webbrowser
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode
import requests
from flask import Flask, request, redirect
from dotenv import load_dotenv
import pytz

load_dotenv()

# Add parent path for database imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.operations import save_token, get_stored_token, is_token_valid

# Dynamic instrument master (no more hardcoded ISINs!)
_instrument_master = None

def get_instrument_master():
    """Lazy-load instrument master."""
    global _instrument_master
    if _instrument_master is None:
        from data_feeds.instrument_master import get_instrument_master as gim
        _instrument_master = gim()
    return _instrument_master


class UpstoxClient:
    """
    Upstox API client with automated OAuth handling.
    """
    
    BASE_URL = "https://api.upstox.com/v2"
    AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
    TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
    
    def __init__(self):
        self.api_key = os.getenv("UPSTOX_API_KEY")
        self.api_secret = os.getenv("UPSTOX_API_SECRET")
        self.redirect_uri = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8765/callback")
        self._access_token = None
        self._token_expires_at = None
        self._auth_code = None
        self._auth_event = threading.Event()
        
        # Allow construction in paper/test/read-only paths without credentials.
        # Live order permission is still blocked by runtime_safety unless a valid
        # broker token exists; auth/order methods will fail closed without keys.
        
        # Try to load existing token
        self._load_stored_token()
    
    def _load_stored_token(self):
        """Load token from database if valid."""
        stored = get_stored_token()
        if stored and is_token_valid():
            self._access_token = stored.access_token
            # Ensure expires_at is stored as naive UTC datetime
            expires_at = stored.expires_at
            if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo is not None:
                expires_at = expires_at.astimezone(pytz.UTC).replace(tzinfo=None)
            self._token_expires_at = expires_at
            print("✓ Loaded existing valid token from database")
        else:
            print("⚠ No valid token found. Authentication required.")
    
    @property
    def access_token(self) -> Optional[str]:
        """Get current access token, checking validity."""
        if self._access_token and self._is_token_expired():
            print("⚠ Token expired. Re-authentication required.")
            self._access_token = None
        return self._access_token
    
    def _is_token_expired(self) -> bool:
        """Check if token is expired (Upstox tokens expire at 3:30 AM IST daily)."""
        if not self._token_expires_at:
            # Assume token expires at next 3:30 AM IST
            ist = pytz.timezone('Asia/Kolkata')
            now_ist = datetime.now(ist)
            expiry = now_ist.replace(hour=3, minute=30, second=0, microsecond=0)
            if now_ist.hour >= 3 and now_ist.minute >= 30:
                expiry += timedelta(days=1)
            return now_ist >= expiry
        
        # Ensure both sides of comparison are naive UTC datetimes
        expires_at = self._token_expires_at
        if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo is not None:
            # Convert timezone-aware to naive UTC
            expires_at = expires_at.astimezone(pytz.UTC).replace(tzinfo=None)
        
        return datetime.utcnow() >= expires_at
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        if not self.access_token:
            raise ValueError("No valid access token. Please authenticate first.")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    
    def get_auth_url(self) -> str:
        """Generate the OAuth authorization URL."""
        params = {
            "client_id": self.api_key,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"
    
    def authenticate(self, open_browser: bool = True) -> bool:
        """
        Perform OAuth authentication flow.
        Opens browser for user login, captures callback, exchanges code for token.
        """
        # Start Flask server to capture callback
        app = Flask(__name__)
        app.logger.disabled = True
        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        
        @app.route('/callback')
        def callback():
            code = request.args.get('code')
            if code:
                self._auth_code = code
                self._auth_event.set()
                return """
                <html>
                <body style="font-family: Arial; text-align: center; padding-top: 50px;">
                    <h1>✓ Authentication Successful!</h1>
                    <p>You can close this window and return to the terminal.</p>
                </body>
                </html>
                """
            return "Error: No authorization code received", 400
        
        # Run server in background thread
        server_thread = threading.Thread(
            target=lambda: app.run(port=8765, debug=False, use_reloader=False)
        )
        server_thread.daemon = True
        server_thread.start()
        
        # Open browser for authentication
        auth_url = self.get_auth_url()
        print(f"\n{'='*60}")
        print("UPSTOX AUTHENTICATION REQUIRED")
        print(f"{'='*60}")
        print(f"\nOpening browser for authentication...")
        print(f"If browser doesn't open, visit:\n{auth_url}\n")
        
        if open_browser:
            webbrowser.open(auth_url)
        
        # Wait for callback (timeout after 5 minutes)
        print("Waiting for authentication callback...")
        if self._auth_event.wait(timeout=300):
            # Exchange code for token
            return self._exchange_code_for_token(self._auth_code)
        else:
            print("✗ Authentication timeout. Please try again.")
            return False
    
    def _exchange_code_for_token(self, auth_code: str) -> bool:
        """Exchange authorization code for access token."""
        try:
            data = {
                "code": auth_code,
                "client_id": self.api_key,
                "client_secret": self.api_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            }
            
            response = requests.post(
                self.TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code == 200:
                token_data = response.json()
                self._access_token = token_data.get("access_token")
                
                # Calculate expiry (3:30 AM IST next day)
                ist = pytz.timezone('Asia/Kolkata')
                now_ist = datetime.now(ist)
                expiry_ist = now_ist.replace(hour=3, minute=30, second=0, microsecond=0)
                if now_ist.hour >= 3 and now_ist.minute >= 30:
                    expiry_ist += timedelta(days=1)
                self._token_expires_at = expiry_ist.astimezone(pytz.UTC).replace(tzinfo=None)
                
                # Save to database
                save_token(
                    access_token=self._access_token,
                    expires_at=self._token_expires_at,
                )
                
                print(f"\n✓ Authentication successful!")
                print(f"  Token expires at: {expiry_ist.strftime('%Y-%m-%d %H:%M:%S IST')}")
                return True
            else:
                print(f"✗ Token exchange failed: {response.text}")
                return False
                
        except Exception as e:
            print(f"✗ Token exchange error: {e}")
            return False
    
    def is_authenticated(self) -> bool:
        """Check if we have a valid token (without triggering auth flow)."""
        return self.access_token is not None
    
    def get_token_expiry_summary(self) -> Dict[str, Any]:
        """Human-readable token expiry for dashboard / preflight."""
        from database.operations import get_stored_token, is_token_valid

        tok = get_stored_token()
        valid = is_token_valid()
        exp = None
        if tok and tok.expires_at:
            exp = tok.expires_at.isoformat() if hasattr(tok.expires_at, "isoformat") else str(tok.expires_at)
        return {"valid": valid, "expires_at_utc": exp}
    
    def ensure_authenticated(self) -> bool:
        """Ensure we have a valid token, authenticating if necessary."""
        if self.access_token:
            return True
        # Fail-closed for unattended / CI: never open browser
        if os.getenv("TRADING_NON_INTERACTIVE", "").strip().lower() in ("1", "true", "yes"):
            print("✗ Non-interactive mode: cannot authenticate without stored token.")
            return False
        return self.authenticate()
    
    # ============== API METHODS ==============
    
    def get_profile(self) -> Dict[str, Any]:
        """Get user profile information."""
        response = requests.get(
            f"{self.BASE_URL}/user/profile",
            headers=self._get_headers()
        )
        return response.json()
    
    def get_funds_and_margin(self) -> Dict[str, Any]:
        """Get account funds and margin details."""
        response = requests.get(
            f"{self.BASE_URL}/user/get-funds-and-margin",
            headers=self._get_headers()
        )
        return response.json()
    
    def get_positions(self) -> Dict[str, Any]:
        """Get current positions."""
        response = requests.get(
            f"{self.BASE_URL}/portfolio/short-term-positions",
            headers=self._get_headers()
        )
        return response.json()
    
    def get_holdings(self) -> Dict[str, Any]:
        """Get current holdings (delivery positions)."""
        response = requests.get(
            f"{self.BASE_URL}/portfolio/long-term-holdings",
            headers=self._get_headers()
        )
        return response.json()
    
    def get_order_book(self) -> Dict[str, Any]:
        """Get all orders for the day."""
        response = requests.get(
            f"{self.BASE_URL}/order/retrieve-all",
            headers=self._get_headers()
        )
        return response.json()
    
    def get_trade_history(self) -> Dict[str, Any]:
        """Get trade history for the day."""
        response = requests.get(
            f"{self.BASE_URL}/order/trades/get-trades-for-day",
            headers=self._get_headers()
        )
        return response.json()
    
    def get_market_quote(self, symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """
        Get real-time market quote for a symbol.
        Dynamically fetches instrument key from Upstox instrument master.
        """
        # Use dynamic instrument master (no more hardcoding!)
        master = get_instrument_master()
        inst = master.get(symbol.upper())
        
        if inst:
            instrument_key = inst.instrument_key
        else:
            # Fallback: try symbol directly (might work for some instruments)
            instrument_key = f"{exchange}_EQ|{symbol.upper()}"
        
        response = requests.get(
            f"{self.BASE_URL}/market-quote/quotes",
            headers=self._get_headers(),
            params={"instrument_key": instrument_key}
        )
        return response.json()
    
    def get_instrument_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get full instrument info from dynamic master.
        Returns symbol, name, ISIN, instrument_key, etc.
        """
        master = get_instrument_master()
        inst = master.get(symbol.upper())
        if inst:
            return {
                "symbol": inst.symbol,
                "name": inst.name,
                "isin": inst.isin,
                "instrument_key": inst.instrument_key,
                "segment": inst.segment,
                "instrument_type": inst.instrument_type,
                "lot_size": inst.lot_size,
                "exchange": inst.exchange,
            }
        return None
    
    def search_symbols(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Search instruments dynamically.
        """
        master = get_instrument_master()
        results = master.search(query, limit=limit)
        return [
            {
                "symbol": r.symbol,
                "name": r.name,
                "isin": r.isin,
                "instrument_key": r.instrument_key,
            }
            for r in results
        ]
    
    def get_all_tradeable_equity(self) -> List[str]:
        """
        Get all tradeable equity symbols from Upstox.
        Use this instead of hardcoded symbol lists!
        """
        master = get_instrument_master()
        return [i.symbol for i in master.get_all_equity()]
    
    def get_nifty50_symbols(self) -> List[str]:
        """Get NIFTY 50 constituent symbols."""
        master = get_instrument_master()
        return master.get_nifty50()
    
    def get_etf_symbols(self) -> List[str]:
        """Get all ETF symbols."""
        master = get_instrument_master()
        return [i.symbol for i in master.get_etfs()]
    
    def get_historical_candles(
        self,
        instrument_key: str,
        interval: str = "day",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get historical OHLC data.
        
        Args:
            instrument_key: e.g., "NSE_EQ|INE528G01035"
            interval: "1minute", "30minute", "day", "week", "month"
            from_date: YYYY-MM-DD format
            to_date: YYYY-MM-DD format
        """
        if not to_date:
            to_date = datetime.now().strftime("%Y-%m-%d")
        if not from_date:
            from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        
        response = requests.get(
            f"{self.BASE_URL}/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}",
            headers=self._get_headers()
        )
        return response.json()
    
    def search_instruments(self, query: str, exchange: str = "NSE") -> List[Dict]:
        """Search for instruments by name or symbol."""
        # Note: Upstox doesn't have a direct search API
        # You'd typically use their instrument master file
        # This is a placeholder for instrument lookup
        return [{"symbol": query, "exchange": exchange, "note": "Use instrument master for full search"}]
    
    def place_order(
        self,
        symbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        order_type: str = "MARKET",
        product: str = "I",  # I=Intraday, D=Delivery
        price: float = 0,
        trigger_price: float = 0,
        disclosed_quantity: int = 0,
        validity: str = "DAY",
        instrument_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Place an order on Upstox.
        
        Args:
            symbol: Trading symbol
            exchange: NSE, BSE, NFO, etc.
            transaction_type: BUY or SELL
            quantity: Number of shares
            order_type: MARKET, LIMIT, SL, SL-M
            product: I (Intraday), D (Delivery), CO, OCO
            price: Limit price (0 for market orders)
            trigger_price: Stop loss trigger price
            validity: DAY, IOC
            instrument_token: Upstox instrument token
        """
        order_data = {
            "quantity": quantity,
            "product": product,
            "validity": validity,
            "price": price,
            "tag": "AI_TRADING_AGENT",
            "instrument_token": instrument_token or f"{exchange}_EQ|{symbol}",
            "order_type": order_type,
            "transaction_type": transaction_type,
            "disclosed_quantity": disclosed_quantity,
            "trigger_price": trigger_price,
            "is_amo": False,
        }
        
        response = requests.post(
            f"{self.BASE_URL}/order/place",
            headers=self._get_headers(),
            json=order_data
        )
        return response.json()
    
    def modify_order(
        self,
        order_id: str,
        *,
        quantity: Optional[int] = None,
        price: float = 0,
        order_type: str = "LIMIT",
        validity: str = "DAY",
        disclosed_quantity: int = 0,
        trigger_price: float = 0,
    ) -> Dict[str, Any]:
        """
        Modify an open/pending order (Upstox v2 PUT /order/modify).
        """
        body: Dict[str, Any] = {
            "order_id": order_id,
            "validity": validity,
            "disclosed_quantity": disclosed_quantity,
        }
        if quantity is not None:
            body["quantity"] = quantity
        body["price"] = price
        body["order_type"] = order_type
        body["trigger_price"] = trigger_price
        response = requests.put(
            f"{self.BASE_URL}/order/modify",
            headers=self._get_headers(),
            json=body,
        )
        return response.json()
    
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an existing order."""
        response = requests.delete(
            f"{self.BASE_URL}/order/cancel",
            headers=self._get_headers(),
            params={"order_id": order_id}
        )
        return response.json()
    
    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Get status of a specific order."""
        response = requests.get(
            f"{self.BASE_URL}/order/details",
            headers=self._get_headers(),
            params={"order_id": order_id}
        )
        return response.json()
    
    # ============== F&O API METHODS ==============
    
    def get_option_chain(
        self,
        instrument_key: str,
        expiry_date: str,
    ) -> Dict[str, Any]:
        """
        Get option chain data for an underlying.
        
        Args:
            instrument_key: Underlying instrument key (e.g., "NSE_INDEX|Nifty 50")
            expiry_date: Expiry date in YYYY-MM-DD format
        
        Returns:
            Option chain with all strikes, call/put prices, OI, Greeks
        """
        response = requests.get(
            f"{self.BASE_URL}/option/chain",
            headers=self._get_headers(),
            params={
                "instrument_key": instrument_key,
                "expiry_date": expiry_date,
            }
        )
        return response.json()
    
    def get_option_contracts(
        self,
        instrument_key: str,
    ) -> Dict[str, Any]:
        """
        Get all available option contracts for an underlying.
        
        Args:
            instrument_key: Underlying instrument key
        
        Returns:
            List of available option contracts with expiry dates
        """
        response = requests.get(
            f"{self.BASE_URL}/option/contract",
            headers=self._get_headers(),
            params={"instrument_key": instrument_key}
        )
        return response.json()
    
    def get_market_quote_ohlc(
        self,
        instrument_key: str,
        interval: str = "1d",
    ) -> Dict[str, Any]:
        """
        Get OHLC market quote for any instrument (equity, F&O).
        
        Args:
            instrument_key: Full instrument key (e.g., "NSE_FO|NIFTY24MAY22000CE")
            interval: "1d" for daily, "1minute" for intraday
        """
        response = requests.get(
            f"{self.BASE_URL}/market-quote/ohlc",
            headers=self._get_headers(),
            params={
                "instrument_key": instrument_key,
                "interval": interval,
            }
        )
        return response.json()
    
    def get_market_quote_ltp(
        self,
        instrument_keys: List[str],
    ) -> Dict[str, Any]:
        """
        Get LTP for multiple instruments (batch quote).
        
        Args:
            instrument_keys: List of instrument keys
        
        Returns:
            LTP for all requested instruments
        """
        # Upstox accepts comma-separated instrument keys
        keys_param = ",".join(instrument_keys)
        response = requests.get(
            f"{self.BASE_URL}/market-quote/ltp",
            headers=self._get_headers(),
            params={"instrument_key": keys_param}
        )
        return response.json()
    
    def get_full_market_quote(
        self,
        instrument_key: str,
    ) -> Dict[str, Any]:
        """
        Get full market quote including depth, OI, Greeks for F&O.
        
        Args:
            instrument_key: Full instrument key
        
        Returns:
            Complete market data including OI for F&O
        """
        response = requests.get(
            f"{self.BASE_URL}/market-quote/quotes",
            headers=self._get_headers(),
            params={"instrument_key": instrument_key}
        )
        return response.json()
    
    def place_fo_order(
        self,
        instrument_token: str,
        transaction_type: str,
        quantity: int,
        order_type: str = "MARKET",
        product: str = "I",  # I=Intraday(MIS), D=Delivery(NRML)
        price: float = 0,
        trigger_price: float = 0,
        validity: str = "DAY",
    ) -> Dict[str, Any]:
        """
        Place an F&O order.
        
        Args:
            instrument_token: F&O instrument token (e.g., "NSE_FO|NIFTY24MAY22000CE")
            transaction_type: BUY or SELL
            quantity: Lot size * number of lots
            order_type: MARKET, LIMIT, SL, SL-M
            product: I (MIS/Intraday), D (NRML/Carry Forward)
            price: Limit price
            trigger_price: Stop loss trigger
            validity: DAY, IOC
        
        Returns:
            Order response with order_id
        """
        order_data = {
            "quantity": quantity,
            "product": product,
            "validity": validity,
            "price": price,
            "tag": "AI_FO_AGENT",
            "instrument_token": instrument_token,
            "order_type": order_type,
            "transaction_type": transaction_type,
            "disclosed_quantity": 0,
            "trigger_price": trigger_price,
            "is_amo": False,
        }
        
        response = requests.post(
            f"{self.BASE_URL}/order/place",
            headers=self._get_headers(),
            json=order_data
        )
        return response.json()

    def parse_order_details(self, resp: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize order/details API response for fill polling.
        """
        out: Dict[str, Any] = {
            "raw_status": "",
            "normalized": "unknown",
            "average_price": 0.0,
            "filled_quantity": 0,
            "pending_quantity": 0,
            "message": "",
        }
        if not resp or resp.get("status") != "success":
            out["message"] = str(resp.get("message") or resp.get("errors") or "")
            return out
        data = resp.get("data")
        if isinstance(data, dict):
            row = data
        elif isinstance(data, list) and data:
            row = data[0]
        else:
            return out
        status = str(
            row.get("status")
            or row.get("order_status")
            or row.get("state")
            or ""
        ).lower()
        out["raw_status"] = status
        # Map Upstox statuses to lifecycle
        if status in ("complete", "filled", "success", "executed", "traded"):
            out["normalized"] = "complete"
        elif status in ("rejected", "cancelled", "canceled"):
            out["normalized"] = "terminal_failed"
        elif status in ("open", "pending", "trigger pending", "trigger_pending", "validation pending"):
            out["normalized"] = "open"
        else:
            out["normalized"] = "open"
        for key in ("average_price", "average_traded_price", "price"):
            v = row.get(key)
            if v is not None:
                try:
                    out["average_price"] = float(v)
                    break
                except (TypeError, ValueError):
                    pass
        for key in ("filled_quantity", "filled_qty"):
            v = row.get(key)
            if v is not None:
                try:
                    out["filled_quantity"] = int(float(v))
                    break
                except (TypeError, ValueError):
                    pass
        pq = row.get("pending_quantity")
        if pq is not None:
            try:
                out["pending_quantity"] = int(float(pq))
            except (TypeError, ValueError):
                pass
        if out["normalized"] == "complete" and out["filled_quantity"] <= 0:
            try:
                out["filled_quantity"] = int(float(row.get("quantity") or 0))
            except (TypeError, ValueError):
                pass
        return out

    def wait_for_fill(
        self,
        order_id: str,
        *,
        timeout_s: float = 45.0,
        poll_s: float = 2.0,
    ) -> Dict[str, Any]:
        """
        Poll order details until filled, rejected/cancelled, or timeout.
        Returns parse_order_details fields plus order_id and timed_out bool.
        """
        import time as _time

        deadline = _time.time() + timeout_s
        last: Dict[str, Any] = {}
        while _time.time() < deadline:
            raw = self.get_order_status(order_id)
            last = self.parse_order_details(raw)
            last["order_id"] = order_id
            if last.get("normalized") == "complete":
                last["timed_out"] = False
                return last
            if last.get("normalized") == "terminal_failed":
                last["timed_out"] = False
                return last
            _time.sleep(poll_s)
        last = last if last else self.parse_order_details(self.get_order_status(order_id))
        last["order_id"] = order_id
        last["timed_out"] = True
        last.setdefault("normalized", "open")
        return last

    def get_fo_pnl_summary(self) -> Dict[str, Any]:
        """
        Aggregate realised / unrealised PnL from short-term positions (broker truth).
        """
        out: Dict[str, Any] = {
            "ok": False,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
            "margin_used": 0.0,
            "available_margin": 0.0,
            "fo_rows": 0,
            "error": "",
        }
        try:
            resp = self.get_positions()
        except Exception as e:
            out["error"] = str(e)
            return out
        if resp.get("status") != "success":
            out["error"] = str(resp.get("message") or resp.get("errors") or "positions_failed")
            return out
        rows = resp.get("data")
        if isinstance(rows, dict) and "positions" in rows:
            rows = rows.get("positions") or []
        if not isinstance(rows, list):
            rows = []
        realized = 0.0
        unrealized = 0.0
        for r in rows:
            seg = str(r.get("segment") or r.get("exchange") or "").upper()
            sym = str(r.get("tradingsymbol") or r.get("trading_symbol") or r.get("symbol") or "")
            is_fo = "FO" in seg or "NFO" in seg or sym.endswith("CE") or sym.endswith("PE")
            if not is_fo:
                continue
            out["fo_rows"] += 1
            for key in ("realised", "realized", "realised_pnl", "realized_pnl", "pnl_realized"):
                v = r.get(key)
                if v is not None:
                    try:
                        realized += float(v)
                    except (TypeError, ValueError):
                        pass
                    break
            for key in ("unrealised", "unrealized", "unrealised_pnl", "unrealized_pnl", "pnl"):
                v = r.get(key)
                if v is not None and key not in ("realised", "realized", "realised_pnl", "realized_pnl"):
                    try:
                        unrealized += float(v)
                    except (TypeError, ValueError):
                        pass
        # Fallback: single pnl field
        if out["fo_rows"] and realized == 0.0 and unrealized == 0.0:
            for r in rows:
                seg = str(r.get("segment") or "").upper()
                if "FO" not in seg and "NFO" not in seg:
                    continue
                v = r.get("pnl") or r.get("m2m") or r.get("net_change")
                if v is not None:
                    try:
                        unrealized += float(v)
                    except (TypeError, ValueError):
                        pass
        out["realized_pnl"] = round(realized, 2)
        out["unrealized_pnl"] = round(unrealized, 2)
        out["total_pnl"] = round(realized + unrealized, 2)
        out["ok"] = True
        try:
            funds = self.get_funds_and_margin()
            if funds.get("status") == "success" and funds.get("data"):
                eq = funds["data"].get("equity") or {}
                out["available_margin"] = float(eq.get("available_margin") or 0)
                out["margin_used"] = float(eq.get("used_margin") or 0)
        except Exception:
            pass
        return out
    
    def get_fo_pnl_today(self) -> Dict[str, Any]:
        """Same as get_fo_pnl_summary; named for risk / daily P&L checks."""
        return self.get_fo_pnl_summary()
    
    def get_instrument_key(
        self,
        symbol: str,
        exchange: str = "NSE",
        instrument_type: str = "EQ",
    ) -> str:
        """
        Build instrument key from symbol.
        
        Args:
            symbol: Trading symbol (e.g., "NIFTY", "RELIANCE")
            exchange: NSE, BSE, NFO, BFO
            instrument_type: EQ (equity), INDEX, FUT, CE (call), PE (put)
        
        Returns:
            Formatted instrument key
        """
        if instrument_type == "INDEX":
            return f"{exchange}_INDEX|{symbol}"
        elif instrument_type in ["FUT", "CE", "PE"]:
            return f"{exchange}_FO|{symbol}"
        else:
            return f"{exchange}_{instrument_type}|{symbol}"
    
    def get_nifty_expiries(self) -> List[str]:
        """Get available Nifty option expiry dates."""
        try:
            result = self.get_option_contracts("NSE_INDEX|Nifty 50")
            if result.get("status") == "success":
                expiries = result.get("data", [])
                return sorted(list(set([e.get("expiry") for e in expiries if e.get("expiry")])))
        except Exception:
            pass
        return []
    
    def get_banknifty_expiries(self) -> List[str]:
        """Get available Bank Nifty option expiry dates."""
        try:
            result = self.get_option_contracts("NSE_INDEX|Nifty Bank")
            if result.get("status") == "success":
                expiries = result.get("data", [])
                return sorted(list(set([e.get("expiry") for e in expiries if e.get("expiry")])))
        except Exception:
            pass
        return []

    # ============== GTT (Good Till Triggered) — Upstox v3 ==============

    GTT_BASE = "https://api.upstox.com/v3"

    def place_gtt_order(
        self,
        *,
        gtt_type: str,
        quantity: int,
        product: str,
        instrument_token: str,
        transaction_type: str,
        rules: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Place a GTT order (v3).

        Args:
            gtt_type: SINGLE or MULTIPLE
            quantity: order qty (lots * lot_size)
            product: I (intraday), D (delivery), MTF
            instrument_token: e.g. NSE_FO|NIFTY…
            transaction_type: BUY or SELL
            rules: list of dicts with strategy/trigger_type/trigger_price/
                   optional trailing_gap and market_protection

        Returns:
            API response with gtt_order_ids on success.
        """
        body = {
            "type": gtt_type,
            "quantity": quantity,
            "product": product,
            "instrument_token": instrument_token,
            "transaction_type": transaction_type,
            "rules": rules,
        }
        response = requests.post(
            f"{self.GTT_BASE}/order/gtt/place",
            headers=self._get_headers(),
            json=body,
        )
        return response.json()

    def cancel_gtt_order(self, gtt_order_id: str) -> Dict[str, Any]:
        """Cancel a pending GTT order (v3)."""
        response = requests.delete(
            f"{self.GTT_BASE}/order/gtt/cancel",
            headers=self._get_headers(),
            json={"gtt_order_id": gtt_order_id},
        )
        return response.json()

    def get_gtt_order_details(self, gtt_order_id: str) -> Dict[str, Any]:
        """Get status/details of a GTT order (v3)."""
        response = requests.get(
            f"{self.GTT_BASE}/order/gtt",
            headers=self._get_headers(),
            params={"gtt_order_id": gtt_order_id},
        )
        return response.json()

    def modify_gtt_order(
        self,
        *,
        gtt_order_id: str,
        gtt_type: str,
        quantity: int,
        rules: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Modify a pending GTT order (v3)."""
        body = {
            "gtt_order_id": gtt_order_id,
            "type": gtt_type,
            "quantity": quantity,
            "rules": rules,
        }
        response = requests.put(
            f"{self.GTT_BASE}/order/gtt/modify",
            headers=self._get_headers(),
            json=body,
        )
        return response.json()

    def gtt_rule_status(self, gtt_order_id: str) -> Dict[str, Any]:
        """
        Parse GTT details into a summary of each rule's status.
        Returns {ok, rules: [{strategy, status, order_id, trigger_price}], raw}.
        """
        resp = self.get_gtt_order_details(gtt_order_id)
        out: Dict[str, Any] = {"ok": False, "rules": [], "raw": resp}
        if resp.get("status") != "success":
            return out
        data = resp.get("data")
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            return out
        out["ok"] = True
        for r in data.get("rules", []):
            out["rules"].append({
                "strategy": r.get("strategy"),
                "status": r.get("status"),
                "order_id": r.get("order_id"),
                "trigger_price": r.get("trigger_price"),
                "message": r.get("message"),
            })
        return out


# Singleton instance
_client_instance = None

def get_upstox_client() -> UpstoxClient:
    """Get or create the Upstox client singleton."""
    global _client_instance
    if _client_instance is None:
        _client_instance = UpstoxClient()
    return _client_instance


if __name__ == "__main__":
    # Test authentication
    client = UpstoxClient()
    if client.ensure_authenticated():
        print("\nFetching profile...")
        profile = client.get_profile()
        print(json.dumps(profile, indent=2))
        
        print("\nFetching funds...")
        funds = client.get_funds_and_margin()
        print(json.dumps(funds, indent=2))
