---
name: trading-experiment
description: "This skill should be used when the user asks to \"run a trading experiment\", \"find a trading strategy\", \"backtest strategies\", \"research crypto strategies\", \"optimize a trading strategy\", \"find alpha\", or wants autonomous strategy research and backtesting. Designs, backtests, and iterates on trading strategies using MCP backtesting tools until a target performance threshold is met on out-of-sample data."
argument-hint: "<seed_idea> [--target-sharpe N] [--max-iterations N] [--symbol PAIR] [--timeframe TF]"
allowed-tools: ["Agent", "mcp__plugin_trading-experiment_trading-lab__fetch_ohlcv", "mcp__plugin_trading-experiment_trading-lab__run_backtest", "mcp__plugin_trading-experiment_trading-lab__optimize_strategy", "mcp__plugin_trading-experiment_trading-lab__save_strategy", "mcp__plugin_trading-experiment_trading-lab__list_strategies", "mcp__plugin_trading-experiment_trading-lab__get_strategy", "mcp__plugin_trading-experiment_trading-lab__get_experiment_summary"]
---

# Trading Experiment Skill

You are orchestrating an autonomous crypto strategy research session. Parse the user's input, set up data, then dispatch the experiment-runner agent to iterate autonomously.

## 1. Parse Arguments

Extract from the user's input:

- **seed_idea** (required, positional): The trading concept to explore. This is everything that isn't a flag.
- **--target-sharpe** (default: 1.5): Out-of-sample Sharpe ratio threshold for declaring success.
- **--target-max-dd** (default: -25.0): Maximum drawdown threshold (percentage, negative number).
- **--max-iterations** (default: 30): Maximum experiment iterations before stopping.
- **--symbol** (default: BTC/USDT): Trading pair.
- **--timeframe** (default: 4h): Candle timeframe.
- **--since** (default: 2021-01-01): Backtest start date.
- **--until** (default: 2024-12-31): Backtest end date.

If the user omits the seed idea, ask for it before proceeding.

## 2. Initial Setup

Before dispatching the agent:

1. Call `fetch_ohlcv` with the parsed symbol, timeframe, since, and until to confirm data availability. Report the number of candles fetched.
2. Summarize the experiment parameters back to the user:
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
- Available in namespace: `Strategy`, `pd` (pandas), `np` (numpy), `ta` (pandas_ta), `math`
- Entry: `self.buy()` / `self.sell()`. Exit: `self.position.close()`
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

After confirming data availability and parameters, dispatch the `experiment-runner` agent using the Agent tool. Pass the full context:

```
Seed idea: {seed_idea}
Symbol: {symbol} | Timeframe: {timeframe} | Date range: {since} to {until}
Target OOS Sharpe: {target_sharpe} | Target max DD: {target_max_dd}%
Max iterations: {max_iterations}

Run the experiment loop. Iterate until targets are met on OOS data or max iterations reached.
Start by designing your first experiment based on the seed idea.
```

## 9. Reporting

When the agent completes (or you receive its final output), present to the user:

- **Result**: Success (targets met) or Exhausted (max iterations / no further progress)
- **Best strategy**: Name, hypothesis, OOS Sharpe, OOS max DD, total trades
- **Strategy code**: Retrieve via `get_strategy` and display
- **Research journey**: Brief timeline of what was tried, what worked, what didn't
- **Unexplored directions**: Ideas that emerged but weren't tested — future research seeds
