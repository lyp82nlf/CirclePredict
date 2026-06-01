from __future__ import annotations

import csv
import io
import json
import math
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Protocol
from urllib.request import ProxyHandler, Request, build_opener

from circle_predict.config import INDICATORS, MARKETS
from circle_predict.env import load_env
from circle_predict.models import IndicatorObservation, MarketDataset


load_env()
DEFAULT_PROXY_URL = "http://127.0.0.1:7890"
PROXY_URL = os.getenv("CIRCLEPREDICT_PROXY_URL", DEFAULT_PROXY_URL)
USER_AGENT = "CirclePredict/0.1"


class MarketDataProvider(Protocol):
    mode: str
    notes: list[str]
    failures: dict[str, str]

    def load(self) -> list[MarketDataset]:
        """Return normalized market datasets for scoring."""


class DataClient:
    def __init__(self, proxy_url: str = PROXY_URL) -> None:
        self.opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))

    def text(self, url: str, timeout: int = 8) -> str:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with self.opener.open(request, timeout=timeout) as response:
            return response.read().decode("utf-8")

    def json(self, url: str, timeout: int = 8) -> dict | list:
        return json.loads(self.text(url, timeout=timeout))


class RealMarketDataProvider:
    """No-fake-data provider. Failed sources are omitted and reported."""

    mode = "real"

    def __init__(self, client: DataClient | None = None) -> None:
        self.client = client or DataClient()
        self.failures: dict[str, str] = {}
        self.notes = [
            f"所有运行时指标均来自实时接口或由实时接口序列派生；通过代理 {PROXY_URL} 访问。",
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
        csi300 = self._eastmoney_index("1.000300")
        csi500 = self._eastmoney_index("1.000905")
        close = average_series(csi300["close"], csi500["close"])
        amount = average_series(csi300["amount"], csi500["amount"])
        as_of = latest_date(close)

        indicators = {
            "cn_valuation_position": normalize_position_series(close),
            "cn_turnover_heat": normalize_position_series(amount),
            "cn_trend_200d": moving_average_gap(close, 200),
            "cn_momentum_60d": momentum(close, 60),
            "cn_volatility": rolling_volatility(close, 60),
        }
        cny = self._yahoo_chart("CNY=X")
        if cny:
            indicators["cn_fx_pressure"] = normalize_position_series(cny)
        else:
            self.notes.append("A 股宏观指标 CNY=X 获取失败，宏观维度缺失。")

        return MarketDataset("cn_equity", MARKETS["cn_equity"], as_of, indicators)

    def _load_us_equity(self) -> MarketDataset:
        spx = self._yahoo_chart("%5EGSPC")
        ndq = self._yahoo_chart("%5EIXIC")
        close = average_series(spx, ndq)
        as_of = latest_date(close)
        indicators = {
            "us_valuation_position": normalize_position_series(close),
            "us_trend_200d": moving_average_gap(close, 200),
            "us_momentum_60d": momentum(close, 60),
            "us_volatility": rolling_volatility(close, 60),
        }

        vix = self._yahoo_chart("%5EVIX")
        if vix:
            indicators["us_vix"] = normalize_position_series(vix)
        else:
            self.notes.append("美股 VIX 获取失败，情绪维度缺失。")

        tnx = self._yahoo_chart("%5ETNX")
        if tnx:
            indicators["us_10y_yield"] = tnx
        else:
            self.notes.append("美股宏观指标 ^TNX 获取失败，宏观维度缺失。")

        return MarketDataset("us_equity", MARKETS["us_equity"], as_of, indicators)

    def _load_crypto(self) -> MarketDataset:
        btc = self._binance_klines("BTCUSDT")
        eth = self._binance_klines("ETHUSDT")
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

        fear_greed = self._alternative_fear_greed()
        if fear_greed:
            indicators["crypto_fear_greed"] = fear_greed
        else:
            self.notes.append("币圈恐惧贪婪获取失败，情绪维度少一个指标。")

        funding = self._binance_funding()
        if funding:
            indicators["crypto_funding"] = funding
        else:
            self.notes.append("Binance BTCUSDT funding 获取失败，情绪维度少一个指标。")

        dxy = self._yahoo_chart("DX-Y.NYB")
        if dxy:
            indicators["crypto_dxy"] = normalize_position_series(dxy)
        else:
            self.notes.append("美元指数 DX-Y.NYB 获取失败，币圈宏观维度缺失。")

        return MarketDataset("crypto", MARKETS["crypto"], as_of, indicators)

    def _eastmoney_index(self, secid: str) -> dict[str, list[IndicatorObservation]]:
        url = (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&beg=20160101&end=20500101"
        )
        payload = self.client.json(url, timeout=8)
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
        payload = self.client.json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10y&interval=1d",
            timeout=10,
        )
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        return [
            IndicatorObservation(datetime.fromtimestamp(timestamp).date(), float(close))
            for timestamp, close in zip(timestamps, closes)
            if close is not None
        ]

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

    def _alternative_fear_greed(self) -> list[IndicatorObservation]:
        try:
            payload = self.client.json("https://api.alternative.me/fng/?limit=0", timeout=8)
        except Exception as error:
            self.notes.append(f"Alternative.me 获取失败：{error}")
            return []
        return [
            IndicatorObservation(date.fromtimestamp(int(item["timestamp"])), float(item["value"]))
            for item in reversed(payload["data"])
        ]

    def _binance_funding(self) -> list[IndicatorObservation]:
        try:
            payload = self.client.json("https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1000", timeout=8)
        except Exception as error:
            self.notes.append(f"Binance funding 获取失败：{error}")
            return []
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
