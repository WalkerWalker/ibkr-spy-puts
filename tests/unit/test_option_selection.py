"""Unit tests for option selection logic using mock data.

These tests run without TWS connection using fixture data.
"""

from datetime import date, timedelta
from pathlib import Path

import pytest

from ibkr_spy_puts.mock_client import MockIBKRClient


# Fixtures directory
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class TestMockClientConnection:
    """Test mock client connection behavior."""

    def test_mock_connect_always_succeeds(self):
        """Mock connection should always succeed."""
        client = MockIBKRClient(fixtures_dir=FIXTURES_DIR)
        assert not client.is_connected

        result = client.connect()

        assert result is True
        assert client.is_connected

    def test_mock_context_manager(self):
        """Mock client works as context manager."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            assert client.is_connected

        assert not client.is_connected

    def test_mock_disconnect(self):
        """Mock disconnect works."""
        client = MockIBKRClient(fixtures_dir=FIXTURES_DIR)
        client.connect()
        assert client.is_connected

        client.disconnect()

        assert not client.is_connected


class TestSpyPrice:
    """Test SPY price retrieval from fixtures."""

    def test_get_spy_price(self):
        """Should return SPY price from fixtures."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            price = client.get_spy_price()

            assert price is not None
            assert price > 0
            assert 100 < price < 1000  # Sanity check


class TestOptionExpirations:
    """Test option expiration retrieval from fixtures."""

    def test_get_option_expirations(self):
        """Should return expirations from fixtures."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            expirations = client.get_option_expirations("SPY")

            assert len(expirations) > 0
            assert all(isinstance(exp, date) for exp in expirations)
            assert expirations == sorted(expirations)  # Should be sorted

    def test_find_expiration_by_dte(self):
        """Should find expiration closest to target DTE."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            expiration = client.find_expiration_by_dte(90, "SPY")

            assert expiration is not None
            assert isinstance(expiration, date)

            # Should be within reasonable range of target
            today = date.today()
            dte = (expiration - today).days
            # Allow some flexibility since fixtures may be from different date
            assert dte > 0  # Should be in the future

    def test_find_expiration_for_different_dtes(self):
        """Should find appropriate expiration for various DTEs."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            exp_30 = client.find_expiration_by_dte(30, "SPY")
            exp_90 = client.find_expiration_by_dte(90, "SPY")
            exp_180 = client.find_expiration_by_dte(180, "SPY")

            assert exp_30 is not None
            assert exp_90 is not None
            assert exp_180 is not None

            # Longer DTE should have later expiration
            assert exp_30 <= exp_90 <= exp_180


class TestOptionChain:
    """Test option chain retrieval from fixtures."""

    def test_get_option_chain_with_greeks(self):
        """Should return option chain from fixtures."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            expiration = client.find_expiration_by_dte(90, "SPY")
            assert expiration is not None

            chain = client.get_option_chain_with_greeks(
                symbol="SPY",
                expiration=expiration,
                right="P",
            )

            assert len(chain) > 0

            # Check option properties
            for opt in chain:
                assert opt.symbol == "SPY"
                assert opt.right == "P"
                assert opt.strike > 0
                assert opt.expiration is not None

    def test_option_chain_has_greeks(self):
        """Options in chain should have delta values."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            expiration = client.find_expiration_by_dte(90, "SPY")
            chain = client.get_option_chain_with_greeks("SPY", expiration, "P")

            options_with_delta = [opt for opt in chain if opt.delta is not None]
            assert len(options_with_delta) > 0

            # Put deltas should be negative
            for opt in options_with_delta:
                assert opt.delta < 0, f"Put delta should be negative, got {opt.delta}"

    def test_option_chain_has_prices(self):
        """Options should have bid/ask prices."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            expiration = client.find_expiration_by_dte(90, "SPY")
            chain = client.get_option_chain_with_greeks("SPY", expiration, "P")

            for opt in chain:
                if opt.bid is not None and opt.ask is not None:
                    assert opt.bid > 0
                    assert opt.ask > 0
                    assert opt.ask >= opt.bid  # Ask should be >= bid


class TestFindPutByDelta:
    """Test put selection by delta."""

    def test_find_put_by_delta(self):
        """Should find put closest to target delta."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            put = client.find_put_by_delta(
                target_delta=-0.15,
                target_dte=90,
                symbol="SPY",
            )

            assert put is not None
            assert put.right == "P"
            assert put.symbol == "SPY"
            assert put.strike > 0

    def test_find_put_delta_is_close_to_target(self):
        """Found put delta should be reasonably close to target."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            target_delta = -0.15

            put = client.find_put_by_delta(
                target_delta=target_delta,
                target_dte=90,
                symbol="SPY",
            )

            assert put is not None
            assert put.delta is not None

            # Delta should be within reasonable range of target
            assert -0.30 <= put.delta <= -0.05, f"Delta {put.delta} not close to {target_delta}"

    def test_find_put_with_different_deltas(self):
        """Should find appropriate strikes for different target deltas."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            put_10 = client.find_put_by_delta(target_delta=-0.10, target_dte=90)
            put_20 = client.find_put_by_delta(target_delta=-0.20, target_dte=90)
            put_30 = client.find_put_by_delta(target_delta=-0.30, target_dte=90)

            assert put_10 is not None
            assert put_20 is not None
            assert put_30 is not None

            # Higher delta (closer to ATM) should have higher strike
            assert put_10.strike <= put_20.strike <= put_30.strike


class TestExitPriceCalculation:
    """Test exit order price calculations."""

    def test_take_profit_price_calculation(self):
        """Calculate take profit buy-back price."""
        # If sold for $1.00, 60% profit means buy back at $0.40
        sell_price = 1.00
        take_profit_pct = 60.0

        buy_back_price = sell_price * (1 - take_profit_pct / 100)

        assert buy_back_price == pytest.approx(0.40)

    def test_stop_loss_price_calculation(self):
        """Calculate stop loss buy-back price."""
        # If sold for $1.00, 200% loss means buy back at $3.00
        sell_price = 1.00
        stop_loss_pct = 200.0

        buy_back_price = sell_price * (1 + stop_loss_pct / 100)

        assert buy_back_price == pytest.approx(3.00)

    def test_exit_prices_with_real_option_price(self):
        """Calculate exit prices using mock option data."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            put = client.find_put_by_delta(target_delta=-0.15, target_dte=90)
            assert put is not None
            assert put.mid is not None

            sell_price = put.mid
            take_profit_pct = 60.0
            stop_loss_pct = 200.0

            take_profit_price = sell_price * (1 - take_profit_pct / 100)
            stop_loss_price = sell_price * (1 + stop_loss_pct / 100)

            # Verify calculations
            assert take_profit_price < sell_price  # Buy back cheaper
            assert stop_loss_price > sell_price  # Buy back more expensive
            assert take_profit_price > 0  # Still positive


class TestAccountSummary:
    """Test account summary retrieval."""

    def test_get_account_summary(self):
        """Should return account summary dict."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            summary = client.get_account_summary()

            assert isinstance(summary, dict)
            assert len(summary) > 0
            assert "NetLiquidation" in summary or "BuyingPower" in summary
