# Trading Experiment

A Claude Code plugin that turns Claude into an autonomous algorithmic trading strategy factory. Give it a market and a direction, and it will continuously generate, mutate, and evolve trading strategies — writing new `backtesting.py` Strategy classes, running them against real market data, and spawning variations of whatever works. It doesn't stop at one good idea. It keeps creating.

## What It Does

You point it at a market:

> "4h timeframe, BTC perp on binance, last 6 months. Start with mean reversion after volume spikes."

Claude then enters an autonomous creation loop:

1. **Generates** a new algorithmic trading strategy as a `backtesting.py` Strategy class — complete with entry logic, exit logic, and risk management
2. **Backtests** it with automatic 70/30 in-sample / out-of-sample validation
3. **Evaluates** the results — Sharpe ratio, drawdown, overfitting detection, trade count
4. **Mutates** winning strategies by rewriting their core logic — replacing the entry mechanism entirely, adding a different market regime filter, switching from momentum to mean-reversion, combining ideas from multiple past strategies. This is not parameter tuning. Each mutation produces a fundamentally new strategy with different trading logic.
5. **Pivots** when an approach is exhausted — abandoning the entire thesis and generating a completely new strategy from a different market idea
6. **Repeats** — constantly writing new strategy code, building on what the market data says works, discarding what doesn't

Each iteration produces a distinct, executable trading strategy with its own logic. Not the same strategy with different numbers — a genuinely different algorithm. The database fills up with a diverse population of strategies, each one a different bet on how the market behaves.

Think of it as a strategy factory that writes new trading algorithms until it hits your performance targets or exhausts its iteration budget.

## Installation

### From the OnSpotify-Plugins marketplace

```
/plugin marketplace add leCheeseRoyale/OnSpotify-Plugins
/plugin install trading-experiment@OnSpotify-Plugins
```

### From source

```
/plugin install --path /path/to/trading-experiment
```

After installing, the MCP server needs its Python dependencies:

```bash
cd <plugin-directory>/server
python -m venv .venv
source .venv/bin/activate        # Linux/Mac
source .venv/Scripts/activate    # Windows
pip install -r requirements.txt
```

## Usage

Run an experiment with the slash command:

```
/experiment
```

Claude will ask what market to target and any preferences you have. Just describe it naturally. Examples:

- "Generate BTC strategies on 4h candles, last year of data. Start with momentum."
- "Create strategies for ETH daily. I don't care what approach — just find something profitable."
- "Build limit-order strategies around support/resistance on SOL/USDT with tight stops"

### What you can configure

| Parameter | Default | Description |
|-----------|---------|-------------|
| Target Sharpe | 1.0 | OOS Sharpe ratio to aim for |
| Max drawdown | -25% | Worst acceptable drawdown |
| Max iterations | 30 | How many experiment cycles to run |
| Timeframe | 4h | Candle interval |
| Date range | Last 6 months | Backtest period |

All of these can be specified conversationally — "aim for Sharpe above 2", "no more than 15% drawdown", "test over 2023-2024", etc.

## Plugin Components

### MCP Server (`server/`)

A Python backtesting engine exposed as MCP tools:

| Tool | Purpose |
|------|---------|
| `fetch_ohlcv` | Fetch and cache market data via yfinance/ccxt |
| `get_market_info` | Trading constraints — fees, leverage, contract type |
| `run_backtest` | Execute strategy with 70/30 train/test split and overfitting detection |
| `optimize_strategy` | Grid search over parameter combinations |
| `save_strategy` | Persist strategy code + results to SQLite |
| `list_strategies` | Query the strategy database |
| `get_strategy` | Full details of a saved strategy |
| `get_experiment_summary` | Overview of the research session |
| `add_helper` | Create reusable utility functions for future strategies |
| `list_helpers` | Show available helper functions |

### Skill (`/experiment`)

Parses your intent, confirms the target market and parameters, then launches the autonomous strategy generation loop.

### Agent (`experiment-runner`)

Autonomous subagent that continuously creates, backtests, and evolves trading strategies in the background — generating new code each iteration.

## Architecture

```
You  ->  Claude Code  ->  /experiment skill (parses intent)
                |              |
                |              +-> experiment-runner agent (autonomous loop)
                |                       |
                |                       +-> fetch_ohlcv (market data)
                |                       +-> get_market_info (trading constraints)
                |                       +-> run_backtest (execute + validate)
                |                       +-> optimize_strategy (grid search)
                |                       +-> save_strategy (persist results)
                |
                +-> SQLite DB (strategies, results, helpers)
                +-> Parquet cache (market data)
```

No separate process. No TUI. Claude Code is the agent.

## Backtest Features

- **Out-of-sample validation** — automatic 70/30 split, every strategy tested on unseen data
- **Overfitting detection** — verdicts of "robust", "moderate_overfit", or "severe_overfit" based on IS/OOS performance degradation
- **Slippage modeling** — configurable slippage baked into commission
- **Helper library** — reusable functions (ATR stops, position sizing, regime filters, z-scores) that persist across experiments
- **Full exec namespace** — Strategy, pandas, numpy, pandas_ta, math, and all saved helpers available to generated code

## License

MIT
