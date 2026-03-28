import math
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy
from loguru import logger


def _extract_metrics(stats, ohlcv_df: pd.DataFrame) -> dict:
    def safe_float(key: str, default: float = 0.0) -> float:
        val = stats.get(key)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return default
        return float(val)

    avg_duration_hours = 0.0
    trades_df = stats["_trades"]
    if len(trades_df) > 0 and "Duration" in trades_df.columns:
        avg_td = trades_df["Duration"].mean()
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
    }


def _build_trade_summary(stats) -> dict:
    trades_df = stats["_trades"]
    if len(trades_df) == 0:
        return {"total": 0, "winners": 0, "losers": 0}

    pnl = trades_df["PnL"].values
    winners = pnl[pnl > 0]
    losers = pnl[pnl < 0]

    signs = np.sign(pnl)
    max_consec_wins = 0
    max_consec_losses = 0
    current_run = 0
    current_sign = 0
    for s in signs:
        if s == current_sign:
            current_run += 1
        else:
            current_sign = s
            current_run = 1
        if s > 0:
            max_consec_wins = max(max_consec_wins, current_run)
        elif s < 0:
            max_consec_losses = max(max_consec_losses, current_run)

    entry_prices = trades_df["EntryPrice"].values
    pnl_pct = (pnl / entry_prices) * 100 if len(entry_prices) > 0 else np.array([])
    winners_pct = pnl_pct[pnl_pct > 0]
    losers_pct = pnl_pct[pnl_pct < 0]

    return {
        "total": int(len(pnl)),
        "winners": int(len(winners)),
        "losers": int(len(losers)),
        "avg_win_pct": float(np.mean(winners_pct)) if len(winners_pct) > 0 else 0.0,
        "avg_loss_pct": float(np.mean(losers_pct)) if len(losers_pct) > 0 else 0.0,
        "largest_win_pct": float(np.max(winners_pct)) if len(winners_pct) > 0 else 0.0,
        "largest_loss_pct": float(np.min(losers_pct)) if len(losers_pct) > 0 else 0.0,
        "max_consecutive_wins": max_consec_wins,
        "max_consecutive_losses": max_consec_losses,
    }


def _get_equity_and_drawdown(stats) -> tuple[list, list]:
    equity = stats["_equity_curve"]["Equity"].tolist()
    eq_series = stats["_equity_curve"]["Equity"]
    running_max = eq_series.cummax()
    dd = ((eq_series - running_max) / running_max * 100).tolist()
    return equity, dd


def validate_strategy_code(code: str) -> dict:
    """Pre-validate strategy code for common issues before running a backtest.

    Returns {"valid": True} or {"valid": False, "errors": [...], "warnings": [...]}.
    """
    errors = []
    warnings = []

    # 1. Syntax check
    try:
        compile(code, "<strategy>", "exec")
    except SyntaxError as e:
        return {"valid": False, "errors": [f"Syntax error on line {e.lineno}: {e.msg}"], "warnings": []}

    # 2. Must contain a class that looks like a Strategy subclass
    if "class " not in code or "Strategy" not in code:
        errors.append("No Strategy subclass found. Code must define a class inheriting from Strategy.")

    # 3. Must have init and next methods
    if "def init(self" not in code:
        errors.append("Missing init(self) method. Strategy must define init() for indicator setup.")
    if "def next(self" not in code:
        errors.append("Missing next(self) method. Strategy must define next() for trading logic.")

    # 4. Check for self.I() usage — indicators must be wrapped
    import re
    # Find computations in init that look like indicators but aren't wrapped in self.I()
    init_match = re.search(r'def init\(self.*?\n(.*?)(?=\n    def |\nclass |\Z)', code, re.DOTALL)
    if init_match:
        init_body = init_match.group(1)
        for line in init_body.split("\n"):
            m = re.match(r'\s*self\.(\w+)\s*=\s*(.*)', line)
            if not m:
                continue
            attr, value = m.group(1), m.group(2).strip()
            if value.startswith("self.I("):
                continue
            if attr.startswith("_"):
                continue
            if re.match(r'^[\d.]+$', value) or value in ("0", "True", "False", "None", "[]", "{}"):
                continue
            if "(" in value or "rolling" in value or ".mean" in value or ".std" in value:
                warnings.append(
                    f"self.{attr} may need self.I() wrapping. "
                    f"Indicator computations in init() must use self.I() to prevent lookahead bias."
                )

    # 5. Check for lookahead patterns in next()
    next_match = re.search(r'def next\(self.*?\n(.*?)(?=\n    def |\nclass |\Z)', code, re.DOTALL)
    if next_match:
        next_body = next_match.group(1)
        # Positive indexing on self.data suggests lookahead
        if re.search(r'self\.data\.\w+\[\d+\]', next_body):
            errors.append(
                "Possible lookahead bias: positive indexing on self.data in next(). "
                "Use negative indices like self.data.Close[-1] for current bar."
            )
        # .iloc usage in next()
        if ".iloc[" in next_body:
            warnings.append(
                "Using .iloc in next() can cause index misalignment. "
                "Use negative indexing on self.I()-wrapped indicators instead: self.my_indicator[-1]"
            )

    # 6. Check for hardcoded dates or symbols
    if re.search(r'20\d{2}-\d{2}-\d{2}', code):
        warnings.append("Hardcoded date found in strategy code. Dates should come from backtest parameters.")
    if re.search(r'["\']BTC|ETH|SOL|USDT["\']', code) and "# " not in code.split("BTC")[0][-20:]:
        warnings.append("Possible hardcoded symbol. Strategies should be symbol-agnostic.")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def _load_custom_helpers():
    """Load user-defined custom helpers if the file exists."""
    custom_path = Path(__file__).resolve().parent.parent.parent / "data" / "custom_helpers.py"
    if not custom_path.exists():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("custom_helpers", custom_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exec_strategy(strategy_code: str):
    import pandas_ta as ta
    from backtester import helpers

    # Merge custom helpers into the helpers namespace
    custom = _load_custom_helpers()
    if custom:
        for name in dir(custom):
            if not name.startswith("_"):
                setattr(helpers, name, getattr(custom, name))

    namespace = {
        "Strategy": Strategy,
        "pd": pd,
        "np": np,
        "ta": ta,
        "math": math,
        "helpers": helpers,
    }
    exec(strategy_code, namespace)
    for value in namespace.values():
        if isinstance(value, type) and issubclass(value, Strategy) and value is not Strategy:
            return value
    raise ValueError("No Strategy subclass found in the provided code")


def _run_single(strategy_cls, ohlcv_df, params, cash, effective_commission, trade_on_close):
    bt = Backtest(
        ohlcv_df, strategy_cls,
        cash=cash,
        commission=effective_commission,
        exclusive_orders=True,
        trade_on_close=trade_on_close,
    )
    stats = bt.run(**(params or {}), finalize_trades=True)
    metrics = _extract_metrics(stats, ohlcv_df)
    trade_summary = _build_trade_summary(stats)
    return stats, metrics, trade_summary


def _oos_verdict(is_sharpe: float, oos_sharpe: float) -> dict:
    if is_sharpe <= 0:
        drop_pct = 100.0 if oos_sharpe < is_sharpe else 0.0
    else:
        drop_pct = max(0.0, (is_sharpe - oos_sharpe) / is_sharpe * 100)

    if drop_pct < 20:
        verdict = "robust"
    elif drop_pct < 50:
        verdict = "moderate_overfit"
    else:
        verdict = "severe_overfit"

    return {
        "sharpe_drop_pct": round(drop_pct, 1),
        "return_drop_pct": 0.0,
        "verdict": verdict,
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
    if params is None:
        params = {}
    effective_commission = commission + slippage

    try:
        strategy_cls = _exec_strategy(strategy_code)

        if validate_oos and len(ohlcv_df) > 100:
            split_idx = int(len(ohlcv_df) * oos_split_ratio)
            train_df = ohlcv_df.iloc[:split_idx]
            test_df = ohlcv_df.iloc[split_idx:]

            train_period = f"{train_df.index[0].strftime('%Y-%m-%d')} to {train_df.index[-1].strftime('%Y-%m-%d')}"
            test_period = f"{test_df.index[0].strftime('%Y-%m-%d')} to {test_df.index[-1].strftime('%Y-%m-%d')}"

            is_stats, is_metrics, is_trade_summary = _run_single(
                strategy_cls, train_df, params, cash, effective_commission, trade_on_close
            )
            is_metrics["period"] = {
                "from": train_df.index[0].strftime("%Y-%m-%d"),
                "to": train_df.index[-1].strftime("%Y-%m-%d"),
            }

            oos_stats, oos_metrics, oos_trade_summary = _run_single(
                strategy_cls, test_df, params, cash, effective_commission, trade_on_close
            )
            oos_metrics["period"] = {
                "from": test_df.index[0].strftime("%Y-%m-%d"),
                "to": test_df.index[-1].strftime("%Y-%m-%d"),
            }

            full_stats, _, _ = _run_single(
                strategy_cls, ohlcv_df, params, cash, effective_commission, trade_on_close
            )
            equity_curve, drawdown_series = _get_equity_and_drawdown(full_stats)

            degradation = _oos_verdict(is_metrics["sharpe_ratio"], oos_metrics["sharpe_ratio"])
            is_ret = is_metrics["total_return_pct"]
            oos_ret = oos_metrics["total_return_pct"]
            if is_ret > 0:
                degradation["return_drop_pct"] = round(
                    max(0.0, (is_ret - oos_ret) / is_ret * 100), 1
                )

            return {
                "status": "success",
                "error": None,
                "in_sample": is_metrics,
                "out_of_sample": oos_metrics,
                "oos_degradation": degradation,
                "trade_summary": is_trade_summary,
                "oos_trade_summary": oos_trade_summary,
                "equity_curve": equity_curve,
                "drawdown_series": drawdown_series,
                "train_period": train_period,
                "test_period": test_period,
            }
        else:
            stats, metrics, trade_summary = _run_single(
                strategy_cls, ohlcv_df, params, cash, effective_commission, trade_on_close
            )
            equity_curve, drawdown_series = _get_equity_and_drawdown(stats)
            metrics["period"] = {
                "from": ohlcv_df.index[0].strftime("%Y-%m-%d"),
                "to": ohlcv_df.index[-1].strftime("%Y-%m-%d"),
            }

            return {
                "status": "success",
                "error": None,
                "in_sample": metrics,
                "out_of_sample": None,
                "oos_degradation": None,
                "trade_summary": trade_summary,
                "equity_curve": equity_curve,
                "drawdown_series": drawdown_series,
            }

    except Exception as e:
        logger.error(f"Backtest failed: {e}\n{traceback.format_exc()}")
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "in_sample": None,
            "out_of_sample": None,
            "oos_degradation": None,
            "trade_summary": None,
            "equity_curve": [],
            "drawdown_series": [],
        }
