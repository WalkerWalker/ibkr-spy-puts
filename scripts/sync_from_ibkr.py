#!/usr/bin/env python3
"""Sync positions from IBKR to the database.

This script connects to IBKR, fetches all SPY put positions, and syncs them
with the database. Uses TRADING_MODE to determine which database to sync:
- TRADING_MODE=paper -> ibkr_puts_paper
- TRADING_MODE=live -> ibkr_puts

Usage:
    # Sync paper positions
    TRADING_MODE=paper poetry run python scripts/sync_from_ibkr.py

    # Sync live positions
    TRADING_MODE=live poetry run python scripts/sync_from_ibkr.py
"""

import asyncio
import logging
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

# Load environment variables
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from ibkr_spy_puts.config import DatabaseSettings, TWSSettings, TradingModeSettings
from ibkr_spy_puts.database import Database, Position

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def fetch_ibkr_positions(tws_settings: TWSSettings) -> list[dict]:
    """Fetch all SPY put positions from IBKR.

    Returns:
        List of position dicts with contract details.
    """
    from ib_insync import IB

    # Create event loop for ib_insync
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    ib = IB()
    positions = []

    try:
        logger.info(f"Connecting to TWS at {tws_settings.host}:{tws_settings.port}...")
        ib.connect(
            host=tws_settings.host,
            port=tws_settings.port,
            clientId=99,
            readonly=True,
            timeout=15,
        )
        logger.info("Connected to TWS")

        # Get account info
        accounts = ib.managedAccounts()
        if accounts:
            trading_mode = "PAPER" if accounts[0].startswith("DU") else "LIVE"
            logger.info(f"Account: {accounts[0]} ({trading_mode})")

        # Get all positions
        for pos in ib.positions():
            c = pos.contract
            if c.symbol == "SPY" and c.secType == "OPT" and getattr(c, "right", "") == "P":
                if pos.position < 0:  # Short position
                    positions.append({
                        "symbol": c.symbol,
                        "strike": c.strike,
                        "expiration": datetime.strptime(
                            c.lastTradeDateOrContractMonth, "%Y%m%d"
                        ).date(),
                        "quantity": abs(int(pos.position)),
                        "avg_cost": pos.avgCost / 100,  # avgCost is per share, not per contract
                    })

        ib.disconnect()
        logger.info(f"Found {len(positions)} SPY put position(s) in IBKR")

    except Exception as e:
        logger.error(f"Failed to fetch positions from IBKR: {e}")
        if ib.isConnected():
            ib.disconnect()
        raise

    return positions


def sync_positions(db: Database, ibkr_positions: list[dict]) -> dict:
    """Sync IBKR positions to database.

    Args:
        db: Database connection.
        ibkr_positions: List of positions from IBKR.

    Returns:
        Dict with sync stats (added, updated, closed).
    """
    stats = {"added": 0, "matched": 0, "closed": 0}

    # Get all open positions from database
    db_positions = db.get_open_positions()
    db_position_map = {}
    for pos in db_positions:
        key = (pos.symbol, float(pos.strike), pos.expiration)
        db_position_map[key] = pos

    # Track which DB positions are still in IBKR
    ibkr_keys = set()

    # Process IBKR positions
    for ibkr_pos in ibkr_positions:
        key = (ibkr_pos["symbol"], ibkr_pos["strike"], ibkr_pos["expiration"])
        ibkr_keys.add(key)

        if key in db_position_map:
            # Position exists in both
            stats["matched"] += 1
            db_pos = db_position_map[key]
            logger.info(
                f"  Match: {ibkr_pos['symbol']} {ibkr_pos['strike']}P "
                f"{ibkr_pos['expiration']} x{ibkr_pos['quantity']}"
            )
        else:
            # Position in IBKR but not in database - add it
            logger.info(
                f"  Adding: {ibkr_pos['symbol']} {ibkr_pos['strike']}P "
                f"{ibkr_pos['expiration']} x{ibkr_pos['quantity']}"
            )

            # Calculate expected TP/SL prices (60% profit, 200% loss)
            entry_price = ibkr_pos["avg_cost"]
            tp_price = round(entry_price * 0.4, 2)  # 60% profit
            sl_price = round(entry_price * 3.0, 2)  # 200% loss

            position = Position(
                symbol=ibkr_pos["symbol"],
                strike=Decimal(str(ibkr_pos["strike"])),
                expiration=ibkr_pos["expiration"],
                quantity=ibkr_pos["quantity"],
                entry_price=Decimal(str(entry_price)),
                entry_time=datetime.now(timezone.utc),  # Unknown actual entry time
                expected_tp_price=Decimal(str(tp_price)),
                expected_sl_price=Decimal(str(sl_price)),
                status="OPEN",
                strategy_id="spy-put-selling",
            )
            position_id = db.insert_position(position)
            stats["added"] += 1
            logger.info(f"    -> Created position ID={position_id}")

    # Check for positions in DB but not in IBKR (closed externally)
    for key, db_pos in db_position_map.items():
        if key not in ibkr_keys:
            logger.warning(
                f"  Position in DB not in IBKR (may be closed): "
                f"{db_pos.symbol} {db_pos.strike}P {db_pos.expiration}"
            )
            stats["closed"] += 1
            # Note: Not auto-closing here to avoid accidental data loss
            # User should manually close or investigate

    return stats


def main():
    """Main entry point."""
    # Get settings
    trading_mode = TradingModeSettings()
    db_settings = DatabaseSettings()
    tws_settings = TWSSettings()

    logger.info("=" * 60)
    logger.info("IBKR Position Sync")
    logger.info("=" * 60)
    logger.info(f"Trading Mode: {trading_mode.mode.upper()}")
    logger.info(f"Database: {db_settings.effective_name}")
    logger.info(f"TWS: {tws_settings.host}:{tws_settings.port}")
    logger.info("=" * 60)

    # Connect to database
    db = Database(settings=db_settings)
    if not db.connect():
        logger.error("Failed to connect to database")
        sys.exit(1)
    logger.info(f"Connected to database: {db_settings.effective_name}")

    try:
        # Fetch positions from IBKR
        ibkr_positions = fetch_ibkr_positions(tws_settings)

        # Sync to database
        logger.info("Syncing positions...")
        stats = sync_positions(db, ibkr_positions)

        # Summary
        logger.info("=" * 60)
        logger.info("Sync Complete")
        logger.info(f"  Matched: {stats['matched']}")
        logger.info(f"  Added: {stats['added']}")
        logger.info(f"  Missing from IBKR: {stats['closed']}")
        logger.info("=" * 60)

    finally:
        db.disconnect()


if __name__ == "__main__":
    main()
