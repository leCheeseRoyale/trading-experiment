import itertools
import traceback

import pandas as pd
from backtesting import Backtest, Strategy
from loguru import logger
from backtester.runner import _run_single_backtest


def optimize_strategy(
    strategy_code: str,
    ohlcv_df: pd.DataFrame,
    param_grid: dict[str, list],
    top_n: int = 5,
    cash: float = 100_000,
    commission: float = 0.001,
    slippage: float = 0.0005,
    trade_on_close: bool = True,
) -> list[dict]:
    """
    Grid search over parameter combinations.
    Returns top_n results sorted by Sharpe ratio descending.
    """
    try:
        param_names = list(param_grid.keys())
        combos = list(itertools.product(*[param_grid[k] for k in param_names]))

        all_results = []
        for combo in combos:
            params = dict(zip(param_names, combo))
            try:
                result = _run_single_backtest(
                    strategy_code, ohlcv_df, params, cash, commission, slippage, trade_on_close,
                )
                if result["error"]:
                    continue
                all_results.append({
                    "params": params,
                    "sharpe_ratio": result["sharpe_ratio"],
                    "total_return_pct": result["total_return_pct"],
                    "max_drawdown_pct": result["max_drawdown_pct"],
                    "total_trades": result["total_trades"],
                    "win_rate_pct": result["win_rate_pct"],
                })
            except Exception as e:
                logger.warning(f"Combo {params} failed: {e}")
                continue

        all_results.sort(key=lambda r: r["sharpe_ratio"], reverse=True)
        return all_results[:top_n]

    except Exception as e:
        logger.error(f"Optimization failed: {e}\n{traceback.format_exc()}")
        return []
