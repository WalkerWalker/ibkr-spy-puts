"""Database operations for trade and order tracking."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from ibkr_spy_puts.config import DatabaseSettings


@dataclass
class Trade:
    """Represents a trade/position record."""

    id: int | None = None
    trade_date: date | None = None
    symbol: str = "SPY"
    strike: Decimal | None = None
    expiration: date | None = None
    quantity: int = 1
    entry_price: Decimal | None = None
    entry_time: datetime | None = None
    expected_tp_price: Decimal | None = None  # Take profit price
    expected_sl_price: Decimal | None = None  # Stop loss price
    status: str = "OPEN"
    strategy_id: str = "spy-put-selling"


@dataclass
class Order:
    """Represents an order in the strategy."""

    id: int | None = None
    trade_id: int | None = None
    ibkr_order_id: int | None = None
    ibkr_perm_id: int | None = None
    ibkr_con_id: int | None = None
    order_type: str | None = None  # PARENT, TAKE_PROFIT, STOP_LOSS
    action: str | None = None  # BUY, SELL
    order_class: str = "LMT"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    fill_price: Decimal | None = None
    fill_time: datetime | None = None
    filled_quantity: int = 0
    quantity: int = 1
    status: str = "PENDING"
    algo_strategy: str | None = None
    algo_priority: str | None = None


@dataclass
class PositionSnapshot:
    """Represents a point-in-time snapshot of a position."""

    id: int | None = None
    trade_id: int | None = None
    snapshot_time: datetime | None = None
    current_bid: Decimal | None = None
    current_ask: Decimal | None = None
    current_mid: Decimal | None = None
    underlying_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    delta: Decimal | None = None
    theta: Decimal | None = None
    gamma: Decimal | None = None
    vega: Decimal | None = None
    iv: Decimal | None = None
    days_to_expiry: int | None = None


class Database:
    """Database connection and operations."""

    def __init__(self, settings: DatabaseSettings | None = None):
        """Initialize database connection.

        Args:
            settings: Database settings. If None, loads from environment.
        """
        self.settings = settings or DatabaseSettings()
        self._conn = None

    def connect(self) -> bool:
        """Establish database connection.

        Returns:
            True if connected successfully.
        """
        try:
            self._conn = psycopg2.connect(
                host=self.settings.host,
                port=self.settings.port,
                dbname=self.settings.name,
                user=self.settings.user,
                password=self.settings.password,
            )
            return True
        except psycopg2.Error as e:
            print(f"Database connection error: {e}")
            return False

    def disconnect(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def is_connected(self) -> bool:
        """Check if database is connected."""
        return self._conn is not None and not self._conn.closed

    @contextmanager
    def cursor(self):
        """Get a database cursor with automatic commit/rollback."""
        if not self.is_connected:
            raise RuntimeError("Database not connected")

        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # =========================================================================
    # Trade Operations
    # =========================================================================

    def insert_trade(self, trade: Trade) -> int:
        """Insert a new trade record.

        Args:
            trade: Trade to insert.

        Returns:
            The new trade ID.
        """
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trades (
                    trade_date, symbol, strike, expiration, quantity,
                    entry_price, entry_time, expected_tp_price, expected_sl_price,
                    status, strategy_id
                ) VALUES (
                    %(trade_date)s, %(symbol)s, %(strike)s, %(expiration)s, %(quantity)s,
                    %(entry_price)s, %(entry_time)s, %(expected_tp_price)s, %(expected_sl_price)s,
                    %(status)s, %(strategy_id)s
                )
                RETURNING id
                """,
                {
                    "trade_date": trade.trade_date or date.today(),
                    "symbol": trade.symbol,
                    "strike": trade.strike,
                    "expiration": trade.expiration,
                    "quantity": trade.quantity,
                    "entry_price": trade.entry_price,
                    "entry_time": trade.entry_time or datetime.now(),
                    "expected_tp_price": trade.expected_tp_price,
                    "expected_sl_price": trade.expected_sl_price,
                    "status": trade.status,
                    "strategy_id": trade.strategy_id,
                },
            )
            result = cur.fetchone()
            return result["id"]

    def close_trade(self, trade_id: int) -> None:
        """Mark a trade as closed.

        Args:
            trade_id: ID of trade to close.
        """
        with self.cursor() as cur:
            cur.execute(
                "UPDATE trades SET status = 'CLOSED' WHERE id = %s",
                (trade_id,),
            )

    def update_trade_exit(
        self,
        trade_id: int,
        exit_price: Decimal,
        exit_time: datetime,
        exit_reason: str,
    ) -> None:
        """Update trade with exit details and mark as closed.

        This triggers the calculate_realized_pnl function in the database.

        Args:
            trade_id: ID of trade to update.
            exit_price: Price at which position was closed.
            exit_time: Time of exit.
            exit_reason: Reason for exit (TAKE_PROFIT, STOP_LOSS, MANUAL, etc).
        """
        with self.cursor() as cur:
            cur.execute(
                """
                UPDATE trades
                SET exit_price = %s,
                    exit_time = %s,
                    exit_reason = %s,
                    status = 'CLOSED'
                WHERE id = %s
                """,
                (exit_price, exit_time, exit_reason, trade_id),
            )

    def get_trade(self, trade_id: int) -> Trade | None:
        """Get a trade by ID.

        Args:
            trade_id: Trade ID.

        Returns:
            Trade or None if not found.
        """
        with self.cursor() as cur:
            cur.execute("SELECT * FROM trades WHERE id = %s", (trade_id,))
            row = cur.fetchone()
            if row:
                return self._row_to_trade(row)
            return None

    def get_open_trades(self) -> list[Trade]:
        """Get all open trades.

        Returns:
            List of open trades.
        """
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY trade_date DESC"
            )
            return [self._row_to_trade(row) for row in cur.fetchall()]

    def get_trades_by_date_range(
        self, start_date: date, end_date: date
    ) -> list[Trade]:
        """Get trades within a date range.

        Args:
            start_date: Start date (inclusive).
            end_date: End date (inclusive).

        Returns:
            List of trades.
        """
        with self.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM trades
                WHERE trade_date BETWEEN %s AND %s
                ORDER BY trade_date DESC
                """,
                (start_date, end_date),
            )
            return [self._row_to_trade(row) for row in cur.fetchall()]

    # =========================================================================
    # Order Operations
    # =========================================================================

    def insert_order(self, order: Order) -> int:
        """Insert a new order record.

        Args:
            order: Order to insert.

        Returns:
            The new order ID.
        """
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO orders (
                    trade_id, ibkr_order_id, ibkr_perm_id, ibkr_con_id,
                    order_type, action, order_class,
                    limit_price, stop_price, quantity, status,
                    algo_strategy, algo_priority
                ) VALUES (
                    %(trade_id)s, %(ibkr_order_id)s, %(ibkr_perm_id)s, %(ibkr_con_id)s,
                    %(order_type)s, %(action)s, %(order_class)s,
                    %(limit_price)s, %(stop_price)s, %(quantity)s, %(status)s,
                    %(algo_strategy)s, %(algo_priority)s
                )
                RETURNING id
                """,
                {
                    "trade_id": order.trade_id,
                    "ibkr_order_id": order.ibkr_order_id,
                    "ibkr_perm_id": order.ibkr_perm_id,
                    "ibkr_con_id": order.ibkr_con_id,
                    "order_type": order.order_type,
                    "action": order.action,
                    "order_class": order.order_class,
                    "limit_price": order.limit_price,
                    "stop_price": order.stop_price,
                    "quantity": order.quantity,
                    "status": order.status,
                    "algo_strategy": order.algo_strategy,
                    "algo_priority": order.algo_priority,
                },
            )
            result = cur.fetchone()
            return result["id"]

    def update_order_fill(
        self,
        order_id: int,
        fill_price: Decimal,
        fill_time: datetime,
        filled_quantity: int,
    ) -> None:
        """Update order with fill information.

        Args:
            order_id: Order ID.
            fill_price: Actual fill price.
            fill_time: When the order filled.
            filled_quantity: Number of contracts filled.
        """
        with self.cursor() as cur:
            cur.execute(
                """
                UPDATE orders
                SET fill_price = %s,
                    fill_time = %s,
                    filled_quantity = %s,
                    status = 'FILLED'
                WHERE id = %s
                """,
                (fill_price, fill_time, filled_quantity, order_id),
            )

    def update_order_status(self, order_id: int, status: str) -> None:
        """Update order status.

        Args:
            order_id: Order ID.
            status: New status (PENDING, SUBMITTED, FILLED, CANCELLED, REJECTED).
        """
        with self.cursor() as cur:
            cur.execute(
                "UPDATE orders SET status = %s WHERE id = %s",
                (status, order_id),
            )

    def update_order_by_ibkr_id(
        self,
        ibkr_order_id: int,
        fill_price: Decimal | None = None,
        fill_time: datetime | None = None,
        status: str | None = None,
    ) -> None:
        """Update order by IBKR order ID.

        Args:
            ibkr_order_id: IBKR's order ID.
            fill_price: Fill price if filled.
            fill_time: Fill time if filled.
            status: New status.
        """
        updates = []
        params = []

        if fill_price is not None:
            updates.append("fill_price = %s")
            params.append(fill_price)
        if fill_time is not None:
            updates.append("fill_time = %s")
            params.append(fill_time)
        if status is not None:
            updates.append("status = %s")
            params.append(status)

        if updates:
            params.append(ibkr_order_id)
            with self.cursor() as cur:
                cur.execute(
                    f"UPDATE orders SET {', '.join(updates)} WHERE ibkr_order_id = %s",
                    params,
                )

    def get_orders_for_trade(self, trade_id: int) -> list[Order]:
        """Get all orders for a trade.

        Args:
            trade_id: Trade ID.

        Returns:
            List of orders.
        """
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM orders WHERE trade_id = %s ORDER BY created_at",
                (trade_id,),
            )
            return [self._row_to_order(row) for row in cur.fetchall()]

    def get_pending_orders(self) -> list[dict[str, Any]]:
        """Get all pending orders with trade info.

        Returns:
            List of orders with trade details.
        """
        with self.cursor() as cur:
            cur.execute("SELECT * FROM pending_orders")
            return list(cur.fetchall())

    # =========================================================================
    # Position Snapshot Operations
    # =========================================================================

    def insert_snapshot(self, snapshot: PositionSnapshot) -> int:
        """Insert a position snapshot.

        Args:
            snapshot: Snapshot to insert.

        Returns:
            The new snapshot ID.
        """
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO position_snapshots (
                    trade_id, snapshot_time,
                    current_bid, current_ask, current_mid, underlying_price,
                    unrealized_pnl, delta, theta, gamma, vega, iv, days_to_expiry
                ) VALUES (
                    %(trade_id)s, %(snapshot_time)s,
                    %(current_bid)s, %(current_ask)s, %(current_mid)s, %(underlying_price)s,
                    %(unrealized_pnl)s, %(delta)s, %(theta)s, %(gamma)s, %(vega)s,
                    %(iv)s, %(days_to_expiry)s
                )
                RETURNING id
                """,
                {
                    "trade_id": snapshot.trade_id,
                    "snapshot_time": snapshot.snapshot_time or datetime.now(),
                    "current_bid": snapshot.current_bid,
                    "current_ask": snapshot.current_ask,
                    "current_mid": snapshot.current_mid,
                    "underlying_price": snapshot.underlying_price,
                    "unrealized_pnl": snapshot.unrealized_pnl,
                    "delta": snapshot.delta,
                    "theta": snapshot.theta,
                    "gamma": snapshot.gamma,
                    "vega": snapshot.vega,
                    "iv": snapshot.iv,
                    "days_to_expiry": snapshot.days_to_expiry,
                },
            )
            result = cur.fetchone()
            return result["id"]

    # =========================================================================
    # View Queries (for Frontend)
    # =========================================================================

    def get_open_positions(self) -> list[dict[str, Any]]:
        """Get all open positions with latest snapshot data.

        Returns:
            List of open positions from the view.
        """
        with self.cursor() as cur:
            cur.execute("SELECT * FROM open_positions ORDER BY expiration")
            return list(cur.fetchall())

    def get_positions_with_orders(self) -> list[dict[str, Any]]:
        """Get all open positions with their related orders.

        Returns:
            List of positions, each with an 'orders' list containing TP/SL orders.
        """
        with self.cursor() as cur:
            # Get positions
            cur.execute("SELECT * FROM open_positions ORDER BY expiration, strike")
            positions = [dict(row) for row in cur.fetchall()]

            # Get active orders for each position (exclude cancelled/filled)
            for pos in positions:
                cur.execute("""
                    SELECT order_type, action, order_class, limit_price, stop_price,
                           ibkr_order_id, status
                    FROM orders
                    WHERE trade_id = %s
                      AND order_type IN ('TAKE_PROFIT', 'STOP_LOSS')
                      AND status IN ('SUBMITTED', 'PRESUBMITTED', 'PENDING')
                    ORDER BY order_type
                """, (pos['id'],))
                pos['orders'] = [dict(row) for row in cur.fetchall()]

            return positions

    def get_trade_history(self) -> list[dict[str, Any]]:
        """Get trade execution history.

        Returns a simple log of all executed trades (entries and exits).
        For short puts: entry is SELL, exit is BUY.

        Returns:
            List of trade records with time, action, contract, qty, price.
        """
        with self.cursor() as cur:
            # Union entries and exits into a single trade history
            cur.execute("""
                SELECT
                    entry_time as time,
                    'SELL' as action,
                    symbol,
                    strike,
                    expiration,
                    quantity as qty,
                    entry_price as price
                FROM trades
                WHERE entry_time IS NOT NULL
                UNION ALL
                SELECT
                    exit_time as time,
                    'BUY' as action,
                    symbol,
                    strike,
                    expiration,
                    quantity as qty,
                    exit_price as price
                FROM trades
                WHERE exit_time IS NOT NULL AND exit_price IS NOT NULL
                ORDER BY time DESC
            """)
            return [dict(row) for row in cur.fetchall()]

    def get_strategy_summary(self) -> dict[str, Any]:
        """Get strategy summary metrics.

        Returns:
            Summary metrics dict.
        """
        with self.cursor() as cur:
            cur.execute("SELECT * FROM strategy_summary")
            result = cur.fetchone()
            return dict(result) if result else {}

    def get_risk_metrics(self) -> dict[str, Any]:
        """Get aggregate risk metrics for all open positions.

        Returns:
            Risk metrics dict.
        """
        with self.cursor() as cur:
            cur.execute("SELECT * FROM risk_metrics")
            result = cur.fetchone()
            return dict(result) if result else {}

    def get_pnl_by_month(self) -> list[dict[str, Any]]:
        """Get P&L aggregated by month.

        Note: With the new trade log structure, P&L is computed by matching
        SELL entries with BUY exits. For now, returns empty until positions
        are closed.

        Returns:
            List of monthly P&L records.
        """
        # TODO: Implement P&L calculation from matching SELL/BUY entries
        return []

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _row_to_trade(self, row: dict[str, Any]) -> Trade:
        """Convert database row to Trade object."""
        return Trade(
            id=row["id"],
            trade_date=row["trade_date"],
            symbol=row["symbol"],
            strike=row["strike"],
            expiration=row["expiration"],
            quantity=row["quantity"],
            entry_price=row["entry_price"],
            entry_time=row["entry_time"],
            expected_tp_price=row.get("expected_tp_price"),
            expected_sl_price=row.get("expected_sl_price"),
            status=row["status"],
            strategy_id=row["strategy_id"],
        )

    def _row_to_order(self, row: dict[str, Any]) -> Order:
        """Convert database row to Order object."""
        return Order(
            id=row["id"],
            trade_id=row["trade_id"],
            ibkr_order_id=row.get("ibkr_order_id"),
            ibkr_perm_id=row.get("ibkr_perm_id"),
            ibkr_con_id=row.get("ibkr_con_id"),
            order_type=row["order_type"],
            action=row["action"],
            order_class=row["order_class"],
            limit_price=row.get("limit_price"),
            stop_price=row.get("stop_price"),
            fill_price=row.get("fill_price"),
            fill_time=row.get("fill_time"),
            filled_quantity=row.get("filled_quantity", 0),
            quantity=row["quantity"],
            status=row["status"],
            algo_strategy=row.get("algo_strategy"),
            algo_priority=row.get("algo_priority"),
        )
