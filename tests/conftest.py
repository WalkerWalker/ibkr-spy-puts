"""Pytest configuration and fixtures."""

import os
import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "paper_trading: mark test as requiring paper trading environment"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )


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


@pytest.fixture
def paper_trading_env():
    """Fixture that provides paper trading environment info.

    Returns a dict with connection details for the paper trading environment.
    """
    return {
        "tws_host": os.getenv("TWS_HOST", "ib-gateway"),
        "tws_port": int(os.getenv("TWS_PORT", "4003")),
        "db_host": os.getenv("DB_HOST", "postgres"),
        "db_port": int(os.getenv("DB_PORT", "5432")),
        "db_name": os.getenv("DB_NAME", "ibkr_puts_paper"),
        "dashboard_url": "http://localhost:8001",
        "is_paper": os.getenv("TRADING_MODE", "paper") == "paper",
    }


@pytest.fixture
def ib_paper_connection(paper_trading_env):
    """Create IB connection for paper trading tests.

    This fixture creates a connection to the paper trading IB Gateway
    and automatically disconnects after the test.
    """
    import asyncio

    # Ensure we have a fresh event loop
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            asyncio.set_event_loop(asyncio.new_event_loop())
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    from ib_insync import IB

    ib = IB()
    host = paper_trading_env["tws_host"]
    port = paper_trading_env["tws_port"]

    # Use a unique client ID to avoid conflicts
    import random
    client_id = random.randint(100, 199)

    try:
        ib.connect(host, port, clientId=client_id, timeout=15)
        yield ib
    finally:
        if ib.isConnected():
            ib.disconnect()


@pytest.fixture
def clean_orders(ib_paper_connection):
    """Fixture that cleans up all orders before and after test.

    Use this fixture to ensure tests start with a clean slate.
    """
    ib = ib_paper_connection

    # Clean before test
    ib.reqGlobalCancel()
    ib.sleep(3)

    yield ib

    # Clean after test
    ib.reqGlobalCancel()
    ib.sleep(2)
