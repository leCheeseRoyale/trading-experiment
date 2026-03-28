"""Reusable helper functions available in the strategy exec namespace as `helpers`.

These save the agent from reinventing common patterns every iteration.
All functions accept numpy arrays or pandas Series and return numpy arrays,
making them compatible with self.I() wrapping.
"""
import numpy as np
import pandas as pd


def atr(high, low, close, period=14):
    """Average True Range. Returns numpy array."""
    high, low, close = pd.Series(high), pd.Series(low), pd.Series(close)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().values


def atr_stop(close, atr_values, multiplier=2.0):
    """ATR-based stop-loss price (long). Returns numpy array of stop prices."""
    return np.asarray(close) - np.asarray(atr_values) * multiplier


def atr_target(close, atr_values, multiplier=3.0):
    """ATR-based take-profit price (long). Returns numpy array of target prices."""
    return np.asarray(close) + np.asarray(atr_values) * multiplier


def rolling_zscore(series, period=20):
    """Rolling z-score of a series. Returns numpy array."""
    s = pd.Series(series)
    mean = s.rolling(period).mean()
    std = s.rolling(period).std()
    return ((s - mean) / std.replace(0, np.nan)).values


def returns(close):
    """Simple returns. Returns numpy array."""
    c = pd.Series(close)
    return c.pct_change().values


def log_returns(close):
    """Log returns. Returns numpy array."""
    c = pd.Series(close)
    return np.log(c / c.shift(1)).values


def volatility(close, period=20):
    """Rolling annualized volatility from log returns. Returns numpy array."""
    lr = pd.Series(log_returns(close))
    return (lr.rolling(period).std() * np.sqrt(252)).values


def regime_filter(close, fast_period=20, slow_period=50):
    """Returns +1 (uptrend), -1 (downtrend), 0 (unclear). Numpy array."""
    c = pd.Series(close)
    fast_ma = c.rolling(fast_period).mean()
    slow_ma = c.rolling(slow_period).mean()
    regime = np.where(fast_ma > slow_ma, 1, np.where(fast_ma < slow_ma, -1, 0))
    return regime


def body_ratio(open_prices, high, low, close):
    """Candle body as fraction of full range. 1.0 = full body, 0.0 = doji."""
    h, l = np.asarray(high), np.asarray(low)
    full_range = h - l
    body = np.abs(np.asarray(close) - np.asarray(open_prices))
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(full_range > 0, body / full_range, 0.0)
    return ratio


def relative_volume(volume, period=20):
    """Volume relative to its rolling average. >1 = above average."""
    v = pd.Series(volume)
    return (v / v.rolling(period).mean()).values


def consecutive_count(condition_array):
    """Count consecutive True values. Resets on False. Returns numpy array of counts."""
    arr = np.asarray(condition_array, dtype=bool)
    result = np.zeros(len(arr), dtype=int)
    count = 0
    for i in range(len(arr)):
        if arr[i]:
            count += 1
        else:
            count = 0
        result[i] = count
    return result


def position_size_pct(equity, stop_distance, risk_pct=0.02):
    """Fixed-risk position sizing. Risk a fixed % of equity per trade.

    Args:
        equity: Current equity value
        stop_distance: Distance from entry to stop in price units
        risk_pct: Fraction of equity to risk (default 2%)

    Returns:
        Position size as fraction (0.0 to 1.0)
    """
    if stop_distance <= 0:
        return 0.1
    risk_amount = equity * risk_pct
    return min(1.0, risk_amount / stop_distance)


def kelly_size(win_rate, avg_win, avg_loss, fraction=0.5):
    """Kelly criterion position sizing (half-Kelly by default for safety).

    Args:
        win_rate: Win rate as decimal (0.55 = 55%)
        avg_win: Average winning trade return (e.g. 0.03 = 3%)
        avg_loss: Average losing trade return as positive number (e.g. 0.02 = 2%)
        fraction: Kelly fraction (0.5 = half-Kelly, safer)

    Returns:
        Position size as fraction (0.0 to 1.0), clamped.
    """
    if avg_loss <= 0 or avg_win <= 0:
        return 0.1
    b = avg_win / avg_loss
    q = 1 - win_rate
    kelly = (win_rate * b - q) / b
    return max(0.0, min(1.0, kelly * fraction))


def volatility_scaled_size(current_vol, target_vol=0.15, base_size=1.0):
    """Scale position size inversely with volatility to target constant risk.

    Args:
        current_vol: Current annualized volatility (e.g. 0.60 = 60%)
        target_vol: Target portfolio volatility (default 15%)
        base_size: Base position size at target vol (default 1.0 = 100%)

    Returns:
        Position size as fraction (0.0 to 1.0)
    """
    if current_vol <= 0:
        return base_size
    return min(1.0, max(0.05, base_size * target_vol / current_vol))


def fixed_fractional_size(equity, price, fraction=0.1):
    """Fixed fractional sizing. Allocate a fixed % of equity per trade.

    Args:
        equity: Current equity
        price: Entry price per unit
        fraction: Fraction of equity to allocate (default 10%)

    Returns:
        Position size as fraction (0.0 to 1.0)
    """
    if price <= 0:
        return fraction
    return min(1.0, fraction)


def max_drawdown_size(current_dd_pct, max_allowed_dd=-25.0, base_size=1.0):
    """Reduce position size as drawdown deepens. At max DD, size goes to 0.

    Args:
        current_dd_pct: Current drawdown as negative percentage (e.g. -10.0)
        max_allowed_dd: Maximum allowed drawdown (e.g. -25.0)
        base_size: Full position size when no drawdown

    Returns:
        Position size as fraction (0.0 to base_size)
    """
    if max_allowed_dd >= 0:
        return base_size
    ratio = 1.0 - (current_dd_pct / max_allowed_dd)
    return max(0.0, min(base_size, ratio * base_size))


def donchian_high(high, period=20):
    """Donchian channel upper band (rolling max of highs)."""
    return pd.Series(high).rolling(period).max().values


def donchian_low(low, period=20):
    """Donchian channel lower band (rolling min of lows)."""
    return pd.Series(low).rolling(period).min().values


def ema(series, period=20):
    """Exponential moving average. Returns numpy array."""
    return pd.Series(series).ewm(span=period, adjust=False).mean().values
