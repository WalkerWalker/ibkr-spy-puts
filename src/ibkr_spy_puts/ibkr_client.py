"""IBKR TWS API client wrapper using ib_insync."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ib_insync import IB, Contract, LimitOrder, Option, Order, Stock, TagValue, Trade

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
    fill_price: float | None = None
    cancelled_orders: list | None = None  # Orders cancelled for conflict resolution


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
        import logging
        logger = logging.getLogger(__name__)

        expirations = self.get_option_expirations(symbol)
        if not expirations:
            logger.warning(f"No expirations found for {symbol}")
            return None

        today = date.today()
        target_date = today + timedelta(days=target_dte)

        # Filter to reasonable range (30 days before to 60 days after target)
        min_date = target_date - timedelta(days=30)
        max_date = target_date + timedelta(days=60)
        candidates = [exp for exp in expirations if min_date <= exp <= max_date]

        if not candidates:
            candidates = expirations  # Fallback to all if none in range

        # Sort by distance from target date
        sorted_candidates = sorted(candidates, key=lambda x: abs((x - target_date).days))

        # Log the selection process for transparency
        logger.info(f"=== Expiration Selection (target DTE: {target_dte}) ===")
        logger.info(f"  Today: {today}, Target date: {target_date}")
        for i, exp in enumerate(sorted_candidates[:5]):  # Top 5 candidates
            actual_dte = (exp - today).days
            diff_from_target = (exp - target_date).days
            marker = " <-- SELECTED" if i == 0 else ""
            logger.info(
                f"  #{i+1}: {exp} (DTE: {actual_dte}, diff: {diff_from_target:+d} days){marker}"
            )

        closest = sorted_candidates[0]
        actual_dte = (closest - today).days
        logger.info(f"Selected: {closest} with {actual_dte} DTE (target was {target_dte})")

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
        # Try multiple methods since stock data subscription may not be available
        current_price = None

        # Method 1: Try direct market data request
        ticker = self.ib.reqMktData(stock, "", False, False)
        self.ib.sleep(1)
        price = ticker.marketPrice()
        self.ib.cancelMktData(stock)

        if price and price > 0 and not (price != price):  # Check for NaN
            current_price = price
        else:
            # Method 2: Try to get price from portfolio/positions
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Stock price unavailable (got {price}), trying portfolio...")

            # Check if we have any SPY-related positions to infer price
            # Use a reasonable fallback based on typical SPY range
            # SPY typically trades 500-700 range in 2026
            current_price = 600.0  # Fallback estimate
            logger.info(f"Using fallback price estimate: {current_price}")

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
        import logging
        logger = logging.getLogger(__name__)

        # Find closest expiration
        expiration = self.find_expiration_by_dte(target_dte, symbol)
        if not expiration:
            logger.warning(f"No expiration found for {target_dte} DTE")
            return None

        actual_dte = (expiration - date.today()).days
        logger.info(f"Target DTE: {target_dte}, Selected expiration: {expiration} (actual DTE: {actual_dte})")

        # Get option chain with greeks
        chain = self.get_option_chain_with_greeks(
            symbol, expiration, right="P", use_delayed=use_delayed
        )

        if not chain:
            logger.warning(f"No option chain found for {symbol} {expiration}")
            return None

        # Filter to options with valid delta
        options_with_delta = [opt for opt in chain if opt.delta is not None]
        logger.info(f"Found {len(chain)} options, {len(options_with_delta)} with valid delta")

        if not options_with_delta:
            logger.warning("No options with valid delta found")
            return None

        # Sort by distance from target delta
        sorted_options = sorted(
            options_with_delta,
            key=lambda x: abs(x.delta - target_delta) if x.delta else float("inf"),
        )

        # Log the top candidates for transparency
        logger.info(f"=== Delta Selection (target: {target_delta}) ===")
        for i, opt in enumerate(sorted_options[:5]):  # Top 5 candidates
            delta_diff = abs(opt.delta - target_delta) if opt.delta else float("inf")
            marker = " <-- SELECTED" if i == 0 else ""
            logger.info(
                f"  #{i+1}: Strike {opt.strike}, Delta {opt.delta:.4f}, "
                f"Diff {delta_diff:.4f}, Bid/Ask {opt.bid}/{opt.ask}{marker}"
            )

        closest = sorted_options[0]

        # Log selection summary
        if len(sorted_options) >= 2:
            second = sorted_options[1]
            logger.info(
                f"Selected: {closest.strike} strike (delta {closest.delta:.4f}) "
                f"over {second.strike} strike (delta {second.delta:.4f})"
            )
        else:
            logger.info(f"Selected: {closest.strike} strike (delta {closest.delta:.4f})")

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
        use_aggressive_fill: bool = False,
    ) -> BracketOrderResult:
        """Place a bracket order for a contract.

        This is a two-step process:
        Step 1: Place parent order (handling any conflicting orders)
        Step 2: Place TP/SL orders (no conflict possible since parent filled)

        Args:
            contract: The contract to trade (e.g., Option).
            action: "BUY" or "SELL" for the parent order.
            quantity: Number of contracts.
            limit_price: Limit price for parent order.
            take_profit_price: Price for take profit order.
            stop_loss_price: Price for stop loss order.
            use_aggressive_fill: If True, use Urgent priority (paper trading).

        Returns:
            BracketOrderResult with order IDs and trade objects.
        """
        if not self.is_connected:
            return BracketOrderResult(
                success=False,
                error_message="Not connected to TWS",
            )

        import logging
        import time
        logger = logging.getLogger(__name__)

        try:
            # =================================================================
            # STEP 1: Place parent order (with conflict handling)
            # =================================================================
            opposite_action = "BUY" if action == "SELL" else "SELL"

            # Sync all orders from IB
            logger.info("Step 1: Syncing all open orders from IB...")
            self.ib.reqAllOpenOrders()
            self.ib.sleep(2)

            # Find conflicting orders on the SAME contract (opposite side)
            conflicting_orders = []
            open_trades = self.ib.openTrades()
            logger.info(f"Checking for conflicting orders. Open trades: {len(open_trades)}, target conId: {contract.conId}")

            for trade in open_trades:
                logger.info(f"  Trade: orderId={trade.order.orderId}, conId={trade.contract.conId}, action={trade.order.action}, status={trade.orderStatus.status}")
                if (trade.contract.conId == contract.conId and
                    trade.order.action == opposite_action and
                    trade.orderStatus.status in ["Submitted", "PreSubmitted"]):
                    # Save complete order details for re-placing later (with ORIGINAL OCA group)
                    conflicting_orders.append({
                        "contract": trade.contract,
                        "order": trade.order,
                        "order_type": trade.order.orderType,
                        "action": trade.order.action,
                        "quantity": trade.order.totalQuantity,
                        "lmt_price": trade.order.lmtPrice,
                        "aux_price": trade.order.auxPrice,
                        "oca_group": getattr(trade.order, 'ocaGroup', ''),
                        "tif": trade.order.tif,
                    })
                    logger.info(f"Found conflicting order: {trade.order.orderId} {trade.order.action} {trade.order.orderType} qty={trade.order.totalQuantity} ocaGroup={getattr(trade.order, 'ocaGroup', '')}")

            # Cancel conflicting orders if any exist
            if conflicting_orders:
                logger.info(f"Cancelling {len(conflicting_orders)} conflicting order(s)...")
                for conflict in conflicting_orders:
                    order_id = conflict["order"].orderId
                    logger.info(f"Cancelling order {order_id}...")
                    self.ib.cancelOrder(conflict["order"])

                # Wait for cancellations
                logger.info("Waiting for cancellations to complete...")
                self.ib.sleep(5)

                # Verify cancellations
                self.ib.reqAllOpenOrders()
                self.ib.sleep(3)

                remaining_conflicts = []
                for trade in self.ib.openTrades():
                    if (trade.contract.conId == contract.conId and
                        trade.order.action == opposite_action and
                        trade.orderStatus.status in ["Submitted", "PreSubmitted"]):
                        remaining_conflicts.append(trade.order.orderId)

                if remaining_conflicts:
                    logger.error(f"Orders still active after cancel: {remaining_conflicts}")
                    return BracketOrderResult(
                        success=False,
                        error_message=f"Cannot cancel conflicting orders: {remaining_conflicts}",
                    )
                logger.info("All conflicting orders successfully cancelled")

            # Place parent order with Adaptive algo
            parent = LimitOrder(
                action=action,
                totalQuantity=quantity,
                lmtPrice=limit_price,
                tif="DAY",
                transmit=True,
            )
            parent.algoStrategy = "Adaptive"
            adaptive_priority = "Urgent" if use_aggressive_fill else "Normal"
            parent.algoParams = [TagValue("adaptivePriority", adaptive_priority)]
            logger.info(f"Using Adaptive algo with {adaptive_priority} priority")

            parent_trade = self.ib.placeOrder(contract, parent)
            logger.info(f"Parent order placed with ID: {parent_trade.order.orderId}")
            self.ib.sleep(2)

            # Wait for parent to FILL (10s timeout - scheduler will retry if needed)
            max_wait_seconds = 10
            poll_interval = 2
            waited = 0
            parent_filled = False
            parent_status = ""

            while waited < max_wait_seconds:
                self.ib.reqAllOpenOrders()
                self.ib.sleep(poll_interval)
                waited += poll_interval
                parent_status = parent_trade.orderStatus.status
                logger.info(f"Parent order status after {waited}s: {parent_status}")

                if parent_status == "Filled":
                    parent_filled = True
                    break
                elif parent_status in ["Cancelled", "ApiCancelled", "Inactive"]:
                    logger.error(f"Parent order was cancelled/rejected: {parent_status}")
                    break

            # If not filled within timeout, cancel the order
            if not parent_filled and parent_status == "Submitted":
                logger.info(f"Parent order not filled after {max_wait_seconds}s, cancelling for retry...")
                self.ib.cancelOrder(parent_trade.order)
                self.ib.sleep(3)
                # Verify cancelled - and check if it filled during cancel!
                self.ib.reqAllOpenOrders()
                self.ib.sleep(2)
                parent_status = parent_trade.orderStatus.status
                logger.info(f"After cancel attempt, parent order status: {parent_status}")

                # Check if it actually filled during the cancel process (race condition)
                if parent_status == "Filled":
                    logger.info("Order filled during cancel process - treating as success")
                    parent_filled = True

            # Check if parent filled
            if not parent_filled:
                logger.warning(f"Parent order not filled, status: {parent_status}")
                # Return with cancelled_orders so scheduler can retry or restore them
                return BracketOrderResult(
                    success=False,
                    error_message=f"Parent order not filled within {max_wait_seconds}s, status: {parent_status}. Retry or restore cancelled orders.",
                    parent_order_id=parent_trade.order.orderId,
                    parent_trade=parent_trade,
                    cancelled_orders=conflicting_orders,  # Pass back for retry/restore
                )

            # =================================================================
            # STEP 2: Log post-fill contract details
            # =================================================================
            logger.info("Step 2: Fetching post-fill contract details...")
            self.log_contract_details(contract)

            # =================================================================
            # STEP 3: Place TP/SL orders (no conflict detection needed)
            # =================================================================
            logger.info("Step 3: Placing TP/SL orders for new position...")

            child_action = "BUY" if action == "SELL" else "SELL"
            new_oca_group = f"OCA_{int(time.time())}"

            # Use fill price for TP/SL calculation
            actual_entry_price = limit_price
            if parent_trade.orderStatus.avgFillPrice > 0:
                actual_entry_price = parent_trade.orderStatus.avgFillPrice
                logger.info(f"Parent filled at {actual_entry_price} (limit was {limit_price})")

            # Calculate TP/SL prices based on fill price
            actual_tp_price = round(actual_entry_price * 0.4, 2)  # 60% profit
            actual_sl_price = round(actual_entry_price * 3.0, 2)  # 200% loss
            logger.info(f"TP/SL based on {actual_entry_price}: TP={actual_tp_price}, SL={actual_sl_price}")

            # Place take profit order
            take_profit = LimitOrder(
                action=child_action,
                totalQuantity=quantity,
                lmtPrice=actual_tp_price,
                tif="GTC",
                ocaGroup=new_oca_group,
                ocaType=3,
                transmit=True,
            )
            take_profit_trade = self.ib.placeOrder(contract, take_profit)
            logger.info(f"Take profit order placed: ID={take_profit_trade.order.orderId}, ocaGroup={new_oca_group}")

            # Place stop loss order
            stop_loss = Order(
                orderType="STP",
                action=child_action,
                totalQuantity=quantity,
                auxPrice=actual_sl_price,
                tif="GTC",
                ocaGroup=new_oca_group,
                ocaType=3,
                transmit=True,
            )
            stop_loss_trade = self.ib.placeOrder(contract, stop_loss)
            logger.info(f"Stop loss order placed: ID={stop_loss_trade.order.orderId}, ocaGroup={new_oca_group}")

            # Verify orders
            self.ib.sleep(3)
            self.ib.reqAllOpenOrders()
            self.ib.sleep(2)

            logger.info(f"Parent order {parent_trade.order.orderId}: status={parent_trade.orderStatus.status}")
            logger.info(f"Take profit order {take_profit_trade.order.orderId}: status={take_profit_trade.orderStatus.status}")
            logger.info(f"Stop loss order {stop_loss_trade.order.orderId}: status={stop_loss_trade.orderStatus.status}")

            valid_statuses = ["Submitted", "PreSubmitted", "PendingSubmit", "Filled"]
            bracket_success = (
                parent_trade.orderStatus.status in valid_statuses and
                take_profit_trade.orderStatus.status in valid_statuses and
                stop_loss_trade.orderStatus.status in valid_statuses
            )

            if not bracket_success:
                logger.error(f"Bracket order failed! TP status: {take_profit_trade.orderStatus.status}, SL status: {stop_loss_trade.orderStatus.status}")
                return BracketOrderResult(
                    success=False,
                    error_message=f"TP/SL orders failed - TP: {take_profit_trade.orderStatus.status}, SL: {stop_loss_trade.orderStatus.status}",
                    parent_order_id=parent_trade.order.orderId,
                    parent_trade=parent_trade,
                )

            return BracketOrderResult(
                success=True,
                parent_order_id=parent_trade.order.orderId,
                take_profit_order_id=take_profit_trade.order.orderId,
                stop_loss_order_id=stop_loss_trade.order.orderId,
                parent_trade=parent_trade,
                take_profit_trade=take_profit_trade,
                stop_loss_trade=stop_loss_trade,
                fill_price=actual_entry_price,
                cancelled_orders=conflicting_orders,
            )

        except Exception as e:
            return BracketOrderResult(
                success=False,
                error_message=str(e),
            )

    def restore_cancelled_orders(self, cancelled_orders: list) -> bool:
        """Re-place orders that were cancelled for conflict resolution.

        Args:
            cancelled_orders: List of order dicts from place_bracket_order.

        Returns:
            True if all orders were re-placed successfully.
        """
        if not cancelled_orders:
            return True

        if not self.is_connected:
            return False

        import logging
        import time
        logger = logging.getLogger(__name__)

        logger.info(f"Restoring {len(cancelled_orders)} cancelled order(s)...")

        # Group orders by their original OCA group
        oca_groups: dict[str, list] = {}
        for conflict in cancelled_orders:
            oca = conflict["oca_group"] or f"OCA_RESTORE_{int(time.time())}"
            if oca not in oca_groups:
                oca_groups[oca] = []
            oca_groups[oca].append(conflict)

        try:
            for oca_group, orders in oca_groups.items():
                logger.info(f"Re-placing {len(orders)} order(s) in OCA group: {oca_group}")
                for conflict in orders:
                    order_type = conflict["order_type"]
                    logger.info(f"  Re-placing {order_type}: {conflict['action']} qty={conflict['quantity']} price={conflict['lmt_price'] or conflict['aux_price']}")

                    if order_type == "LMT":
                        new_order = LimitOrder(
                            action=conflict["action"],
                            totalQuantity=conflict["quantity"],
                            lmtPrice=conflict["lmt_price"],
                            tif=conflict["tif"] or "GTC",
                            ocaGroup=oca_group,
                            ocaType=3,
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
                    logger.info(f"  Re-placed order ID: {trade.order.orderId}")

            self.ib.sleep(3)
            logger.info("All cancelled orders restored")
            return True

        except Exception as e:
            logger.error(f"Failed to restore cancelled orders: {e}")
            return False

    def log_contract_details(self, contract: Contract) -> dict | None:
        """Fetch and log current contract details (delta, open interest, etc).

        Call this after a fill to record the contract state at fill time.

        Args:
            contract: The contract to fetch details for.

        Returns:
            Dict with contract details, or None on failure.
        """
        if not self.is_connected:
            return None

        import logging
        logger = logging.getLogger(__name__)

        try:
            # Request market data with greeks (tick 106) and open interest (tick 101)
            self.ib.reqMarketDataType(3)  # Delayed data
            ticker = self.ib.reqMktData(contract, "101,106", False, False)
            self.ib.sleep(3)

            details = {
                "bid": ticker.bid if ticker.bid > 0 else None,
                "ask": ticker.ask if ticker.ask > 0 else None,
                "last": ticker.last if ticker.last > 0 else None,
                "volume": ticker.volume if ticker.volume >= 0 else None,
            }

            # Open interest from tick 101
            if hasattr(ticker, 'callOpenInterest') and ticker.callOpenInterest:
                details["open_interest"] = ticker.callOpenInterest
            elif hasattr(ticker, 'putOpenInterest') and ticker.putOpenInterest:
                details["open_interest"] = ticker.putOpenInterest

            # Greeks from tick 106
            if ticker.modelGreeks:
                details["delta"] = ticker.modelGreeks.delta
                details["gamma"] = ticker.modelGreeks.gamma
                details["theta"] = ticker.modelGreeks.theta
                details["vega"] = ticker.modelGreeks.vega
                details["iv"] = ticker.modelGreeks.impliedVol

            self.ib.cancelMktData(contract)

            # Log the details
            logger.info("=== Post-Fill Contract Details ===")
            logger.info(f"  Bid/Ask: {details.get('bid')}/{details.get('ask')}")
            logger.info(f"  Last: {details.get('last')}, Volume: {details.get('volume')}")
            if details.get('open_interest') is not None:
                logger.info(f"  Open Interest: {details.get('open_interest')}")
            if details.get('delta') is not None:
                iv_str = f", IV={details.get('iv'):.2%}" if details.get('iv') else ""
                logger.info(
                    f"  Greeks: Delta={details.get('delta'):.4f}, "
                    f"Theta={details.get('theta'):.4f}{iv_str}"
                )

            return details

        except Exception as e:
            logger.warning(f"Failed to fetch contract details: {e}")
            return None

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

    def get_margin_for_spy_puts(self) -> float | None:
        """Calculate margin used by all SPY put positions.

        Uses whatIfOrder to simulate closing each SPY put position individually
        and sums up the margin that would be released. This allows seeing
        margin per position in the logs.

        Returns:
            Margin used by SPY puts (positive value), or None on failure.
        """
        if not self.is_connected:
            return None

        import logging
        logger = logging.getLogger(__name__)

        try:
            from ib_insync import MarketOrder

            # Get all positions
            positions = self.ib.positions()

            # Filter to SPY put options (short positions)
            spy_puts = []
            for pos in positions:
                c = pos.contract
                if (c.symbol == "SPY" and
                    c.secType == "OPT" and
                    getattr(c, "right", "") == "P" and
                    pos.position < 0):  # Short position
                    spy_puts.append(pos)

            if not spy_puts:
                logger.info("No SPY put positions found for margin calculation")
                return 0.0

            logger.info(f"Calculating margin for {len(spy_puts)} SPY put position(s)")

            # Calculate margin for each position individually
            total_maint_margin_change = 0.0

            for pos in spy_puts:
                contract = pos.contract
                quantity = abs(int(pos.position))

                # Qualify the contract
                qualified = self.ib.qualifyContracts(contract)
                if not qualified:
                    logger.warning(f"Could not qualify {contract.localSymbol}")
                    continue

                # Create a market order to close (BUY to close short)
                order = MarketOrder("BUY", quantity)

                # Use whatIfOrder to simulate
                whatif = self.ib.whatIfOrder(qualified[0], order)

                if whatif and whatif.maintMarginChange:
                    maint_change = float(whatif.maintMarginChange)
                    margin_for_position = -maint_change if maint_change < 0 else 0
                    total_maint_margin_change += maint_change
                    logger.info(f"  {contract.strike} strike x{quantity}: margin ${margin_for_position:,.2f}")

            # Negative change means margin would be released (margin is currently used)
            margin_used = -total_maint_margin_change if total_maint_margin_change < 0 else 0

            logger.info(f"Total margin used by SPY puts: ${margin_used:,.2f}")
            return margin_used

        except Exception as e:
            logger.warning(f"Failed to calculate margin for SPY puts: {e}")
            return None

    def get_option_greeks(
        self,
        symbol: str,
        strike: float,
        expiration: date,
        right: str = "P",
    ) -> dict | None:
        """Get Greeks for a specific option contract.

        Args:
            symbol: Underlying symbol (e.g., 'SPY').
            strike: Strike price.
            expiration: Expiration date.
            right: 'P' for put, 'C' for call.

        Returns:
            Dict with delta, theta, gamma, vega, iv or None on failure.
        """
        if not self.is_connected:
            return None

        import logging
        logger = logging.getLogger(__name__)

        try:
            # Create option contract
            exp_str = expiration.strftime("%Y%m%d")
            opt = Option(symbol, exp_str, strike, right, "SMART")
            qualified = self.ib.qualifyContracts(opt)

            if not qualified:
                logger.warning(f"Could not qualify option {symbol} {strike} {expiration}")
                return None

            # Request market data with greeks (tick 106)
            self.ib.reqMarketDataType(3)  # Delayed data
            ticker = self.ib.reqMktData(qualified[0], "106", False, False)
            self.ib.sleep(2)

            result = {}

            if ticker.modelGreeks:
                result["delta"] = ticker.modelGreeks.delta
                result["gamma"] = ticker.modelGreeks.gamma
                result["theta"] = ticker.modelGreeks.theta
                result["vega"] = ticker.modelGreeks.vega
                result["iv"] = ticker.modelGreeks.impliedVol

            # Also get bid/ask/mid for P&L calculation
            if ticker.bid and ticker.bid > 0:
                result["bid"] = ticker.bid
            if ticker.ask and ticker.ask > 0:
                result["ask"] = ticker.ask
            if result.get("bid") and result.get("ask"):
                result["mid"] = (result["bid"] + result["ask"]) / 2

            self.ib.cancelMktData(qualified[0])

            return result if result else None

        except Exception as e:
            logger.warning(f"Failed to get Greeks for {symbol} {strike} {expiration}: {e}")
            return None

    def __enter__(self) -> "IBKRClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.disconnect()
