"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def tws_settings():
    """Provide default TWS settings for tests."""
    from ibkr_spy_puts.config import TWSSettings

    return TWSSettings()


@pytest.fixture
def ibkr_client():
    """Provide an unconnected IBKRClient for tests."""
    from ibkr_spy_puts.ibkr_client import IBKRClient

    return IBKRClient()
