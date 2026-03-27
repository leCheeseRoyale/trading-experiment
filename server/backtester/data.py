from pathlib import Path

import pandas as pd
from loguru import logger


_YF_INTERVALS = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h",
    "1d": "1d", "1w": "1wk", "1M": "1mo",
}

_YF_TICKERS = {
    "BTC/USDT": "BTC-USD", "BTC/USD": "BTC-USD",
    "ETH/USDT": "ETH-USD", "ETH/USD": "ETH-USD",
    "SOL/USDT": "SOL-USD", "SOL/USD": "SOL-USD",
    "BNB/USDT": "BNB-USD", "BNB/USD": "BNB-USD",
    "ADA/USDT": "ADA-USD", "ADA/USD": "ADA-USD",
    "XRP/USDT": "XRP-USD", "XRP/USD": "XRP-USD",
    "DOGE/USDT": "DOGE-USD", "DOGE/USD": "DOGE-USD",
    "DOT/USDT": "DOT-USD", "DOT/USD": "DOT-USD",
    "AVAX/USDT": "AVAX-USD", "AVAX/USD": "AVAX-USD",
    "LINK/USDT": "LINK-USD", "LINK/USD": "LINK-USD",
}

# Default cache directory: <plugin_root>/data/data_cache/
_DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "data_cache"


def _cache_path(symbol: str, timeframe: str, source: str, since: str, until: str, cache_dir: Path) -> Path:
    """Build parquet cache file path from parameters."""
    safe_symbol = symbol.replace("/", "-")
    return cache_dir / f"{source}_{safe_symbol}_{timeframe}_{since}_{until}.parquet"


def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h OHLCV data to 4h candles."""
    return df.resample("4h").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna()


def fetch_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "4h",
    since: str = "2021-01-01",
    until: str = "2024-12-31",
    exchange_id: str = "binance",
    cache_dir: Path | None = None,
    exchange_instance: object | None = None,
    source: str = "auto",
) -> pd.DataFrame:
    """
    Fetch OHLCV data with parquet caching.

    Tries sources in this order based on `source` param:
    - "auto": Try yfinance first (free, reliable), fall back to ccxt
    - "yfinance": Yahoo Finance only (free, no API key, good for daily/4h)
    - "ccxt": Exchange API via ccxt (needs working exchange access)

    Returns
    -------
    pd.DataFrame
        DataFrame with DatetimeIndex and columns: Open, High, Low, Close, Volume.
    """
    if cache_dir is None:
        cache_dir = _DEFAULT_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    if exchange_instance is not None:
        cache_file = _cache_path(symbol, timeframe, "ccxt", since, until, cache_dir)
        if cache_file.exists():
            logger.info(f"Loading cached data from {cache_file}")
            return pd.read_parquet(cache_file)
        return _fetch_from_ccxt_instance(
            exchange_instance, symbol, timeframe, since, until, cache_file,
        )

    actual_source = _resolve_source(source, symbol, timeframe, since)

    cache_file = _cache_path(symbol, timeframe, actual_source, since, until, cache_dir)
    if cache_file.exists():
        logger.info(f"Loading cached data from {cache_file}")
        return pd.read_parquet(cache_file)

    if actual_source == "yfinance":
        df = _fetch_from_yfinance(symbol, timeframe, since, until)
    else:
        df = _fetch_from_ccxt(exchange_id, symbol, timeframe, since, until)

    df = _normalize_ohlcv(df, since, until)

    if len(df) > 0:
        logger.info(f"Caching {len(df)} candles to {cache_file}")
        df.to_parquet(cache_file)
    return df


def _resolve_source(source: str, symbol: str, timeframe: str, since: str) -> str:
    """Decide which data source to actually use."""
    if source == "yfinance":
        return "yfinance"
    if source == "ccxt":
        return "ccxt"
    if symbol in _YF_TICKERS:
        return "yfinance"
    return "ccxt"


def _normalize_ohlcv(df: pd.DataFrame, since: str, until: str) -> pd.DataFrame:
    """Ensure UTC timezone, filter to date range, sort, and clean index."""
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df.index.name = None
    df = df[df.index >= pd.Timestamp(since, tz="UTC")]
    df = df[df.index <= pd.Timestamp(until, tz="UTC")]
    df = df[~df.index.duplicated(keep="first")]
    return df.sort_index()


def _fetch_from_yfinance(symbol: str, timeframe: str, since: str, until: str) -> pd.DataFrame:
    """Fetch OHLCV data from Yahoo Finance via yfinance."""
    import yfinance as yf

    ticker = _YF_TICKERS.get(symbol)
    if ticker is None:
        ticker = f"{symbol.split('/')[0]}-USD"

    needs_resample = (timeframe == "4h")

    if needs_resample:
        from datetime import datetime as _dt
        days_back = (_dt.now() - _dt.fromisoformat(since)).days
        if days_back > 720:
            logger.info(f"Range too old for hourly data ({days_back}d). Using daily candles.")
            yf_interval = "1d"
            needs_resample = False
        else:
            yf_interval = "1h"
    else:
        yf_interval = _YF_INTERVALS.get(timeframe, "1d")

    logger.info(f"Fetching {symbol} ({ticker}) {timeframe} from Yahoo Finance (interval={yf_interval})...")

    data = yf.download(
        ticker,
        start=since,
        end=until,
        interval=yf_interval,
        auto_adjust=True,
        progress=False,
    )

    if data.empty:
        raise ValueError(f"No data returned from yfinance for {ticker}")

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    df = data[["Open", "High", "Low", "Close", "Volume"]].copy()

    if needs_resample:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = _resample_to_4h(df)

    logger.info(f"Got {len(df)} candles from Yahoo Finance")
    return df


def _fetch_from_ccxt(
    exchange_id: str, symbol: str, timeframe: str,
    since: str, until: str,
) -> pd.DataFrame:
    """Fetch OHLCV data from a ccxt exchange."""
    import ccxt

    logger.info(f"Fetching {symbol} {timeframe} from {exchange_id} via ccxt...")

    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({"enableRateLimit": True})

    return _fetch_from_ccxt_instance(exchange, symbol, timeframe, since, until)


def _fetch_from_ccxt_instance(
    exchange, symbol: str, timeframe: str,
    since: str, until: str, cache_file: Path | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV using a ccxt exchange instance."""
    since_ts = int(pd.Timestamp(since).timestamp() * 1000)
    until_ts = int(pd.Timestamp(until).timestamp() * 1000)

    all_candles = []
    current_since = since_ts

    while current_since < until_ts:
        candles = exchange.fetch_ohlcv(symbol, timeframe, since=current_since, limit=1000)
        if not candles:
            break

        all_candles.extend(candles)
        last_ts = candles[-1][0]

        if last_ts >= until_ts:
            break
        if last_ts <= current_since:
            break

        current_since = last_ts + 1

    if not all_candles:
        raise ValueError(f"No data returned for {symbol} {timeframe} from ccxt")

    df = pd.DataFrame(all_candles, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)

    if cache_file is not None:
        df = df[~df.index.duplicated(keep="first")]
        df = df[df.index <= pd.Timestamp(until, tz="UTC")]
        df = df.sort_index()
        df.index.name = None
        logger.info(f"Caching {len(df)} candles to {cache_file}")
        df.to_parquet(cache_file)

    logger.info(f"Got {len(df)} candles from ccxt")
    return df
