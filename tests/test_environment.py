"""Tests to verify Python environment is correctly set up.

These tests should pass immediately after `poetry install` without any external dependencies.
"""

import sys


class TestPythonEnvironment:
    """Verify Python environment is correctly configured."""

    def test_python_version(self):
        """Verify Python version is 3.11+."""
        assert sys.version_info >= (3, 11), (
            f"Python 3.11+ required, got {sys.version_info.major}.{sys.version_info.minor}"
        )

    def test_import_ib_insync(self):
        """Verify ib_insync is installed and importable."""
        import ib_insync

        assert ib_insync is not None
        # Verify we can access the main IB class
        from ib_insync import IB

        assert IB is not None

    def test_import_psycopg2(self):
        """Verify psycopg2 is installed and importable."""
        import psycopg2

        assert psycopg2 is not None

    def test_import_pydantic(self):
        """Verify pydantic is installed and importable."""
        import pydantic
        from pydantic_settings import BaseSettings

        assert pydantic is not None
        assert BaseSettings is not None

    def test_import_dotenv(self):
        """Verify python-dotenv is installed and importable."""
        from dotenv import load_dotenv

        assert load_dotenv is not None

    def test_import_project_modules(self):
        """Verify project modules are importable."""
        from ibkr_spy_puts import __version__
        from ibkr_spy_puts.config import Settings, get_settings
        from ibkr_spy_puts.ibkr_client import IBKRClient

        assert __version__ == "0.1.0"
        assert Settings is not None
        assert get_settings is not None
        assert IBKRClient is not None

    def test_config_loads(self):
        """Verify configuration loads correctly (from .env or defaults)."""
        from ibkr_spy_puts.config import get_settings

        settings = get_settings()

        # TWS settings
        assert settings.tws.host == "127.0.0.1"
        assert settings.tws.port in (7496, 7497)  # Live or paper
        # ClientId 0 is the master clientId - can manage orders from any clientId
        assert settings.tws.client_id == 0

        # Strategy settings
        assert settings.strategy.symbol == "SPY"
        assert settings.strategy.quantity >= 1
        assert settings.strategy.target_dte == 90
        assert settings.strategy.target_delta == -0.15

        # Exit order settings (TP/SL)
        assert settings.exit_orders.enabled is True
        assert settings.exit_orders.take_profit_pct == 60.0
        assert settings.exit_orders.stop_loss_pct == 200.0

        # Schedule settings (may be overridden by .env)
        assert settings.schedule.trade_at_open is True
        assert settings.schedule.trade_time in ("09:30", "09:35")  # Default or configured

    def test_ibkr_client_instantiation(self):
        """Verify IBKRClient can be instantiated without connecting."""
        from ibkr_spy_puts.ibkr_client import IBKRClient

        client = IBKRClient()
        assert client is not None
        assert client.is_connected is False
