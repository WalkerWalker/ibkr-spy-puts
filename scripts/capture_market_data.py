#!/usr/bin/env python3
"""Capture market data from TWS and save as fixtures for offline testing.

Run this script during market hours to capture:
- SPY price
- Option expirations
- Option chain with greeks

Usage:
    poetry run python scripts/capture_market_data.py

The data will be saved to tests/fixtures/
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ibkr_spy_puts.config import TWSSettings, StrategySettings
from ibkr_spy_puts.ibkr_client import IBKRClient


def serialize_date(obj):
    """JSON serializer for date objects."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def capture_market_data(
    symbol: str = "SPY",
    target_dte: int = 90,
    port: int | None = None,
) -> dict:
    """Capture market data from TWS.

    Args:
        symbol: The underlying symbol to capture.
        target_dte: Target DTE for option chain capture.
        port: TWS port (default from settings).

    Returns:
        Dictionary with all captured data.
    """
    settings = TWSSettings()
    if port:
        settings = TWSSettings(port=port)

    print(f"Connecting to TWS on {settings.host}:{settings.port}...")

    client = IBKRClient(settings=settings)
    if not client.connect():
        print("ERROR: Failed to connect to TWS. Is it running?")
        return {}

    try:
        data = {
            "captured_at": datetime.now().isoformat(),
            "symbol": symbol,
            "target_dte": target_dte,
        }

        # Capture SPY price
        print(f"Fetching {symbol} price...")
        price = client.get_spy_price()
        if price:
            print(f"  {symbol} price: ${price:.2f}")
            data["spy_price"] = price
        else:
            print(f"  WARNING: Could not get {symbol} price")
            data["spy_price"] = None

        # Capture account summary
        print("Fetching account summary...")
        summary = client.get_account_summary()
        data["account_summary"] = summary
        print(f"  Got {len(summary)} account fields")

        # Capture option expirations
        print(f"Fetching {symbol} option expirations...")
        expirations = client.get_option_expirations(symbol)
        data["expirations"] = [exp.isoformat() for exp in expirations]
        print(f"  Found {len(expirations)} expiration dates")

        # Find closest expiration to target DTE
        print(f"Finding expiration closest to {target_dte} DTE...")
        closest_exp = client.find_expiration_by_dte(target_dte, symbol)
        if closest_exp:
            actual_dte = (closest_exp - date.today()).days
            print(f"  Closest expiration: {closest_exp} ({actual_dte} DTE)")
            data["target_expiration"] = closest_exp.isoformat()
            data["actual_dte"] = actual_dte

            # Capture option chain with greeks for this expiration
            print(f"Fetching option chain with greeks for {closest_exp}...")
            chain = client.get_option_chain_with_greeks(
                symbol, closest_exp, right="P", use_delayed=True
            )
            print(f"  Got {len(chain)} options with greeks")

            # Serialize option chain (excluding ib_insync Contract objects)
            chain_data = []
            for opt in chain:
                chain_data.append({
                    "symbol": opt.symbol,
                    "strike": opt.strike,
                    "expiration": opt.expiration.isoformat(),
                    "right": opt.right,
                    "delta": opt.delta,
                    "bid": opt.bid,
                    "ask": opt.ask,
                    "mid": opt.mid,
                })
            data["option_chain"] = chain_data

            # Find put by delta
            print("Finding put closest to -0.15 delta...")
            strategy = StrategySettings()
            put = client.find_put_by_delta(
                target_delta=strategy.target_delta,
                target_dte=target_dte,
                symbol=symbol,
            )
            if put:
                delta_str = f"{put.delta:.4f}" if put.delta else "N/A"
                print(f"  Found: Strike ${put.strike}, Delta {delta_str}")
                data["selected_put"] = {
                    "symbol": put.symbol,
                    "strike": put.strike,
                    "expiration": put.expiration.isoformat(),
                    "right": put.right,
                    "delta": put.delta,
                    "bid": put.bid,
                    "ask": put.ask,
                    "mid": put.mid,
                }
            else:
                print("  WARNING: Could not find put by delta")
                data["selected_put"] = None
        else:
            print(f"  WARNING: Could not find expiration for {target_dte} DTE")
            data["target_expiration"] = None
            data["option_chain"] = []
            data["selected_put"] = None

        return data

    finally:
        client.disconnect()
        print("Disconnected from TWS.")


def save_fixtures(data: dict, fixtures_dir: Path) -> None:
    """Save captured data as JSON fixtures.

    Args:
        data: Captured market data.
        fixtures_dir: Directory to save fixtures.
    """
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    # Save complete data
    complete_path = fixtures_dir / "market_data.json"
    with open(complete_path, "w") as f:
        json.dump(data, f, indent=2, default=serialize_date)
    print(f"Saved complete data to {complete_path}")

    # Save individual fixtures for easy loading
    if data.get("spy_price"):
        spy_path = fixtures_dir / "spy_price.json"
        with open(spy_path, "w") as f:
            json.dump({"price": data["spy_price"], "captured_at": data["captured_at"]}, f, indent=2)
        print(f"Saved SPY price to {spy_path}")

    if data.get("expirations"):
        exp_path = fixtures_dir / "spy_expirations.json"
        with open(exp_path, "w") as f:
            json.dump({"expirations": data["expirations"], "captured_at": data["captured_at"]}, f, indent=2)
        print(f"Saved expirations to {exp_path}")

    if data.get("option_chain"):
        chain_path = fixtures_dir / "spy_option_chain.json"
        with open(chain_path, "w") as f:
            json.dump({
                "symbol": data["symbol"],
                "expiration": data.get("target_expiration"),
                "target_dte": data["target_dte"],
                "actual_dte": data.get("actual_dte"),
                "spy_price": data.get("spy_price"),
                "chain": data["option_chain"],
                "captured_at": data["captured_at"],
            }, f, indent=2)
        print(f"Saved option chain to {chain_path}")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Capture market data for offline testing")
    parser.add_argument("--symbol", default="SPY", help="Symbol to capture (default: SPY)")
    parser.add_argument("--dte", type=int, default=90, help="Target DTE (default: 90)")
    parser.add_argument("--port", type=int, help="TWS port (default: from settings)")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent.parent / "tests" / "fixtures",
        help="Output directory for fixtures",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("IBKR Market Data Capture")
    print("=" * 60)
    print(f"Symbol: {args.symbol}")
    print(f"Target DTE: {args.dte}")
    print(f"Output: {args.output}")
    print("=" * 60)

    data = capture_market_data(
        symbol=args.symbol,
        target_dte=args.dte,
        port=args.port,
    )

    if data:
        save_fixtures(data, args.output)
        print("=" * 60)
        print("Capture complete!")
    else:
        print("=" * 60)
        print("Capture failed. Make sure TWS is running and market is open.")
        sys.exit(1)


if __name__ == "__main__":
    main()
