"""Tests to verify TWS connection.

These tests require TWS or IB Gateway to be running.
Tests are skipped if TWS is not available.
"""

import socket

import pytest

from ibkr_spy_puts.config import get_settings
from ibkr_spy_puts.ibkr_client import IBKRClient


def is_tws_running(host: str = "127.0.0.1", port: int = 7496) -> bool:
    """Check if TWS/IB Gateway is running and accepting connections."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


# Get settings for skip condition
_settings = get_settings()
_tws_available = is_tws_running(_settings.tws.host, _settings.tws.port)

skip_if_no_tws = pytest.mark.skipif(
    not _tws_available,
    reason=f"TWS not running on {_settings.tws.host}:{_settings.tws.port}",
)


class TestTWSConnection:
    """Test TWS connection functionality."""

    @skip_if_no_tws
    def test_connect_to_tws(self):
        """Test connecting to TWS."""
        client = IBKRClient()
        try:
            success = client.connect()
            assert success, "Failed to connect to TWS"
            assert client.is_connected, "Client should be connected"
        finally:
            client.disconnect()

    @skip_if_no_tws
    def test_context_manager(self):
        """Test using IBKRClient as context manager."""
        with IBKRClient() as client:
            assert client.is_connected, "Client should be connected in context"

    @skip_if_no_tws
    def test_get_spy_price(self):
        """Test getting SPY price."""
        with IBKRClient() as client:
            price = client.get_spy_price()
            assert price is not None, "Should get SPY price"
            assert price > 0, f"SPY price should be positive, got {price}"
            # Sanity check: SPY should be between $100 and $1000
            assert 100 < price < 1000, f"SPY price {price} seems unreasonable"

    @skip_if_no_tws
    def test_get_account_summary(self):
        """Test getting account summary."""
        with IBKRClient() as client:
            summary = client.get_account_summary()
            assert isinstance(summary, dict), "Summary should be a dict"
            # Should have some account info
            assert len(summary) > 0, "Summary should not be empty"

    @skip_if_no_tws
    def test_get_option_expirations(self):
        """Test getting option expiration dates."""
        with IBKRClient() as client:
            expirations = client.get_option_expirations("SPY")
            assert len(expirations) > 0, "Should have expiration dates"
            # Expirations should be sorted
            assert expirations == sorted(expirations), "Expirations should be sorted"

    @skip_if_no_tws
    def test_find_expiration_by_dte(self):
        """Test finding expiration by target DTE."""
        from datetime import date, timedelta

        with IBKRClient() as client:
            # Find expiration closest to 90 DTE
            expiration = client.find_expiration_by_dte(90, "SPY")
            assert expiration is not None, "Should find an expiration"

            # Should be within reasonable range of 90 days
            today = date.today()
            dte = (expiration - today).days
            assert 60 <= dte <= 120, f"DTE {dte} should be close to 90"

    @skip_if_no_tws
    def test_find_put_by_delta(self):
        """Test finding put by target delta."""
        with IBKRClient() as client:
            option = client.find_put_by_delta(
                target_delta=-0.15,
                target_dte=90,
                symbol="SPY",
            )
            assert option is not None, "Should find an option"
            assert option.right == "P", "Should be a put"
            assert option.symbol == "SPY", "Should be SPY"
            assert option.strike > 0, "Strike should be positive"

            # Delta should be reasonably close to target
            if option.delta is not None:
                assert -0.30 <= option.delta <= -0.05, f"Delta {option.delta} should be near -0.15"


class TestTWSConnectionWithoutTWS:
    """Tests that work even without TWS running."""

    def test_client_creation(self):
        """Test client can be created without TWS."""
        client = IBKRClient()
        assert not client.is_connected

    def test_disconnect_when_not_connected(self):
        """Test disconnect is safe when not connected."""
        client = IBKRClient()
        client.disconnect()  # Should not raise

    def test_connect_failure_handling(self):
        """Test connection failure is handled gracefully."""
        from ibkr_spy_puts.config import TWSSettings

        # Use a port that definitely won't have TWS
        settings = TWSSettings(port=59999)
        client = IBKRClient(settings=settings)
        success = client.connect()
        assert not success, "Connection to invalid port should fail"
        assert not client.is_connected
