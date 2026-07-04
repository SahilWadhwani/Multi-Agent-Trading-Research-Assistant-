"""
Upstox WebSocket V3 Price Feed.

Connects to Upstox Market Data Feed V3 (protobuf) for real-time option prices.
Falls back to REST polling if WebSocket is unavailable.
Feeds prices into PriceHub for instant exit monitoring.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set

import pytz
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.price_hub import get_price_hub, PriceHub

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# Protobuf import
try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "proto"))
    import MarketDataFeed_pb2 as pb
    PROTOBUF_AVAILABLE = True
except ImportError:
    PROTOBUF_AVAILABLE = False
    logger.warning("protobuf not available - WebSocket V3 disabled")

# Async websockets import
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets library not installed")


AUTH_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"


class UpstoxWSFeed:
    """
    Upstox WebSocket V3 feed with automatic reconnection.

    Subscribes to instrument keys and pushes LTP updates to PriceHub.
    Runs in a dedicated thread with its own asyncio event loop.
    """

    def __init__(self, access_token: str):
        self._access_token = access_token
        self._hub: PriceHub = get_price_hub()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws = None
        self._connected = False
        self._subscribed_keys: Set[str] = set()
        self._reconnect_delay = 2.0
        self._max_reconnect_delay = 30.0

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self, initial_keys: Optional[List[str]] = None) -> None:
        """Start the WebSocket feed in a background thread."""
        if self._running:
            return
        if not WEBSOCKETS_AVAILABLE or not PROTOBUF_AVAILABLE:
            logger.warning("WebSocket V3 unavailable (missing deps). Using REST fallback only.")
            return

        if initial_keys:
            self._subscribed_keys.update(initial_keys)

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ws-feed")
        self._thread.start()
        logger.info("WebSocket feed thread started")

    def stop(self) -> None:
        """Stop the WebSocket feed."""
        self._running = False
        if self._ws and self._loop and self._loop.is_running():
            async def _close_ws():
                try:
                    await self._ws.close()
                except Exception:
                    pass
            asyncio.run_coroutine_threadsafe(_close_ws(), self._loop)
            time.sleep(0.3)
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        self._connected = False
        logger.info("WebSocket feed stopped")

    def subscribe(self, instrument_keys: List[str]) -> None:
        """Add instrument keys to subscription (thread-safe)."""
        new_keys = set(instrument_keys) - self._subscribed_keys
        if not new_keys:
            return
        self._subscribed_keys.update(new_keys)
        for key in new_keys:
            self._hub.add_subscription(key)
        if self._connected and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send_subscribe(list(new_keys)), self._loop
            )

    def unsubscribe(self, instrument_keys: List[str]) -> None:
        """Remove instrument keys from subscription."""
        remove_keys = set(instrument_keys) & self._subscribed_keys
        if not remove_keys:
            return
        self._subscribed_keys -= remove_keys
        for key in remove_keys:
            self._hub.remove_subscription(key)
        if self._connected and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send_unsubscribe(list(remove_keys)), self._loop
            )

    def _run_loop(self) -> None:
        """Runs the asyncio event loop in a dedicated thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connection_loop())
        except Exception as e:
            if self._running:
                logger.error("WebSocket loop crashed: %s", e)
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self._loop.close()
            self._connected = False

    async def _connection_loop(self) -> None:
        """Reconnection loop - keeps trying until stopped."""
        delay = self._reconnect_delay
        while self._running:
            try:
                await self._connect_and_stream()
            except Exception as e:
                logger.warning("WebSocket disconnected: %s", e)

            self._connected = False
            if not self._running:
                break
            logger.info("Reconnecting in %.1fs...", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, self._max_reconnect_delay)

    async def _connect_and_stream(self) -> None:
        """Single connection session."""
        redirect_uri = self._get_authorized_uri()
        if not redirect_uri:
            raise RuntimeError("Failed to get WebSocket authorize URI")

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        async with websockets.connect(redirect_uri, ssl=ssl_ctx) as ws:
            self._ws = ws
            self._connected = True
            self._reconnect_delay = 2.0
            logger.info("WebSocket V3 connected")

            await asyncio.sleep(0.5)

            if self._subscribed_keys:
                await self._send_subscribe(list(self._subscribed_keys))

            async for message in ws:
                if not self._running:
                    break
                self._decode_and_dispatch(message)

    def _get_authorized_uri(self) -> Optional[str]:
        """Call Upstox V3 authorize endpoint to get redirect URI."""
        try:
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self._access_token}",
            }
            resp = requests.get(AUTH_URL, headers=headers, timeout=10)
            data = resp.json()
            if data.get("status") == "success":
                return data["data"]["authorized_redirect_uri"]
            logger.error("Auth failed: %s", data)
        except Exception as e:
            logger.error("Auth request error: %s", e)
        return None

    async def _send_subscribe(self, keys: List[str]) -> None:
        """Send subscription message for instrument keys."""
        if not self._ws:
            return
        msg = {
            "guid": f"sub-{int(time.time())}",
            "method": "sub",
            "data": {
                "mode": "ltpc",
                "instrumentKeys": keys,
            },
        }
        binary = json.dumps(msg).encode("utf-8")
        await self._ws.send(binary)
        logger.info("Subscribed to %d keys", len(keys))

    async def _send_unsubscribe(self, keys: List[str]) -> None:
        """Send unsubscribe message."""
        if not self._ws:
            return
        msg = {
            "guid": f"unsub-{int(time.time())}",
            "method": "unsub",
            "data": {
                "instrumentKeys": keys,
            },
        }
        binary = json.dumps(msg).encode("utf-8")
        await self._ws.send(binary)

    def _decode_and_dispatch(self, raw: bytes) -> None:
        """Decode protobuf message and push to PriceHub."""
        try:
            feed_resp = pb.FeedResponse()
            feed_resp.ParseFromString(raw)

            if feed_resp.type == pb.market_info:
                return

            for instrument_key, feed in feed_resp.feeds.items():
                ltp = 0.0
                volume = 0
                oi = 0.0

                if feed.HasField("ltpc"):
                    ltp = feed.ltpc.ltp
                elif feed.HasField("fullFeed"):
                    ff = feed.fullFeed
                    if ff.HasField("marketFF"):
                        ltp = ff.marketFF.ltpc.ltp
                        volume = ff.marketFF.vtt
                        oi = ff.marketFF.oi
                    elif ff.HasField("indexFF"):
                        ltp = ff.indexFF.ltpc.ltp
                elif feed.HasField("firstLevelWithGreeks"):
                    ltp = feed.firstLevelWithGreeks.ltpc.ltp
                    volume = feed.firstLevelWithGreeks.vtt
                    oi = feed.firstLevelWithGreeks.oi

                if ltp > 0:
                    self._hub.update(instrument_key, ltp, volume=volume, oi=oi)

        except Exception as e:
            logger.debug("Protobuf decode error: %s", e)


class RESTPoller:
    """
    Fallback REST poller for when WebSocket is unavailable or stale.
    Polls subscribed keys every poll_interval seconds.
    """

    def __init__(self, access_token: str, poll_interval: float = 10.0):
        self._access_token = access_token
        self._hub = get_price_hub()
        self._poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="rest-poller")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _poll_loop(self) -> None:
        from mcp_server.upstox_client import get_upstox_client

        while self._running:
            try:
                keys = self._hub.get_subscriptions()
                if not keys:
                    time.sleep(self._poll_interval)
                    continue

                stale_keys = [k for k in keys if self._hub.is_stale(k, max_age_s=25.0)]
                if not stale_keys:
                    time.sleep(self._poll_interval)
                    continue

                client = get_upstox_client()
                for key in stale_keys:
                    if not self._running:
                        break
                    try:
                        result = client.get_full_market_quote(key)
                        if result.get("status") == "success" and result.get("data"):
                            data = result["data"]
                            quote_key = next(iter(data.keys()), None)
                            if quote_key:
                                ltp = data[quote_key].get("last_price") or data[quote_key].get("ltp")
                                if ltp:
                                    self._hub.update(key, float(ltp))
                    except Exception as e:
                        logger.debug("REST poll error for %s: %s", key, e)

                time.sleep(self._poll_interval)
            except Exception as e:
                logger.warning("REST poller error: %s", e)
                time.sleep(self._poll_interval)


class PriceFeedManager:
    """
    Manages both WebSocket and REST fallback feeds.

    - Starts WebSocket as primary
    - REST poller activates for stale keys
    """

    def __init__(self, access_token: str):
        self._access_token = access_token
        self._ws_feed: Optional[UpstoxWSFeed] = None
        self._rest_poller: Optional[RESTPoller] = None
        self._started = False

    @property
    def connected(self) -> bool:
        if self._ws_feed:
            return self._ws_feed.connected
        return False

    def start(self, initial_keys: Optional[List[str]] = None) -> None:
        """Start price feed (WS + REST fallback)."""
        if self._started:
            return
        self._started = True

        if WEBSOCKETS_AVAILABLE and PROTOBUF_AVAILABLE:
            self._ws_feed = UpstoxWSFeed(self._access_token)
            self._ws_feed.start(initial_keys)
            print("   WebSocket V3 feed starting...")
        else:
            print("   WebSocket unavailable, using REST polling only")

        self._rest_poller = RESTPoller(self._access_token, poll_interval=10.0)
        self._rest_poller.start()

        if initial_keys:
            hub = get_price_hub()
            for key in initial_keys:
                hub.add_subscription(key)

    def stop(self) -> None:
        """Stop all feeds."""
        if self._ws_feed:
            self._ws_feed.stop()
        if self._rest_poller:
            self._rest_poller.stop()
        self._started = False

    def subscribe(self, instrument_keys: List[str]) -> None:
        """Subscribe to instrument keys."""
        hub = get_price_hub()
        for key in instrument_keys:
            hub.add_subscription(key)
        if self._ws_feed:
            self._ws_feed.subscribe(instrument_keys)

    def unsubscribe(self, instrument_keys: List[str]) -> None:
        """Unsubscribe from instrument keys."""
        hub = get_price_hub()
        for key in instrument_keys:
            hub.remove_subscription(key)
        if self._ws_feed:
            self._ws_feed.unsubscribe(instrument_keys)

    def get_status(self) -> Dict[str, Any]:
        hub = get_price_hub()
        return {
            "ws_connected": self.connected,
            "subscriptions": list(hub.get_subscriptions()),
            "cached_prices": len(hub.get_all_prices()),
            "started": self._started,
        }


_manager: Optional[PriceFeedManager] = None


def get_price_feed_manager() -> Optional[PriceFeedManager]:
    """Get the global PriceFeedManager (None if not started)."""
    return _manager


def start_price_feed(access_token: str, initial_keys: Optional[List[str]] = None) -> PriceFeedManager:
    """Start the global price feed manager."""
    global _manager
    if _manager is not None:
        return _manager
    _manager = PriceFeedManager(access_token)
    _manager.start(initial_keys)
    return _manager


def stop_price_feed() -> None:
    """Stop the global price feed manager."""
    global _manager
    if _manager:
        _manager.stop()
        _manager = None
