# Strategy Code Guide

## Requirements

All strategy code must be a valid `backtesting.py` Strategy subclass that runs within an exec() namespace containing: `Strategy`, `pd` (pandas), `np` (numpy), `ta` (pandas_ta), `math`.

### Class Structure

```python
import pandas_ta as ta
import numpy as np

class MyStrategy(Strategy):
    # Tunable parameters as class-level variables
    lookback = 14
    threshold = 30

    def init(self):
        # Wrap ALL computations in self.I()
        close = pd.Series(self.data.Close)
        self.rsi = self.I(ta.rsi, close, length=self.lookback)

    def next(self):
        if self.rsi[-1] < self.threshold and not self.position:
            self.buy()
        elif self.rsi[-1] > (100 - self.threshold) and self.position:
            self.position.close()
```

### Key Rules

1. **self.I() is mandatory** for any computation used in `next()`. This handles alignment and prevents lookahead.
2. **Convert to pd.Series** when calling pandas-ta: `pd.Series(self.data.Close)`
3. **Access latest value with [-1]**: `self.rsi[-1]`, `self.data.Close[-1]`
4. **No future data**: Only reference `[-1]` (current bar) or earlier indices
5. **Parameters as class variables**: Enables sweeps via `optimize_strategy`

### Available Data

```python
self.data.Open    # Open prices
self.data.High    # High prices
self.data.Low     # Low prices
self.data.Close   # Close prices
self.data.Volume  # Volume
```

### Entry and Exit

```python
self.buy()              # Market order: buy at next bar's open (or current close if trade_on_close=True)
self.sell()             # Short position
self.position.close()   # Close current position
self.position           # Truthy if in a position
self.position.is_long   # True if long
self.position.is_short  # True if short
self.position.pl_pct    # Current P&L percentage
self.position.size      # Position size (number of units)
```

### Order Types

backtesting.py supports four order types:

```python
# Market order (default) — executes immediately
self.buy()

# Limit order — executes only if price drops to limit price
self.buy(limit=9500)    # Buy if price reaches 9500 or below

# Stop order — executes only if price rises to stop price (breakout entry)
self.buy(stop=10500)    # Buy if price reaches 10500 or above

# Combined — limit/stop with SL/TP attached
self.buy(limit=9500, sl=9200, tp=10200)
```

Pending orders (limit/stop) expire if not filled before the next signal.

### Stop-Loss and Take-Profit

```python
# Absolute price levels
self.buy(sl=stop_price, tp=target_price)

# ATR-based stops (using helpers module)
atr_val = self.atr[-1]
entry = self.data.Close[-1]
self.buy(sl=entry - 2 * atr_val, tp=entry + 3 * atr_val)

# Trailing stop — manage manually in next()
if self.position.is_long:
    new_stop = self.data.Close[-1] - 2 * self.atr[-1]
    if new_stop > self._trailing_stop:
        self._trailing_stop = new_stop
    if self.data.Close[-1] < self._trailing_stop:
        self.position.close()
```

### Position Sizing

By default, `self.buy()` uses 100% of available equity. Control with `size`:

```python
self.buy(size=0.5)      # Use 50% of equity
self.buy(size=0.25)     # Use 25% of equity

# Risk-based sizing: risk 2% of equity per trade
stop_distance = self.data.Close[-1] - stop_price
size_frac = helpers.position_size_pct(self.equity, stop_distance, risk_pct=0.02)
self.buy(size=size_frac, sl=stop_price)
```

The `size` parameter is a fraction of equity (0.0 to 1.0) when < 1, or number of units when >= 1.

### Custom Indicators from Raw Data

Not everything needs pandas-ta. Build from OHLCV directly:

```python
class RangeBreakout(Strategy):
    lookback = 20
    z_threshold = 2.0

    def init(self):
        high = pd.Series(self.data.High)
        low = pd.Series(self.data.Low)
        close = pd.Series(self.data.Close)

        # Range as fraction of price
        price_range = (high - low) / close

        # Rolling z-score of range
        mean = price_range.rolling(self.lookback).mean()
        std = price_range.rolling(self.lookback).std()
        self.range_z = self.I(lambda: ((price_range - mean) / std).values)

        # Direction bias: close position in candle
        self.body_ratio = self.I(lambda: ((close - low) / (high - low)).values)

    def next(self):
        # Range compression followed by directional candle
        if self.range_z[-1] < -1.5 and self.body_ratio[-1] > 0.7:
            if not self.position:
                self.buy()
        elif self.position and self.range_z[-1] > self.z_threshold:
            self.position.close()
```

### Volume-Based Example

```python
class VolumeDryUp(Strategy):
    vol_lookback = 20
    vol_threshold = 0.5
    exit_bars = 10

    def init(self):
        vol = pd.Series(self.data.Volume)
        close = pd.Series(self.data.Close)

        vol_ma = vol.rolling(self.vol_lookback).mean()
        self.rel_volume = self.I(lambda: (vol / vol_ma).values)

        # Trend direction
        self.sma = self.I(lambda: close.rolling(50).mean().values)
        self._bar_count = 0

    def next(self):
        if self.rel_volume[-1] < self.vol_threshold:
            if self.data.Close[-1] > self.sma[-1] and not self.position:
                self.buy()
                self._bar_count = 0

        if self.position:
            self._bar_count += 1
            if self._bar_count >= self.exit_bars:
                self.position.close()
```

### The `helpers` Module

A pre-loaded module of reusable functions available as `helpers` in the exec namespace. These return numpy arrays and work with `self.I()`. **Use these instead of reimplementing common patterns.**

```python
# Volatility & Range
helpers.atr(high, low, close, period=14)         # Average True Range
helpers.volatility(close, period=20)             # Annualized rolling vol from log returns

# Risk Management
helpers.atr_stop(close, atr_vals, multiplier=2)  # ATR-based stop price (long)
helpers.atr_target(close, atr_vals, multiplier=3) # ATR-based take-profit (long)

# Position Sizing (multiple methods)
helpers.position_size_pct(equity, stop_dist, risk_pct=0.02)  # Fixed-risk: risk 2% per trade
helpers.kelly_size(win_rate, avg_win, avg_loss, fraction=0.5) # Kelly criterion (half-Kelly default)
helpers.volatility_scaled_size(current_vol, target_vol=0.15)  # Vol-target: scale size to constant risk
helpers.fixed_fractional_size(equity, price, fraction=0.1)    # Fixed fraction of equity
helpers.max_drawdown_size(current_dd, max_dd=-25.0)           # Reduce size as DD deepens

# Statistics
helpers.rolling_zscore(series, period=20)         # Rolling z-score
helpers.returns(close)                            # Simple returns
helpers.log_returns(close)                        # Log returns

# Trend & Regime
helpers.regime_filter(close, fast=20, slow=50)   # +1 uptrend, -1 downtrend, 0 unclear
helpers.ema(series, period=20)                    # Exponential moving average

# Price Structure
helpers.body_ratio(open, high, low, close)       # Candle body / range (0=doji, 1=full)
helpers.donchian_high(high, period=20)            # Rolling max of highs
helpers.donchian_low(low, period=20)              # Rolling min of lows

# Volume
helpers.relative_volume(volume, period=20)        # Volume / rolling avg (>1 = above avg)

# Pattern
helpers.consecutive_count(bool_array)             # Count consecutive True values
```

Example using helpers for risk-managed entries:

```python
class ATRBreakout(Strategy):
    atr_period = 14
    atr_sl_mult = 2.0
    atr_tp_mult = 3.0
    risk_per_trade = 0.02

    def init(self):
        h = pd.Series(self.data.High)
        l = pd.Series(self.data.Low)
        c = pd.Series(self.data.Close)
        self.atr = self.I(helpers.atr, h, l, c, self.atr_period)
        self.dc_high = self.I(helpers.donchian_high, h, period=20)
        self.regime = self.I(helpers.regime_filter, c)

    def next(self):
        if self.regime[-1] > 0 and self.data.Close[-1] > self.dc_high[-2]:
            if not self.position:
                entry = self.data.Close[-1]
                sl = entry - self.atr_sl_mult * self.atr[-1]
                tp = entry + self.atr_tp_mult * self.atr[-1]
                size = helpers.position_size_pct(
                    self.equity, entry - sl, self.risk_per_trade
                )
                self.buy(size=size, sl=sl, tp=tp)
```

### Market-Specific Strategy Patterns

#### Spot Trading (long-only)
```python
class SpotMeanReversion(Strategy):
    lookback = 20
    z_entry = -2.0
    risk_per_trade = 0.02

    def init(self):
        c = pd.Series(self.data.Close)
        self.zscore = self.I(helpers.rolling_zscore, c, self.lookback)
        h, l = pd.Series(self.data.High), pd.Series(self.data.Low)
        self.atr = self.I(helpers.atr, h, l, c)

    def next(self):
        if self.zscore[-1] < self.z_entry and not self.position:
            sl = self.data.Close[-1] - 2 * self.atr[-1]
            size = helpers.position_size_pct(self.equity, self.data.Close[-1] - sl, self.risk_per_trade)
            self.buy(size=size, sl=sl)
        elif self.zscore[-1] > 0 and self.position:
            self.position.close()
```

#### Perpetual Futures (long + short, with leverage awareness)
```python
class PerpBreakout(Strategy):
    channel_period = 20
    atr_period = 14
    leverage = 3  # Simulated via position size
    risk_per_trade = 0.01  # Lower risk per trade when leveraged

    def init(self):
        h = pd.Series(self.data.High)
        l = pd.Series(self.data.Low)
        c = pd.Series(self.data.Close)
        self.dc_high = self.I(helpers.donchian_high, h, self.channel_period)
        self.dc_low = self.I(helpers.donchian_low, l, self.channel_period)
        self.atr = self.I(helpers.atr, h, l, c, self.atr_period)
        self.regime = self.I(helpers.regime_filter, c)

    def next(self):
        if self.position:
            return

        entry = self.data.Close[-1]
        atr_val = self.atr[-1]

        # Long breakout
        if entry > self.dc_high[-2] and self.regime[-1] > 0:
            sl = entry - 2 * atr_val
            # Leverage amplifies size but also narrows liquidation
            liq_price = entry * (1 - 1/self.leverage)  # Approximate liquidation
            sl = max(sl, liq_price * 1.01)  # Stop before liquidation
            size = helpers.position_size_pct(self.equity, entry - sl, self.risk_per_trade)
            self.buy(size=min(size * self.leverage, 0.95), sl=sl)

        # Short breakout
        elif entry < self.dc_low[-2] and self.regime[-1] < 0:
            sl = entry + 2 * atr_val
            size = helpers.position_size_pct(self.equity, sl - entry, self.risk_per_trade)
            self.sell(size=min(size * self.leverage, 0.95), sl=sl)
```

#### Collateral Currency Considerations
- **USDT-margined (linear)**: P&L in USDT. Standard behavior — `self.equity` is in USDT terms.
- **Coin-margined (inverse)**: P&L in the base asset (BTC/ETH). When BTC drops, your collateral drops AND your position loses — double exposure. Model this by being more conservative on position sizes.
- **Cross-collateral**: Some exchanges accept BTC/ETH/SOL as collateral for USDT-margined contracts. The collateral value fluctuates — model with wider stops.

#### Funding Rate Modeling
For perpetual contracts on longer timeframes, funding rates are a cost of holding:
```python
class FundingAwareStrategy(Strategy):
    # Approximate 8-hourly funding cost as drag on returns
    est_funding_per_8h = 0.0001  # 0.01% typical
    max_hold_bars = 30  # Max bars to hold (limits funding drag)

    def init(self):
        # ... your indicators
        self._hold_bars = 0

    def next(self):
        if self.position:
            self._hold_bars += 1
            # Exit if holding too long (funding drag)
            if self._hold_bars >= self.max_hold_bars:
                self.position.close()
                self._hold_bars = 0
        # ... entry logic
```

### Creating Custom Helpers

When you find yourself writing the same utility across multiple strategies, call `add_helper` to save it:

```
add_helper(
    function_name="vwap_deviation",
    function_code="def vwap_deviation(close, volume, period=20):\n    ...",
    description="Rolling VWAP deviation as z-score"
)
```

The function becomes available as `helpers.vwap_deviation()` in all future strategies. Call `list_helpers` to see what's already available before writing custom code.

### Common Pitfalls

1. **Forgetting self.I()**: Raw pandas operations on self.data cause index misalignment
2. **Using .iloc in next()**: Use negative indexing `[-1]`, `[-2]` instead
3. **Lambda with self.I()**: When using lambda, return `.values` to avoid index issues
4. **Lookahead bias**: Don't use future bars. `self.data.Close[-1]` is the current bar only
5. **Too many conditions**: Keep it focused. Test one idea per strategy.
6. **Underestimating trading costs**: Commission is charged PER LEG, not per round-trip. A buy + sell costs 2x the commission_pct. With default commission (0.1%) + slippage (0.05%), each leg costs 0.15%, so a full round-trip costs **0.30%**. High-frequency strategies with many trades can lose 10-30% of returns to costs alone. Always check: `total_trades * 0.003 * avg_position_size` gives approximate total cost drag.
7. **Ignoring OOS**: Never trust in-sample metrics alone. If OOS Sharpe drops >50% from IS, the strategy is likely overfit. The backtest tool randomizes the train/test split each run to prevent implicit fitting to a fixed test window.
