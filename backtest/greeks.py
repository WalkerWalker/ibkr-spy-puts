"""Black-Scholes option pricing and Greeks computation."""

import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes put option price."""
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_put_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes put delta (negative value between -1 and 0)."""
    if T <= 0 or sigma <= 0:
        return -1.0 if S < K else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return norm.cdf(d1) - 1.0


def implied_volatility(
    market_price: float, S: float, K: float, T: float, r: float,
    lower: float = 0.01, upper: float = 5.0,
) -> float | None:
    """Solve for implied volatility using Brent's method.

    Returns None if no solution found (e.g., price below intrinsic).
    """
    if T <= 0 or market_price <= 0:
        return None
    intrinsic = max(K * np.exp(-r * T) - S, 0.0)
    if market_price < intrinsic - 0.01:
        return None
    try:
        iv = brentq(
            lambda sigma: bs_put_price(S, K, T, r, sigma) - market_price,
            lower, upper, xtol=1e-6, maxiter=100,
        )
        return iv
    except (ValueError, RuntimeError):
        return None


def compute_chain_deltas(
    strikes: np.ndarray,
    mid_prices: np.ndarray,
    spot: float,
    dte_years: float,
    r: float = 0.05,
) -> np.ndarray:
    """Compute put deltas for an array of strikes.

    First solves for IV per strike, then computes delta.
    Returns array of deltas (negative values).
    """
    deltas = np.full(len(strikes), np.nan)
    for i, (K, price) in enumerate(zip(strikes, mid_prices)):
        if price <= 0 or np.isnan(price):
            continue
        iv = implied_volatility(price, spot, K, dte_years, r)
        if iv is not None:
            deltas[i] = bs_put_delta(spot, K, dte_years, r, iv)
    return deltas
