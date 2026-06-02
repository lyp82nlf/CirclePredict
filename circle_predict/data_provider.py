from __future__ import annotations

import csv
import io
import json
import math
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Protocol
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from circle_predict.config import INDICATORS, MARKETS
from circle_predict.env import load_env
from circle_predict.models import IndicatorObservation, MarketDataset


load_env()
DEFAULT_PROXY_URL = ""
PROXY_URL = os.getenv("CIRCLEPREDICT_PROXY_URL", DEFAULT_PROXY_URL).strip()
HTTP_RETRIES = int(os.getenv("CIRCLEPREDICT_HTTP_RETRIES", "2"))
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class MarketDataProvider(Protocol):
    mode: str
    notes: list[str]
    failures: dict[str, str]

    def load(self) -> list[MarketDataset]:
        """Return normalized market datasets for scoring."""


def normalize_proxy_url(proxy_url: str | None) -> str:
    value = (proxy_url or "").strip()
    if value.lower() in {"", "none", "direct", "off", "false", "0"}:
        return ""
    return value


class DataClient:
    def __init__(self, proxy_url: str = PROXY_URL, retries: int = HTTP_RETRIES) -> None:
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.retries = max(0, retries)
        if self.proxy_url:
            self.opener = build_opener(ProxyHandler({"http": self.proxy_url, "https": self.proxy_url}))
        else:
            self.opener = build_opener(ProxyHandler({}))

    def text(self, url: str, timeout: int = 8, headers: dict[str, str] | None = None) -> str:
        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/csv,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "close",
            **(headers or {}),
        }
        request = Request(url, headers=request_headers)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    return response.read().decode("utf-8")
            except Exception as error:
                last_error = error
                if attempt < self.retries:
                    time.sleep(0.6 * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Request failed without an error: {url}")

    def json(self, url: str, timeout: int = 8, headers: dict[str, str] | None = None) -> dict | list:
        return json.loads(self.text(url, timeout=timeout, headers=headers))


class RealMarketDataProvider:
    """No-fake-data provider. Failed sources are omitted and reported."""

    mode = "real"

    def __init__(self, client: DataClient | None = None) -> None:
        self.client = client or DataClient()
        self.failures: dict[str, str] = {}
        proxy_note = f"通过代理 {self.client.proxy_url} 访问" if self.client.proxy_url else "不使用代理，直接访问"
        self.notes = [
            f"所有运行时指标均来自实时接口或由实时接口序列派生；{proxy_note}。",
            "接口失败时不填充假数据，相关指标会缺失，评分只基于成功获取的真实指标。",
        ]

    def load(self) -> list[MarketDataset]:
        loaders = (
            ("cn_equity", self._load_cn_equity),
            ("us_equity", self._load_us_equity),
            ("crypto", self._load_crypto),
        )
        datasets: list[MarketDataset] = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(loader): (market, loader.__name__) for market, loader in loaders}
            for future in as_completed(futures):
                market, name = futures[future]
                try:
                    datasets.append(future.result())
                except Exception as error:
                    self.failures[market] = str(error)
                    self.notes.append(f"{name} 失败：{error}")
        return sorted(datasets, key=lambda dataset: ("cn_equity", "us_equity", "crypto").index(dataset.market))

    def _load_cn_equity(self) -> MarketDataset:
        try:
            csi300 = self._eastmoney_index("1.000300")
            csi500 = self._eastmoney_index("1.000905")
        except Exception as error:
            self.notes.append(f"Eastmoney A 股核心指数主源不可用，改用 Yahoo A 股指数代理：{error}")
            csi300 = {"close": self._yahoo_chart("000300.SS"), "amount": []}
            csi500 = {"close": self._yahoo_chart("000905.SS"), "amount": []}
        close = average_series(csi300["close"], csi500["close"])
        as_of = latest_date(close)

        indicators = {
            "cn_valuation_position": normalize_position_series(close),
            "cn_trend_200d": moving_average_gap(close, 200),
            "cn_momentum_60d": momentum(close, 60),
            "cn_volatility": rolling_volatility(close, 60),
        }
        amount = average_series(csi300["amount"], csi500["amount"])
        if amount:
            indicators["cn_turnover_heat"] = normalize_position_series(amount)
        else:
            self.notes.append("A 股成交热度获取失败，情绪维度少一个指标。")

        cny = self._safe_series(lambda: self._yahoo_chart("CNY=X"), "A 股宏观指标 CNY=X 获取失败，宏观维度缺失。")
        if cny:
            indicators["cn_fx_pressure"] = normalize_position_series(cny)

        return MarketDataset("cn_equity", MARKETS["cn_equity"], as_of, indicators)

    def _load_us_equity(self) -> MarketDataset:
        try:
            spx = self._yahoo_chart("^GSPC")
            ndq = self._yahoo_chart("^IXIC")
        except Exception as error:
            self.notes.append(f"Yahoo 美股核心指数主源不可用，改用 Stooq ETF 代理：{error}")
            spx = self._stooq_daily("spy.us")
            ndq = self._stooq_daily("qqq.us")
        close = average_series(spx, ndq)
        as_of = latest_date(close)
        indicators = {
            "us_valuation_position": normalize_position_series(close),
            "us_trend_200d": moving_average_gap(close, 200),
            "us_momentum_60d": momentum(close, 60),
            "us_volatility": rolling_volatility(close, 60),
        }

        vix = self._safe_series(
            lambda: self._yahoo_chart("^VIX"),
            "Yahoo VIX 主源不可用，尝试 Stooq VIX 代理。",
        ) or self._safe_series(
            lambda: self._stooq_daily("^vix"),
            "Stooq VIX 获取失败，情绪维度缺失。",
        )
        if vix:
            indicators["us_vix"] = normalize_position_series(vix)

        tnx = self._safe_series(
            lambda: self._yahoo_chart("^TNX"),
            "Yahoo ^TNX 主源不可用，尝试 FRED DGS10。",
        ) or self._fred_csv("DGS10")
        if tnx:
            indicators["us_10y_yield"] = tnx

        return MarketDataset("us_equity", MARKETS["us_equity"], as_of, indicators)

    def _load_crypto(self) -> MarketDataset:
        try:
            btc = self._binance_klines("BTCUSDT")
            eth = self._binance_klines("ETHUSDT")
        except Exception as error:
            self.notes.append(f"Binance 现货 K 线主源不可用，改用 CoinGecko 市场图表：{error}")
            btc = self._coingecko_market_chart("bitcoin")
            eth = self._coingecko_market_chart("ethereum")
        close = average_series(btc["price"], eth["price"])
        price_position = normalize_position_series(close)
        volume = average_series(btc["volume"], eth["volume"])
        as_of = latest_date(close)

        indicators = {
            "crypto_price_position": price_position,
            "crypto_volume_heat": normalize_position_series(volume),
            "crypto_trend_200d": moving_average_gap(close, 200),
            "crypto_momentum_60d": momentum(close, 60),
            "crypto_volatility": rolling_volatility(close, 60),
        }

        fear_greed = self._safe_series(
            lambda: self._alternative_fear_greed(),
            "Alternative.me 加密恐惧贪婪获取失败，情绪维度少一个指标。",
        )
        if fear_greed:
            indicators["crypto_fear_greed"] = fear_greed

        funding = self._safe_series(
            lambda: self._binance_funding(),
            "Binance BTCUSDT funding 获取失败，情绪维度少一个指标。",
        )
        if funding:
            indicators["crypto_funding"] = funding

        dxy = self._safe_series(lambda: self._yahoo_chart("DX-Y.NYB"), "美元指数 DX-Y.NYB 获取失败，币圈宏观维度缺失。")
        if dxy:
            indicators["crypto_dxy"] = normalize_position_series(dxy)

        return MarketDataset("crypto", MARKETS["crypto"], as_of, indicators)

    def _safe_series(self, loader, note: str) -> list[IndicatorObservation]:
        try:
            return loader()
        except Exception as error:
            self.notes.append(f"{note} 原因：{error}")
            return []

    def _eastmoney_index(self, secid: str) -> dict[str, list[IndicatorObservation]]:
        url = (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&beg=20160101&end=20500101"
        )
        payload = self.client.json(url, timeout=10, headers={"Referer": "https://quote.eastmoney.com/"})
        klines = payload["data"]["klines"]
        close: list[IndicatorObservation] = []
        amount: list[IndicatorObservation] = []
        for row in klines:
            parts = row.split(",")
            point_date = date.fromisoformat(parts[0])
            close.append(IndicatorObservation(point_date, float(parts[2])))
            amount.append(IndicatorObservation(point_date, float(parts[6])))
        return {"close": close, "amount": amount}

    def _fred_csv(self, series_id: str) -> list[IndicatorObservation]:
        try:
            text = self.client.text(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}", timeout=6)
        except Exception as error:
            self.notes.append(f"FRED {series_id} 获取失败：{error}")
            return []
        rows = csv.DictReader(io.StringIO(text))
        series: list[IndicatorObservation] = []
        start = date.today() - timedelta(days=365 * 10 + 30)
        for row in rows:
            value = row.get(series_id)
            if not value or value == ".":
                continue
            point_date = date.fromisoformat(row["observation_date"])
            if point_date >= start:
                series.append(IndicatorObservation(point_date, float(value)))
        return series

    def _yahoo_chart(self, symbol: str) -> list[IndicatorObservation]:
        encoded_symbol = quote(symbol, safe="")
        payload = self.client.json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}?range=10y&interval=1d",
            timeout=10,
            headers={"Referer": "https://finance.yahoo.com/"},
        )
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        return [
            IndicatorObservation(datetime.fromtimestamp(timestamp).date(), float(close))
            for timestamp, close in zip(timestamps, closes)
            if close is not None
        ]

    def _stooq_daily(self, symbol: str) -> list[IndicatorObservation]:
        text = self.client.text(f"https://stooq.com/q/d/l/?s={symbol}&i=d", timeout=10)
        rows = csv.DictReader(io.StringIO(text))
        start = date.today() - timedelta(days=365 * 10 + 30)
        series: list[IndicatorObservation] = []
        for row in rows:
            close = row.get("Close")
            if not close or close == "No data":
                continue
            point_date = date.fromisoformat(row["Date"])
            if point_date >= start:
                series.append(IndicatorObservation(point_date, float(close)))
        if not series:
            raise ValueError(f"Stooq returned no data for {symbol}")
        return series

    def _binance_klines(self, symbol: str) -> dict[str, list[IndicatorObservation]]:
        payload = self.client.json(
            f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit=1000",
            timeout=10,
        )
        price: list[IndicatorObservation] = []
        volume: list[IndicatorObservation] = []
        for row in payload:
            point_date = datetime.fromtimestamp(row[0] / 1000).date()
            price.append(IndicatorObservation(point_date, float(row[4])))
            volume.append(IndicatorObservation(point_date, float(row[7])))
        return {
            "price": price,
            "volume": volume,
        }

    def _coingecko_market_chart(self, coin_id: str) -> dict[str, list[IndicatorObservation]]:
        end = int(datetime.now().timestamp())
        start = int((datetime.now() - timedelta(days=365 * 10 + 30)).timestamp())
        payload = self.client.json(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart/range"
            f"?vs_currency=usd&from={start}&to={end}",
            timeout=14,
            headers={"Referer": "https://www.coingecko.com/"},
        )
        return {
            "price": timestamp_points(payload.get("prices", [])),
            "volume": timestamp_points(payload.get("total_volumes", [])),
        }

    def _alternative_fear_greed(self) -> list[IndicatorObservation]:
        payload = self.client.json("https://api.alternative.me/fng/?limit=0", timeout=8)
        return [
            IndicatorObservation(date.fromtimestamp(int(item["timestamp"])), float(item["value"]))
            for item in reversed(payload["data"])
        ]

    def _binance_funding(self) -> list[IndicatorObservation]:
        payload = self.client.json("https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1000", timeout=8)
        return [
            IndicatorObservation(datetime.fromtimestamp(item["fundingTime"] / 1000).date(), float(item["fundingRate"]) * 100)
            for item in payload
        ]


class SampleMarketDataProvider:
    """Test-only deterministic provider. Do not use for runtime scoring."""

    mode = "sample"
    notes = ["测试专用样例数据，不用于运行时评分。"]
    failures: dict[str, str] = {}

    def __init__(self, as_of_date: date | None = None) -> None:
        self.as_of_date = as_of_date or date.today()

    def load(self) -> list[MarketDataset]:
        return [
            MarketDataset(
                market=market,
                label=label,
                as_of_date=self.as_of_date,
                indicators=self._market_indicators(market),
            )
            for market, label in MARKETS.items()
        ]

    def _market_indicators(self, market: str) -> dict[str, list[IndicatorObservation]]:
        indicators = [indicator for indicator in INDICATORS if indicator.market == market]
        return {indicator.id: self._series(indicator.id) for indicator in indicators}

    def _series(self, indicator_id: str) -> list[IndicatorObservation]:
        rng = random.Random(indicator_id)
        dates = [self.as_of_date - timedelta(days=7 * index) for index in range(520)]
        dates.reverse()
        phase = rng.uniform(0, math.pi * 2)
        return [
            IndicatorObservation(point_date, max(0.01, 50 + 20 * math.sin(index / 23 + phase) + rng.uniform(-4, 4)))
            for index, point_date in enumerate(dates)
        ]


def timestamp_points(rows: list[list[float]]) -> list[IndicatorObservation]:
    return [
        IndicatorObservation(datetime.fromtimestamp(timestamp / 1000).date(), float(value))
        for timestamp, value in rows
    ]


def latest_date(series: list[IndicatorObservation]) -> date:
    if not series:
        raise ValueError("empty series")
    return max(point.date for point in series if point.value is not None)


def values_by_date(series: list[IndicatorObservation]) -> dict[date, float]:
    return {point.date: point.value for point in series if point.value is not None}


def average_series(*series_list: list[IndicatorObservation]) -> list[IndicatorObservation]:
    maps = [values_by_date(series) for series in series_list if series]
    if not maps:
        return []
    dates = sorted(set.intersection(*(set(item.keys()) for item in maps)))
    return [IndicatorObservation(point_date, sum(item[point_date] for item in maps) / len(maps)) for point_date in dates]


def normalize_position_series(series: list[IndicatorObservation], lookback: int = 365 * 10) -> list[IndicatorObservation]:
    values = [point for point in series if point.value is not None]
    output: list[IndicatorObservation] = []
    for index, point in enumerate(values):
        start = max(0, index - lookback)
        window = [item.value for item in values[start : index + 1] if item.value is not None]
        if len(window) < 20:
            continue
        lo, hi = min(window), max(window)
        normalized = 50.0 if hi == lo else (point.value - lo) / (hi - lo) * 100
        output.append(IndicatorObservation(point.date, normalized))
    return output


def moving_average_gap(series: list[IndicatorObservation], days: int) -> list[IndicatorObservation]:
    values = [point for point in series if point.value is not None]
    output: list[IndicatorObservation] = []
    for index, point in enumerate(values):
        if index + 1 < days:
            continue
        avg = sum(item.value for item in values[index + 1 - days : index + 1]) / days
        output.append(IndicatorObservation(point.date, (point.value / avg - 1) * 100))
    return output


def momentum(series: list[IndicatorObservation], days: int) -> list[IndicatorObservation]:
    values = [point for point in series if point.value is not None]
    return [
        IndicatorObservation(values[index].date, (values[index].value / values[index - days].value - 1) * 100)
        for index in range(days, len(values))
    ]


def rolling_volatility(series: list[IndicatorObservation], days: int) -> list[IndicatorObservation]:
    values = [point for point in series if point.value is not None]
    output: list[IndicatorObservation] = []
    for index in range(days, len(values)):
        window = values[index + 1 - days : index + 1]
        returns = [math.log(window[i].value / window[i - 1].value) for i in range(1, len(window))]
        mean = sum(returns) / len(returns)
        variance = sum((item - mean) ** 2 for item in returns) / len(returns)
        output.append(IndicatorObservation(values[index].date, math.sqrt(variance) * math.sqrt(252) * 100))
    return output
