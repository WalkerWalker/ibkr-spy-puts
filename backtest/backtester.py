"""Theta Engine backtester.

Strategy: Sell SPY puts at 0.15 delta, ~90 DTE.
Exit: 60% profit target or 3x stop loss (2x net loss) or expiration.
Entry: 1 contract per trading day.
"""

import numpy as np
import pandas as pd

from greeks import compute_chain_deltas


# Strategy parameters
TARGET_DELTA = -0.15
TARGET_DTE = 90
DTE_TOLERANCE = 15  # accept 75-105 DTE
PROFIT_TARGET = 0.60  # close at 60% of max profit (option drops to 40% of entry credit)
STOP_LOSS_MULT = 3.0  # close when option reaches 3x entry credit
RISK_FREE_RATE = 0.05


class Position:
    def __init__(self, entry_date, expiration, strike, entry_credit, entry_spy):
        self.entry_date = entry_date
        self.expiration = expiration
        self.strike = strike
        self.entry_credit = entry_credit  # premium received (per share)
        self.entry_spy = entry_spy
        self.exit_date = None
        self.exit_price = None
        self.exit_reason = None
        self.pnl = None

    @property
    def tp_price(self):
        """Take profit: option drops to 40% of entry credit (60% profit captured)."""
        return self.entry_credit * (1 - PROFIT_TARGET)

    @property
    def sl_price(self):
        """Stop loss: option rises to 3x entry credit."""
        return self.entry_credit * STOP_LOSS_MULT

    def close(self, date, price, reason):
        self.exit_date = date
        self.exit_price = price
        self.exit_reason = reason
        # PnL = credit received - debit to close (per share, multiply by 100 for per contract)
        self.pnl = (self.entry_credit - price) * 100

    def to_dict(self):
        return {
            "entry_date": self.entry_date,
            "expiration": self.expiration,
            "strike": self.strike,
            "entry_credit": self.entry_credit,
            "entry_spy": self.entry_spy,
            "exit_date": self.exit_date,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "pnl": self.pnl,
            "tp_price": self.tp_price,
            "sl_price": self.sl_price,
        }


def find_target_expiration(date: pd.Timestamp, expirations: list[str]) -> str | None:
    """Find the expiration closest to TARGET_DTE from the given date."""
    best = None
    best_diff = float("inf")
    for exp_str in expirations:
        exp = pd.Timestamp(exp_str)
        dte = (exp - date).days
        if abs(dte - TARGET_DTE) <= DTE_TOLERANCE:
            diff = abs(dte - TARGET_DTE)
            if diff < best_diff:
                best = exp_str
                best_diff = diff
    return best


def find_target_strike(
    chain_day: pd.DataFrame, spot: float, dte_years: float,
) -> tuple[float, float] | None:
    """Find the strike closest to TARGET_DELTA and return (strike, bid_price).

    Returns None if no suitable strike found.
    """
    if chain_day.empty:
        return None

    # Filter out strikes with no liquidity
    valid = chain_day[(chain_day["bid"] > 0.05) & (chain_day["ask"] > 0)].copy()
    if valid.empty:
        return None

    # Compute deltas
    strikes = valid["strike"].values
    mid_prices = valid["mid"].values
    deltas = compute_chain_deltas(strikes, mid_prices, spot, dte_years, RISK_FREE_RATE)

    # Find strike closest to target delta
    valid_mask = ~np.isnan(deltas)
    if not valid_mask.any():
        return None

    valid_deltas = deltas[valid_mask]
    valid_strikes = strikes[valid_mask]
    valid_bids = valid["bid"].values[valid_mask]

    idx = np.argmin(np.abs(valid_deltas - TARGET_DELTA))
    selected_delta = valid_deltas[idx]

    # Only accept if delta is reasonably close
    if abs(selected_delta - TARGET_DELTA) > 0.05:
        return None

    return valid_strikes[idx], valid_bids[idx], selected_delta


def run_backtest(
    spy_prices: pd.DataFrame,
    chains: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Run the theta engine backtest.

    Args:
        spy_prices: DataFrame with columns [date, spy_close]
        chains: dict mapping expiration string -> DataFrame with
                columns [date, strike, bid, ask, mid, ...]

    Returns:
        DataFrame with one row per closed position.
    """
    expirations = sorted(chains.keys())
    open_positions: list[Position] = []
    closed_positions: list[Position] = []
    trading_days = spy_prices["date"].sort_values().unique()

    print(f"Running backtest over {len(trading_days)} trading days...")
    print(f"Parameters: delta={TARGET_DELTA}, DTE={TARGET_DTE}, "
          f"TP={PROFIT_TARGET*100:.0f}%, SL={STOP_LOSS_MULT}x")
    print()

    for day_idx, date in enumerate(trading_days):
        date = pd.Timestamp(date)
        spy_row = spy_prices[spy_prices["date"] == date]
        if spy_row.empty:
            continue
        spot = spy_row["spy_close"].iloc[0]

        # --- Check open positions for exit ---
        still_open = []
        for pos in open_positions:
            exp_ts = pd.Timestamp(pos.expiration)
            dte_days = (exp_ts - date).days

            # Check if expired
            if dte_days <= 0:
                # At expiration, option settles at intrinsic value
                intrinsic = max(pos.strike - spot, 0.0)
                pos.close(date, intrinsic, "expiration")
                closed_positions.append(pos)
                continue

            # Look up current price
            chain = chains.get(pos.expiration)
            if chain is None:
                still_open.append(pos)
                continue

            day_chain = chain[chain["date"] == date]
            if day_chain.empty:
                still_open.append(pos)
                continue

            strike_row = day_chain[day_chain["strike"] == pos.strike]
            if strike_row.empty:
                still_open.append(pos)
                continue

            current_ask = strike_row["ask"].iloc[0]
            current_mid = strike_row["mid"].iloc[0]

            # Use ask for stop loss (cost to buy back), mid for TP
            # TP check: option dropped enough (use mid as realistic exit)
            if current_mid <= pos.tp_price:
                pos.close(date, current_mid, "take_profit")
                closed_positions.append(pos)
                continue

            # SL check: option rose too much (use ask as conservative exit)
            if current_ask >= pos.sl_price:
                pos.close(date, current_ask, "stop_loss")
                closed_positions.append(pos)
                continue

            still_open.append(pos)

        open_positions = still_open

        # --- Try to open a new position ---
        # Skip weekends (shouldn't be in trading days but just in case)
        if date.weekday() >= 5:
            continue

        target_exp = find_target_expiration(date, expirations)
        if target_exp is None:
            continue

        chain = chains.get(target_exp)
        if chain is None:
            continue

        day_chain = chain[chain["date"] == date]
        if day_chain.empty:
            continue

        exp_ts = pd.Timestamp(target_exp)
        dte_years = (exp_ts - date).days / 365.25

        result = find_target_strike(day_chain, spot, dte_years)
        if result is None:
            continue

        strike, bid_price, actual_delta = result

        # Open position: sell put at bid
        pos = Position(
            entry_date=date,
            expiration=target_exp,
            strike=strike,
            entry_credit=bid_price,
            entry_spy=spot,
        )
        open_positions.append(pos)

        if day_idx % 50 == 0:
            print(f"  {date.strftime('%Y-%m-%d')}: SPY={spot:.2f}, "
                  f"opened {strike:.0f}P @ ${bid_price:.2f} "
                  f"(delta={actual_delta:.3f}, DTE={(exp_ts-date).days}), "
                  f"open={len(open_positions)}, closed={len(closed_positions)}")

    # Close any remaining open positions at last available price
    print(f"\n  Closing {len(open_positions)} positions still open at end of backtest...")
    for pos in open_positions:
        chain = chains.get(pos.expiration)
        last_date = trading_days[-1]
        last_date = pd.Timestamp(last_date)
        if chain is not None:
            day_chain = chain[chain["date"] == last_date]
            strike_row = day_chain[day_chain["strike"] == pos.strike]
            if not strike_row.empty:
                pos.close(last_date, strike_row["mid"].iloc[0], "end_of_backtest")
            else:
                pos.close(last_date, pos.entry_credit, "end_of_backtest_no_data")
        else:
            pos.close(last_date, pos.entry_credit, "end_of_backtest_no_data")
        closed_positions.append(pos)

    # Build results DataFrame
    results = pd.DataFrame([p.to_dict() for p in closed_positions])
    return results


def print_summary(results: pd.DataFrame):
    """Print backtest summary statistics."""
    if results.empty:
        print("No trades!")
        return

    total_trades = len(results)
    winners = results[results["pnl"] > 0]
    losers = results[results["pnl"] <= 0]
    win_rate = len(winners) / total_trades * 100

    total_pnl = results["pnl"].sum()
    avg_pnl = results["pnl"].mean()
    avg_win = winners["pnl"].mean() if len(winners) > 0 else 0
    avg_loss = losers["pnl"].mean() if len(losers) > 0 else 0

    total_credit = results["entry_credit"].sum() * 100
    pcr = total_pnl / total_credit * 100 if total_credit > 0 else 0

    # DIT (days in trade)
    results["dit"] = (pd.to_datetime(results["exit_date"]) -
                      pd.to_datetime(results["entry_date"])).dt.days
    avg_dit = results["dit"].mean()

    # Exit reasons
    exit_counts = results["exit_reason"].value_counts()

    # Max drawdown (cumulative P/L)
    cum_pnl = results.sort_values("exit_date")["pnl"].cumsum()
    rolling_max = cum_pnl.cummax()
    drawdown = cum_pnl - rolling_max
    max_dd = drawdown.min()

    print("=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Period:        {results['entry_date'].min()} to {results['exit_date'].max()}")
    print(f"Total trades:  {total_trades}")
    print(f"Win rate:      {win_rate:.1f}%")
    print()
    print(f"Total P/L:     ${total_pnl:,.2f}")
    print(f"Avg P/L:       ${avg_pnl:,.2f}")
    print(f"Avg winner:    ${avg_win:,.2f}")
    print(f"Avg loser:     ${avg_loss:,.2f}")
    print()
    print(f"Total credit:  ${total_credit:,.2f}")
    print(f"PCR:           {pcr:.1f}%")
    print(f"Avg DIT:       {avg_dit:.1f} days")
    print(f"Max drawdown:  ${max_dd:,.2f}")
    print()
    print("Exit reasons:")
    for reason, count in exit_counts.items():
        print(f"  {reason}: {count} ({count/total_trades*100:.1f}%)")
    print("=" * 60)
