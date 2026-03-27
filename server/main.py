import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from sqlmodel import select

# Add server directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from backtester.data import fetch_ohlcv as _fetch_ohlcv
from backtester.runner import run_backtest as _run_backtest
from backtester.optimizer import optimize_strategy as _optimize_strategy
from config.defaults import DEFAULTS
from db.models import Strategy, StrategyStatus, BacktestResult
from db.session import get_session, init_db

# Configure logging
logger.remove()
logger.add(sys.stderr, level="WARNING")

server = Server("trading-lab")

# In-memory data cache (symbol+timeframe+dates -> DataFrame)
_data_cache: dict[str, "pd.DataFrame"] = {}


def _cache_key(symbol: str, timeframe: str, since: str, until: str) -> str:
    return f"{symbol}|{timeframe}|{since}|{until}"


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="fetch_ohlcv",
            description="Fetch OHLCV market data for a symbol/timeframe. Data is cached server-side for subsequent backtest calls.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "default": "BTC/USDT", "description": "Trading pair e.g. BTC/USDT"},
                    "timeframe": {"type": "string", "default": "4h", "description": "Candle timeframe: 1h, 4h, 1d"},
                    "since": {"type": "string", "default": "2021-01-01", "description": "Start date (YYYY-MM-DD)"},
                    "until": {"type": "string", "default": "2024-12-31", "description": "End date (YYYY-MM-DD)"},
                    "source": {"type": "string", "default": "auto", "description": "Data source: auto, yfinance, ccxt"},
                },
                "required": [],
            },
        ),
        Tool(
            name="run_backtest",
            description="Execute a backtesting.py Strategy subclass against OHLCV data. Returns in-sample and out-of-sample metrics with overfitting detection. Data must be fetched first via fetch_ohlcv.",
            inputSchema={
                "type": "object",
                "properties": {
                    "strategy_code": {"type": "string", "description": "Full Python source code for a backtesting.py Strategy subclass"},
                    "symbol": {"type": "string", "default": "BTC/USDT"},
                    "timeframe": {"type": "string", "default": "4h"},
                    "since": {"type": "string", "default": "2021-01-01"},
                    "until": {"type": "string", "default": "2024-12-31"},
                    "params": {"type": "object", "default": {}, "description": "Optional parameter overrides"},
                    "initial_capital": {"type": "number"},
                    "commission_pct": {"type": "number"},
                    "slippage_pct": {"type": "number"},
                    "trade_on_close": {"type": "boolean", "default": True},
                    "validate_oos": {"type": "boolean", "default": True, "description": "Enable train/test split for out-of-sample validation"},
                },
                "required": ["strategy_code"],
            },
        ),
        Tool(
            name="optimize_strategy",
            description="Grid search over parameter combinations for a strategy. Returns top N results sorted by Sharpe ratio.",
            inputSchema={
                "type": "object",
                "properties": {
                    "strategy_code": {"type": "string"},
                    "param_grid": {"type": "object", "description": "Parameter grid e.g. {\"rsi_period\": [10, 14, 20]}"},
                    "symbol": {"type": "string", "default": "BTC/USDT"},
                    "timeframe": {"type": "string", "default": "4h"},
                    "since": {"type": "string", "default": "2021-01-01"},
                    "until": {"type": "string", "default": "2024-12-31"},
                    "top_n": {"type": "integer", "default": 5},
                    "initial_capital": {"type": "number"},
                    "commission_pct": {"type": "number"},
                    "slippage_pct": {"type": "number"},
                },
                "required": ["strategy_code", "param_grid"],
            },
        ),
        Tool(
            name="save_strategy",
            description="Save a strategy and its backtest results to the database for tracking.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "code": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "experiment_type": {"type": "string", "description": "pivot, refine, mutate, or sweep"},
                    "symbol": {"type": "string", "default": "BTC/USDT"},
                    "timeframe": {"type": "string", "default": "4h"},
                    "since": {"type": "string", "default": "2021-01-01"},
                    "until": {"type": "string", "default": "2024-12-31"},
                    "metrics": {"type": "object", "description": "Backtest results to store"},
                    "parent_name": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "market_concept": {"type": "string"},
                },
                "required": ["name", "code", "hypothesis", "metrics"],
            },
        ),
        Tool(
            name="list_strategies",
            description="List strategies from the database with optional filtering and sorting.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter: DONE, ERROR, SKIPPED"},
                    "sort_by": {"type": "string", "default": "sharpe", "description": "Sort by: sharpe, return, created"},
                    "limit": {"type": "integer", "default": 20},
                    "tag": {"type": "string", "description": "Filter by tag"},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_strategy",
            description="Get full details of a single strategy including code, metrics, and trade summary.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Strategy name"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="get_experiment_summary",
            description="Get a high-level overview of the research session: total strategies, best result, concepts explored.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "fetch_ohlcv":
            return await _handle_fetch_ohlcv(arguments)
        elif name == "run_backtest":
            return await _handle_run_backtest(arguments)
        elif name == "optimize_strategy":
            return await _handle_optimize(arguments)
        elif name == "save_strategy":
            return await _handle_save_strategy(arguments)
        elif name == "list_strategies":
            return await _handle_list_strategies(arguments)
        elif name == "get_strategy":
            return await _handle_get_strategy(arguments)
        elif name == "get_experiment_summary":
            return await _handle_experiment_summary(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def _handle_fetch_ohlcv(args: dict):
    symbol = args.get("symbol", "BTC/USDT")
    timeframe = args.get("timeframe", "4h")
    since = args.get("since", "2021-01-01")
    until = args.get("until", "2024-12-31")
    source = args.get("source", "auto")

    key = _cache_key(symbol, timeframe, since, until)

    if key in _data_cache:
        df = _data_cache[key]
        return [TextContent(type="text", text=json.dumps({
            "status": "cached",
            "symbol": symbol,
            "timeframe": timeframe,
            "rows": len(df),
            "date_range": {"from": str(df.index[0].date()), "to": str(df.index[-1].date())},
        }))]

    df = _fetch_ohlcv(symbol=symbol, timeframe=timeframe, since=since, until=until, source=source)
    _data_cache[key] = df

    return [TextContent(type="text", text=json.dumps({
        "status": "fetched",
        "symbol": symbol,
        "timeframe": timeframe,
        "rows": len(df),
        "date_range": {"from": str(df.index[0].date()), "to": str(df.index[-1].date())},
    }))]


async def _handle_run_backtest(args: dict):
    symbol = args.get("symbol", "BTC/USDT")
    timeframe = args.get("timeframe", "4h")
    since = args.get("since", "2021-01-01")
    until = args.get("until", "2024-12-31")

    key = _cache_key(symbol, timeframe, since, until)
    if key not in _data_cache:
        df = _fetch_ohlcv(symbol=symbol, timeframe=timeframe, since=since, until=until)
        _data_cache[key] = df

    df = _data_cache[key]

    result = _run_backtest(
        strategy_code=args["strategy_code"],
        ohlcv_df=df,
        params=args.get("params"),
        cash=args.get("initial_capital", DEFAULTS["initial_capital"]),
        commission=args.get("commission_pct", DEFAULTS["commission_pct"]),
        slippage=args.get("slippage_pct", DEFAULTS["slippage_pct"]),
        trade_on_close=args.get("trade_on_close", DEFAULTS["trade_on_close"]),
        validate_oos=args.get("validate_oos", True),
    )

    # Don't send equity_curve/drawdown_series to save context
    result.pop("equity_curve", None)
    result.pop("drawdown_series", None)
    result.pop("raw_stats", None)

    return [TextContent(type="text", text=json.dumps(result, default=str))]


async def _handle_optimize(args: dict):
    symbol = args.get("symbol", "BTC/USDT")
    timeframe = args.get("timeframe", "4h")
    since = args.get("since", "2021-01-01")
    until = args.get("until", "2024-12-31")

    key = _cache_key(symbol, timeframe, since, until)
    if key not in _data_cache:
        df = _fetch_ohlcv(symbol=symbol, timeframe=timeframe, since=since, until=until)
        _data_cache[key] = df

    df = _data_cache[key]

    results = _optimize_strategy(
        strategy_code=args["strategy_code"],
        ohlcv_df=df,
        param_grid=args["param_grid"],
        top_n=args.get("top_n", 5),
        cash=args.get("initial_capital", DEFAULTS["initial_capital"]),
        commission=args.get("commission_pct", DEFAULTS["commission_pct"]),
        slippage=args.get("slippage_pct", DEFAULTS["slippage_pct"]),
    )

    return [TextContent(type="text", text=json.dumps(results, default=str))]


async def _handle_save_strategy(args: dict):
    parent_id = None
    generation = 0

    if args.get("parent_name"):
        with get_session() as session:
            parent = session.exec(
                select(Strategy).where(Strategy.name == args["parent_name"])
            ).first()
            if parent:
                parent_id = parent.id
                generation = parent.generation + 1

    metrics = args.get("metrics", {})

    with get_session() as session:
        strategy = Strategy(
            name=args["name"],
            parent_id=parent_id,
            generation=generation,
            status=StrategyStatus.DONE,
            symbol=args.get("symbol", "BTC/USDT"),
            timeframe=args.get("timeframe", "4h"),
            exchange="",
            date_from=datetime.strptime(args.get("since", "2021-01-01"), "%Y-%m-%d").date(),
            date_to=datetime.strptime(args.get("until", "2024-12-31"), "%Y-%m-%d").date(),
            parameters=args.get("params", {}),
            code=args["code"],
            hypothesis=args["hypothesis"],
            tags=args.get("tags", [args.get("experiment_type", "pivot")]),
            created_at=datetime.now(timezone.utc),
            ran_at=datetime.now(timezone.utc),
        )
        session.add(strategy)
        session.commit()
        session.refresh(strategy)

        # Extract metrics for BacktestResult
        # Handle both flat metrics and structured (in_sample/out_of_sample) metrics
        is_metrics = metrics.get("in_sample", metrics)
        oos_metrics = metrics.get("out_of_sample", {})
        oos_deg = metrics.get("oos_degradation", {})

        result = BacktestResult(
            strategy_id=strategy.id,
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
            oos_verdict=oos_deg.get("verdict", ""),
            train_period=str(is_metrics.get("period", {}).get("from", "")) + " to " + str(is_metrics.get("period", {}).get("to", "")) if is_metrics.get("period") else "",
            test_period=str(oos_metrics.get("period", {}).get("from", "")) + " to " + str(oos_metrics.get("period", {}).get("to", "")) if oos_metrics.get("period") else "",
        )
        session.add(result)

        return [TextContent(type="text", text=json.dumps({
            "saved": True,
            "strategy_id": strategy.id,
            "name": strategy.name,
        }))]


async def _handle_list_strategies(args: dict):
    with get_session() as session:
        query = select(Strategy, BacktestResult).outerjoin(
            BacktestResult, BacktestResult.strategy_id == Strategy.id
        )

        status = args.get("status")
        if status:
            query = query.where(Strategy.status == StrategyStatus(status))

        tag = args.get("tag")
        # Tag filtering would need JSON contains — skip for SQLite simplicity

        sort_by = args.get("sort_by", "sharpe")
        if sort_by == "return":
            query = query.order_by(BacktestResult.total_return_pct.desc())
        elif sort_by == "created":
            query = query.order_by(Strategy.created_at.desc())
        else:
            query = query.order_by(BacktestResult.sharpe_ratio.desc())

        limit = args.get("limit", 20)
        rows = session.exec(query.limit(limit)).all()

        strategies = []
        for s, r in rows:
            strategies.append({
                "name": s.name,
                "status": s.status.value,
                "sharpe_ratio": r.sharpe_ratio if r else None,
                "total_return_pct": r.total_return_pct if r else None,
                "max_drawdown_pct": r.max_drawdown_pct if r else None,
                "total_trades": r.total_trades if r else None,
                "oos_verdict": r.oos_verdict if r else None,
                "oos_sharpe": r.oos_sharpe_ratio if r else None,
                "hypothesis": (s.hypothesis or "")[:120],
                "created_at": s.created_at.isoformat() if s.created_at else None,
            })

        return [TextContent(type="text", text=json.dumps(strategies, default=str))]


async def _handle_get_strategy(args: dict):
    with get_session() as session:
        strat = session.exec(
            select(Strategy).where(Strategy.name == args["name"])
        ).first()

        if not strat:
            return [TextContent(type="text", text=json.dumps({"error": f"Strategy '{args['name']}' not found"}))]

        result = session.exec(
            select(BacktestResult).where(BacktestResult.strategy_id == strat.id)
        ).first()

        parent_name = None
        if strat.parent_id:
            parent = session.get(Strategy, strat.parent_id)
            parent_name = parent.name if parent else None

        data = {
            "name": strat.name,
            "code": strat.code,
            "hypothesis": strat.hypothesis,
            "experiment_type": strat.tags[0] if strat.tags else "",
            "market_concept": (strat.parameters or {}).get("market_concept", ""),
            "parent_name": parent_name,
            "generation": strat.generation,
            "symbol": strat.symbol,
            "timeframe": strat.timeframe,
            "tags": strat.tags,
            "status": strat.status.value,
            "created_at": strat.created_at.isoformat() if strat.created_at else None,
        }

        if result:
            data["metrics"] = {
                "in_sample": {
                    "total_return_pct": result.total_return_pct,
                    "sharpe_ratio": result.sharpe_ratio,
                    "sortino_ratio": result.sortino_ratio,
                    "calmar_ratio": result.calmar_ratio,
                    "max_drawdown_pct": result.max_drawdown_pct,
                    "win_rate_pct": result.win_rate_pct,
                    "profit_factor": result.profit_factor,
                    "total_trades": result.total_trades,
                    "avg_trade_duration_hours": result.avg_trade_duration_hours,
                    "cagr_pct": result.cagr_pct,
                    "buy_hold_return_pct": result.buy_hold_return_pct,
                    "excess_return_pct": result.excess_return_pct,
                },
                "out_of_sample": {
                    "total_return_pct": result.oos_total_return_pct,
                    "sharpe_ratio": result.oos_sharpe_ratio,
                    "max_drawdown_pct": result.oos_max_drawdown_pct,
                    "win_rate_pct": result.oos_win_rate_pct,
                    "total_trades": result.oos_total_trades,
                },
                "oos_verdict": result.oos_verdict,
                "train_period": result.train_period,
                "test_period": result.test_period,
            }
            data["trade_summary"] = result.trades if isinstance(result.trades, dict) else {}

        return [TextContent(type="text", text=json.dumps(data, default=str))]


async def _handle_experiment_summary(args: dict):
    with get_session() as session:
        all_strategies = session.exec(select(Strategy)).all()

        if not all_strategies:
            return [TextContent(type="text", text=json.dumps({
                "total_strategies": 0,
                "message": "No experiments yet. Use run_backtest to start.",
            }))]

        by_status = {}
        concepts = set()
        for s in all_strategies:
            by_status[s.status.value] = by_status.get(s.status.value, 0) + 1
            mc = (s.parameters or {}).get("market_concept", "")
            if mc:
                concepts.add(mc)

        # Best strategy by Sharpe
        best_row = session.exec(
            select(Strategy, BacktestResult)
            .join(BacktestResult, BacktestResult.strategy_id == Strategy.id)
            .where(Strategy.status == StrategyStatus.DONE)
            .order_by(BacktestResult.sharpe_ratio.desc())
            .limit(1)
        ).first()

        best = None
        if best_row:
            s, r = best_row
            best = {
                "name": s.name,
                "sharpe_ratio": r.sharpe_ratio,
                "total_return_pct": r.total_return_pct,
                "oos_verdict": r.oos_verdict,
                "oos_sharpe": r.oos_sharpe_ratio,
            }

        # Recent 5
        recent_rows = session.exec(
            select(Strategy, BacktestResult)
            .outerjoin(BacktestResult, BacktestResult.strategy_id == Strategy.id)
            .order_by(Strategy.created_at.desc())
            .limit(5)
        ).all()

        recent = []
        for s, r in recent_rows:
            recent.append({
                "name": s.name,
                "status": s.status.value,
                "sharpe": r.sharpe_ratio if r else None,
                "return_pct": r.total_return_pct if r else None,
                "oos_verdict": r.oos_verdict if r else None,
                "hypothesis": (s.hypothesis or "")[:100],
            })

        return [TextContent(type="text", text=json.dumps({
            "total_strategies": len(all_strategies),
            "strategies_by_status": by_status,
            "best_strategy": best,
            "recent_strategies": recent,
            "concepts_explored": list(concepts),
        }, default=str))]


async def main():
    init_db()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
