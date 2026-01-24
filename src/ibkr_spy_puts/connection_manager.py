"""Persistent connection manager for IB Gateway.

Maintains a single persistent connection to the gateway in a background thread,
providing real-time data to the dashboard without spawning subprocesses.

This is a READ-ONLY connection for dashboard data. Trading is done separately
by the scheduler with its own connection.
"""

import asyncio
import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ib_insync import IB, MarketOrder, Option, Stock

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
class PositionData:
    """Enriched position data with Greeks and P&L."""
    # From database
    id: int
    symbol: str
    strike: float
    expiration: str
    quantity: int
    entry_price: float
    entry_time: datetime | None
    expected_tp_price: float | None
    expected_sl_price: float | None
    strategy_id: str | None

    # From IBKR (live)
    current_price: float | None = None
    price_source: str | None = None  # "bid_ask", "last", "close", or None
    bid: float | None = None
    ask: float | None = None
    delta: float | None = None
    theta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    iv: float | None = None
    margin: float | None = None

    # Calculated
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None
    days_to_expiry: int | None = None
    days_in_trade: int | None = None


@dataclass
class CachedData:
    """Cached data from IBKR."""
    status: ConnectionStatus = field(default_factory=ConnectionStatus)
    orders: list[dict] = field(default_factory=list)
    positions: list[PositionData] = field(default_factory=list)
    spy_price: SpyPrice = field(default_factory=SpyPrice)
    last_update: datetime | None = None


def _is_valid(v) -> bool:
    """Check if a numeric value is valid."""
    return v is not None and not math.isnan(v) and v > 0


class IBConnectionManager:
    """Manages a persistent connection to IB Gateway.

    Runs in a background thread with its own event loop to avoid
    conflicts with FastAPI's async event loop.

    This connection is for reading dashboard data only.
    Trading is done by the scheduler with a separate connection.
    """

    def __init__(self, settings: TWSSettings | None = None):
        self.settings = settings or TWSSettings()
        self.ib = IB()
        self._cache = CachedData()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()  # Reentrant lock for nested calls
        self._loop: asyncio.AbstractEventLoop | None = None

        # Market data subscriptions
        self._spy_contract = None
        self._spy_ticker = None
        self._option_tickers: dict[str, Any] = {}  # key -> ticker
        self._option_contracts: dict[str, Option] = {}  # key -> contract

        # Database positions (refreshed periodically)
        self._db_positions: list[dict] = []

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
                    # Process events for streaming data
                    self.ib.sleep(5)
                else:
                    self._stop_event.wait(5)
            except Exception as e:
                logger.error(f"Connection manager error: {e}")
                self._update_status(connected=False, error=str(e))
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
                readonly=False,  # Need for whatIfOrder (margin calculation)
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
            # Use delayed data type (3) - no subscription required
            self.ib.reqMarketDataType(3)

            self._spy_contract = Stock("SPY", "SMART", "USD")
            self.ib.qualifyContracts(self._spy_contract)
            self._spy_ticker = self.ib.reqMktData(self._spy_contract, "", False, False)
            logger.info("SPY streaming subscription started")

        except Exception as e:
            logger.error(f"Failed to subscribe to SPY data: {e}")

    def _get_position_key(self, symbol: str, strike: float, expiration: str) -> str:
        """Generate a unique key for a position."""
        # Normalize expiration to YYYYMMDD
        exp_str = str(expiration).replace("-", "")
        return f"{symbol}_{int(strike)}_{exp_str}"

    def _load_db_positions(self):
        """Load positions from database."""
        try:
            from ibkr_spy_puts.database import Database
            from ibkr_spy_puts.config import DatabaseSettings

            db = Database(DatabaseSettings())
            db.connect()
            try:
                self._db_positions = db.get_positions_for_display()
            finally:
                db.disconnect()
        except Exception as e:
            logger.error(f"Failed to load positions from DB: {e}")

    def _subscribe_option_data(self):
        """Subscribe to market data for all option positions."""
        if not self._db_positions:
            return

        # Use delayed data for options
        self.ib.reqMarketDataType(3)

        for pos in self._db_positions:
            exp = pos['expiration']
            if hasattr(exp, 'strftime'):
                exp_str = exp.strftime('%Y%m%d')
            else:
                exp_str = str(exp).replace('-', '')

            key = self._get_position_key(pos['symbol'], float(pos['strike']), exp_str)

            # Skip if already subscribed
            if key in self._option_tickers:
                continue

            try:
                contract = Option(pos['symbol'], exp_str, float(pos['strike']), 'P', 'SMART')
                qualified = self.ib.qualifyContracts(contract)
                if qualified:
                    # Request with Greeks (tick type 106)
                    ticker = self.ib.reqMktData(qualified[0], "106", False, False)
                    self._option_tickers[key] = ticker
                    self._option_contracts[key] = qualified[0]
                    logger.debug(f"Subscribed to {key}")
            except Exception as e:
                logger.error(f"Failed to subscribe to {key}: {e}")

    def _update_spy_price(self):
        """Update SPY price from streaming ticker."""
        if not self._spy_ticker:
            return

        spy_price = SpyPrice(last_update=datetime.now())

        if _is_valid(self._spy_ticker.last):
            spy_price.price = self._spy_ticker.last
        elif _is_valid(self._spy_ticker.bid) and _is_valid(self._spy_ticker.ask):
            spy_price.price = (self._spy_ticker.bid + self._spy_ticker.ask) / 2

        if _is_valid(self._spy_ticker.close):
            spy_price.close = self._spy_ticker.close

        if spy_price.price and spy_price.close:
            spy_price.change = round(spy_price.price - spy_price.close, 2)
            spy_price.change_pct = round((spy_price.change / spy_price.close) * 100, 2)

        if spy_price.price:
            with self._lock:
                self._cache.spy_price = spy_price

    def _update_orders(self):
        """Update cached orders."""
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

        with self._lock:
            self._cache.orders = orders

    def _calculate_margin(self, contract: Option, quantity: int) -> float | None:
        """Calculate margin for a position using whatIfOrder."""
        try:
            order = MarketOrder("BUY", quantity)
            whatif = self.ib.whatIfOrder(contract, order)
            if whatif and whatif.maintMarginChange:
                maint_change = float(whatif.maintMarginChange)
                # Margin per contract
                return (-maint_change if maint_change < 0 else 0) / quantity
        except Exception as e:
            logger.debug(f"Failed to calculate margin: {e}")
        return None

    def _update_positions(self):
        """Update enriched positions with live data."""
        # Reload positions from DB periodically
        self._load_db_positions()

        # Subscribe to any new positions
        self._subscribe_option_data()

        # Wait for data to arrive
        self.ib.sleep(2)

        enriched = []
        today = datetime.now().date()

        for pos in self._db_positions:
            exp = pos['expiration']
            if hasattr(exp, 'strftime'):
                exp_str = exp.strftime('%Y%m%d')
                exp_date = exp
            else:
                exp_str = str(exp).replace('-', '')
                from datetime import datetime as dt
                exp_date = dt.strptime(exp_str, '%Y%m%d').date()

            key = self._get_position_key(pos['symbol'], float(pos['strike']), exp_str)

            # Create position data from DB
            entry_time = pos.get('entry_time')
            if hasattr(entry_time, 'date'):
                entry_date = entry_time.date()
            else:
                entry_date = today

            position_data = PositionData(
                id=pos['id'],
                symbol=pos['symbol'],
                strike=float(pos['strike']),
                expiration=exp_str,
                quantity=pos['quantity'],
                entry_price=float(pos['entry_price']),
                entry_time=entry_time,
                expected_tp_price=float(pos['expected_tp_price']) if pos.get('expected_tp_price') else None,
                expected_sl_price=float(pos['expected_sl_price']) if pos.get('expected_sl_price') else None,
                strategy_id=pos.get('strategy_id'),
                days_to_expiry=(exp_date - today).days,
                days_in_trade=(today - entry_date).days,
            )

            # Enrich with live data from ticker
            ticker = self._option_tickers.get(key)
            if ticker:
                # Price: prefer bid/ask mid, fallback to last, then close
                if _is_valid(ticker.bid) and _is_valid(ticker.ask):
                    position_data.current_price = (ticker.bid + ticker.ask) / 2
                    position_data.bid = ticker.bid
                    position_data.ask = ticker.ask
                    position_data.price_source = "bid_ask"
                elif _is_valid(ticker.last):
                    position_data.current_price = ticker.last
                    position_data.price_source = "last"
                elif _is_valid(ticker.close):
                    position_data.current_price = ticker.close
                    position_data.price_source = "close"

                # Greeks from modelGreeks
                if ticker.modelGreeks:
                    g = ticker.modelGreeks
                    position_data.delta = g.delta
                    position_data.theta = g.theta
                    position_data.gamma = g.gamma
                    position_data.vega = g.vega
                    position_data.iv = g.impliedVol

                # Calculate P&L
                if position_data.current_price and position_data.entry_price:
                    # For short puts: profit when price goes down
                    pnl = (position_data.entry_price - position_data.current_price) * 100 * position_data.quantity
                    position_data.unrealized_pnl = round(pnl, 2)

                    premium_collected = position_data.entry_price * 100 * position_data.quantity
                    if premium_collected > 0:
                        position_data.unrealized_pnl_pct = round((pnl / premium_collected) * 100, 2)

            # Get margin (do this less frequently as it's slower)
            contract = self._option_contracts.get(key)
            if contract and position_data.margin is None:
                position_data.margin = self._calculate_margin(contract, position_data.quantity)

            enriched.append(position_data)

        with self._lock:
            self._cache.positions = enriched
            self._cache.last_update = datetime.now()

    def _update_cache(self):
        """Update all cached data."""
        try:
            self._update_spy_price()
            self._update_orders()
            self._update_positions()

            with self._lock:
                self._cache.status.last_update = datetime.now()

        except Exception as e:
            logger.error(f"Failed to update cache: {e}")
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
        """Get cached enriched positions."""
        with self._lock:
            positions = []
            for p in self._cache.positions:
                positions.append({
                    "id": p.id,
                    "symbol": p.symbol,
                    "strike": p.strike,
                    "expiration": p.expiration,
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "entry_time": p.entry_time.isoformat() if p.entry_time else None,
                    "expected_tp_price": p.expected_tp_price,
                    "expected_sl_price": p.expected_sl_price,
                    "strategy_id": p.strategy_id,
                    "current_price": p.current_price,
                    "price_source": p.price_source,
                    "bid": p.bid,
                    "ask": p.ask,
                    "delta": p.delta,
                    "theta": p.theta,
                    "gamma": p.gamma,
                    "vega": p.vega,
                    "iv": p.iv,
                    "margin": p.margin,
                    "unrealized_pnl": p.unrealized_pnl,
                    "unrealized_pnl_pct": p.unrealized_pnl_pct,
                    "days_to_expiry": p.days_to_expiry,
                    "days_in_trade": p.days_in_trade,
                })
            return positions

    def get_spy_price(self) -> dict:
        """Get cached SPY price data."""
        with self._lock:
            spy = self._cache.spy_price
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
                "positions": self.get_positions(),
                "spy_price": self.get_spy_price(),
                "last_update": self._cache.last_update.isoformat() if self._cache.last_update else None,
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
