"""Mock IBKR client for offline testing.

This module provides a MockIBKRClient that loads market data from JSON fixtures
instead of connecting to TWS. This enables:
- Development without market hours restriction
- Fast unit tests without network calls
- Reproducible tests with consistent data
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ibkr_spy_puts.ibkr_client import BracketOrderResult, OptionContract


# Default fixtures directory
DEFAULT_FIXTURES_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures"


@dataclass
class MockOption:
    """Mock ib_insync Option contract for testing."""

    symbol: str
    lastTradeDateOrContractMonth: str
    strike: float
    right: str
    exchange: str = "SMART"
    conId: int = 0


class MockIBKRClient:
    """Mock IBKR client that loads data from fixtures.

    Provides the same interface as IBKRClient but uses pre-captured
    JSON data instead of live TWS connection.
    """

    def __init__(self, fixtures_dir: Path | str | None = None):
        """Initialize the mock client.

        Args:
            fixtures_dir: Directory containing JSON fixtures.
                         Defaults to tests/fixtures/
        """
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else DEFAULT_FIXTURES_DIR
        self._connected = False
        self._data: dict[str, Any] = {}
        self._load_fixtures()

    def _load_fixtures(self) -> None:
        """Load all available fixtures."""
        # Load complete market data if available
        market_data_path = self.fixtures_dir / "market_data.json"
        if market_data_path.exists():
            with open(market_data_path) as f:
                self._data = json.load(f)
        else:
            # Load individual fixtures
            self._load_individual_fixtures()

    def _load_individual_fixtures(self) -> None:
        """Load individual fixture files."""
        # SPY price
        spy_price_path = self.fixtures_dir / "spy_price.json"
        if spy_price_path.exists():
            with open(spy_price_path) as f:
                data = json.load(f)
                self._data["spy_price"] = data.get("price")

        # Expirations
        exp_path = self.fixtures_dir / "spy_expirations.json"
        if exp_path.exists():
            with open(exp_path) as f:
                data = json.load(f)
                self._data["expirations"] = data.get("expirations", [])

        # Option chain
        chain_path = self.fixtures_dir / "spy_option_chain.json"
        if chain_path.exists():
            with open(chain_path) as f:
                data = json.load(f)
                self._data["option_chain"] = data.get("chain", [])
                self._data["target_expiration"] = data.get("expiration")
                self._data["target_dte"] = data.get("target_dte")
                self._data["spy_price"] = data.get("spy_price")

    @property
    def is_connected(self) -> bool:
        """Check if 'connected' (mock always succeeds)."""
        return self._connected

    def connect(self) -> bool:
        """Simulate connection (always succeeds)."""
        self._connected = True
        return True

    def disconnect(self) -> None:
        """Simulate disconnection."""
        self._connected = False

    def get_spy_price(self, use_delayed: bool = True) -> float | None:
        """Get SPY price from fixtures.

        Args:
            use_delayed: Ignored in mock (for API compatibility).

        Returns:
            SPY price from fixtures or None.
        """
        return self._data.get("spy_price")

    def get_account_summary(self) -> dict:
        """Get account summary from fixtures.

        Returns:
            Account summary dict or mock data.
        """
        return self._data.get("account_summary", {
            "NetLiquidation": "100000.00",
            "BuyingPower": "400000.00",
            "AvailableFunds": "100000.00",
        })

    def get_option_expirations(self, symbol: str = "SPY") -> list[date]:
        """Get option expirations from fixtures.

        Args:
            symbol: Symbol (only SPY supported in mock).

        Returns:
            List of expiration dates.
        """
        exp_strings = self._data.get("expirations", [])
        return [date.fromisoformat(exp) for exp in exp_strings]

    def find_expiration_by_dte(
        self, target_dte: int, symbol: str = "SPY"
    ) -> date | None:
        """Find expiration closest to target DTE.

        Args:
            target_dte: Target days to expiration.
            symbol: Symbol (only SPY supported in mock).

        Returns:
            Closest expiration date or None.
        """
        expirations = self.get_option_expirations(symbol)
        if not expirations:
            return None

        today = date.today()
        target_date = today + timedelta(days=target_dte)

        closest = min(expirations, key=lambda x: abs((x - target_date).days))
        return closest

    def get_option_chain_with_greeks(
        self,
        symbol: str,
        expiration: date,
        right: str = "P",
        use_delayed: bool = True,
    ) -> list[OptionContract]:
        """Get option chain from fixtures.

        Args:
            symbol: Symbol (only SPY supported in mock).
            expiration: Expiration date.
            right: 'P' for puts, 'C' for calls.
            use_delayed: Ignored in mock.

        Returns:
            List of OptionContract from fixtures.
        """
        chain_data = self._data.get("option_chain", [])

        results = []
        for opt_data in chain_data:
            # Filter by right if needed
            if opt_data.get("right") != right:
                continue

            exp_str = expiration.strftime("%Y%m%d")
            mock_contract = MockOption(
                symbol=opt_data["symbol"],
                lastTradeDateOrContractMonth=exp_str,
                strike=opt_data["strike"],
                right=opt_data["right"],
            )

            results.append(
                OptionContract(
                    symbol=opt_data["symbol"],
                    strike=opt_data["strike"],
                    expiration=date.fromisoformat(opt_data["expiration"]),
                    right=opt_data["right"],
                    delta=opt_data.get("delta"),
                    bid=opt_data.get("bid"),
                    ask=opt_data.get("ask"),
                    mid=opt_data.get("mid"),
                    contract=mock_contract,  # type: ignore
                )
            )

        return results

    def find_put_by_delta(
        self,
        target_delta: float = -0.15,
        target_dte: int = 90,
        symbol: str = "SPY",
        use_delayed: bool = True,
    ) -> OptionContract | None:
        """Find put closest to target delta from fixtures.

        Args:
            target_delta: Target delta (negative for puts).
            target_dte: Target days to expiration.
            symbol: Symbol (only SPY supported in mock).
            use_delayed: Ignored in mock.

        Returns:
            OptionContract closest to target delta or None.
        """
        # Find closest expiration
        expiration = self.find_expiration_by_dte(target_dte, symbol)
        if not expiration:
            return None

        # Get option chain
        chain = self.get_option_chain_with_greeks(
            symbol, expiration, right="P", use_delayed=use_delayed
        )

        if not chain:
            return None

        # Filter to options with valid delta
        options_with_delta = [opt for opt in chain if opt.delta is not None]

        if not options_with_delta:
            return None

        # Find option closest to target delta
        closest = min(
            options_with_delta,
            key=lambda x: abs(x.delta - target_delta) if x.delta else float("inf"),
        )

        return closest

    def place_bracket_order(
        self,
        contract: Any,
        action: str,
        quantity: int,
        limit_price: float,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> BracketOrderResult:
        """Simulate placing a bracket order.

        Args:
            contract: The contract to trade (ignored in mock).
            action: "BUY" or "SELL".
            quantity: Number of contracts.
            limit_price: Limit price for parent order.
            take_profit_price: Price for take profit order.
            stop_loss_price: Price for stop loss order.

        Returns:
            BracketOrderResult with simulated order IDs.
        """
        if not self._connected:
            return BracketOrderResult(
                success=False,
                error_message="Not connected",
            )

        # Generate mock order IDs
        import random
        base_id = random.randint(10000, 99999)

        return BracketOrderResult(
            success=True,
            parent_order_id=base_id,
            take_profit_order_id=base_id + 1,
            stop_loss_order_id=base_id + 2,
            parent_trade=None,  # No real trade objects in mock
            take_profit_trade=None,
            stop_loss_trade=None,
        )

    def __enter__(self) -> "MockIBKRClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.disconnect()


def get_client(use_mock: bool = False, fixtures_dir: Path | None = None):
    """Factory function to get either real or mock client.

    Args:
        use_mock: If True, return MockIBKRClient.
        fixtures_dir: Fixtures directory for mock client.

    Returns:
        IBKRClient or MockIBKRClient instance.
    """
    if use_mock:
        return MockIBKRClient(fixtures_dir=fixtures_dir)
    else:
        from ibkr_spy_puts.ibkr_client import IBKRClient
        return IBKRClient()
