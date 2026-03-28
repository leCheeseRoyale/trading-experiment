import itertools
import traceback

import pandas as pd
from backtesting import Backtest, Strategy
from loguru import logger

from backtester.runner import _exec_strategy


def optimize_strategy(
    strategy_code: str,
    ohlcv_df: pd.DataFrame,
    param_grid: dict[str, list],
    top_n: int = 5,
    cash: float = 100_000,
    commission: float = 0.001,
    slippage: float = 0.0005,
) -> list[dict]:
    effective_commission = commission + slippage
    try:
        strategy_cls = _exec_strategy(strategy_code)
        bt = Backtest(
            ohlcv_df, strategy_cls,
            cash=cash,
            commission=effective_commission,
            exclusive_orders=True,
        )

        all_results = []
        param_names = list(param_grid.keys())
        combos = list(itertools.product(*[param_grid[k] for k in param_names]))

        for combo in combos:
            params = dict(zip(param_names, combo))
            try:
                combo_stats = bt.run(**params)
                sharpe = combo_stats.get("Sharpe Ratio", 0.0)
                if pd.isna(sharpe):
                    sharpe = 0.0
                all_results.append({
                    "params": params,
                    "sharpe_ratio": float(sharpe),
                    "total_return_pct": float(combo_stats.get("Return [%]", 0.0)),
                    "max_drawdown_pct": float(combo_stats.get("Max. Drawdown [%]", 0.0)),
                    "total_trades": int(combo_stats.get("# Trades", 0)),
                })
            except Exception as e:
                logger.warning(f"Combo {params} failed: {e}")
                continue

        all_results.sort(key=lambda r: r["sharpe_ratio"], reverse=True)
        return all_results[:top_n]

    except Exception as e:
        logger.error(f"Optimization failed: {e}\n{traceback.format_exc()}")
        return []
