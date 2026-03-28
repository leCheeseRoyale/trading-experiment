import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP
from loguru import logger
from sqlmodel import select

from backtester.data import fetch_ohlcv as _fetch_ohlcv, check_data_quality
from backtester.runner import run_backtest as _run_backtest, validate_strategy_code
from backtester.optimizer import optimize_strategy as _optimize
from db.models import Strategy, StrategyStatus, BacktestResult
from db.session import get_session, init_db
from config.defaults import DEFAULTS

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "data_cache").mkdir(exist_ok=True)

init_db(DATA_DIR / "lab.db")

mcp = FastMCP("trading-lab")


@mcp.tool()
def fetch_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "4h",
    since: str = "2021-01-01",
    until: str = "2024-12-31",
    source: str = "auto",
) -> str:
    """Fetch and cache OHLCV market data. Returns metadata (not raw data).

    Data is cached server-side as parquet. Subsequent backtest calls
    use the cached data automatically by matching symbol/timeframe/dates.
    """
    cache_dir = DATA_DIR / "data_cache"
    df = _fetch_ohlcv(
        symbol=symbol, timeframe=timeframe,
        since=since, until=until,
        cache_dir=cache_dir, source=source,
    )
    quality = check_data_quality(df, timeframe)
    result = {
        "status": "success",
        "row_count": len(df),
        "date_range": quality["date_range"],
        "columns": list(df.columns),
        "symbol": symbol,
        "timeframe": timeframe,
        "data_quality": quality,
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def run_backtest(
    strategy_code: str,
    symbol: str = "BTC/USDT",
    timeframe: str = "4h",
    since: str = "2021-01-01",
    until: str = "2024-12-31",
    params: str = "{}",
    initial_capital: float = 100_000,
    commission_pct: float = 0.001,
    slippage_pct: float = 0.0005,
    trade_on_close: bool = True,
    validate_oos: bool = True,
    oos_split_ratio: float = 0.7,
) -> str:
    """Run a backtest on a strategy code string with optional out-of-sample validation.

    The strategy code must define a class that subclasses backtesting.Strategy.
    Available in the exec namespace: Strategy, pd, np (numpy), ta (pandas_ta), math, helpers.

    When validate_oos is true (default), data is split into train/test periods.
    The split ratio varies randomly between 0.6-0.8 each run to prevent implicit
    OOS fitting across many iterations. Both in-sample and out-of-sample metrics
    are returned so you can detect overfitting.

    IMPORTANT: Commission is per trade leg. A full round-trip (buy + sell) costs
    2x the commission rate. With commission_pct=0.001 and slippage_pct=0.0005,
    each leg costs 0.15%, so a round-trip costs 0.30% total.

    Args:
        strategy_code: Python source defining a backtesting.py Strategy subclass
        symbol: Trading pair, e.g. "BTC/USDT"
        timeframe: Candle timeframe, e.g. "4h", "1d"
        since: Backtest start date (YYYY-MM-DD)
        until: Backtest end date (YYYY-MM-DD)
        params: JSON string of parameter overrides, e.g. '{"rsi_period": 14}'
        initial_capital: Starting cash (default 100000)
        commission_pct: Commission rate per trade leg (default 0.001 = 0.1%). Round-trip = 2x.
        slippage_pct: Slippage estimate per leg (default 0.0005 = 0.05%). Round-trip = 2x.
        trade_on_close: Execute at bar close (true) or next open (false)
        validate_oos: Enable train/test split for out-of-sample validation
        oos_split_ratio: Fraction of data for training (default 0.7). Auto-randomized +/-0.1.
    """
    # Pre-validate strategy code before running
    validation = validate_strategy_code(strategy_code)
    if not validation["valid"]:
        return json.dumps({
            "status": "error",
            "error": "Strategy code failed pre-validation",
            "validation_errors": validation["errors"],
            "validation_warnings": validation["warnings"],
            "in_sample": None,
            "out_of_sample": None,
            "oos_degradation": None,
            "trade_summary": None,
        }, indent=2)

    cache_dir = DATA_DIR / "data_cache"
    ohlcv = _fetch_ohlcv(
        symbol=symbol, timeframe=timeframe,
        since=since, until=until,
        cache_dir=cache_dir,
    )

    parsed_params = json.loads(params) if isinstance(params, str) else params

    # Randomize OOS split ratio to prevent implicit fitting to a fixed test window
    import random
    if validate_oos:
        actual_split = max(0.5, min(0.85, oos_split_ratio + random.uniform(-0.1, 0.1)))
    else:
        actual_split = oos_split_ratio

    result = _run_backtest(
        strategy_code=strategy_code,
        ohlcv_df=ohlcv,
        params=parsed_params,
        cash=initial_capital,
        commission=commission_pct,
        slippage=slippage_pct,
        trade_on_close=trade_on_close,
        validate_oos=validate_oos,
        oos_split_ratio=actual_split,
    )

    # Include validation warnings even on success
    if validation["warnings"]:
        result["validation_warnings"] = validation["warnings"]

    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def optimize_strategy(
    strategy_code: str,
    param_grid: str,
    symbol: str = "BTC/USDT",
    timeframe: str = "4h",
    since: str = "2021-01-01",
    until: str = "2024-12-31",
    top_n: int = 5,
    initial_capital: float = 100_000,
    commission_pct: float = 0.001,
    slippage_pct: float = 0.0005,
) -> str:
    """Run grid search over parameter combinations. Returns top N by Sharpe.

    Only use on strategies that have already shown Sharpe > 0.3 with default params.

    Args:
        strategy_code: Strategy code string
        param_grid: JSON string mapping param names to value lists,
                    e.g. '{"rsi_period": [10, 14, 20], "threshold": [25, 30]}'
        top_n: Number of top results to return (default 5)
    """
    cache_dir = DATA_DIR / "data_cache"
    ohlcv = _fetch_ohlcv(
        symbol=symbol, timeframe=timeframe,
        since=since, until=until,
        cache_dir=cache_dir,
    )

    grid = json.loads(param_grid) if isinstance(param_grid, str) else param_grid

    results = _optimize(
        strategy_code=strategy_code,
        ohlcv_df=ohlcv,
        param_grid=grid,
        top_n=top_n,
        cash=initial_capital,
        commission=commission_pct,
        slippage=slippage_pct,
    )
    return json.dumps(results, indent=2, default=str)


@mcp.tool()
def save_strategy(
    name: str,
    code: str,
    hypothesis: str,
    experiment_type: str,
    symbol: str = "BTC/USDT",
    timeframe: str = "4h",
    market_concept: str = "",
    metrics: str = "{}",
    parent_name: str = "",
    tags: str = "[]",
) -> str:
    """Save a strategy and its backtest results to the database and as a .py file.

    Call this after running a backtest to persist the strategy. The strategy code
    is saved both to the database and as a standalone .py file in a strategies/
    directory within the plugin data folder, so strategies survive as real files.

    Args:
        name: Unique strategy name
        code: Full strategy source code
        hypothesis: What you expected and why
        experiment_type: "pivot", "refine", "mutate", or "sweep"
        market_concept: The market idea being tested (plain language)
        metrics: JSON string with full backtest results (from run_backtest output)
        parent_name: Name of parent strategy if refining/mutating
        tags: JSON string list of tags, e.g. '["momentum", "rsi"]'
    """
    parsed_metrics = json.loads(metrics) if isinstance(metrics, str) else metrics
    parsed_tags = json.loads(tags) if isinstance(tags, str) else tags

    parent_id = None
    generation = 0
    if parent_name:
        with get_session() as session:
            parent = session.exec(
                select(Strategy).where(Strategy.name == parent_name)
            ).first()
            if parent:
                parent_id = parent.id
                generation = parent.generation + 1

    with get_session() as session:
        strategy = Strategy(
            name=name,
            parent_id=parent_id,
            generation=generation,
            status=StrategyStatus.DONE,
            symbol=symbol,
            timeframe=timeframe,
            code=code,
            hypothesis=hypothesis,
            experiment_type=experiment_type,
            market_concept=market_concept,
            tags=parsed_tags,
            created_at=datetime.now(timezone.utc),
            ran_at=datetime.now(timezone.utc),
        )
        session.add(strategy)
        session.commit()
        session.refresh(strategy)
        strategy_id = strategy.id

    is_metrics = parsed_metrics.get("in_sample") or {}
    oos_metrics = parsed_metrics.get("out_of_sample") or {}
    degradation = parsed_metrics.get("oos_degradation") or {}
    trade_summary = parsed_metrics.get("trade_summary") or {}

    if is_metrics:
        with get_session() as session:
            result = BacktestResult(
                strategy_id=strategy_id,
                total_return_pct=is_metrics.get("total_return_pct", 0.0),
                buy_hold_return_pct=is_metrics.get("buy_hold_return_pct", 0.0),
                excess_return_pct=is_metrics.get("excess_return_pct", 0.0),
                cagr_pct=is_metrics.get("cagr_pct", 0.0),
                sharpe_ratio=is_metrics.get("sharpe_ratio", 0.0),
                sortino_ratio=is_metrics.get("sortino_ratio", 0.0),
                calmar_ratio=is_metrics.get("calmar_ratio", 0.0),
                max_drawdown_pct=is_metrics.get("max_drawdown_pct", 0.0),
                win_rate_pct=is_metrics.get("win_rate_pct", 0.0),
                profit_factor=is_metrics.get("profit_factor", 0.0),
                total_trades=is_metrics.get("total_trades", 0),
                avg_trade_duration_hours=is_metrics.get("avg_trade_duration_hours", 0.0),
                oos_total_return_pct=oos_metrics.get("total_return_pct", 0.0),
                oos_sharpe_ratio=oos_metrics.get("sharpe_ratio", 0.0),
                oos_max_drawdown_pct=oos_metrics.get("max_drawdown_pct", 0.0),
                oos_win_rate_pct=oos_metrics.get("win_rate_pct", 0.0),
                oos_total_trades=oos_metrics.get("total_trades", 0),
                oos_verdict=degradation.get("verdict", ""),
                train_period=parsed_metrics.get("train_period", ""),
                test_period=parsed_metrics.get("test_period", ""),
                trade_summary=trade_summary,
            )
            session.add(result)

    # Write strategy as a standalone .py file
    strategies_dir = DATA_DIR / "strategies"
    strategies_dir.mkdir(exist_ok=True)
    safe_name = name.replace(" ", "_").replace("/", "_")
    py_path = strategies_dir / f"{safe_name}.py"

    file_header = (
        f'"""\n'
        f"Strategy: {name}\n"
        f"Hypothesis: {hypothesis}\n"
        f"Experiment: {experiment_type}\n"
        f"Concept: {market_concept}\n"
        f"Symbol: {symbol} | Timeframe: {timeframe}\n"
    )
    if is_metrics:
        file_header += (
            f"IS Sharpe: {is_metrics.get('sharpe_ratio', 0):.2f} | "
            f"OOS Sharpe: {oos_metrics.get('sharpe_ratio', 0):.2f} | "
            f"OOS Verdict: {degradation.get('verdict', 'n/a')}\n"
        )
    if parent_name:
        file_header += f"Parent: {parent_name}\n"
    file_header += f'"""\n\n'

    py_path.write_text(file_header + code, encoding="utf-8")

    return json.dumps({
        "strategy_id": strategy_id,
        "saved": True,
        "file": str(py_path),
    }, indent=2)


@mcp.tool()
def list_strategies(
    status: str = "",
    sort_by: str = "sharpe",
    limit: int = 20,
    tag: str = "",
) -> str:
    """List strategies from the database with optional filtering and sorting.

    Args:
        status: Filter by status ("done", "error", "skipped"). Empty = all.
        sort_by: Sort order: "sharpe" (default), "return", "created"
        limit: Max results (default 20)
        tag: Filter to strategies with this tag
    """
    with get_session() as session:
        query = select(Strategy, BacktestResult).outerjoin(
            BacktestResult, BacktestResult.strategy_id == Strategy.id
        )

        if status:
            try:
                status_enum = StrategyStatus(status.lower())
                query = query.where(Strategy.status == status_enum)
            except ValueError:
                return json.dumps({"error": f"Unknown status: {status}"})

        rows = session.exec(query).all()

        if tag:
            rows = [(s, r) for s, r in rows if tag in (s.tags or [])]

        def sort_key(row):
            s, r = row
            if sort_by == "sharpe":
                return r.sharpe_ratio if r else -999
            elif sort_by == "return":
                return r.total_return_pct if r else -999
            else:
                return s.created_at.timestamp() if s.created_at else 0

        rows.sort(key=sort_key, reverse=True)
        rows = rows[:limit]

        results = []
        for s, r in rows:
            entry = {
                "name": s.name,
                "status": s.status.value,
                "experiment_type": s.experiment_type,
                "hypothesis": (s.hypothesis or "")[:120],
                "created_at": s.created_at.isoformat() if s.created_at else "",
            }
            if r:
                entry.update({
                    "sharpe_ratio": r.sharpe_ratio,
                    "total_return_pct": r.total_return_pct,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "total_trades": r.total_trades,
                    "oos_sharpe_ratio": r.oos_sharpe_ratio,
                    "oos_verdict": r.oos_verdict,
                })
            results.append(entry)

        return json.dumps(results, indent=2, default=str)


@mcp.tool()
def get_strategy(name: str) -> str:
    """Get full details of a strategy by name, including code and all metrics.

    Use this to retrieve the code of a successful strategy or to review
    what was tried in a previous experiment.
    """
    with get_session() as session:
        strategy = session.exec(
            select(Strategy).where(Strategy.name == name)
        ).first()

        if not strategy:
            return json.dumps({"error": f"Strategy '{name}' not found"})

        result = session.exec(
            select(BacktestResult).where(BacktestResult.strategy_id == strategy.id)
        ).first()

        parent_name = ""
        if strategy.parent_id:
            parent = session.get(Strategy, strategy.parent_id)
            if parent:
                parent_name = parent.name

        data = {
            "name": strategy.name,
            "code": strategy.code,
            "hypothesis": strategy.hypothesis,
            "experiment_type": strategy.experiment_type,
            "market_concept": strategy.market_concept,
            "parent_name": parent_name,
            "generation": strategy.generation,
            "symbol": strategy.symbol,
            "timeframe": strategy.timeframe,
            "tags": strategy.tags,
            "status": strategy.status.value,
            "created_at": strategy.created_at.isoformat() if strategy.created_at else "",
        }

        if result:
            data["in_sample"] = {
                "total_return_pct": result.total_return_pct,
                "sharpe_ratio": result.sharpe_ratio,
                "sortino_ratio": result.sortino_ratio,
                "calmar_ratio": result.calmar_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
                "win_rate_pct": result.win_rate_pct,
                "profit_factor": result.profit_factor,
                "total_trades": result.total_trades,
                "cagr_pct": result.cagr_pct,
                "buy_hold_return_pct": result.buy_hold_return_pct,
                "excess_return_pct": result.excess_return_pct,
                "avg_trade_duration_hours": result.avg_trade_duration_hours,
            }
            data["out_of_sample"] = {
                "total_return_pct": result.oos_total_return_pct,
                "sharpe_ratio": result.oos_sharpe_ratio,
                "max_drawdown_pct": result.oos_max_drawdown_pct,
                "win_rate_pct": result.oos_win_rate_pct,
                "total_trades": result.oos_total_trades,
            }
            data["oos_verdict"] = result.oos_verdict
            data["train_period"] = result.train_period
            data["test_period"] = result.test_period
            data["trade_summary"] = result.trade_summary

        return json.dumps(data, indent=2, default=str)


@mcp.tool()
def get_experiment_summary() -> str:
    """Get a high-level overview of the research session.

    Use this to quickly orient yourself: how many strategies tested,
    what's the best so far, what concepts have been explored.
    """
    with get_session() as session:
        all_strategies = session.exec(select(Strategy)).all()

        by_status = {}
        concepts = set()
        for s in all_strategies:
            status_val = s.status.value
            by_status[status_val] = by_status.get(status_val, 0) + 1
            if s.market_concept:
                concepts.add(s.market_concept)

        best = None
        best_rows = session.exec(
            select(Strategy, BacktestResult)
            .join(BacktestResult, BacktestResult.strategy_id == Strategy.id)
            .where(Strategy.status == StrategyStatus.DONE)
        ).all()

        if best_rows:
            best_row = max(
                best_rows,
                key=lambda r: r[1].oos_sharpe_ratio if r[1].oos_sharpe_ratio else r[1].sharpe_ratio,
            )
            bs, br = best_row
            best = {
                "name": bs.name,
                "sharpe_ratio": br.sharpe_ratio,
                "oos_sharpe_ratio": br.oos_sharpe_ratio,
                "total_return_pct": br.total_return_pct,
                "oos_verdict": br.oos_verdict,
            }

        recent = session.exec(
            select(Strategy, BacktestResult)
            .outerjoin(BacktestResult, BacktestResult.strategy_id == Strategy.id)
            .order_by(Strategy.created_at.desc())
            .limit(5)
        ).all()

        recent_list = []
        for s, r in recent:
            entry = {
                "name": s.name,
                "experiment_type": s.experiment_type,
                "hypothesis": (s.hypothesis or "")[:100],
                "status": s.status.value,
            }
            if r:
                entry["sharpe_ratio"] = r.sharpe_ratio
                entry["oos_sharpe_ratio"] = r.oos_sharpe_ratio
                entry["oos_verdict"] = r.oos_verdict
            recent_list.append(entry)

        summary = {
            "total_strategies": len(all_strategies),
            "strategies_by_status": by_status,
            "best_strategy": best,
            "recent_strategies": recent_list,
            "concepts_explored": sorted(concepts),
        }
        return json.dumps(summary, indent=2, default=str)


@mcp.tool()
def add_helper(
    function_name: str,
    function_code: str,
    description: str = "",
) -> str:
    """Add a reusable helper function that persists across all future backtests.

    The function is saved to custom_helpers.py and automatically loaded into the
    strategy exec namespace as part of `helpers`. Use this when you find yourself
    writing the same utility logic in multiple strategies.

    Requirements:
    - Function must accept numpy arrays or pandas Series
    - Function must return numpy arrays (for self.I() compatibility)
    - Available imports in custom_helpers: numpy (as np), pandas (as pd)
    - Function name must not collide with existing helpers

    Args:
        function_name: Name of the function (e.g. "funding_rate_signal")
        function_code: Complete Python function definition starting with "def ..."
        description: One-line description of what the function does
    """
    custom_path = DATA_DIR / "custom_helpers.py"

    # Validate syntax
    try:
        compile(function_code, f"<{function_name}>", "exec")
    except SyntaxError as e:
        return json.dumps({"error": f"Syntax error in function: {e}"})

    # Check for name collision with built-in helpers
    from backtester import helpers as builtin_helpers
    if hasattr(builtin_helpers, function_name):
        return json.dumps({
            "error": f"'{function_name}' already exists in built-in helpers. Choose a different name."
        })

    # Read existing custom helpers or start fresh
    if custom_path.exists():
        existing = custom_path.read_text(encoding="utf-8")
    else:
        existing = '"""Custom helper functions created by the experiment agent."""\nimport numpy as np\nimport pandas as pd\n'

    # Check if function already exists in custom helpers
    if f"def {function_name}(" in existing:
        # Replace existing function
        lines = existing.split("\n")
        new_lines = []
        skip = False
        for line in lines:
            if line.startswith(f"def {function_name}("):
                skip = True
                continue
            if skip and (line.startswith("def ") or (line.strip() == "" and not line.startswith(" "))):
                skip = False
            if not skip:
                new_lines.append(line)
        existing = "\n".join(new_lines)

    # Append new function
    header = f"\n\n# {description}\n" if description else "\n\n"
    custom_path.write_text(
        existing.rstrip() + header + function_code + "\n",
        encoding="utf-8",
    )

    return json.dumps({
        "saved": True,
        "function_name": function_name,
        "file": str(custom_path),
        "note": f"'{function_name}' is now available as helpers.{function_name}() in all future strategies.",
    }, indent=2)


@mcp.tool()
def get_market_info(
    symbol: str = "BTC/USDT",
    exchange_id: str = "binance",
) -> str:
    """Get detailed market information for a trading pair from the exchange.

    Returns contract details needed for realistic strategy design: fees, order types,
    contract type (spot/perpetual/futures), leverage limits, tick size, minimum order,
    collateral currency, funding rate info, and margin requirements.

    Use this BEFORE designing strategies to understand the real trading constraints.
    The agent should check market info to know:
    - What fees to model (maker/taker)
    - Whether perpetual funding rates matter for the timeframe
    - What leverage is available and whether to account for liquidation
    - What collateral currency is used (USD, USDT, BTC, etc.)
    - Minimum order sizes and tick sizes for realistic position sizing

    Args:
        symbol: Trading pair, e.g. "BTC/USDT", "ETH/USDT:USDT" (perpetual)
        exchange_id: Exchange to query, e.g. "binance", "bybit", "okx"
    """
    try:
        import ccxt
        exchange_class = getattr(ccxt, exchange_id, None)
        if not exchange_class:
            return json.dumps({"error": f"Unknown exchange: {exchange_id}"})

        exchange = exchange_class({"enableRateLimit": True})
        exchange.load_markets()

        # Try exact match first, then common variations
        market = None
        candidates = [symbol, f"{symbol}:USDT", f"{symbol}:USD"]
        for candidate in candidates:
            if candidate in exchange.markets:
                market = exchange.markets[candidate]
                break

        if not market:
            # List available symbols for this base
            base = symbol.split("/")[0] if "/" in symbol else symbol
            available = [s for s in exchange.markets if s.startswith(base)][:10]
            return json.dumps({
                "error": f"Symbol '{symbol}' not found on {exchange_id}",
                "available_matches": available,
            }, indent=2)

        # Extract comprehensive market details
        info = {
            "symbol": market.get("symbol", symbol),
            "exchange": exchange_id,
            "type": market.get("type", "unknown"),  # spot, swap, future, option
            "contract_type": market.get("subType", market.get("type", "")),  # linear, inverse, perpetual

            # Collateral & Settlement
            "base_currency": market.get("base", ""),
            "quote_currency": market.get("quote", ""),
            "settle_currency": market.get("settle", market.get("quote", "")),
            "collateral": market.get("settle", market.get("quote", "USDT")),
            "is_linear": market.get("linear", True),  # True = USDT-margined, False = coin-margined
            "is_inverse": market.get("inverse", False),

            # Fees
            "maker_fee": market.get("maker", 0.001),
            "taker_fee": market.get("taker", 0.001),
            "fee_note": "Maker fee applies to limit orders, taker fee to market orders. Model taker for conservative estimates.",

            # Contract specifications
            "contract_size": market.get("contractSize", 1),
            "tick_size": market.get("precision", {}).get("price", 0.01),
            "min_amount": market.get("limits", {}).get("amount", {}).get("min", 0),
            "min_cost": market.get("limits", {}).get("cost", {}).get("min", 0),
            "max_leverage": market.get("limits", {}).get("leverage", {}).get("max", 1),

            # Precision
            "price_precision": market.get("precision", {}).get("price", 2),
            "amount_precision": market.get("precision", {}).get("amount", 4),
        }

        # Perpetual-specific details
        if market.get("type") in ("swap", "future") or "PERP" in str(market.get("id", "")):
            info["is_perpetual"] = True
            info["funding_rate_note"] = (
                "Perpetual contracts have funding rates (typically every 8h). "
                "Positive rate = longs pay shorts, negative = shorts pay longs. "
                "For 4h+ timeframes, funding impact is small (~0.01-0.03% per 8h). "
                "For 1h or shorter, funding can erode edge significantly on leveraged positions."
            )
            info["leverage_note"] = (
                f"Max leverage: {info['max_leverage']}x. "
                f"Higher leverage amplifies returns AND drawdowns. "
                f"Liquidation price = entry +/- (1/leverage * entry) approximately. "
                f"For backtesting, model leverage as a multiplier on position size and "
                f"add a hard stop at the liquidation price."
            )
        else:
            info["is_perpetual"] = False
            info["leverage_note"] = "Spot market. No leverage, no funding rates, no liquidation risk."

        # Strategy design guidance based on market type
        if info["is_perpetual"]:
            info["strategy_considerations"] = {
                "fees": f"Use taker fee ({info['taker_fee']}) + slippage for conservative modeling",
                "funding": "Account for funding rates on positions held > 8 hours. Net impact is small for intraday but compounds for swing trades.",
                "leverage": "If using leverage, add liquidation price as a hard stop-loss. Reduce position size proportionally.",
                "collateral": f"Settled in {info['settle_currency']}. P&L is in {info['settle_currency']}.",
                "direction": "Can go long AND short. Short selling is native, no borrowing cost.",
            }
        else:
            info["strategy_considerations"] = {
                "fees": f"Use taker fee ({info['taker_fee']}) + slippage for conservative modeling",
                "direction": "Long only (or long-biased). Short requires margin borrowing which adds cost.",
                "collateral": f"Quote currency is {info['quote_currency']}.",
            }

        return json.dumps(info, indent=2, default=str)

    except ImportError:
        return json.dumps({
            "error": "ccxt not installed. Market info requires ccxt.",
            "fallback": {
                "typical_spot_fees": {"maker": 0.001, "taker": 0.001},
                "typical_perp_fees": {"maker": 0.0002, "taker": 0.0005},
                "note": "Use these defaults if exchange data unavailable.",
            },
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch market info: {e}"}, indent=2)


@mcp.tool()
def list_helpers() -> str:
    """List all available helper functions (built-in + custom).

    Shows function names and their docstrings so the agent knows what's available
    and doesn't reinvent existing utilities.
    """
    from backtester import helpers as builtin_helpers

    result = {"built_in": {}, "custom": {}}

    for name in sorted(dir(builtin_helpers)):
        if name.startswith("_") or name in ("np", "pd"):
            continue
        fn = getattr(builtin_helpers, name)
        if callable(fn):
            doc = (fn.__doc__ or "").split("\n")[0].strip()
            result["built_in"][name] = doc

    custom_path = DATA_DIR / "custom_helpers.py"
    if custom_path.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("custom_helpers", custom_path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            for name in sorted(dir(mod)):
                if name.startswith("_") or name in ("np", "pd"):
                    continue
                fn = getattr(mod, name)
                if callable(fn):
                    doc = (fn.__doc__ or "").split("\n")[0].strip()
                    result["custom"][name] = doc
        except Exception as e:
            result["custom_error"] = str(e)

    return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run()
