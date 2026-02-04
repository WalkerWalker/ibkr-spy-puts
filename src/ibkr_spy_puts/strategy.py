"""Put selling strategy implementation.

This module contains the core logic for:
- Selecting the appropriate put option based on DTE and delta
- Placing sell orders with exit orders (take profit / stop loss) in OCA group
- Tracking trade execution
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ibkr_spy_puts.config import ExitOrderSettings, StrategySettings
from ibkr_spy_puts.ibkr_client import TradeResult as IBKRTradeResult, OptionContract


@dataclass
class ExitPrices:
    """Calculated exit order prices (take profit and stop loss)."""

    sell_price: float  # Price we're selling the put at
    take_profit_price: float  # Buy back price for profit (lower)
    stop_loss_price: float  # Buy back price for stop loss (higher)

    @classmethod
    def calculate(
        cls,
        sell_price: float,
        take_profit_pct: float,
        stop_loss_pct: float,
    ) -> "ExitPrices":
        """Calculate exit prices from sell price and percentages.

        Args:
            sell_price: The price we're selling the put at.
            take_profit_pct: Profit percentage (e.g., 60 = take 60% profit).
            stop_loss_pct: Loss percentage (e.g., 200 = stop at 200% loss).

        Returns:
            ExitPrices with calculated take profit and stop loss.

        Example:
            sell_price = 1.00, take_profit_pct = 60, stop_loss_pct = 200
            take_profit_price = 1.00 * (1 - 0.60) = 0.40 (buy back at $0.40)
            stop_loss_price = 1.00 * (1 + 2.00) = 3.00 (buy back at $3.00)
        """
        take_profit_price = sell_price * (1 - take_profit_pct / 100)
        stop_loss_price = sell_price * (1 + stop_loss_pct / 100)

        return cls(
            sell_price=sell_price,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
        )


@dataclass
class TradeOrder:
    """Represents a planned trade order."""

    option: OptionContract
    action: str  # "SELL" or "BUY"
    quantity: int
    order_type: str  # "LMT" or "MKT"
    limit_price: float | None
    exit_prices: ExitPrices | None


@dataclass
class TradeResult:
    """Result of a trade execution."""

    success: bool
    order_id: int | None
    sell_order_id: int | None
    take_profit_order_id: int | None
    stop_loss_order_id: int | None
    message: str
    timestamp: datetime
    fill_price: float | None = None
    cancelled_orders: list | None = None  # Orders cancelled for conflict, to be restored
    commission: float | None = None  # Commission from the trade
    sell_trade: any = None  # The IB Trade object for accessing fill details


class IBKRClientProtocol(Protocol):
    """Protocol for IBKR client interface (allows mock injection)."""

    @property
    def is_connected(self) -> bool: ...

    def connect(self) -> bool: ...

    def disconnect(self) -> None: ...

    def get_spy_price(self, use_delayed: bool = True) -> float | None: ...

    def find_put_by_delta(
        self,
        target_delta: float,
        target_dte: int,
        symbol: str,
        use_delayed: bool = True,
    ) -> OptionContract | None: ...

    def execute_trade(
        self,
        contract: any,
        action: str,
        quantity: int,
        limit_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        use_aggressive_fill: bool = False,
    ) -> IBKRTradeResult: ...

    def restore_cancelled_orders(self, cancelled_orders: list) -> bool: ...


class PutSellingStrategy:
    """Strategy for selling puts on SPY with exit orders (TP/SL)."""

    def __init__(
        self,
        client: IBKRClientProtocol,
        strategy_settings: StrategySettings | None = None,
        exit_settings: ExitOrderSettings | None = None,
    ):
        """Initialize the strategy.

        Args:
            client: IBKR client (real or mock).
            strategy_settings: Strategy configuration.
            exit_settings: Exit order configuration (TP/SL).
        """
        self.client = client
        self.strategy = strategy_settings or StrategySettings()
        self.exit_orders = exit_settings or ExitOrderSettings()

    def select_option(self) -> OptionContract | None:
        """Select the put option to sell based on strategy settings.

        Returns:
            Selected OptionContract or None if not found.
        """
        return self.client.find_put_by_delta(
            target_delta=self.strategy.target_delta,
            target_dte=self.strategy.target_dte,
            symbol=self.strategy.symbol,
        )

    def calculate_limit_price(self, option: OptionContract) -> float:
        """Calculate limit price for the sell order.

        Always uses mid price. Fill speed is controlled by Adaptive algo priority:
        - Normal priority (live): seeks price improvement
        - Urgent priority (paper): prioritizes fast fill

        Args:
            option: The option contract to sell.

        Returns:
            Mid price, rounded to 2 decimal places.
        """
        if option.mid is not None:
            return round(option.mid, 2)
        elif option.bid is not None and option.ask is not None:
            return round((option.bid + option.ask) / 2, 2)
        elif option.bid is not None:
            # Fallback to bid if no ask available
            return option.bid

        raise ValueError("Option has no price data")

    def calculate_exit_prices(self, sell_price: float) -> ExitPrices:
        """Calculate exit order prices (TP/SL).

        Args:
            sell_price: The price we're selling the put at.

        Returns:
            ExitPrices with take profit and stop loss.
        """
        return ExitPrices.calculate(
            sell_price=sell_price,
            take_profit_pct=self.exit_orders.take_profit_pct,
            stop_loss_pct=self.exit_orders.stop_loss_pct,
        )

    def create_trade_order(self) -> TradeOrder | None:
        """Create a trade order based on current market conditions.

        Returns:
            TradeOrder ready to execute, or None if no suitable option found.
        """
        # Select the option
        option = self.select_option()
        if option is None:
            return None

        # Calculate prices
        if self.strategy.order_type == "MKT":
            limit_price = None
            # For exit price calculation, use mid price as estimate
            sell_price = option.mid or option.bid or 0
        else:
            limit_price = self.calculate_limit_price(option)
            sell_price = limit_price

        # Calculate exit prices if enabled
        exit_prices = None
        if self.exit_orders.enabled and sell_price > 0:
            exit_prices = self.calculate_exit_prices(sell_price)

        return TradeOrder(
            option=option,
            action="SELL",
            quantity=self.strategy.quantity,
            order_type=self.strategy.order_type,
            limit_price=limit_price,
            exit_prices=exit_prices,
        )

    def execute_trade(self, order: TradeOrder, dry_run: bool = False) -> TradeResult:
        """Execute a trade order.

        Args:
            order: The trade order to execute.
            dry_run: If True, don't actually place the order (for testing).

        Returns:
            TradeResult with execution details.
        """
        if dry_run:
            return TradeResult(
                success=True,
                order_id=None,
                sell_order_id=None,
                take_profit_order_id=None,
                stop_loss_order_id=None,
                message="DRY RUN - Order not placed",
                timestamp=datetime.now(),
            )

        # Validate we have exit prices if exit orders are enabled
        if self.exit_orders.enabled and order.exit_prices is None:
            return TradeResult(
                success=False,
                order_id=None,
                sell_order_id=None,
                take_profit_order_id=None,
                stop_loss_order_id=None,
                message="Exit orders enabled but no exit prices calculated",
                timestamp=datetime.now(),
            )

        # Validate limit price for LMT orders
        if order.order_type == "LMT" and order.limit_price is None:
            return TradeResult(
                success=False,
                order_id=None,
                sell_order_id=None,
                take_profit_order_id=None,
                stop_loss_order_id=None,
                message="Limit order requires limit price",
                timestamp=datetime.now(),
            )

        try:
            if self.exit_orders.enabled and order.exit_prices:
                # Place sell order, then exit orders (TP/SL) after fill
                result = self.client.execute_trade(
                    contract=order.option.contract,
                    action=order.action,
                    quantity=order.quantity,
                    limit_price=order.limit_price or order.exit_prices.sell_price,
                    take_profit_price=order.exit_prices.take_profit_price,
                    stop_loss_price=order.exit_prices.stop_loss_price,
                    use_aggressive_fill=self.strategy.use_aggressive_fill,
                )

                if result.success:
                    # Extract fill price from sell trade if available
                    fill_price = result.fill_price
                    if not fill_price and result.sell_trade and result.sell_trade.orderStatus.avgFillPrice:
                        fill_price = result.sell_trade.orderStatus.avgFillPrice
                    return TradeResult(
                        success=True,
                        order_id=result.sell_order_id,
                        sell_order_id=result.sell_order_id,
                        take_profit_order_id=result.take_profit_order_id,
                        stop_loss_order_id=result.stop_loss_order_id,
                        message="Order placed successfully with exit orders",
                        timestamp=datetime.now(),
                        fill_price=fill_price,
                        cancelled_orders=result.cancelled_orders,
                        commission=result.commission,
                        sell_trade=result.sell_trade,
                    )
                else:
                    return TradeResult(
                        success=False,
                        order_id=None,
                        sell_order_id=result.sell_order_id,
                        take_profit_order_id=None,
                        stop_loss_order_id=None,
                        message=f"Order failed: {result.error_message}",
                        timestamp=datetime.now(),
                        cancelled_orders=result.cancelled_orders,
                    )
            else:
                # Place single order (no exit orders)
                # This would need place_single_order implementation
                return TradeResult(
                    success=False,
                    order_id=None,
                    sell_order_id=None,
                    take_profit_order_id=None,
                    stop_loss_order_id=None,
                    message="Single order (no exit orders) not yet implemented",
                    timestamp=datetime.now(),
                )

        except Exception as e:
            return TradeResult(
                success=False,
                order_id=None,
                sell_order_id=None,
                take_profit_order_id=None,
                stop_loss_order_id=None,
                message=f"Order execution error: {e}",
                timestamp=datetime.now(),
            )

    def run(
        self,
        dry_run: bool = False,
        max_retries: int = 10,
    ) -> tuple[TradeOrder | None, TradeResult]:
        """Run the complete strategy with retry logic.

        If the order doesn't fill within the timeout, the order is cancelled
        and a new contract is selected for retry. After all retries, any
        cancelled orders are restored.

        Args:
            dry_run: If True, don't actually place the order.
            max_retries: Maximum number of retry attempts (default 10).

        Returns:
            Tuple of (TradeOrder, TradeResult). TradeOrder may be None if no option found.
        """
        import logging
        logger = logging.getLogger(__name__)

        # Ensure connected
        if not self.client.is_connected:
            return None, TradeResult(
                success=False,
                order_id=None,
                sell_order_id=None,
                take_profit_order_id=None,
                stop_loss_order_id=None,
                message="Client not connected",
                timestamp=datetime.now(),
            )

        # Each attempt is atomic: cancel orders -> execute -> restore on success or failure
        last_order = None
        last_result = None

        for attempt in range(1, max_retries + 1):
            logger.info(f"Trade attempt {attempt}/{max_retries}")

            # Create trade order (selects option based on delta, calculates mid price)
            order = self.create_trade_order()
            if order is None:
                logger.warning(f"Attempt {attempt}: No suitable option found")
                if attempt < max_retries:
                    logger.info(f"Retrying option selection...")
                continue  # Try next attempt

            last_order = order
            logger.info(f"Attempt {attempt}: Selected {order.option.symbol} {order.option.strike}P @ ${order.limit_price:.2f}")

            # Execute the trade (this cancels conflicting orders, places SELL, places TP/SL)
            result = self.execute_trade(order, dry_run=dry_run)
            last_result = result

            if result.success:
                # Success! Restore any cancelled orders from conflicting positions
                if result.cancelled_orders:
                    logger.info(f"Trade filled! Restoring {len(result.cancelled_orders)} cancelled order(s)...")
                    self.client.restore_cancelled_orders(result.cancelled_orders)
                return order, result

            # Failed - immediately restore cancelled orders to make attempt atomic
            if result.cancelled_orders:
                logger.info(f"Attempt {attempt} failed. Restoring {len(result.cancelled_orders)} cancelled order(s)...")
                self.client.restore_cancelled_orders(result.cancelled_orders)

            logger.warning(f"Attempt {attempt} failed: {result.message}")

            if attempt < max_retries:
                logger.info(f"Retrying with new contract selection...")
            else:
                logger.error(f"All {max_retries} attempts failed")

        # All retries exhausted - no cancelled orders to restore (already restored after each attempt)
        if last_order is None:
            logger.error(f"All {max_retries} attempts failed: No suitable option found")

        return last_order, TradeResult(
            success=False,
            order_id=None,
            sell_order_id=last_result.sell_order_id if last_result else None,
            take_profit_order_id=None,
            stop_loss_order_id=None,
            message=f"Failed after {max_retries} attempts: {last_result.message if last_result else 'No suitable option found'}",
            timestamp=datetime.now(),
        )

    def describe_trade(self, order: TradeOrder) -> str:
        """Generate a human-readable description of a trade order.

        Args:
            order: The trade order to describe.

        Returns:
            Formatted string describing the trade.
        """
        lines = [
            "=" * 60,
            "TRADE ORDER SUMMARY",
            "=" * 60,
            f"Action: {order.action} {order.quantity} contract(s)",
            f"Symbol: {order.option.symbol}",
            f"Strike: ${order.option.strike:.2f}",
            f"Expiration: {order.option.expiration}",
            f"Delta: {order.option.delta:.4f}" if order.option.delta else "Delta: N/A",
            f"Order Type: {order.order_type}",
        ]

        if order.limit_price:
            lines.append(f"Limit Price: ${order.limit_price:.2f}")

        if order.option.bid and order.option.ask:
            lines.append(f"Market: ${order.option.bid:.2f} / ${order.option.ask:.2f}")

        if order.exit_prices:
            lines.extend([
                "",
                "EXIT ORDERS:",
                f"  Sell at: ${order.exit_prices.sell_price:.2f}",
                f"  Take Profit: Buy back at ${order.exit_prices.take_profit_price:.2f} "
                f"({self.exit_orders.take_profit_pct}% profit)",
                f"  Stop Loss: Buy back at ${order.exit_prices.stop_loss_price:.2f} "
                f"({self.exit_orders.stop_loss_pct}% max loss)",
            ])

        lines.append("=" * 60)
        return "\n".join(lines)
