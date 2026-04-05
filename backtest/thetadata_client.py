"""ThetaData v3 REST API client for downloading historical option data."""

import io
import time
import json
from pathlib import Path

import httpx
import pandas as pd

BASE_URL = "http://localhost:25503/v3"
DATA_DIR = Path(__file__).parent / "data"
RATE_LIMIT_DELAY = 3.5  # seconds between requests (20 req/min = 3s, add buffer)


def _get(endpoint: str, params: dict, timeout: int = 60) -> str:
    """Make a GET request to the ThetaData terminal."""
    params.setdefault("format", "csv")
    url = f"{BASE_URL}/{endpoint}"
    resp = httpx.get(url, params=params, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"ThetaData API error ({resp.status_code}): {resp.text[:200]}")
    return resp.text


def get_expirations(symbol: str = "SPY") -> list[str]:
    """Get all available expiration dates for a symbol."""
    cache_file = DATA_DIR / f"{symbol}_expirations.csv"
    if cache_file.exists():
        df = pd.read_csv(cache_file)
        return df["expiration"].tolist()

    text = _get("option/list/expirations", {"symbol": symbol})
    df = pd.read_csv(io.StringIO(text))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_file, index=False)
    return df["expiration"].tolist()


def get_spy_prices(start_date: str, end_date: str) -> pd.DataFrame:
    """Get SPY underlying EOD prices for a date range."""
    cache_file = DATA_DIR / f"SPY_stock_{start_date}_{end_date}.csv"
    if cache_file.exists():
        return pd.read_csv(cache_file, parse_dates=["date"])

    text = _get("stock/history/eod", {
        "symbol": "SPY",
        "start_date": start_date.replace("-", ""),
        "end_date": end_date.replace("-", ""),
    })
    df = pd.read_csv(io.StringIO(text))
    # Extract date from the created timestamp
    df["date"] = pd.to_datetime(df["created"], format="ISO8601").dt.date
    df["date"] = pd.to_datetime(df["date"])
    df = df.rename(columns={"close": "spy_close", "open": "spy_open",
                             "high": "spy_high", "low": "spy_low"})
    df = df[["date", "spy_open", "spy_high", "spy_low", "spy_close"]].copy()
    df = df.sort_values("date").reset_index(drop=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_file, index=False)
    return df


def get_option_chain_eod(
    symbol: str,
    expiration: str,
    start_date: str,
    end_date: str,
    right: str = "put",
) -> pd.DataFrame:
    """Get EOD option chain data for a specific expiration across a date range.

    Returns DataFrame with columns: date, strike, bid, ask, close, volume, etc.
    One request covers the entire date range for one expiration.
    """
    cache_file = DATA_DIR / f"{symbol}_{right}_{expiration}_{start_date}_{end_date}.csv"
    if cache_file.exists():
        return pd.read_csv(cache_file, parse_dates=["date"])

    text = _get("option/history/eod", {
        "symbol": symbol,
        "expiration": expiration.replace("-", ""),
        "right": right,
        "start_date": start_date.replace("-", ""),
        "end_date": end_date.replace("-", ""),
    })

    if "No data found" in text or not text.strip():
        return pd.DataFrame()

    df = pd.read_csv(io.StringIO(text))
    if df.empty:
        return df

    # Parse dates (mixed formats: some have .microseconds, some don't)
    df["date"] = pd.to_datetime(df["created"], format="ISO8601").dt.date
    df["date"] = pd.to_datetime(df["date"])
    df["mid"] = (df["bid"] + df["ask"]) / 2

    # Keep useful columns
    cols = ["date", "strike", "bid", "ask", "mid", "close", "open", "high", "low", "volume"]
    df = df[[c for c in cols if c in df.columns]].copy()
    df = df.sort_values(["date", "strike"]).reset_index(drop=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_file, index=False)
    time.sleep(RATE_LIMIT_DELAY)
    return df


def download_backtest_data(
    start_date: str,
    end_date: str,
    target_dte_range: tuple[int, int] = (60, 120),
    symbol: str = "SPY",
) -> dict:
    """Download all data needed for backtesting.

    Downloads SPY prices and option chains for expirations that fall within
    the target DTE range during the backtest period.

    Returns dict with 'spy_prices' DataFrame and 'chains' dict of DataFrames
    keyed by expiration date string.
    """
    print(f"Downloading data for {start_date} to {end_date}...")

    # 1. SPY underlying prices
    print("  Fetching SPY prices...")
    spy_prices = get_spy_prices(start_date, end_date)
    print(f"  Got {len(spy_prices)} trading days")

    # 2. Get relevant expirations
    print("  Fetching expirations...")
    all_expirations = get_expirations(symbol)

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    min_dte, max_dte = target_dte_range

    # Filter to monthly expirations only (3rd Friday of each month)
    # This reduces downloads from 166+ (daily expirations) to ~15
    monthly_exps = []
    for exp_str in all_expirations:
        exp = pd.Timestamp(exp_str)
        # 3rd Friday: weekday==4 (Friday) and day between 15-21
        if exp.weekday() == 4 and 15 <= exp.day <= 21:
            # Check if this expiration is relevant to our date range
            earliest_entry = exp - pd.Timedelta(days=max_dte)
            latest_entry = exp - pd.Timedelta(days=min_dte)
            if latest_entry >= start and earliest_entry <= end:
                monthly_exps.append(exp_str)

    relevant_exps = sorted(monthly_exps)
    print(f"  Found {len(relevant_exps)} monthly expirations")

    # 3. Download option chains
    chains = {}
    for i, exp in enumerate(relevant_exps):
        # Determine the date range to download for this expiration
        exp_ts = pd.Timestamp(exp)
        dl_start = max(start, exp_ts - pd.Timedelta(days=max_dte))
        dl_end = min(end, exp_ts)

        print(f"  [{i+1}/{len(relevant_exps)}] Downloading {exp} "
              f"({dl_start.strftime('%Y-%m-%d')} to {dl_end.strftime('%Y-%m-%d')})...")
        df = get_option_chain_eod(
            symbol, exp,
            dl_start.strftime("%Y-%m-%d"),
            dl_end.strftime("%Y-%m-%d"),
        )
        if not df.empty:
            chains[exp] = df
            print(f"    Got {len(df)} rows, {df['date'].nunique()} days, "
                  f"{df['strike'].nunique()} strikes")
        else:
            print(f"    No data")

    print(f"Done! {len(chains)} expirations with data.")
    return {"spy_prices": spy_prices, "chains": chains}
