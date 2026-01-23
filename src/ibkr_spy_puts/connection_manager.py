"""Persistent connection manager for IB Gateway.

Maintains a single persistent connection to the gateway in a background thread,
providing real-time status updates to the dashboard without spawning subprocesses.
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ib_insync import IB, Option, Stock

from ibkr_spy_puts.config import TWSSettings

logger = logging.getLogger(__name__)


@dataclass
class ConnectionStatus:
    """Current connection status."""
    connected: bool = False
    logged_in: bool = False
    account: str | None = None
    trading_mode: str | None = None
    ready_to_trade: bool = False
    error: str | None = None
    last_update: datetime | None = None


@dataclass
class SpyPrice:
    """SPY price data."""
    price: float | None = None
    close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    last_update: datetime | None = None


@dataclass
class CachedData:
    """Cached data from IBKR."""
    status: ConnectionStatus = field(default_factory=ConnectionStatus)
    orders: list[dict] = field(default_factory=list)
    positions: list[dict] = field(default_factory=list)
    spy_price: SpyPrice = field(default_factory=SpyPrice)


class IBConnectionManager:
    """Manages a persistent connection to IB Gateway.

    Runs in a background thread with its own event loop to avoid
    conflicts with FastAPI's async event loop.
    """

    def __init__(self, settings: TWSSettings | None = None):
        self.settings = settings or TWSSettings()
        self.ib = IB()
        self._cache = CachedData()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._spy_ticker = None
        self._spy_contract = None

    def start(self):
        """Start the connection manager in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Connection manager already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Connection manager started")

    def stop(self):
        """Stop the connection manager."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        if self.ib.isConnected():
            self.ib.disconnect()
        logger.info("Connection manager stopped")

    def _run(self):
        """Main loop running in background thread."""
        # Create a new event loop for this thread
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        while not self._stop_event.is_set():
            try:
                self._ensure_connected()
                if self.ib.isConnected():
                    self._update_cache()
            except Exception as e:
                logger.error(f"Connection manager error: {e}")
                self._update_status(connected=False, error=str(e))

            # Wait before next update (5 seconds)
            self._stop_event.wait(5)

        # Cleanup
        if self.ib.isConnected():
            self.ib.disconnect()

    def _ensure_connected(self):
        """Ensure we're connected to the gateway."""
        if self.ib.isConnected():
            return

        try:
            logger.info(f"Connecting to {self.settings.host}:{self.settings.port}")
            self.ib.connect(
                self.settings.host,
                self.settings.port,
                clientId=50,  # Dedicated client ID for connection manager
                readonly=True,
                timeout=15,
            )

            # Get account info
            accounts = self.ib.managedAccounts()
            if accounts:
                account = accounts[0]
                trading_mode = "PAPER" if account.startswith("DU") else "LIVE"
                self._update_status(
                    connected=True,
                    logged_in=True,
                    account=account,
                    trading_mode=trading_mode,
                    ready_to_trade=True,
                )
                logger.info(f"Connected to {trading_mode} account {account}")

                # Subscribe to SPY market data
                self._subscribe_spy_data()
            else:
                self._update_status(connected=True, logged_in=False)

        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            self._update_status(connected=False, error=str(e))

    def _subscribe_spy_data(self):
        """Subscribe to SPY market data."""
        try:
            # Use live data (type 1) - we have Cboe One subscription for BATS
            self.ib.reqMarketDataType(1)

            # Use BATS exchange (part of Cboe, covered by Cboe One subscription)
            self._spy_contract = Stock("SPY", "BATS", "USD")
            self._spy_ticker = self.ib.reqMktData(self._spy_contract, "", False, False)
            logger.info("Subscribed to SPY live market data via BATS")
        except Exception as e:
            logger.error(f"Failed to subscribe to SPY data: {e}")

    def _update_cache(self):
        """Update cached orders and positions."""
        try:
            # Request open orders
            self.ib.reqAllOpenOrders()
            self.ib.sleep(0.5)

            orders = []
            for trade in self.ib.openTrades():
                c, o, s = trade.contract, trade.order, trade.orderStatus
                orders.append({
                    "symbol": c.symbol,
                    "sec_type": c.secType,
                    "strike": getattr(c, "strike", None),
                    "expiration": getattr(c, "lastTradeDateOrContractMonth", None),
                    "right": getattr(c, "right", None),
                    "action": o.action,
                    "order_type": o.orderType,
                    "quantity": int(o.totalQuantity),
                    "limit_price": o.lmtPrice if o.lmtPrice else None,
                    "stop_price": o.auxPrice if o.auxPrice else None,
                    "status": s.status,
                    "filled": int(s.filled),
                    "remaining": int(s.remaining),
                    "oca_group": o.ocaGroup if o.ocaGroup else None,
                })

            # Get positions
            positions = []
            for pos in self.ib.positions():
                c = pos.contract
                if c.secType == "OPT":
                    positions.append({
                        "symbol": c.symbol,
                        "strike": c.strike,
                        "expiration": c.lastTradeDateOrContractMonth,
                        "right": c.right,
                        "quantity": int(pos.position),
                        "avg_cost": pos.avgCost,
                    })

            # Update SPY price from ticker
            spy_price = SpyPrice(last_update=datetime.now())
            if self._spy_ticker:
                # Get price (prefer last, then mid)
                if self._spy_ticker.last and self._spy_ticker.last > 0:
                    spy_price.price = self._spy_ticker.last
                elif self._spy_ticker.bid and self._spy_ticker.bid > 0:
                    spy_price.price = (self._spy_ticker.bid + self._spy_ticker.ask) / 2

                # Get previous close
                if self._spy_ticker.close and self._spy_ticker.close > 0:
                    spy_price.close = self._spy_ticker.close

                # Calculate change
                if spy_price.price and spy_price.close:
                    spy_price.change = round(spy_price.price - spy_price.close, 2)
                    spy_price.change_pct = round((spy_price.change / spy_price.close) * 100, 2)

            with self._lock:
                self._cache.orders = orders
                self._cache.positions = positions
                self._cache.spy_price = spy_price
                self._cache.status.last_update = datetime.now()

        except Exception as e:
            logger.error(f"Failed to update cache: {e}")
            # Connection might be lost
            if not self.ib.isConnected():
                self._update_status(connected=False, error=str(e))

    def _update_status(
        self,
        connected: bool = False,
        logged_in: bool = False,
        account: str | None = None,
        trading_mode: str | None = None,
        ready_to_trade: bool = False,
        error: str | None = None,
    ):
        """Update connection status."""
        with self._lock:
            self._cache.status = ConnectionStatus(
                connected=connected,
                logged_in=logged_in,
                account=account,
                trading_mode=trading_mode,
                ready_to_trade=ready_to_trade,
                error=error,
                last_update=datetime.now(),
            )

    def get_status(self) -> dict:
        """Get current connection status."""
        with self._lock:
            status = self._cache.status
            return {
                "connected": status.connected,
                "logged_in": status.logged_in,
                "account": status.account,
                "trading_mode": status.trading_mode,
                "ready_to_trade": status.ready_to_trade,
                "error": status.error,
                "last_update": status.last_update.isoformat() if status.last_update else None,
            }

    def get_orders(self) -> list[dict]:
        """Get cached orders."""
        with self._lock:
            return self._cache.orders.copy()

    def get_positions(self) -> list[dict]:
        """Get cached positions."""
        with self._lock:
            return self._cache.positions.copy()

    def get_spy_price(self) -> dict:
        """Get cached SPY price data."""
        with self._lock:
            spy = self._cache.spy_price
            # If no price data, it's likely a subscription issue
            if spy.price is None:
                return {
                    "price": None,
                    "close": None,
                    "change": None,
                    "change_pct": None,
                    "error": "No subscription",
                }
            return {
                "price": spy.price,
                "close": spy.close,
                "change": spy.change,
                "change_pct": spy.change_pct,
                "error": None,
            }

    def get_all(self) -> dict:
        """Get all cached data."""
        with self._lock:
            return {
                "connection": {
                    "connected": self._cache.status.connected,
                    "logged_in": self._cache.status.logged_in,
                    "account": self._cache.status.account,
                    "trading_mode": self._cache.status.trading_mode,
                    "ready_to_trade": self._cache.status.ready_to_trade,
                    "error": self._cache.status.error,
                },
                "live_orders": self._cache.orders.copy(),
                "ibkr_positions": self._cache.positions.copy(),
            }


# Global connection manager instance
_manager: IBConnectionManager | None = None


def get_connection_manager() -> IBConnectionManager:
    """Get the global connection manager, creating it if needed."""
    global _manager
    if _manager is None:
        _manager = IBConnectionManager()
    return _manager


def start_connection_manager():
    """Start the global connection manager."""
    manager = get_connection_manager()
    manager.start()


def stop_connection_manager():
    """Stop the global connection manager."""
    global _manager
    if _manager is not None:
        _manager.stop()
        _manager = None
