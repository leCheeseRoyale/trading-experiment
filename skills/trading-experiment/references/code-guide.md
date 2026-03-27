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
self.buy()              # Open long position
self.sell()             # Open short position (if allowed)
self.position.close()   # Close current position
self.position           # Truthy if in a position
self.position.is_long   # True if long
self.position.is_short  # True if short
self.position.pl_pct    # Current P&L percentage
```

### Stop-Loss and Take-Profit

```python
self.buy(sl=stop_price, tp=target_price)   # With absolute price levels
```

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

### Common Pitfalls

1. **Forgetting self.I()**: Raw pandas operations on self.data cause index misalignment
2. **Using .iloc in next()**: Use negative indexing `[-1]`, `[-2]` instead
3. **Lambda with self.I()**: When using lambda, return `.values` to avoid index issues
4. **Lookahead bias**: Don't use future bars. `self.data.Close[-1]` is the current bar only
5. **Too many conditions**: Keep it focused. Test one idea per strategy.
