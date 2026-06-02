from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from circle_predict.config import LONG_WINDOW, MARKETS, SHORT_WINDOW
from circle_predict.data_provider import MarketDataProvider, RealMarketDataProvider
from circle_predict.env import ROOT, load_env
from circle_predict.scoring import build_market_payload


load_env()
UTC = timezone.utc
_CACHE: dict | None = None
_CACHE_EXPIRES_AT: datetime | None = None
_LAST_SUCCESS_CACHE: dict | None = None
BEIJING_TZ = timezone(timedelta(hours=8))
CACHE_ROLLOVER_HOUR = 6
FAILURE_RETRY_MINUTES = int(os.getenv("CIRCLEPREDICT_FAILURE_RETRY_MINUTES", "15"))
DISK_CACHE_PATH = ROOT / ".cache" / "dashboard-last-success.json"
FAILURE_NOTE_PATTERNS = (
    "获取失败",
    " 失败：",
    "未返回有效",
    "未成功返回",
    "维度缺失",
    "少一个指标",
)


def build_dashboard_payload(provider: MarketDataProvider | None = None) -> dict:
    provider = provider or RealMarketDataProvider()
    scored_markets = {
        dataset.market: build_market_payload(dataset, SHORT_WINDOW, LONG_WINDOW)
        for dataset in provider.load()
    }
    markets = [
        scored_markets.get(market_id) or unavailable_market_payload(market_id, provider.failures.get(market_id, "数据源未返回有效数据"))
        for market_id in MARKETS
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "data_mode": provider.mode,
        "data_notes": list(provider.notes),
        "score_formula": {
            "valuation": 0.40,
            "sentiment": 0.30,
            "market": 0.20,
            "macro": 0.10,
        },
        "score_semantics": "higher_is_more_overvalued",
        "windows": {
            "short": {"label": "短周期", "days": SHORT_WINDOW.days},
            "long": {"label": "长周期", "days": LONG_WINDOW.days},
        },
        "markets": markets,
    }


def unavailable_market_payload(market_id: str, reason: str) -> dict:
    return {
        "market": market_id,
        "label": MARKETS[market_id],
        "as_of_date": "",
        "available": False,
        "unavailable_reason": reason,
        "short_score": None,
        "long_score": None,
        "dimension_scores": {},
        "short_dimension_scores": {},
        "position_label": "数据不可用",
        "position_range_label": "--",
        "meaning": "真实数据源本次没有成功返回，未计算评分。",
        "action_advice": "请稍后刷新或检查代理/数据源。",
        "indicators": [],
        "stale_indicators": [],
        "history": [],
    }


def next_cache_expiry(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    local_now = now.astimezone(BEIJING_TZ)
    rollover = local_now.replace(hour=CACHE_ROLLOVER_HOUR, minute=0, second=0, microsecond=0)
    if local_now >= rollover:
        rollover += timedelta(days=1)
    return rollover.astimezone(UTC)


def short_retry_expiry(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now + timedelta(minutes=FAILURE_RETRY_MINUTES)


def payload_is_degraded(payload: dict) -> bool:
    unavailable = any(market.get("available") is False for market in payload.get("markets", []))
    notes = " ".join(payload.get("data_notes") or [])
    return unavailable or any(pattern in notes for pattern in FAILURE_NOTE_PATTERNS)


def copy_payload(payload: dict) -> dict:
    return copy.deepcopy(payload)


def load_last_success_cache() -> dict | None:
    global _LAST_SUCCESS_CACHE
    if _LAST_SUCCESS_CACHE is not None:
        return copy_payload(_LAST_SUCCESS_CACHE)
    if not DISK_CACHE_PATH.exists():
        return None
    try:
        with DISK_CACHE_PATH.open("r", encoding="utf-8") as file:
            _LAST_SUCCESS_CACHE = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return copy_payload(_LAST_SUCCESS_CACHE)


def save_last_success_cache(payload: dict) -> None:
    global _LAST_SUCCESS_CACHE
    _LAST_SUCCESS_CACHE = copy_payload(payload)
    try:
        DISK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DISK_CACHE_PATH.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)
    except OSError:
        pass


def use_stale_success_cache(stale_payload: dict, failed_payload: dict) -> dict:
    payload = copy_payload(stale_payload)
    failed_notes = " ".join(failed_payload.get("data_notes") or [])
    payload["generated_at"] = datetime.now(UTC).isoformat()
    payload["using_stale_success_cache"] = True
    payload["stale_success_generated_at"] = stale_payload.get("generated_at")
    payload["data_notes"] = [
        f"本次刷新失败，沿用最近一次成功数据；最近成功时间：{stale_payload.get('generated_at', '未知')}.",
        f"本次失败原因：{failed_notes}",
    ]
    return payload


def clear_dashboard_cache() -> None:
    global _CACHE, _CACHE_EXPIRES_AT, _LAST_SUCCESS_CACHE
    _CACHE = None
    _CACHE_EXPIRES_AT = None
    _LAST_SUCCESS_CACHE = None


def get_dashboard_payload(provider: MarketDataProvider | None = None, force_refresh: bool = False) -> dict:
    global _CACHE, _CACHE_EXPIRES_AT
    now = datetime.now(UTC)
    if not force_refresh and _CACHE is not None and _CACHE_EXPIRES_AT is not None and now < _CACHE_EXPIRES_AT:
        payload = copy_payload(_CACHE)
        payload["cache"] = {"status": "hit", "expires_at": _CACHE_EXPIRES_AT.isoformat()}
        return payload

    fetched_payload = build_dashboard_payload(provider)
    degraded = payload_is_degraded(fetched_payload)
    if degraded:
        stale_payload = load_last_success_cache()
        _CACHE_EXPIRES_AT = short_retry_expiry(now)
        if stale_payload is not None:
            payload = use_stale_success_cache(stale_payload, fetched_payload)
            _CACHE = payload
            payload = copy_payload(payload)
            payload["cache"] = {
                "status": "stale_fallback",
                "expires_at": _CACHE_EXPIRES_AT.isoformat(),
                "retry_after_minutes": FAILURE_RETRY_MINUTES,
            }
            return payload

        _CACHE = fetched_payload
        payload = copy_payload(fetched_payload)
        payload["cache"] = {
            "status": "degraded_miss",
            "expires_at": _CACHE_EXPIRES_AT.isoformat(),
            "retry_after_minutes": FAILURE_RETRY_MINUTES,
        }
        return payload

    save_last_success_cache(fetched_payload)
    _CACHE = fetched_payload
    _CACHE_EXPIRES_AT = next_cache_expiry(now)
    payload = copy_payload(fetched_payload)
    payload["cache"] = {"status": "miss", "expires_at": _CACHE_EXPIRES_AT.isoformat()}
    return payload
