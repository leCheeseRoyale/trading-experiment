---
name: experiment
description: "This skill should be used when the user asks to \"run a trading experiment\", \"find a trading strategy\", \"backtest strategies\", \"research crypto strategies\", \"optimize a trading strategy\", \"find alpha\", or wants autonomous strategy research and backtesting. Designs, backtests, and iterates on trading strategies using MCP backtesting tools until a target performance threshold is met on out-of-sample data."
argument-hint: "Describe what you want to trade and how — in plain English"
allowed-tools: ["Agent", "mcp__plugin_trading-experiment_trading-lab__fetch_ohlcv", "mcp__plugin_trading-experiment_trading-lab__run_backtest", "mcp__plugin_trading-experiment_trading-lab__optimize_strategy", "mcp__plugin_trading-experiment_trading-lab__save_strategy", "mcp__plugin_trading-experiment_trading-lab__list_strategies", "mcp__plugin_trading-experiment_trading-lab__get_strategy", "mcp__plugin_trading-experiment_trading-lab__get_experiment_summary", "mcp__plugin_trading-experiment_trading-lab__add_helper", "mcp__plugin_trading-experiment_trading-lab__get_market_info", "mcp__plugin_trading-experiment_trading-lab__list_helpers"]
---

# Trading Experiment Skill

You are orchestrating an autonomous crypto strategy research session. Understand what the user wants in their own words, confirm the plan, then dispatch the experiment-runner agent.

## 1. Understand the User's Intent

The user describes what they want in natural language. Your job is to extract the experiment parameters from their description conversationally. DO NOT require flags or structured arguments.

**What to extract (ask for anything missing):**

- **Trading idea / approach**: What market behavior or strategy concept to explore? Examples: "limit orders at support/resistance levels", "mean reversion after volume spikes", "breakout entries with tight stops"
- **Instrument**: What to trade? Symbol (e.g. BTC/USDT), contract type (spot, perp), exchange (binance, bybit). If they say "btcusdt perp on binance", that's `BTC/USDT:USDT` on binance.
- **Timeframe**: What candle interval? (1h, 4h, 1d, etc.)
- **Date range**: What period to test over? They might say "last 6 months", "2023 to now", "since the last halving" — convert to concrete dates.
- **Risk management preferences**: How aggressive or conservative? Position sizing approach (fixed %, risk-per-trade, Kelly)? Max drawdown tolerance? Stop-loss style (ATR-based, fixed %, levels)?
- **Entry style**: Signals (cross X, break Y) vs levels (limit orders at support/resistance, grid)? Market orders vs limit orders?
- **Success criteria**: What makes a strategy "good enough"? Target Sharpe, max drawdown, minimum trades, win rate? If not specified, use Sharpe >= 1.0 OOS and DD >= -25% as defaults.
- **Max iterations**: How long to search? Default 30 if not mentioned.

**Example user inputs and how to parse them:**

User: "4h timeframe over the last 6 months, the btcusdt perp contract on binance. Focus on levels for limit orders with good risk management for steady growth. Dynamic position sizing based on risk % if stopped out."
- Symbol: BTC/USDT:USDT (perp), exchange: binance
- Timeframe: 4h
- Since: 6 months ago from today, Until: today
- Idea: support/resistance level identification for limit order entries
- Risk: dynamic position sizing (risk % of equity per trade), stop-loss based
- Style: limit orders, not signal-based entries
- Target: steady growth = moderate Sharpe (>= 1.0), low drawdown (<= -15%)

User: "Find me something that works on ETH daily, I don't care what"
- Symbol: ETH/USDT, Timeframe: 1d
- Idea: open-ended exploration
- Everything else: defaults

**If the user's description is clear enough to proceed, confirm the parameters and move on. If key details are missing (especially the trading idea and instrument), ask ONE focused question to fill the gap. Don't interrogate — infer reasonable defaults from context.**

## 2. Initial Setup

Before dispatching the agent:

1. Call `get_market_info` with the parsed symbol to understand trading constraints: fees (maker/taker), contract type (spot/perpetual), leverage limits, collateral currency, funding rate impact, minimum order sizes. This informs realistic backtest parameters.
2. Call `fetch_ohlcv` with the parsed symbol, timeframe, since, and until to confirm data availability. Report the number of candles fetched.
3. Call `list_helpers` to show the agent what utilities are already available (so it doesn't reinvent them).
4. Summarize the experiment parameters back to the user:
   - Seed idea
   - Symbol / timeframe / date range
   - Target thresholds (Sharpe, max DD)
   - Max iterations

## 3. Research Methodology

Encode these principles in the agent dispatch. The experiment-runner must understand:

**Think like a researcher, not an indicator configurator.** The goal is to discover exploitable market behaviors, not to tune RSI thresholds.

- Start with a market IDEA — a belief about how and why price moves in predictable ways. Then figure out how to measure and test it.
- Ideas from outside finance are valuable: information theory (entropy of returns), physics (mean reversion as spring dynamics), behavioral science (overreaction to round numbers), game theory (liquidity provider incentives).
- Raw price and volume math is just as valid as pandas-ta indicators. Rolling z-scores, return distributions, volume-weighted price levels, order flow imbalance proxies — all fair game.
- Each experiment tests ONE idea cleanly. State a falsifiable hypothesis before writing code.
- Experiment types:
  - **pivot**: Entirely new idea. Use when current direction is exhausted or overfitting.
  - **refine**: Small tweak to a proven idea. Use when Sharpe > 0.3 and OOS holds up.
  - **mutate**: Change the implementation (different indicator, different entry logic) while keeping the same core thesis. Use when idea seems sound but execution is wrong.
  - **sweep**: Parameter grid search. Use ONLY when Sharpe > 0.5 and you want to find optimal params.

## 4. Iteration Protocol

The agent follows this loop each iteration:

**Step 1 — Plan**: Decide what to test and why. State a falsifiable hypothesis. Reference what you learned from previous iterations. If pivoting, summarize what the previous direction taught you about the market.

**Step 2 — Code**: Write a `backtesting.py` Strategy subclass following these rules:
- Subclass `Strategy` (available in namespace, no import needed)
- Wrap ALL indicator computations in `self.I(fn, ...)` inside `init()`
- Declare parameters as class-level variables (e.g., `sma_period = 20`)
- Access OHLCV data via `self.data.Close`, `self.data.High`, `self.data.Low`, `self.data.Open`, `self.data.Volume`
- Convert to pandas when needed: `pd.Series(self.data.Close)`
- Available in namespace: `Strategy`, `pd` (pandas), `np` (numpy), `ta` (pandas_ta), `math`, `helpers` (reusable functions — ATR, stops, sizing, regime filters, z-scores, etc.)
- Entry: `self.buy()` / `self.sell()` / `self.buy(limit=price)` / `self.buy(stop=price)`. Exit: `self.position.close()`
- Position sizing: `self.buy(size=0.5)` for 50% equity, or use `helpers.position_size_pct(equity, stop_dist, risk_pct)` for risk-based sizing
- Risk management: `self.buy(sl=stop_price, tp=target_price)` for stop-loss/take-profit at absolute price levels
- Use `helpers.atr()`, `helpers.atr_stop()`, `helpers.atr_target()` for ATR-based risk management instead of reimplementing
- Position sizing methods: `helpers.position_size_pct()` (fixed risk), `helpers.kelly_size()` (Kelly criterion), `helpers.volatility_scaled_size()` (vol-targeting), `helpers.max_drawdown_size()` (DD-aware scaling)
- If you need a helper that doesn't exist, create it with `add_helper` so it persists across all future strategies
- Call `list_helpers` to see what's available before writing custom utility code
- See `references/code-guide.md` for full helpers API, order types, and examples

**Market-aware design:**
- Check `get_market_info` results to understand real trading constraints
- Use the actual taker fee from market info as commission_pct (not just the default 0.1%)
- For perpetual contracts: account for funding rates on positions held > 8h, model leverage as position size multiplier with liquidation stop
- For spot: strategies are long-only unless margin is available
- Collateral currency matters: USDT-margined (linear) vs coin-margined (inverse) affects P&L calculation
- Minimum order sizes and tick sizes should inform position sizing logic
- NO lookahead bias — `next()` must only use data available at the current bar
- No hardcoded dates, symbols, or magic numbers without param declarations
- Add brief comments in `init()` explaining what each indicator captures

**Step 3 — Backtest**: Call `run_backtest` with `validate_oos=true`. This splits data into in-sample (70%) and out-of-sample (30%) and reports both.

**Step 4 — Analyze**: Interpret results honestly.
- Was the hypothesis confirmed or refuted?
- How much does performance degrade from IS to OOS? Degradation > 50% suggests overfitting.
- What does this tell you about the MARKET, not about your code?
- Is the trade count sufficient (>= 5)?

**Step 5 — Save**: Call `save_strategy` with the strategy name, code, hypothesis, metrics, and experiment type. Do NOT paste full strategy code into the conversation — save it and reference by name.

**Step 6 — Decide**: Based on analysis, choose next action:
- **Refine** if OOS Sharpe > 0.3 and not overfitting
- **Mutate** if idea seems right but implementation is wrong
- **Pivot** if idea is fundamentally flawed or severely overfitting
- **Sweep** if OOS Sharpe > 0.5 and want to optimize
- **Stop** if targets met on OOS data or idea space is exhausted

## 5. Stop Conditions

The experiment ends when ANY of these is true:

1. **Target met**: OOS Sharpe >= target_sharpe AND OOS max drawdown >= target_max_dd (remember DD is negative, so -15% >= -25% is a pass)
2. **Max iterations reached**: Report best result found
3. **Idea space exhausted**: Agent honestly determines no further progress is likely. Report what was tried and why it didn't work.

## 6. Guardrails

Enforce these automatically:

- **Reject** any strategy with fewer than 5 trades — the idea is too restrictive, loosen entry conditions
- **Never refine** a strategy with OOS verdict "severe_overfit" — pivot instead
- **Force pivot** after 5+ consecutive iterations with no OOS Sharpe improvement — the current direction is a dead end
- **Never sweep** until Sharpe > 0.3 — optimize validated ideas, not noise
- **Auto-reject** strategies where IS Sharpe > 3x OOS Sharpe — classic overfit signature

## 7. Context Management

Keep the conversation efficient:

- Save strategy code via `save_strategy`. Reference strategies by name, not by pasting code.
- Call `get_experiment_summary` every 5-10 iterations to reorient. It returns the full history of what has been tried.
- Maintain a mental scratchpad tracking: what market behaviors work, what doesn't, open questions, best OOS result so far.
- When pivoting, write one sentence summarizing the key learning from the abandoned direction before moving on.

## 8. Dispatch

After confirming data availability and parameters, dispatch the `experiment-runner` agent using the Agent tool. Pass the full context from the conversation — the user's original description, all extracted parameters, market info, and available helpers:

```
Trading idea: {user's description in their own words}
Instrument: {symbol} ({contract_type}) on {exchange}
Timeframe: {timeframe} | Date range: {since} to {until}
Entry style: {market orders / limit orders / stop orders / as user described}
Risk management: {user's preferences — sizing method, stop style, max DD tolerance}
Target: OOS Sharpe >= {target_sharpe}, Max DD >= {target_max_dd}%
Max iterations: {max_iterations}
Commission: {actual taker fee from get_market_info} + {slippage}

Market info: {key details — fees, contract type, leverage, collateral, funding rate notes}
Available helpers: {summary from list_helpers}

Run the experiment loop. Design your first experiment based on the trading idea.
Iterate until targets are met on OOS data or max iterations reached.
```

## 9. Reporting

When the agent completes (or you receive its final output), present to the user:

- **Result**: Success (targets met) or Exhausted (max iterations / no further progress)
- **Best strategy**: Name, hypothesis, OOS Sharpe, OOS max DD, total trades
- **Strategy code**: Retrieve via `get_strategy` and display
- **Research journey**: Brief timeline of what was tried, what worked, what didn't
- **Unexplored directions**: Ideas that emerged but weren't tested — future research seeds
