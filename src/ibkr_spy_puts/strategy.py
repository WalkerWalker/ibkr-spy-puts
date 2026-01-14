"""Put selling strategy implementation.

This module contains the core logic for:
- Selecting the appropriate put option based on DTE and delta
- Creating and placing orders with bracket (take profit / stop loss)
- Tracking trade execution
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ibkr_spy_puts.config import BracketSettings, StrategySettings
from ibkr_spy_puts.ibkr_client import BracketOrderResult, OptionContract


@dataclass
class BracketPrices:
    """Calculated bracket order prices."""

    sell_price: float  # Price we're selling the put at
    take_profit_price: float  # Buy back price for profit (lower)
    stop_loss_price: float  # Buy back price for stop loss (higher)

    @classmethod
    def calculate(
        cls,
        sell_price: float,
        take_profit_pct: float,
        stop_loss_pct: float,
    ) -> "BracketPrices":
        """Calculate bracket prices from sell price and percentages.

        Args:
            sell_price: The price we're selling the put at.
            take_profit_pct: Profit percentage (e.g., 60 = take 60% profit).
            stop_loss_pct: Loss percentage (e.g., 200 = stop at 200% loss).

        Returns:
            BracketPrices with calculated take profit and stop loss.

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
    bracket_prices: BracketPrices | None


@dataclass
class TradeResult:
    """Result of a trade execution."""

    success: bool
    order_id: int | None
    parent_order_id: int | None
    take_profit_order_id: int | None
    stop_loss_order_id: int | None
    message: str
    timestamp: datetime


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

    def place_bracket_order(
        self,
        contract: any,
        action: str,
        quantity: int,
        limit_price: float,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> BracketOrderResult: ...


class PutSellingStrategy:
    """Strategy for selling puts on SPY with bracket orders."""

    def __init__(
        self,
        client: IBKRClientProtocol,
        strategy_settings: StrategySettings | None = None,
        bracket_settings: BracketSettings | None = None,
    ):
        """Initialize the strategy.

        Args:
            client: IBKR client (real or mock).
            strategy_settings: Strategy configuration.
            bracket_settings: Bracket order configuration.
        """
        self.client = client
        self.strategy = strategy_settings or StrategySettings()
        self.bracket = bracket_settings or BracketSettings()

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

        Args:
            option: The option contract to sell.

        Returns:
            Limit price (mid price minus offset for better fill).
        """
        if option.mid is not None:
            # Use mid price minus a small offset
            return round(option.mid - self.strategy.limit_offset, 2)
        elif option.bid is not None:
            # Fall back to bid if no mid
            return option.bid
        else:
            raise ValueError("Option has no price data")

    def calculate_bracket_prices(self, sell_price: float) -> BracketPrices:
        """Calculate bracket order prices.

        Args:
            sell_price: The price we're selling the put at.

        Returns:
            BracketPrices with take profit and stop loss.
        """
        return BracketPrices.calculate(
            sell_price=sell_price,
            take_profit_pct=self.bracket.take_profit_pct,
            stop_loss_pct=self.bracket.stop_loss_pct,
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
            # For bracket calculation, use mid price as estimate
            sell_price = option.mid or option.bid or 0
        else:
            limit_price = self.calculate_limit_price(option)
            sell_price = limit_price

        # Calculate bracket prices if enabled
        bracket_prices = None
        if self.bracket.enabled and sell_price > 0:
            bracket_prices = self.calculate_bracket_prices(sell_price)

        return TradeOrder(
            option=option,
            action="SELL",
            quantity=self.strategy.quantity,
            order_type=self.strategy.order_type,
            limit_price=limit_price,
            bracket_prices=bracket_prices,
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
                parent_order_id=None,
                take_profit_order_id=None,
                stop_loss_order_id=None,
                message="DRY RUN - Order not placed",
                timestamp=datetime.now(),
            )

        # Validate we have bracket prices if bracket is enabled
        if self.bracket.enabled and order.bracket_prices is None:
            return TradeResult(
                success=False,
                order_id=None,
                parent_order_id=None,
                take_profit_order_id=None,
                stop_loss_order_id=None,
                message="Bracket enabled but no bracket prices calculated",
                timestamp=datetime.now(),
            )

        # Validate limit price for LMT orders
        if order.order_type == "LMT" and order.limit_price is None:
            return TradeResult(
                success=False,
                order_id=None,
                parent_order_id=None,
                take_profit_order_id=None,
                stop_loss_order_id=None,
                message="Limit order requires limit price",
                timestamp=datetime.now(),
            )

        try:
            if self.bracket.enabled and order.bracket_prices:
                # Place bracket order (parent + take profit + stop loss)
                result = self.client.place_bracket_order(
                    contract=order.option.contract,
                    action=order.action,
                    quantity=order.quantity,
                    limit_price=order.limit_price or order.bracket_prices.sell_price,
                    take_profit_price=order.bracket_prices.take_profit_price,
                    stop_loss_price=order.bracket_prices.stop_loss_price,
                )

                if result.success:
                    return TradeResult(
                        success=True,
                        order_id=result.parent_order_id,
                        parent_order_id=result.parent_order_id,
                        take_profit_order_id=result.take_profit_order_id,
                        stop_loss_order_id=result.stop_loss_order_id,
                        message="Bracket order placed successfully",
                        timestamp=datetime.now(),
                    )
                else:
                    return TradeResult(
                        success=False,
                        order_id=None,
                        parent_order_id=None,
                        take_profit_order_id=None,
                        stop_loss_order_id=None,
                        message=f"Bracket order failed: {result.error_message}",
                        timestamp=datetime.now(),
                    )
            else:
                # Place single order (no bracket)
                # This would need place_single_order implementation
                return TradeResult(
                    success=False,
                    order_id=None,
                    parent_order_id=None,
                    take_profit_order_id=None,
                    stop_loss_order_id=None,
                    message="Single order (non-bracket) not yet implemented",
                    timestamp=datetime.now(),
                )

        except Exception as e:
            return TradeResult(
                success=False,
                order_id=None,
                parent_order_id=None,
                take_profit_order_id=None,
                stop_loss_order_id=None,
                message=f"Order execution error: {e}",
                timestamp=datetime.now(),
            )

    def run(self, dry_run: bool = False) -> tuple[TradeOrder | None, TradeResult]:
        """Run the complete strategy: select option and place order.

        Args:
            dry_run: If True, don't actually place the order.

        Returns:
            Tuple of (TradeOrder, TradeResult). TradeOrder may be None if no option found.
        """
        # Ensure connected
        if not self.client.is_connected:
            return None, TradeResult(
                success=False,
                order_id=None,
                parent_order_id=None,
                take_profit_order_id=None,
                stop_loss_order_id=None,
                message="Client not connected",
                timestamp=datetime.now(),
            )

        # Create trade order
        order = self.create_trade_order()
        if order is None:
            return None, TradeResult(
                success=False,
                order_id=None,
                parent_order_id=None,
                take_profit_order_id=None,
                stop_loss_order_id=None,
                message="No suitable option found",
                timestamp=datetime.now(),
            )

        # Execute the trade
        result = self.execute_trade(order, dry_run=dry_run)
        return order, result

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

        if order.bracket_prices:
            lines.extend([
                "",
                "BRACKET ORDERS:",
                f"  Sell at: ${order.bracket_prices.sell_price:.2f}",
                f"  Take Profit: Buy back at ${order.bracket_prices.take_profit_price:.2f} "
                f"({self.bracket.take_profit_pct}% profit)",
                f"  Stop Loss: Buy back at ${order.bracket_prices.stop_loss_price:.2f} "
                f"({self.bracket.stop_loss_pct}% max loss)",
            ])

        lines.append("=" * 60)
        return "\n".join(lines)
