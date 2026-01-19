"""Scheduler for daily put selling strategy.

Uses APScheduler to run the strategy at market open (configurable).
Automatically skips weekends and US market holidays.
"""

import logging
import signal
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

# Load environment variables
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

import pandas_market_calendars as mcal
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ibkr_spy_puts.config import ScheduleSettings, get_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class MarketCalendar:
    """NYSE market calendar for holiday detection."""

    def __init__(self):
        """Initialize the NYSE calendar."""
        self.nyse = mcal.get_calendar("NYSE")
        # Cache valid trading days for performance
        self._cache: dict[int, set[date]] = {}

    def _get_trading_days_for_year(self, year: int) -> set[date]:
        """Get all trading days for a year (cached)."""
        if year not in self._cache:
            start = f"{year}-01-01"
            end = f"{year}-12-31"
            schedule = self.nyse.schedule(start_date=start, end_date=end)
            self._cache[year] = {d.date() for d in schedule.index}
        return self._cache[year]

    def is_trading_day(self, check_date: date | None = None) -> bool:
        """Check if a date is a trading day.

        Args:
            check_date: Date to check. Defaults to today.

        Returns:
            True if the date is a trading day.
        """
        if check_date is None:
            check_date = date.today()

        trading_days = self._get_trading_days_for_year(check_date.year)
        return check_date in trading_days

    def next_trading_day(self, from_date: date | None = None) -> date:
        """Get the next trading day.

        Args:
            from_date: Starting date. Defaults to today.

        Returns:
            The next trading day (could be today if today is a trading day).
        """
        if from_date is None:
            from_date = date.today()

        check_date = from_date
        for _ in range(10):  # Max 10 days lookahead
            if self.is_trading_day(check_date):
                return check_date
            check_date += timedelta(days=1)

        # Fallback: return the next weekday
        return check_date

    def get_holidays(self, year: int) -> list[date]:
        """Get all market holidays for a year.

        Args:
            year: The year to get holidays for.

        Returns:
            List of holiday dates.
        """
        # Get all weekdays in the year
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        all_weekdays = set()
        current = start
        while current <= end:
            if current.weekday() < 5:  # Monday = 0, Friday = 4
                all_weekdays.add(current)
            current += timedelta(days=1)

        # Trading days
        trading_days = self._get_trading_days_for_year(year)

        # Holidays = weekdays that are not trading days
        holidays = sorted(all_weekdays - trading_days)
        return holidays


class TradingScheduler:
    """Scheduler for running the trading strategy."""

    def __init__(
        self,
        trade_func: Callable[[], None],
        settings: ScheduleSettings | None = None,
        force_run: bool = False,
    ):
        """Initialize the scheduler.

        Args:
            trade_func: Function to call when it's time to trade.
            settings: Schedule settings.
            force_run: If True, bypass trading day check (for testing on weekends).
        """
        self.trade_func = trade_func
        self.settings = settings or ScheduleSettings()
        self.force_run = force_run
        self.calendar = MarketCalendar()
        self.scheduler = BlockingScheduler(timezone=self.settings.timezone)
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup graceful shutdown handlers."""
        def shutdown(signum, frame):
            logger.info("Received shutdown signal, stopping scheduler...")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

    def _execute_trade(self):
        """Execute the trade if today is a trading day."""
        today = date.today()

        if not self.calendar.is_trading_day(today):
            if self.force_run:
                logger.warning(f"Force running trade on non-trading day: {today}")
            else:
                logger.info(f"Skipping trade - {today} is not a trading day")
                return

        logger.info(f"Executing scheduled trade for {today}")
        try:
            self.trade_func()
            logger.info("Trade execution completed")
        except Exception as e:
            logger.error(f"Trade execution failed: {e}", exc_info=True)

    def _parse_trade_time(self) -> tuple[int, int]:
        """Parse trade time from settings.

        Returns:
            Tuple of (hour, minute).
        """
        parts = self.settings.trade_time.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return hour, minute

    def start(self):
        """Start the scheduler."""
        hour, minute = self._parse_trade_time()

        # Schedule: Monday-Friday normally, or all days if force_run is enabled
        day_of_week = "mon-sun" if self.force_run else "mon-fri"
        trigger = CronTrigger(
            day_of_week=day_of_week,
            hour=hour,
            minute=minute,
            timezone=self.settings.timezone,
        )

        self.scheduler.add_job(
            self._execute_trade,
            trigger=trigger,
            id="daily_trade",
            name="Daily Put Selling",
            replace_existing=True,
        )

        next_run = self.get_next_run_time()
        if next_run:
            logger.info(f"Scheduler started. Next trade: {next_run}")
        else:
            logger.info(f"Scheduler started. Waiting for next scheduled time...")
        logger.info(f"Trade time: {hour:02d}:{minute:02d} {self.settings.timezone}")

        # Print upcoming holidays
        holidays = self.calendar.get_holidays(date.today().year)
        upcoming = [h for h in holidays if h >= date.today()][:5]
        if upcoming:
            logger.info(f"Upcoming market holidays: {', '.join(str(h) for h in upcoming)}")

        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped")

    def stop(self):
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler shutdown complete")

    def get_next_run_time(self) -> datetime | None:
        """Get the next scheduled run time.

        Returns:
            Next run datetime or None if not scheduled.
        """
        job = self.scheduler.get_job("daily_trade")
        if job:
            # APScheduler 4.x uses different API
            try:
                return job.next_run_time
            except AttributeError:
                # Fallback for newer APScheduler versions
                return None
        return None

    def run_now(self):
        """Run the trade immediately (for testing)."""
        logger.info("Manual trade trigger")
        self._execute_trade()


def create_trade_function(
    use_mock: bool = False,
    dry_run: bool = False,
    port: int | None = None,
) -> Callable[[], None]:
    """Create the trade function for the scheduler.

    Args:
        use_mock: Use mock client.
        dry_run: Don't actually place orders.
        port: TWS port override.

    Returns:
        Trade function that can be called by the scheduler.
    """
    def trade():
        import asyncio
        from decimal import Decimal
        from ibkr_spy_puts.config import BracketSettings, DatabaseSettings, TWSSettings
        from ibkr_spy_puts.database import Database, Position, Trade
        from ibkr_spy_puts.strategy import PutSellingStrategy

        # ib_insync requires an event loop - create one for this thread
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Create client
        if use_mock:
            from ibkr_spy_puts.mock_client import MockIBKRClient
            client = MockIBKRClient()
        else:
            from ibkr_spy_puts.ibkr_client import IBKRClient
            settings = TWSSettings()
            if port:
                settings = TWSSettings(port=port)
            client = IBKRClient(settings=settings)

        # Connect to database
        db = Database(settings=DatabaseSettings())
        if not db.connect():
            logger.error("Failed to connect to database")
            return
        logger.info("Connected to database")

        # Connect to TWS
        logger.info("Connecting to TWS...")
        if not client.connect():
            logger.error("Failed to connect to TWS")
            db.disconnect()
            return

        try:
            strategy = PutSellingStrategy(client)
            trade_order, result = strategy.run(dry_run=dry_run)

            if trade_order:
                logger.info(strategy.describe_trade(trade_order))

            logger.info(f"Result: {result.message}")

            # Record to database if successful and not dry run
            if result.success and not dry_run and trade_order:
                logger.info("Recording trade to database...")

                # Determine actual entry price - use fill price if available, else limit price
                entry_price = trade_order.limit_price
                if result.fill_price and result.fill_price > 0:
                    entry_price = result.fill_price
                    logger.info(f"Using actual fill price: {entry_price} (limit was {trade_order.limit_price})")

                # Recalculate TP/SL based on actual entry price
                from ibkr_spy_puts.strategy import BracketPrices
                bracket_settings = BracketSettings()
                actual_bracket = BracketPrices.calculate(
                    sell_price=entry_price,
                    take_profit_pct=bracket_settings.take_profit_pct,
                    stop_loss_pct=bracket_settings.stop_loss_pct,
                )

                # Log to trades table (execution history)
                db_trade = Trade(
                    trade_date=date.today(),
                    symbol=trade_order.option.symbol,
                    strike=Decimal(str(trade_order.option.strike)),
                    expiration=trade_order.option.expiration,
                    quantity=trade_order.quantity,
                    action="SELL",
                    price=Decimal(str(entry_price)),
                    fill_time=datetime.now(),
                    strategy_id="spy-put-selling",
                )
                trade_id = db.insert_trade(db_trade)
                logger.info(f"Logged trade execution: ID={trade_id}")

                # Create position record (the book)
                position = Position(
                    symbol=trade_order.option.symbol,
                    strike=Decimal(str(trade_order.option.strike)),
                    expiration=trade_order.option.expiration,
                    quantity=trade_order.quantity,
                    entry_price=Decimal(str(entry_price)),
                    entry_time=datetime.now(),
                    expected_tp_price=Decimal(str(actual_bracket.take_profit_price)),
                    expected_sl_price=Decimal(str(actual_bracket.stop_loss_price)),
                    status="OPEN",
                    strategy_id="spy-put-selling",
                )
                position_id = db.insert_position(position)
                logger.info(f"Created position: ID={position_id}")

                # Orders are live in IBKR - not persisted to database
                if result.take_profit_order_id:
                    logger.info(f"Take profit order placed: {result.take_profit_order_id}")
                if result.stop_loss_order_id:
                    logger.info(f"Stop loss order placed: {result.stop_loss_order_id}")

                logger.info("Trade recorded to database successfully!")

            elif result.success and dry_run:
                logger.info("DRY RUN - Trade not recorded to database")

        finally:
            client.disconnect()
            db.disconnect()
            logger.info("Disconnected from TWS and database")

    return trade


def run_scheduler(
    use_mock: bool = False,
    dry_run: bool = False,
    port: int | None = None,
    run_immediately: bool = False,
    force_run: bool = False,
):
    """Run the trading scheduler.

    Args:
        use_mock: Use mock client.
        dry_run: Don't actually place orders.
        port: TWS port override.
        run_immediately: Execute trade immediately before starting scheduler.
        force_run: Bypass trading day check (for weekend testing).
    """
    # Reload settings after dotenv is loaded
    import os
    schedule_settings = ScheduleSettings(
        trade_time=os.getenv("SCHEDULE_TRADE_TIME", "09:30"),
        timezone=os.getenv("SCHEDULE_TIMEZONE", "America/New_York"),
    )

    # Check for force run from environment
    force_run = force_run or os.getenv("FORCE_RUN", "").lower() in ("true", "1", "yes")

    settings = get_settings()
    trade_func = create_trade_function(
        use_mock=use_mock,
        dry_run=dry_run,
        port=port,
    )

    scheduler = TradingScheduler(
        trade_func=trade_func,
        settings=schedule_settings,
        force_run=force_run,
    )

    logger.info("=" * 60)
    logger.info("IBKR SPY Put Selling Bot - Scheduler Mode")
    logger.info("=" * 60)
    logger.info(f"Mode: {'MOCK' if use_mock else 'LIVE'}")
    logger.info(f"Dry Run: {dry_run}")
    if not use_mock:
        logger.info(f"TWS Port: {port or settings.tws.port}")
    logger.info(f"Run Immediately: {run_immediately}")
    if force_run:
        logger.warning("Force Run: ENABLED (will run on non-trading days)")
    logger.info("=" * 60)

    # Execute immediately if requested
    if run_immediately:
        logger.info("Executing trade immediately...")
        scheduler.run_now()

    scheduler.start()
