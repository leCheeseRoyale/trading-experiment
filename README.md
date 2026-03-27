# Trading Experiment Plugin

Claude Code plugin for autonomous crypto strategy research. Claude designs experiments, writes backtesting.py strategy code, runs backtests via MCP tools, analyzes results, and iterates until a target threshold is met.

## Quick Start

```bash
# Install server dependencies
cd server
python -m venv .venv
source .venv/Scripts/activate  # Windows (use bin/activate on Linux/Mac)
pip install -r requirements.txt

# Install plugin in Claude Code
claude --plugin-dir /path/to/PIO12

# Run an experiment
/trading-experiment "BTC mean reversion using volume-price divergence" --target-sharpe 1.5
```

## How It Works

The plugin provides:

- **MCP Server** (`server/`) — 7 tools wrapping a backtesting engine with automatic out-of-sample validation
- **Skill** (`/trading-experiment`) — Research methodology that guides Claude through the experiment loop
- **Agent** (`experiment-runner`) — Autonomous subagent that iterates in the background

### MCP Tools

| Tool | Purpose |
|------|---------|
| `fetch_ohlcv` | Fetch and cache market data (yfinance/ccxt) |
| `run_backtest` | Execute strategy with automatic 70/30 train/test split |
| `optimize_strategy` | Grid search over parameter combinations |
| `save_strategy` | Persist strategy + results to SQLite |
| `list_strategies` | Query strategy database |
| `get_strategy` | Full details of a single strategy |
| `get_experiment_summary` | Research session overview |

### Backtest Features

- **Slippage modeling** — baked into effective commission (configurable)
- **Out-of-sample validation** — automatic 70/30 split with overfitting detection
- **Expanded exec namespace** — Strategy, pd, np, ta (pandas_ta), math
- **Trade summaries** — context-efficient reporting (no raw trade dumps)
- **Overfitting verdict** — "robust", "moderate_overfit", or "severe_overfit"

## Architecture

```
User -> Claude Code -> MCP Server (Python)
             |              |
             |              +-- run_backtest(code, symbol, ...)
             |              +-- fetch_ohlcv(symbol, timeframe, ...)
             |              +-- save_strategy(name, code, metrics, ...)
             |
             +-- /trading-experiment skill (methodology)
             +-- experiment-runner agent (autonomous iteration)
```

No separate agent loop. No TUI. Claude Code IS the agent.

## Configuration

All backtest parameters are configurable per-call via MCP tool arguments. Defaults:

| Parameter | Default | Description |
|-----------|---------|-------------|
| initial_capital | 100,000 | Starting cash |
| commission_pct | 0.001 | Trading fee (0.1%) |
| slippage_pct | 0.0005 | Slippage estimate (0.05%) |
| trade_on_close | true | Execute at close vs next open |
| oos_split_ratio | 0.7 | Train/test split (70/30) |
| target_sharpe | 1.5 | OOS Sharpe target |
| target_max_dd | -25.0 | Max drawdown threshold |
