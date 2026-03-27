# Trading Experiment Plugin — Design Spec

## What This Is

A Claude Code plugin that turns Claude into an autonomous crypto strategy researcher. The user says `/trading-experiment` with a seed idea and a success threshold, and Claude iterates — designing experiments, writing strategy code, running backtests via MCP tools, analyzing results, and repeating until the threshold is met or it gets stuck.

No separate agent loop. No TUI. No prompt templates. Claude Code IS the agent, with full reasoning capability instead of lobotomized API calls through plan.txt/reflect.txt slots.

## Architecture

```
User ──► Claude Code ──► MCP Server (Python process)
              │                │
              │                ├── run_backtest(code, symbol, timeframe, ...)
              │                ├── fetch_ohlcv(symbol, timeframe, since, until)
              │                ├── optimize_strategy(code, param_grid, ...)
              │                ├── save_strategy(name, code, hypothesis, metrics, ...)
              │                ├── list_strategies(status?, sort_by?, limit?)
              │                ├── get_strategy(name_or_id)
              │                └── get_experiment_summary()
              │
              ├── /trading-experiment skill (methodology + iteration loop)
              └── experiment-runner agent (autonomous subagent)
```

**Three components:**

1. **MCP Server** — A Python process exposing backtesting infrastructure as tools. Wraps the backtester, data fetcher, DB, and adds the realism fixes missing from the old implementation. This is the "hands" — it does the computation.

2. **Skill (`/trading-experiment`)** — Encodes the research methodology. Tells Claude how to think about experiments, when to pivot, when to stop, how to evaluate. This is the "brain" — it shapes Claude's reasoning.

3. **Agent (`experiment-runner`)** — A subagent definition that Claude can dispatch to run experiments autonomously in the background. Uses the MCP tools directly. This is what makes it hands-off.

---

## Component 1: MCP Server

### Server Setup

Python process using the `mcp` SDK. Runs as a stdio server configured in `.mcp.json`. Manages its own SQLite database and data cache within the plugin's data directory.

### Tools

#### `fetch_ohlcv`

Fetches OHLCV market data with caching. Ported from existing `backtester/data.py`.

```
Parameters:
  symbol: str          — e.g. "BTC/USDT" (default: "BTC/USDT")
  timeframe: str       — "1h", "4h", "1d" (default: "4h")
  since: str           — ISO date, e.g. "2021-01-01"
  until: str           — ISO date, e.g. "2024-12-31"
  source: str          — "auto", "yfinance", "ccxt" (default: "auto")

Returns:
  JSON with: row_count, date_range, columns, cache_status
  (Data is cached server-side — the tool confirms availability,
   subsequent backtest calls use the cached data by reference)
```

Why not return raw OHLCV? It would blow up Claude's context window with thousands of rows. The server caches it internally; backtest tools reference it by (symbol, timeframe, since, until).

#### `run_backtest`

Executes a strategy code string against cached OHLCV data. This is the core tool.

```
Parameters:
  strategy_code: str   — Full Python source (backtesting.py Strategy subclass)
  symbol: str          — Must match a previously fetched dataset
  timeframe: str
  since: str
  until: str
  params: dict?        — Optional parameter overrides
  initial_capital: float?  — Default 100,000
  commission_pct: float?   — Default 0.001 (0.1%)
  slippage_pct: float?     — Default 0.0005 (0.05%) — ACTUALLY USED NOW
  trade_on_close: bool?    — Default true
  validate_oos: bool?      — Default true — enables train/test split

Returns:
  JSON with:
    status: "success" | "error"
    error: str?

    # In-sample metrics (train period, or full period if validate_oos=false)
    in_sample:
      total_return_pct, sharpe_ratio, sortino_ratio, calmar_ratio,
      max_drawdown_pct, win_rate_pct, profit_factor, total_trades,
      avg_trade_duration_hours, cagr_pct,
      buy_hold_return_pct, excess_return_pct
      period: {from, to}

    # Out-of-sample metrics (test period — only if validate_oos=true)
    out_of_sample:
      (same metrics as above)
      period: {from, to}

    # Overfitting indicator
    oos_degradation:
      sharpe_drop_pct: float   — how much Sharpe dropped from IS to OOS
      return_drop_pct: float
      verdict: "robust" | "moderate_overfit" | "severe_overfit"

    # Trade summary (not full trade list — context-friendly)
    trade_summary:
      total: int
      winners: int
      losers: int
      avg_win_pct: float
      avg_loss_pct: float
      largest_win_pct: float
      largest_loss_pct: float
      max_consecutive_wins: int
      max_consecutive_losses: int
```

Key design decisions:
- **Slippage is baked into effective commission** (`commission + slippage`) since backtesting.py has no native slippage param. This is the standard approximation.
- **OOS validation is on by default.** 70/30 train/test split. The agent always sees whether its strategy generalizes.
- **Trade list is summarized, not dumped.** Full trade list would waste context. Summary stats tell Claude everything it needs.
- **Exec namespace includes** `Strategy`, `pd`, `np` (numpy), `ta` (pandas_ta), `math`. The old implementation only had Strategy and pd.

#### `optimize_strategy`

Grid search over parameter combinations.

```
Parameters:
  strategy_code: str
  param_grid: dict     — e.g. {"rsi_period": [10, 14, 20], "threshold": [25, 30, 35]}
  symbol: str
  timeframe: str
  since: str
  until: str
  top_n: int?          — Default 5
  initial_capital: float?
  commission_pct: float?
  slippage_pct: float?

Returns:
  JSON with top_n results, each containing:
    params: dict
    sharpe_ratio, total_return_pct, max_drawdown_pct, total_trades
    oos_sharpe_ratio (if enough data for OOS validation)
```

#### `save_strategy`

Persists a strategy and its results to the DB for tracking.

```
Parameters:
  name: str
  code: str
  hypothesis: str
  experiment_type: str     — "pivot", "refine", "mutate", "sweep"
  symbol: str
  timeframe: str
  metrics: dict            — backtest results to store
  parent_name: str?        — strategy this was derived from
  tags: list[str]?
  market_concept: str?

Returns:
  JSON with: strategy_id, saved: true
```

#### `list_strategies`

Query the strategy database.

```
Parameters:
  status: str?         — "done", "error", "skipped"
  sort_by: str?        — "sharpe", "return", "created" (default: "sharpe")
  limit: int?          — Default 20
  tag: str?            — Filter by tag

Returns:
  JSON array of strategy summaries:
    name, status, sharpe_ratio, total_return_pct, max_drawdown_pct,
    total_trades, oos_verdict, hypothesis (truncated), created_at
```

#### `get_strategy`

Get full details of a single strategy.

```
Parameters:
  name: str            — Strategy name (or id)

Returns:
  JSON with full strategy record:
    name, code, hypothesis, experiment_type, market_concept,
    parent_name, generation, symbol, timeframe,
    all metrics (IS + OOS), trade_summary, tags, created_at
```

#### `get_experiment_summary`

High-level overview of the research session. Designed so Claude can quickly orient itself.

```
Parameters: none

Returns:
  JSON with:
    total_strategies: int
    strategies_by_status: {done: N, error: N, skipped: N}
    best_strategy: {name, sharpe, return, oos_verdict}
    recent_strategies: last 5 with key metrics
    concepts_explored: list of unique market_concepts tried
    current_direction: last strategy's hypothesis
```

### Database

Reuses the existing SQLModel schema from `db/models.py` with these additions to `BacktestResult`:

- `oos_total_return_pct: float` — Out-of-sample return
- `oos_sharpe_ratio: float` — Out-of-sample Sharpe
- `oos_max_drawdown_pct: float` — Out-of-sample max DD
- `oos_win_rate_pct: float` — Out-of-sample win rate
- `oos_total_trades: int` — Out-of-sample trade count
- `oos_verdict: str` — "robust" / "moderate_overfit" / "severe_overfit"
- `train_period: str` — e.g. "2021-01-01 to 2023-10-01"
- `test_period: str` — e.g. "2023-10-01 to 2024-12-31"

The DB file lives at `{plugin_data_dir}/lab.db`. Data cache at `{plugin_data_dir}/data_cache/`.

### Backtest Realism Fixes (vs old implementation)

| Issue | Old behavior | New behavior |
|-------|-------------|-------------|
| Slippage | Configured but ignored | Baked into effective commission |
| Exec namespace | Only `Strategy`, `pd` | Adds `np`, `ta`, `math` |
| OOS validation | None — all in-sample | 70/30 split, both metrics returned |
| trade_on_close | Hardcoded (next-open) | Configurable, default true |
| Error context | Bare exception string | Strategy name + code snippet + full traceback |
| Trade data to agent | Full trade list (context bomb) | Summarized stats only |

---

## Component 2: Skill (`/trading-experiment`)

The skill is a markdown file that gets loaded when the user invokes `/trading-experiment`. It instructs Claude on the research methodology and iteration loop.

### Invocation

```
/trading-experiment <seed_idea> [--target-sharpe 1.5] [--max-iterations 30] [--symbol BTC/USDT] [--timeframe 4h]
```

Or just:
```
/trading-experiment
```
(Claude asks for the seed idea conversationally)

### What the Skill Encodes

**Research methodology** — the same creative researcher philosophy from the current prompts (system.txt, plan.txt, reflect.txt), but as guidance to Claude Code itself rather than API call templates. Claude is smarter when it reasons in-context than when it fills template slots.

**Iteration protocol:**

1. Fetch data for the target symbol/timeframe (once, cached)
2. Design an experiment based on the seed idea
3. Write strategy code
4. Run backtest via MCP tool (with OOS validation)
5. Analyze results — what worked, what didn't, what does it mean about the market
6. Decide next action: refine, mutate, pivot, sweep, or stop
7. Repeat from step 2

**Stop conditions** (checked after each iteration):
- Target threshold met (e.g. OOS Sharpe > 1.5 AND OOS max DD > -25%)
- Max iterations reached
- Claude determines it has exhausted the idea space and reports back

**Context management guidance:**
- Don't dump full strategy code into conversation history — save it via `save_strategy`, reference by name
- Use `get_experiment_summary` to reorient after many iterations, not `list_strategies` with limit=100
- Keep a running scratchpad as a conversation-local note (what works, what doesn't, open questions)
- When pivoting, briefly summarize what you learned from the previous direction

**Guardrails:**
- Auto-reject strategies with < 5 trades (not enough signal)
- Flag OOS degradation > 50% as likely overfitting
- If 5+ consecutive strategies show no improvement, pivot to a fundamentally different approach
- Never optimize parameters on an unvalidated idea (Sharpe must be > 0.3 before sweeping)

**Success criteria** — the skill defines what "done" means:
- A strategy that meets the user's target thresholds on OOS data (not in-sample)
- Claude reports: strategy name, code, key metrics (IS and OOS), hypothesis, and what it learned
- Strategy is saved to DB for later retrieval

### Skill Content Structure

The skill markdown will have:
1. **Preamble** — parse arguments, set defaults, fetch data
2. **Methodology** — how to think about experiments (condensed from system.txt + plan.txt)
3. **Iteration loop** — step-by-step protocol with MCP tool calls
4. **Reflection guide** — how to interpret results (condensed from reflect.txt)
5. **Stop conditions** — when to stop and what to report
6. **Code generation guide** — requirements for strategy code (from code.txt)

---

## Component 3: Agent (`experiment-runner`)

A subagent definition that Claude Code can dispatch to run experiments autonomously. This is what makes it truly hands-off — the user invokes `/trading-experiment`, Claude dispatches the subagent, and the subagent iterates in the background using MCP tools.

### Agent Definition

```yaml
name: experiment-runner
description: >
  Autonomous strategy researcher. Runs the experiment loop:
  design → code → backtest → analyze → repeat.
  Uses MCP tools for all computation. Stops when target
  threshold is met or max iterations reached.
tools:
  - all MCP tools from the trading server
  - Read, Write, Bash (for any file operations)
model: sonnet  # Fast enough for iteration, smart enough for code gen
```

### How It Works

1. User says `/trading-experiment "mean reversion on BTC" --target-sharpe 1.5`
2. The skill loads, parses args, fetches data via MCP
3. Claude dispatches the `experiment-runner` agent with:
   - The seed idea
   - Target thresholds
   - Symbol/timeframe/date range
   - The methodology instructions
4. The agent iterates autonomously, using MCP tools
5. When done, it returns the best strategy to the main conversation
6. Claude presents the results to the user

The user can check in anytime — the agent's work is visible in the conversation if run in foreground, or they get notified when it completes if run in background.

---

## Directory Structure

```
C:\Users\Maxwell\AI\PIO12\
├── plugin.json                         # Plugin manifest
├── .mcp.json                           # MCP server config
├── skills/
│   └── trading-experiment.md           # Main research skill
├── agents/
│   └── experiment-runner.md            # Autonomous subagent
├── server/                             # MCP server (Python)
│   ├── __init__.py
│   ├── main.py                         # MCP server entry — tool registrations
│   ├── requirements.txt                # mcp, backtesting, pandas-ta, etc.
│   ├── setup.sh                        # Venv + install
│   ├── backtester/
│   │   ├── __init__.py
│   │   ├── runner.py                   # Enhanced run_backtest with OOS + slippage
│   │   ├── optimizer.py                # Grid search (ported)
│   │   └── data.py                     # OHLCV fetch + cache (ported)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py                   # Strategy, BacktestResult (with OOS fields)
│   │   └── session.py                  # SQLite + WAL (ported)
│   └── config/
│       └── defaults.py                 # Default values for backtest params
└── data/                               # Runtime data (gitignored)
    ├── lab.db                          # SQLite database
    └── data_cache/                     # Parquet cache
```

### What's Ported vs New

| File | Source | Changes |
|------|--------|---------|
| `server/backtester/runner.py` | `crypto_lab/backtester/runner.py` | + slippage, + OOS split, + expanded namespace, + trade summary |
| `server/backtester/optimizer.py` | `crypto_lab/backtester/optimizer.py` | + slippage, + OOS metrics in results |
| `server/backtester/data.py` | `crypto_lab/backtester/data.py` | Ported as-is (already good) |
| `server/db/models.py` | `crypto_lab/db/models.py` | + OOS fields on BacktestResult, - ChatMessage, - AgentState |
| `server/db/session.py` | `crypto_lab/db/session.py` | Ported as-is |
| `server/main.py` | New | MCP tool registrations wrapping above modules |
| `skills/trading-experiment.md` | New (replaces agent/prompts/*.txt) | Research methodology as skill |
| `agents/experiment-runner.md` | New (replaces agent/loop.py) | Autonomous iteration as subagent |
| `plugin.json` | New | Plugin manifest |

### What's Dropped

- `agent/loop.py` — Claude Code IS the loop now
- `agent/planner.py`, `coder.py`, `reflector.py` — Claude reasons natively, no template slots
- `agent/prompts/*.txt` — Condensed into the skill
- `agent/chat_handler.py` — You're already in Claude Code's conversation
- `agent/ai_client.py` — No separate API calls needed
- `dashboard/` (entire directory) — Claude Code is the interface
- `config/lab.toml` — Defaults in code, overrides via tool params
- `main.py` — Plugin commands replace CLI

---

## Config & Defaults

No TOML file. Defaults are in `server/config/defaults.py`:

```python
DEFAULTS = {
    "initial_capital": 100_000,
    "commission_pct": 0.001,      # 0.1%
    "slippage_pct": 0.0005,       # 0.05%
    "trade_on_close": True,
    "oos_split_ratio": 0.7,       # 70% train, 30% test
    "min_trades": 5,              # auto-reject below this
    "max_iterations": 30,
    "target_sharpe": 1.5,         # default success threshold
    "target_max_dd": -25.0,       # default max drawdown threshold
}
```

All overridable via MCP tool parameters or skill arguments.

---

## User Experience

### Scenario 1: Quick experiment
```
User: /trading-experiment "BTC mean reversion using RSI on 4h" --target-sharpe 1.5
Claude: [fetches data, runs 12 iterations, finds strategy with OOS Sharpe 1.62]
Claude: Found a strategy meeting your threshold. "volatility_squeeze_rsi_v8"
        achieves Sharpe 1.62 out-of-sample (1.84 in-sample) with max DD -18%.
        [shows code, metrics, what it learned]
```

### Scenario 2: Exploratory
```
User: /trading-experiment
Claude: What market behavior do you want to explore? And what's your
        target — minimum Sharpe ratio and max drawdown you'd accept?
User: Volume-price divergence on ETH, Sharpe > 1.0 is fine
Claude: [iterates, pivots twice, finds working approach after 20 iterations]
```

### Scenario 3: Check in on progress
```
User: How's it going?
Claude: [calls get_experiment_summary]
Claude: 8 iterations in. Best so far is "vol_breakout_v5" with OOS Sharpe 0.94.
        I've been exploring volatility compression — it shows promise but the
        entries are too early. Trying a confirmation filter next.
```

### Scenario 4: Redirect
```
User: Stop trying momentum stuff, try a statistical approach — return distribution skew
Claude: [pivots immediately, starts new direction]
```

---

## Testing

### MCP Server Tests
Port and enhance existing tests from `crypto_lab/tests/`:
- `test_runner.py` — add tests for slippage, OOS split, expanded namespace
- `test_optimizer.py` — add OOS metrics test
- `test_data.py` — port as-is (mocked ccxt)
- `test_models.py` — update for new OOS fields
- New: `test_mcp_tools.py` — test each MCP tool end-to-end

### Integration Test
A script that simulates a full experiment cycle:
1. Fetch data
2. Run backtest with a known strategy
3. Save results
4. List strategies
5. Verify OOS metrics are present and reasonable

### Manual Validation
After building, install the plugin and run `/trading-experiment "RSI mean reversion on BTC"` to verify the full loop works end-to-end.
