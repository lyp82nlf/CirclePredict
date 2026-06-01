from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from circle_predict.config import LONG_WINDOW, MARKETS, SHORT_WINDOW
from circle_predict.data_provider import MarketDataProvider, RealMarketDataProvider
from circle_predict.scoring import build_market_payload


_CACHE: dict | None = None
_CACHE_EXPIRES_AT: datetime | None = None
BEIJING_TZ = timezone(timedelta(hours=8))
CACHE_ROLLOVER_HOUR = 6


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
        "data_notes": provider.notes,
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


def get_dashboard_payload() -> dict:
    global _CACHE, _CACHE_EXPIRES_AT
    now = datetime.now(UTC)
    if _CACHE is not None and _CACHE_EXPIRES_AT is not None and now < _CACHE_EXPIRES_AT:
        payload = dict(_CACHE)
        payload["cache"] = {"status": "hit", "expires_at": _CACHE_EXPIRES_AT.isoformat()}
        return payload

    payload = build_dashboard_payload()
    _CACHE = payload
    _CACHE_EXPIRES_AT = next_cache_expiry(now)
    payload = dict(payload)
    payload["cache"] = {"status": "miss", "expires_at": _CACHE_EXPIRES_AT.isoformat()}
    return payload
