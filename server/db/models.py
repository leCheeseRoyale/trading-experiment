import enum
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field


class StrategyStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


class Strategy(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    parent_id: Optional[int] = Field(default=None, foreign_key="strategy.id")
    generation: int = 0
    status: StrategyStatus = StrategyStatus.PENDING
    symbol: str = "BTC/USDT"
    timeframe: str = "4h"
    exchange: str = "binance"
    date_from: date = Field(default_factory=lambda: date(2021, 1, 1))
    date_to: date = Field(default_factory=lambda: date(2024, 12, 31))
    parameters: dict = Field(default_factory=dict, sa_column=Column(JSON))
    code: str = ""
    hypothesis: str = ""
    experiment_type: str = ""
    market_concept: str = ""
    tags: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ran_at: Optional[datetime] = None


class BacktestResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    strategy_id: int = Field(foreign_key="strategy.id", unique=True)

    # In-sample metrics
    total_return_pct: float = 0.0
    buy_hold_return_pct: float = 0.0
    excess_return_pct: float = 0.0
    cagr_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_trade_duration_hours: float = 0.0

    # Out-of-sample metrics
    oos_total_return_pct: float = 0.0
    oos_sharpe_ratio: float = 0.0
    oos_max_drawdown_pct: float = 0.0
    oos_win_rate_pct: float = 0.0
    oos_total_trades: int = 0
    oos_verdict: str = ""
    train_period: str = ""
    test_period: str = ""

    # Serialized data
    equity_curve: list = Field(default_factory=list, sa_column=Column(JSON))
    drawdown_series: list = Field(default_factory=list, sa_column=Column(JSON))
    trades: list = Field(default_factory=list, sa_column=Column(JSON))
    raw_stats: dict = Field(default_factory=dict, sa_column=Column(JSON))
    trade_summary: dict = Field(default_factory=dict, sa_column=Column(JSON))

    error_log: Optional[str] = None
