"""Unit tests for scheduler and market calendar."""

from datetime import date, timedelta

import pytest

from ibkr_spy_puts.scheduler import MarketCalendar


class TestMarketCalendar:
    """Test market calendar functionality."""

    @pytest.fixture
    def calendar(self):
        """Create a market calendar instance."""
        return MarketCalendar()

    def test_weekday_is_usually_trading_day(self, calendar):
        """Weekdays that aren't holidays should be trading days."""
        # Find a recent Monday that's not a holiday (use a known date)
        # January 6, 2025 was a Monday and trading day
        test_date = date(2025, 1, 6)
        assert calendar.is_trading_day(test_date) is True

    def test_weekend_is_not_trading_day(self, calendar):
        """Weekends should not be trading days."""
        # January 4, 2025 was a Saturday
        saturday = date(2025, 1, 4)
        sunday = date(2025, 1, 5)

        assert calendar.is_trading_day(saturday) is False
        assert calendar.is_trading_day(sunday) is False

    def test_christmas_is_not_trading_day(self, calendar):
        """Christmas should not be a trading day."""
        # Christmas 2024 was on Wednesday
        christmas = date(2024, 12, 25)
        assert calendar.is_trading_day(christmas) is False

    def test_new_years_is_not_trading_day(self, calendar):
        """New Year's Day should not be a trading day."""
        # New Year's 2025 was on Wednesday
        new_years = date(2025, 1, 1)
        assert calendar.is_trading_day(new_years) is False

    def test_next_trading_day_from_weekend(self, calendar):
        """Next trading day from weekend should be Monday."""
        # January 4, 2025 was a Saturday
        saturday = date(2025, 1, 4)
        next_day = calendar.next_trading_day(saturday)

        # Should be Monday January 6, 2025
        assert next_day == date(2025, 1, 6)
        assert next_day.weekday() == 0  # Monday

    def test_next_trading_day_from_trading_day(self, calendar):
        """Next trading day from a trading day should be same day."""
        # January 6, 2025 was a Monday (trading day)
        monday = date(2025, 1, 6)
        next_day = calendar.next_trading_day(monday)

        assert next_day == monday

    def test_get_holidays_returns_list(self, calendar):
        """get_holidays should return a list of dates."""
        holidays = calendar.get_holidays(2025)

        assert isinstance(holidays, list)
        assert len(holidays) > 0
        assert all(isinstance(h, date) for h in holidays)

    def test_holidays_include_major_holidays(self, calendar):
        """Holidays should include major US holidays."""
        holidays = calendar.get_holidays(2025)

        # Check for some expected holidays in 2025
        # New Year's Day (Jan 1)
        assert date(2025, 1, 1) in holidays

        # MLK Day (third Monday of January)
        assert date(2025, 1, 20) in holidays

        # Independence Day (July 4)
        assert date(2025, 7, 4) in holidays

        # Thanksgiving (fourth Thursday of November)
        assert date(2025, 11, 27) in holidays

        # Christmas
        assert date(2025, 12, 25) in holidays

    def test_caching_works(self, calendar):
        """Calendar caching should work for repeated calls."""
        # Call twice for same year
        holidays1 = calendar.get_holidays(2025)
        holidays2 = calendar.get_holidays(2025)

        assert holidays1 == holidays2

    def test_today_check_works(self, calendar):
        """is_trading_day with no argument should check today."""
        # This just verifies no exception is raised
        result = calendar.is_trading_day()
        assert isinstance(result, bool)


class TestSchedulerConfig:
    """Test scheduler configuration."""

    def test_schedule_settings_defaults(self):
        """Test default schedule settings."""
        from ibkr_spy_puts.config import ScheduleSettings

        settings = ScheduleSettings()

        assert settings.trade_at_open is True
        assert settings.trade_time == "09:30"
        assert settings.timezone == "America/New_York"

    def test_schedule_settings_custom(self):
        """Test custom schedule settings."""
        from ibkr_spy_puts.config import ScheduleSettings

        settings = ScheduleSettings(
            trade_time="10:00",
            timezone="US/Eastern",
        )

        assert settings.trade_time == "10:00"
        assert settings.timezone == "US/Eastern"


class TestTradeFunction:
    """Test the trade function creation."""

    def test_create_trade_function_mock(self):
        """Test creating a mock trade function."""
        from ibkr_spy_puts.scheduler import create_trade_function

        trade_func = create_trade_function(use_mock=True, dry_run=True)

        assert callable(trade_func)

    def test_trade_function_executes_with_mock(self):
        """Test trade function executes without error in mock mode."""
        from ibkr_spy_puts.scheduler import create_trade_function

        trade_func = create_trade_function(use_mock=True, dry_run=True)

        # Should not raise
        trade_func()
