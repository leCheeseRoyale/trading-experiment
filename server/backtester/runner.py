import math
import traceback

import numpy as np
import pandas as pd
import pandas_ta as ta
from backtesting import Backtest, Strategy
from loguru import logger


def _run_single_backtest(
    strategy_code: str,
    ohlcv_df: pd.DataFrame,
    params: dict | None = None,
    cash: float = 100_000,
    commission: float = 0.001,
    slippage: float = 0.0005,
    trade_on_close: bool = True,
) -> dict:
    """Run a single backtest (no OOS split). Returns metrics dict."""
    if params is None:
        params = {}

    try:
        namespace = {"Strategy": Strategy, "pd": pd, "np": np, "ta": ta, "math": math}
        exec(strategy_code, namespace)

        strategy_cls = None
        for value in namespace.values():
            if isinstance(value, type) and issubclass(value, Strategy) and value is not Strategy:
                strategy_cls = value
                break

        if strategy_cls is None:
            raise ValueError("No Strategy subclass found in the provided code")

        effective_commission = commission + slippage

        bt = Backtest(
            ohlcv_df, strategy_cls,
            cash=cash,
            commission=effective_commission,
            exclusive_orders=True,
            trade_on_close=trade_on_close,
        )
        stats = bt.run(**params)

        equity_curve = stats["_equity_curve"]["Equity"].tolist()

        equity_series = stats["_equity_curve"]["Equity"]
        running_max = equity_series.cummax()
        drawdown_series = ((equity_series - running_max) / running_max * 100).tolist()

        trades_df = stats["_trades"]

        # Trade SUMMARY instead of full trade list (context-friendly)
        trade_summary = _summarize_trades(trades_df)

        raw_stats = {}
        for key, val in stats.items():
            if key.startswith("_"):
                continue
            try:
                if isinstance(val, (int, float, str, bool)):
                    raw_stats[key] = val
                elif isinstance(val, pd.Timestamp):
                    raw_stats[key] = val.isoformat()
                else:
                    raw_stats[key] = str(val)
            except Exception:
                raw_stats[key] = str(val)

        def safe_float(key: str, default: float = 0.0) -> float:
            val = stats.get(key)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return default
            return float(val)

        avg_duration_hours = 0.0
        if len(trades_df) > 0 and "Duration" in trades_df.columns:
            durations = trades_df["Duration"]
            avg_td = durations.mean()
            if pd.notna(avg_td):
                avg_duration_hours = avg_td.total_seconds() / 3600

        buy_hold_return = 0.0
        if len(ohlcv_df) > 1:
            first_close = ohlcv_df["Close"].iloc[0]
            last_close = ohlcv_df["Close"].iloc[-1]
            if first_close > 0:
                buy_hold_return = (last_close - first_close) / first_close * 100

        strategy_return = safe_float("Return [%]")

        return {
            "total_return_pct": strategy_return,
            "buy_hold_return_pct": buy_hold_return,
            "excess_return_pct": strategy_return - buy_hold_return,
            "cagr_pct": safe_float("Return (Ann.) [%]"),
            "sharpe_ratio": safe_float("Sharpe Ratio"),
            "sortino_ratio": safe_float("Sortino Ratio"),
            "calmar_ratio": safe_float("Calmar Ratio"),
            "max_drawdown_pct": safe_float("Max. Drawdown [%]"),
            "win_rate_pct": safe_float("Win Rate [%]"),
            "profit_factor": safe_float("Profit Factor"),
            "total_trades": int(stats.get("# Trades", 0)),
            "avg_trade_duration_hours": avg_duration_hours,
            "equity_curve": equity_curve,
            "drawdown_series": drawdown_series,
            "trade_summary": trade_summary,
            "raw_stats": raw_stats,
            "error": None,
        }

    except Exception as e:
        logger.error(f"Backtest failed: {e}\n{traceback.format_exc()}")
        return {
            "total_return_pct": 0.0, "buy_hold_return_pct": 0.0, "excess_return_pct": 0.0,
            "cagr_pct": 0.0, "sharpe_ratio": 0.0, "sortino_ratio": 0.0, "calmar_ratio": 0.0,
            "max_drawdown_pct": 0.0, "win_rate_pct": 0.0, "profit_factor": 0.0,
            "total_trades": 0, "avg_trade_duration_hours": 0.0,
            "equity_curve": [], "drawdown_series": [],
            "trade_summary": {}, "raw_stats": {},
            "error": f"{type(e).__name__}: {e}",
        }


def _summarize_trades(trades_df: pd.DataFrame) -> dict:
    """Summarize trades for context-efficient reporting."""
    if len(trades_df) == 0:
        return {"total": 0}

    pnl = trades_df["PnL"].values if "PnL" in trades_df.columns else []
    ret_pct = trades_df["ReturnPct"].values if "ReturnPct" in trades_df.columns else []

    winners = sum(1 for p in pnl if p > 0)
    losers = sum(1 for p in pnl if p < 0)

    win_returns = [r for r in ret_pct if r > 0] if len(ret_pct) > 0 else []
    loss_returns = [r for r in ret_pct if r < 0] if len(ret_pct) > 0 else []

    # Max consecutive wins/losses
    max_con_wins = 0
    max_con_losses = 0
    cur_wins = 0
    cur_losses = 0
    for p in pnl:
        if p > 0:
            cur_wins += 1
            cur_losses = 0
            max_con_wins = max(max_con_wins, cur_wins)
        elif p < 0:
            cur_losses += 1
            cur_wins = 0
            max_con_losses = max(max_con_losses, cur_losses)
        else:
            cur_wins = 0
            cur_losses = 0

    return {
        "total": len(trades_df),
        "winners": winners,
        "losers": losers,
        "avg_win_pct": float(np.mean(win_returns) * 100) if win_returns else 0.0,
        "avg_loss_pct": float(np.mean(loss_returns) * 100) if loss_returns else 0.0,
        "largest_win_pct": float(max(ret_pct) * 100) if len(ret_pct) > 0 else 0.0,
        "largest_loss_pct": float(min(ret_pct) * 100) if len(ret_pct) > 0 else 0.0,
        "max_consecutive_wins": max_con_wins,
        "max_consecutive_losses": max_con_losses,
    }


def run_backtest(
    strategy_code: str,
    ohlcv_df: pd.DataFrame,
    params: dict | None = None,
    cash: float = 100_000,
    commission: float = 0.001,
    slippage: float = 0.0005,
    trade_on_close: bool = True,
    validate_oos: bool = True,
    oos_split_ratio: float = 0.7,
) -> dict:
    """
    Run a backtest with optional out-of-sample validation.

    When validate_oos=True (default), splits data into train/test,
    runs both, and returns metrics for each plus an overfitting verdict.
    """
    if not validate_oos:
        result = _run_single_backtest(
            strategy_code, ohlcv_df, params, cash, commission, slippage, trade_on_close,
        )
        result["period"] = {
            "from": str(ohlcv_df.index[0].date()),
            "to": str(ohlcv_df.index[-1].date()),
        }
        return {"status": "error" if result["error"] else "success", **result}

    # Split data for OOS validation
    split_idx = int(len(ohlcv_df) * oos_split_ratio)
    if split_idx < 50 or (len(ohlcv_df) - split_idx) < 20:
        # Not enough data for meaningful split — run full period only
        result = _run_single_backtest(
            strategy_code, ohlcv_df, params, cash, commission, slippage, trade_on_close,
        )
        result["period"] = {
            "from": str(ohlcv_df.index[0].date()),
            "to": str(ohlcv_df.index[-1].date()),
        }
        result["oos_note"] = "Insufficient data for train/test split"
        return {"status": "error" if result["error"] else "success", **result}

    train_df = ohlcv_df.iloc[:split_idx]
    test_df = ohlcv_df.iloc[split_idx:]

    # Run in-sample
    is_result = _run_single_backtest(
        strategy_code, train_df, params, cash, commission, slippage, trade_on_close,
    )

    if is_result["error"]:
        return {"status": "error", **is_result}

    # Run out-of-sample
    oos_result = _run_single_backtest(
        strategy_code, test_df, params, cash, commission, slippage, trade_on_close,
    )

    # Compute overfitting verdict
    is_sharpe = is_result["sharpe_ratio"]
    oos_sharpe = oos_result["sharpe_ratio"] if not oos_result["error"] else 0.0

    if is_sharpe > 0:
        sharpe_drop_pct = ((is_sharpe - oos_sharpe) / is_sharpe) * 100
    else:
        sharpe_drop_pct = 0.0

    is_ret = is_result["total_return_pct"]
    oos_ret = oos_result["total_return_pct"] if not oos_result["error"] else 0.0
    if abs(is_ret) > 0.01:
        return_drop_pct = ((is_ret - oos_ret) / abs(is_ret)) * 100
    else:
        return_drop_pct = 0.0

    if sharpe_drop_pct < 30:
        verdict = "robust"
    elif sharpe_drop_pct < 60:
        verdict = "moderate_overfit"
    else:
        verdict = "severe_overfit"

    return {
        "status": "success",
        "in_sample": {
            **{k: v for k, v in is_result.items() if k not in ("equity_curve", "drawdown_series", "raw_stats", "error")},
            "period": {"from": str(train_df.index[0].date()), "to": str(train_df.index[-1].date())},
        },
        "out_of_sample": {
            **{k: v for k, v in oos_result.items() if k not in ("equity_curve", "drawdown_series", "raw_stats", "error")},
            "period": {"from": str(test_df.index[0].date()), "to": str(test_df.index[-1].date())},
        },
        "oos_degradation": {
            "sharpe_drop_pct": round(sharpe_drop_pct, 1),
            "return_drop_pct": round(return_drop_pct, 1),
            "verdict": verdict,
        },
        "equity_curve": is_result["equity_curve"],
        "drawdown_series": is_result["drawdown_series"],
        "raw_stats": is_result["raw_stats"],
        "error": None,
    }
