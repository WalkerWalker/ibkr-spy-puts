"""IBKR TWS API client wrapper using ib_insync."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ib_insync import IB, Contract, LimitOrder, Option, Order, Stock, Trade

from ibkr_spy_puts.config import TWSSettings


@dataclass
class OptionContract:
    """Represents a selected option contract with its details."""

    symbol: str
    strike: float
    expiration: date
    right: str  # 'P' for put, 'C' for call
    delta: float | None
    bid: float | None
    ask: float | None
    mid: float | None
    contract: Option


@dataclass
class BracketOrderResult:
    """Result of placing a bracket order."""

    success: bool
    parent_order_id: int | None = None
    take_profit_order_id: int | None = None
    stop_loss_order_id: int | None = None
    parent_trade: Trade | None = None
    take_profit_trade: Trade | None = None
    stop_loss_trade: Trade | None = None
    error_message: str | None = None


class IBKRClient:
    """Client for interacting with IBKR TWS API."""

    def __init__(self, settings: TWSSettings | None = None):
        """Initialize the IBKR client.

        Args:
            settings: TWS connection settings. If None, uses defaults.
        """
        self.settings = settings or TWSSettings()
        self.ib = IB()

    @property
    def is_connected(self) -> bool:
        """Check if connected to TWS."""
        return self.ib.isConnected()

    def connect(self) -> bool:
        """Connect to TWS/IB Gateway.

        Returns:
            True if connection successful, False otherwise.
        """
        try:
            self.ib.connect(
                host=self.settings.host,
                port=self.settings.port,
                clientId=self.settings.client_id,
                readonly=False,
            )
            # Wait for connection to stabilize
            self.ib.sleep(1)
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def disconnect(self) -> None:
        """Disconnect from TWS."""
        if self.is_connected:
            self.ib.disconnect()

    def get_spy_price(self, use_delayed: bool = True) -> float | None:
        """Get current SPY price.

        Args:
            use_delayed: If True, use delayed market data (free).
                        If False, requires real-time data subscription.

        Returns:
            Current SPY price or None if unavailable.
        """
        if not self.is_connected:
            return None

        # Set market data type: 1=Live, 2=Frozen, 3=Delayed, 4=Delayed Frozen
        if use_delayed:
            self.ib.reqMarketDataType(3)
        else:
            self.ib.reqMarketDataType(1)

        spy = Stock("SPY", "SMART", "USD")
        self.ib.qualifyContracts(spy)

        ticker = self.ib.reqMktData(spy, "", False, False)
        self.ib.sleep(2)  # Wait for data

        price = ticker.marketPrice()
        self.ib.cancelMktData(spy)

        return price if price > 0 else None

    def get_account_summary(self) -> dict:
        """Get account summary information.

        Returns:
            Dictionary with account info.
        """
        if not self.is_connected:
            return {}

        account_values = self.ib.accountSummary()
        return {av.tag: av.value for av in account_values}

    def get_option_expirations(self, symbol: str = "SPY") -> list[date]:
        """Get available option expiration dates for a symbol.

        Args:
            symbol: The underlying symbol.

        Returns:
            List of available expiration dates, sorted ascending.
        """
        if not self.is_connected:
            return []

        stock = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(stock)

        chains = self.ib.reqSecDefOptParams(
            stock.symbol, "", stock.secType, stock.conId
        )

        expirations: set[date] = set()
        for chain in chains:
            for exp_str in chain.expirations:
                exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
                expirations.add(exp_date)

        return sorted(expirations)

    def find_expiration_by_dte(
        self, target_dte: int, symbol: str = "SPY"
    ) -> date | None:
        """Find the expiration date closest to target DTE.

        Args:
            target_dte: Target days to expiration.
            symbol: The underlying symbol.

        Returns:
            Expiration date closest to target DTE, or None if unavailable.
        """
        expirations = self.get_option_expirations(symbol)
        if not expirations:
            return None

        today = date.today()
        target_date = today + timedelta(days=target_dte)

        # Find expiration closest to target date
        closest = min(expirations, key=lambda x: abs((x - target_date).days))
        return closest

    def get_option_chain_with_greeks(
        self,
        symbol: str,
        expiration: date,
        right: str = "P",
        use_delayed: bool = True,
    ) -> list[OptionContract]:
        """Get option chain with greeks for a specific expiration.

        Args:
            symbol: The underlying symbol.
            expiration: Option expiration date.
            right: 'P' for puts, 'C' for calls.
            use_delayed: Use delayed market data.

        Returns:
            List of OptionContract with greeks and prices.
        """
        if not self.is_connected:
            return []

        # Set market data type
        if use_delayed:
            self.ib.reqMarketDataType(3)
        else:
            self.ib.reqMarketDataType(1)

        stock = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(stock)

        # Get option chain parameters
        chains = self.ib.reqSecDefOptParams(
            stock.symbol, "", stock.secType, stock.conId
        )

        # Find strikes for the specified expiration
        exp_str = expiration.strftime("%Y%m%d")
        strikes: set[float] = set()
        exchange = "SMART"

        for chain in chains:
            if exp_str in chain.expirations:
                strikes.update(chain.strikes)
                if chain.exchange == "SMART":
                    exchange = "SMART"
                elif not exchange:
                    exchange = chain.exchange

        if not strikes:
            return []

        # Get current price to filter reasonable strikes
        ticker = self.ib.reqMktData(stock, "", False, False)
        self.ib.sleep(1)
        current_price = ticker.marketPrice()
        self.ib.cancelMktData(stock)

        # Filter strikes to reasonable range (within 20% of current price)
        if current_price and current_price > 0:
            min_strike = current_price * 0.80
            max_strike = current_price * 1.05  # For puts, focus on OTM
            strikes = {s for s in strikes if min_strike <= s <= max_strike}

        # Create option contracts
        options = []
        for strike in sorted(strikes):
            opt = Option(symbol, exp_str, strike, right, "SMART")
            options.append(opt)

        if not options:
            return []

        # Qualify contracts
        qualified = self.ib.qualifyContracts(*options)

        # Request market data and greeks for all options
        tickers = []
        for opt in qualified:
            # Request with greeks (generic tick 106 = option greeks)
            ticker = self.ib.reqMktData(opt, "106", False, False)
            tickers.append((opt, ticker))

        # Wait for data
        self.ib.sleep(3)

        # Collect results
        results = []
        for opt, ticker in tickers:
            delta = None
            if ticker.modelGreeks:
                delta = ticker.modelGreeks.delta

            bid = ticker.bid if ticker.bid > 0 else None
            ask = ticker.ask if ticker.ask > 0 else None
            mid = None
            if bid and ask:
                mid = (bid + ask) / 2

            exp_date = datetime.strptime(opt.lastTradeDateOrContractMonth, "%Y%m%d").date()

            results.append(
                OptionContract(
                    symbol=symbol,
                    strike=opt.strike,
                    expiration=exp_date,
                    right=right,
                    delta=delta,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    contract=opt,
                )
            )

            # Cancel market data
            self.ib.cancelMktData(opt)

        return results

    def find_put_by_delta(
        self,
        target_delta: float = -0.15,
        target_dte: int = 90,
        symbol: str = "SPY",
        use_delayed: bool = True,
    ) -> OptionContract | None:
        """Find a put option closest to target delta and DTE.

        Args:
            target_delta: Target delta (negative for puts, e.g., -0.15).
            target_dte: Target days to expiration.
            symbol: The underlying symbol.
            use_delayed: Use delayed market data.

        Returns:
            OptionContract closest to target delta, or None if unavailable.
        """
        # Find closest expiration
        expiration = self.find_expiration_by_dte(target_dte, symbol)
        if not expiration:
            return None

        # Get option chain with greeks
        chain = self.get_option_chain_with_greeks(
            symbol, expiration, right="P", use_delayed=use_delayed
        )

        if not chain:
            return None

        # Filter to options with valid delta
        options_with_delta = [opt for opt in chain if opt.delta is not None]

        if not options_with_delta:
            # Fallback: return option with strike closest to typical delta range
            # For -0.15 delta, roughly 5-7% OTM
            return None

        # Find option closest to target delta
        closest = min(
            options_with_delta,
            key=lambda x: abs(x.delta - target_delta) if x.delta else float("inf"),
        )

        return closest

    def create_bracket_order(
        self,
        action: str,
        quantity: int,
        limit_price: float,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> tuple[Order, Order, Order]:
        """Create a bracket order (parent + take profit + stop loss).

        Manually creates orders with explicit transmit flags to ensure
        proper transmission to IB.

        Args:
            action: "BUY" or "SELL" for the parent order.
            quantity: Number of contracts.
            limit_price: Limit price for parent order.
            take_profit_price: Price for take profit order (opposite side).
            stop_loss_price: Price for stop loss order (opposite side).

        Returns:
            Tuple of (parent_order, take_profit_order, stop_loss_order).
        """
        # Get the opposite action for child orders
        opposite_action = "BUY" if action == "SELL" else "SELL"

        # Create parent order (entry) - transmit=False to wait for children
        parent = LimitOrder(
            action=action,
            totalQuantity=quantity,
            lmtPrice=limit_price,
            transmit=False,
        )

        # Create take profit order - transmit=False to wait for stop loss
        take_profit = LimitOrder(
            action=opposite_action,
            totalQuantity=quantity,
            lmtPrice=take_profit_price,
            transmit=False,
            parentId=parent.orderId,
        )

        # Create stop loss order - transmit=True to send all orders
        stop_loss = Order(
            orderType="STP",
            action=opposite_action,
            totalQuantity=quantity,
            auxPrice=stop_loss_price,
            transmit=True,
            parentId=parent.orderId,
        )

        return parent, take_profit, stop_loss

    def place_bracket_order(
        self,
        contract: Contract,
        action: str,
        quantity: int,
        limit_price: float,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> BracketOrderResult:
        """Place a bracket order for a contract.

        Args:
            contract: The contract to trade (e.g., Option).
            action: "BUY" or "SELL" for the parent order.
            quantity: Number of contracts.
            limit_price: Limit price for parent order.
            take_profit_price: Price for take profit order.
            stop_loss_price: Price for stop loss order.

        Returns:
            BracketOrderResult with order IDs and trade objects.
        """
        if not self.is_connected:
            return BracketOrderResult(
                success=False,
                error_message="Not connected to TWS",
            )

        try:
            # Determine the opposite action for finding conflicting orders
            opposite_action = "BUY" if action == "SELL" else "SELL"

            # First, sync all orders from IB (including from other clients)
            import logging
            logger = logging.getLogger(__name__)

            logger.info("Syncing all open orders from IB...")
            self.ib.reqAllOpenOrders()
            self.ib.sleep(2)

            # Find conflicting orders on the same contract
            # (orders on the opposite side that would block our new order)
            conflicting_orders = []
            open_trades = self.ib.openTrades()
            logger.info(f"Checking for conflicting orders. Open trades: {len(open_trades)}, target conId: {contract.conId}")

            for trade in open_trades:
                logger.info(f"  Trade: orderId={trade.order.orderId}, conId={trade.contract.conId}, action={trade.order.action}, status={trade.orderStatus.status}")
                # Check if same contract and opposite action
                if (trade.contract.conId == contract.conId and
                    trade.order.action == opposite_action and
                    trade.orderStatus.status in ["Submitted", "PreSubmitted"]):
                    # Save complete order details for re-placing later
                    conflicting_orders.append({
                        "contract": trade.contract,
                        "order": trade.order,
                        "order_type": trade.order.orderType,
                        "action": trade.order.action,
                        "quantity": trade.order.totalQuantity,
                        "lmt_price": trade.order.lmtPrice,
                        "aux_price": trade.order.auxPrice,
                        "parent_id": trade.order.parentId,
                        "oca_group": getattr(trade.order, 'ocaGroup', ''),
                        "tif": trade.order.tif,
                    })
                    logger.info(f"Found conflicting order: {trade.order.orderId} {trade.order.action} {trade.order.orderType} qty={trade.order.totalQuantity}")

            # Cancel conflicting orders
            if conflicting_orders:
                logger.info(f"Cancelling {len(conflicting_orders)} conflicting order(s)...")

                # Try global cancel for orders from other clients
                logger.info("Using globalCancel to cancel all open orders...")
                self.ib.reqGlobalCancel()

                # Wait for cancellations to fully process
                logger.info("Waiting for cancellations to complete...")
                self.ib.sleep(5)

                # Verify cancellations by checking order status
                self.ib.reqAllOpenOrders()
                self.ib.sleep(3)

                # Verify all conflicting orders are actually cancelled
                remaining_conflicts = []
                for trade in self.ib.openTrades():
                    if (trade.contract.conId == contract.conId and
                        trade.order.action == opposite_action and
                        trade.orderStatus.status in ["Submitted", "PreSubmitted"]):
                        remaining_conflicts.append(trade.order.orderId)

                if remaining_conflicts:
                    logger.error(f"Orders still active after globalCancel: {remaining_conflicts}")
                    logger.error("Cannot proceed - please manually cancel these orders in TWS")
                    return BracketOrderResult(
                        success=False,
                        error_message=f"Cannot cancel conflicting orders: {remaining_conflicts}",
                    )
                else:
                    logger.info("All conflicting orders successfully cancelled")

            # Determine the opposite action for child orders
            child_action = "BUY" if action == "SELL" else "SELL"

            # Generate OCA group for child orders
            import time
            oca_group = f"BRACKET_{int(time.time())}"

            # Create and place parent order first (transmit=False to wait for children)
            parent = LimitOrder(
                action=action,
                totalQuantity=quantity,
                lmtPrice=limit_price,
                transmit=False,
            )
            parent_trade = self.ib.placeOrder(contract, parent)
            parent_order_id = parent_trade.order.orderId
            logger.info(f"Parent order placed with ID: {parent_order_id}")

            # Create and place take profit order with OCA group
            take_profit = LimitOrder(
                action=child_action,
                totalQuantity=quantity,
                lmtPrice=take_profit_price,
                transmit=False,
                parentId=parent_order_id,
                ocaGroup=oca_group,
                ocaType=3,
            )
            take_profit_trade = self.ib.placeOrder(contract, take_profit)
            logger.info(f"Take profit order placed with ID: {take_profit_trade.order.orderId}, ocaGroup={oca_group}")

            # Create and place stop loss order (transmit=True to send all orders)
            stop_loss = Order(
                orderType="STP",
                action=child_action,
                totalQuantity=quantity,
                auxPrice=stop_loss_price,
                transmit=True,
                parentId=parent_order_id,
                ocaGroup=oca_group,
                ocaType=3,
            )
            stop_loss_trade = self.ib.placeOrder(contract, stop_loss)
            logger.info(f"Stop loss order placed with ID: {stop_loss_trade.order.orderId}, transmit=True, ocaGroup={oca_group}")

            # Wait for order acknowledgment from IB
            self.ib.sleep(3)

            # Request all open orders to force sync and get updated status
            self.ib.reqAllOpenOrders()
            self.ib.sleep(2)

            # Log order status for debugging
            logger.info(f"Parent order {parent_trade.order.orderId}: status={parent_trade.orderStatus.status}")
            logger.info(f"Take profit order {take_profit_trade.order.orderId}: status={take_profit_trade.orderStatus.status}")
            logger.info(f"Stop loss order {stop_loss_trade.order.orderId}: status={stop_loss_trade.orderStatus.status}")

            # Check if the bracket order was successfully submitted or filled
            valid_statuses = ["Submitted", "PreSubmitted", "PendingSubmit", "Filled"]
            bracket_success = (
                parent_trade.orderStatus.status in valid_statuses and
                stop_loss_trade.orderStatus.status in valid_statuses
            )

            if not bracket_success:
                logger.error(f"Bracket order failed! Parent status: {parent_trade.orderStatus.status}")

            # Always re-place cancelled conflicting orders (to restore original protection)
            if conflicting_orders:
                logger.info(f"Re-placing {len(conflicting_orders)} cancelled order(s) as OCA group...")

                # Generate a unique OCA group name
                import time
                oca_group = f"OCA_{int(time.time())}"

                placed_orders = []
                for i, conflict in enumerate(conflicting_orders):
                    order_type = conflict["order_type"]
                    is_last = (i == len(conflicting_orders) - 1)
                    logger.info(f"Re-placing {order_type} order: {conflict['action']} qty={conflict['quantity']}, ocaGroup={oca_group}")

                    # Create new order based on saved details with OCA group
                    # OCA orders must all have transmit=True (they're not parent-child linked)
                    if order_type == "LMT":
                        new_order = LimitOrder(
                            action=conflict["action"],
                            totalQuantity=conflict["quantity"],
                            lmtPrice=conflict["lmt_price"],
                            tif=conflict["tif"] or "GTC",
                            ocaGroup=oca_group,
                            ocaType=3,  # 3 = reduce position, cancel other
                            transmit=True,
                        )
                    elif order_type == "STP":
                        new_order = Order(
                            orderType="STP",
                            action=conflict["action"],
                            totalQuantity=conflict["quantity"],
                            auxPrice=conflict["aux_price"],
                            tif=conflict["tif"] or "GTC",
                            ocaGroup=oca_group,
                            ocaType=3,
                            transmit=True,
                        )
                    else:
                        logger.warning(f"Unknown order type {order_type}, skipping")
                        continue

                    trade = self.ib.placeOrder(conflict["contract"], new_order)
                    placed_orders.append(trade)

                self.ib.sleep(3)

                # Verify re-placed orders
                self.ib.reqAllOpenOrders()
                self.ib.sleep(2)
                for trade in placed_orders:
                    logger.info(f"Re-placed order {trade.order.orderId}: status={trade.orderStatus.status}")

                logger.info(f"Successfully re-placed {len(conflicting_orders)} order(s) in OCA group {oca_group}")

            if not bracket_success:
                return BracketOrderResult(
                    success=False,
                    error_message=f"Bracket order failed - parent status: {parent_trade.orderStatus.status}",
                    parent_order_id=parent_trade.order.orderId,
                )

            return BracketOrderResult(
                success=True,
                parent_order_id=parent_trade.order.orderId,
                take_profit_order_id=take_profit_trade.order.orderId,
                stop_loss_order_id=stop_loss_trade.order.orderId,
                parent_trade=parent_trade,
                take_profit_trade=take_profit_trade,
                stop_loss_trade=stop_loss_trade,
            )

        except Exception as e:
            return BracketOrderResult(
                success=False,
                error_message=str(e),
            )

    def place_single_order(
        self,
        contract: Contract,
        action: str,
        quantity: int,
        order_type: str = "LMT",
        limit_price: float | None = None,
    ) -> Trade | None:
        """Place a single order (no bracket).

        Args:
            contract: The contract to trade.
            action: "BUY" or "SELL".
            quantity: Number of contracts.
            order_type: "LMT" or "MKT".
            limit_price: Limit price (required for LMT orders).

        Returns:
            Trade object or None on failure.
        """
        if not self.is_connected:
            return None

        try:
            if order_type == "LMT":
                if limit_price is None:
                    raise ValueError("limit_price required for LMT orders")
                order = LimitOrder(action, quantity, limit_price)
            else:
                from ib_insync import MarketOrder
                order = MarketOrder(action, quantity)

            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(1)
            return trade

        except Exception as e:
            print(f"Order placement failed: {e}")
            return None

    def __enter__(self) -> "IBKRClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.disconnect()
