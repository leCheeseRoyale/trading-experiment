# Trading Experiment

A Claude Code plugin that turns Claude into an autonomous trading strategy researcher. Describe a trading idea in plain English, and Claude will design experiments, write strategy code, run backtests, analyze results, and iterate — all without manual intervention — until it finds a strategy that meets your performance targets on out-of-sample data.

## What It Does

You say something like:

> "4h timeframe, BTC perp on binance, last 6 months. Try mean reversion after volume spikes with ATR-based stops."

Claude then:

1. Fetches market data and checks trading constraints (fees, contract type, leverage)
2. Designs a falsifiable hypothesis and writes a `backtesting.py` Strategy class
3. Runs the backtest with automatic 70/30 in-sample / out-of-sample validation
4. Analyzes results, detects overfitting, and decides what to try next
5. Repeats — refining, mutating, or pivoting — until targets are met or iterations run out

The entire loop runs autonomously. You can set Sharpe ratio targets, max drawdown thresholds, and iteration limits. Results are saved to a local SQLite database so nothing is lost.

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

Claude will ask what you want to trade and how. Just describe it naturally — no flags or structured arguments required. Examples:

- "BTC mean reversion using volume-price divergence, 4h candles, last year"
- "Find me something that works on ETH daily, I don't care what"
- "Limit orders at support/resistance levels on SOL/USDT with tight risk management"

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

Research methodology prompt that guides Claude through understanding your intent, confirming parameters, and dispatching the autonomous agent.

### Agent (`experiment-runner`)

Autonomous subagent that runs the full experiment loop in the background — plan, code, backtest, analyze, repeat.

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
