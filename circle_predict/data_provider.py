from __future__ import annotations

import csv
import io
import json
import math
import os
import random
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
try:
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - optional transport may be absent before pip install.
    curl_requests = None

from circle_predict.config import INDICATORS, MARKETS
from circle_predict.env import load_env
from circle_predict.models import IndicatorObservation, MarketDataset


load_env()
DEFAULT_PROXY_URL = ""
PROXY_URL = os.getenv("CIRCLEPREDICT_PROXY_URL", DEFAULT_PROXY_URL).strip()
HTTP_RETRIES = int(os.getenv("CIRCLEPREDICT_HTTP_RETRIES", "2"))
CURL_FALLBACK = os.getenv("CIRCLEPREDICT_CURL_FALLBACK", "1").strip().lower() not in {"0", "false", "off", "no"}
CURL_RETRIES = int(os.getenv("CIRCLEPREDICT_CURL_RETRIES", "2"))
STOOQ_API_KEY = os.getenv("CIRCLEPREDICT_STOOQ_API_KEY", "").strip()
YAHOO_COOKIE = os.getenv("CIRCLEPREDICT_YAHOO_COOKIE", "").strip()
YAHOO_CURL_RETRIES = int(os.getenv("CIRCLEPREDICT_YAHOO_CURL_RETRIES", "3"))
YAHOO_REQUEST_SPACING_SECONDS = float(os.getenv("CIRCLEPREDICT_YAHOO_REQUEST_SPACING_SECONDS", "1.5"))
YAHOO_IMPERSONATE = [
    item.strip()
    for item in os.getenv("CIRCLEPREDICT_YAHOO_IMPERSONATE", "safari_ios,chrome").split(",")
    if item.strip()
]
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
YAHOO_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
CN_EQUITY_DAILY_CUTOFF = datetime_time(15, 30)


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


def is_yahoo_chart_url(url: str) -> bool:
    return "query1.finance.yahoo.com" in url


def yahoo_browser_headers() -> dict[str, str]:
    headers = {
        "User-Agent": YAHOO_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "max-age=0",
        "Priority": "u=0, i",
        "Sec-CH-UA": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "Sec-CH-UA-Mobile": "?1",
        "Sec-CH-UA-Platform": '"iOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if YAHOO_COOKIE:
        headers["Cookie"] = YAHOO_COOKIE
    return headers


def cn_equity_completed_through(now: datetime | None = None) -> date:
    local_now = now.astimezone(BEIJING_TZ) if now and now.tzinfo else now or datetime.now(BEIJING_TZ)
    target = local_now.date()
    if local_now.time() < CN_EQUITY_DAILY_CUTOFF:
        target -= timedelta(days=1)
    return target


def trim_series_through(series: list[IndicatorObservation], through_date: date) -> list[IndicatorObservation]:
    return [point for point in series if point.date <= through_date]


def trim_index_through(index_data: dict[str, list[IndicatorObservation]], through_date: date) -> dict[str, list[IndicatorObservation]]:
    return {
        "close": trim_series_through(index_data["close"], through_date),
        "amount": trim_series_through(index_data["amount"], through_date),
    }


class DataClient:
    _yahoo_lock = threading.Lock()
    _last_yahoo_request_at = 0.0

    def __init__(self, proxy_url: str = PROXY_URL, retries: int = HTTP_RETRIES, curl_fallback: bool = CURL_FALLBACK) -> None:
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.retries = max(0, retries)
        self.curl_fallback = curl_fallback
        self.last_transport = ""
        self.session = requests.Session()
        self.session.trust_env = False
        if self.proxy_url:
            self.proxies = {"http": self.proxy_url, "https": self.proxy_url}
        else:
            self.proxies = {}

    def text(self, url: str, timeout: int = 8, headers: dict[str, str] | None = None, minimal_headers: bool = False) -> str:
        if is_yahoo_chart_url(url):
            request_headers = {**yahoo_browser_headers(), **(headers or {})}
        elif minimal_headers:
            request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
        else:
            request_headers = {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Connection": "close",
                **(headers or {}),
            }
        last_error: Exception | None = None
        if is_yahoo_chart_url(url) and self.curl_fallback:
            with self._yahoo_lock:
                for attempt in range(max(1, YAHOO_CURL_RETRIES)):
                    try:
                        self._wait_for_yahoo_slot()
                        if curl_requests is not None:
                            try:
                                return self._curl_cffi_text(url, timeout=timeout, headers=request_headers)
                            except Exception as error:
                                last_error = error
                        text = self._curl_text(url, timeout=timeout, headers=request_headers)
                        self.last_transport = "curl"
                        return text
                    except Exception as error:
                        last_error = error
                        if attempt < YAHOO_CURL_RETRIES - 1:
                            time.sleep(2 + attempt * 2 + random.uniform(0, 0.8))
            if last_error is not None:
                raise last_error

        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, headers=request_headers, proxies=self.proxies, timeout=timeout)
                response.raise_for_status()
                self.last_transport = "requests"
                return response.text
            except Exception as error:
                last_error = error
                if attempt < self.retries:
                    time.sleep(0.6 * (attempt + 1))
        if self.curl_fallback:
            try:
                return self._curl_text_with_retries(url, timeout=timeout, headers=request_headers)
            except Exception as error:
                last_error = error
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Request failed without an error: {url}")

    def _curl_text_with_retries(self, url: str, timeout: int, headers: dict[str, str]) -> str:
        last_error: Exception | None = None
        for attempt in range(max(1, CURL_RETRIES)):
            try:
                text = self._curl_text(url, timeout=timeout, headers=headers)
                self.last_transport = "curl"
                return text
            except Exception as error:
                last_error = error
                if attempt < CURL_RETRIES - 1:
                    time.sleep(0.8 * (attempt + 1) + random.uniform(0, 0.2))
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"curl failed without an error: {url}")

    def _wait_for_yahoo_slot(self) -> None:
        elapsed = time.monotonic() - self.__class__._last_yahoo_request_at
        wait_seconds = YAHOO_REQUEST_SPACING_SECONDS - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        self.__class__._last_yahoo_request_at = time.monotonic()

    def _curl_cffi_text(self, url: str, timeout: int, headers: dict[str, str]) -> str:
        if curl_requests is None:
            raise RuntimeError("curl_cffi is not installed")

        last_error: Exception | None = None
        for profile in YAHOO_IMPERSONATE or ["chrome"]:
            try:
                response = curl_requests.get(
                    url,
                    headers=headers,
                    proxies=self.proxies,
                    timeout=timeout,
                    impersonate=profile,
                )
                response.raise_for_status()
                self.last_transport = f"curl_cffi:{profile}"
                return response.text
            except Exception as error:
                last_error = error
        if last_error is not None:
            raise last_error
        raise RuntimeError("curl_cffi failed without an error")

    def json(self, url: str, timeout: int = 8, headers: dict[str, str] | None = None, minimal_headers: bool = False) -> dict | list:
        return json.loads(self.text(url, timeout=timeout, headers=headers, minimal_headers=minimal_headers))

    def _curl_text(self, url: str, timeout: int, headers: dict[str, str]) -> str:
        command = [
            "curl",
            "-fsSL",
            "--compressed",
            "--max-time",
            str(timeout),
            "-A",
            headers["User-Agent"],
        ]
        if self.proxy_url:
            command.extend(["-x", self.proxy_url])
        for key, value in headers.items():
            if key.lower() == "user-agent":
                continue
            if key.lower() == "cookie":
                command.extend(["-b", value])
                continue
            command.extend(["-H", f"{key}: {value}"])
        command.append(url)

        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            message = result.stderr.strip() or f"curl exited with {result.returncode}"
            raise RuntimeError(message)
        return result.stdout


class RealMarketDataProvider:
    """No-fake-data provider. Failed sources are omitted and reported."""

    mode = "real"

    def __init__(self, client: DataClient | None = None) -> None:
        self.client = client or DataClient()
        self.failures: dict[str, str] = {}
        proxy_note = f"外网接口通过代理 {self.client.proxy_url} 访问；Eastmoney 优先直连" if self.client.proxy_url else "不使用代理，直接访问"
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
        csi300 = self._safe_index(lambda: self._eastmoney_index("1.000300"), "Eastmoney 沪深300 获取失败")
        csi500 = self._safe_index(lambda: self._eastmoney_index("1.000905"), "Eastmoney 中证500 获取失败")
        if not csi300["close"] and not csi500["close"]:
            self.notes.append("Eastmoney A 股核心指数均不可用，尝试 Yahoo A 股指数代理。")
            csi300 = self._safe_index(lambda: {"close": self._yahoo_chart("000300.SS"), "amount": []}, "Yahoo 沪深300代理获取失败")
            csi500 = self._safe_index(lambda: {"close": self._yahoo_chart("000905.SS"), "amount": []}, "Yahoo 中证500代理获取失败")
        completed_through = cn_equity_completed_through()
        raw_latest_dates = [
            latest_date(series)
            for series in (csi300["close"], csi500["close"])
            if series
        ]
        csi300 = trim_index_through(csi300, completed_through)
        csi500 = trim_index_through(csi500, completed_through)
        close = average_series(csi300["close"], csi500["close"])
        as_of = latest_date(close)
        if raw_latest_dates and max(raw_latest_dates) > as_of:
            self.notes.append(f"A 股已排除未收盘日线，按最近完整交易日 {as_of.isoformat()} 计算。")

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
        spx = self._safe_series(
            lambda: self._stooq_daily("spy.us"),
            "Stooq SPY 获取失败，尝试 Yahoo 标普500。",
        ) or self._yahoo_chart("^GSPC")
        ndq = self._safe_series(
            lambda: self._stooq_daily("qqq.us"),
            "Stooq QQQ 获取失败，尝试 Yahoo 纳指。",
        ) or self._yahoo_chart("^IXIC")
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
            "Yahoo VIX 获取失败，情绪维度缺失。",
        )
        if vix:
            indicators["us_vix"] = normalize_position_series(vix)

        tnx = self._safe_series(
            lambda: self._yahoo_chart("^TNX"),
            "Yahoo ^TNX 获取失败，尝试 FRED DGS10。",
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

    def _safe_index(self, loader, note: str) -> dict[str, list[IndicatorObservation]]:
        try:
            return loader()
        except Exception as error:
            self.notes.append(f"{note}。原因：{error}")
            return {"close": [], "amount": []}

    def _eastmoney_index(self, secid: str) -> dict[str, list[IndicatorObservation]]:
        url = (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&beg=20160101&end=20500101"
        )
        headers = {"Referer": "https://quote.eastmoney.com/"}
        try:
            direct_client = DataClient(proxy_url="direct", retries=self.client.retries, curl_fallback=self.client.curl_fallback)
            payload = direct_client.json(url, timeout=10, headers=headers)
        except Exception:
            payload = self.client.json(url, timeout=10, headers=headers)
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
        if not STOOQ_API_KEY:
            raise ValueError("Stooq CSV now requires an API key; set CIRCLEPREDICT_STOOQ_API_KEY to enable this source")
        text = self.client.text(f"https://stooq.com/q/d/l/?s={symbol}&i=d&apikey={STOOQ_API_KEY}", timeout=10, minimal_headers=True)
        if "get_apikey" in text.lower():
            raise ValueError("Stooq returned API key instructions instead of CSV data")
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
