from __future__ import annotations

from datetime import date, timedelta
from statistics import fmean

from circle_predict.config import DIMENSION_WEIGHTS, INDICATORS_BY_ID, POSITION_BANDS, source_links_for
from circle_predict.models import Dimension, Direction, IndicatorDefinition, IndicatorObservation, MarketDataset, ScoreWindow


def percentile_score(values: list[float], current: float, direction: Direction) -> float:
    if not values:
        raise ValueError("values cannot be empty")

    less_or_equal = sum(1 for value in values if value <= current)
    percentile = less_or_equal / len(values) * 100
    score = percentile if direction == Direction.HIGH_GOOD else 100 - percentile
    return round(max(0, min(100, score)), 2)


def invert_score(score: float) -> float:
    return round(100 - score, 2)


def position_band(score: float) -> dict:
    for band in POSITION_BANDS:
        if score >= band["min"]:
            return dict(band)
    return dict(POSITION_BANDS[-1])


def indicator_state(score: float) -> str:
    if score >= 80:
        return "极热/极高位置"
    if score >= 70:
        return "偏热/偏高位置"
    if score >= 50:
        return "中性偏高"
    if score >= 30:
        return "中性偏低"
    return "偏冷/偏低位置"


def direction_text(direction: Direction) -> str:
    if direction == Direction.HIGH_BAD:
        return "这个指标越高，通常代表越热、越贵或风险压力越大，所以位置分会更高。"
    return "这个指标越高，通常代表趋势、流动性或基本面更友好，所以会降低位置分。"


def indicator_explanation(definition: IndicatorDefinition, value: float, percentile: float, position: float) -> dict:
    return {
        "current_value": f"当前值是 {value:.2f}{'' if definition.unit == 'index' else definition.unit}。",
        "calculation": f"先把当前值放到该指标自己的历史序列里计算分位数，目前原始分位约 {percentile:.1f}%；再按指标方向换算为 0-100 的位置分，得到 {position:.1f} 分。",
        "meaning": direction_text(definition.direction),
        "current_state": f"当前状态：{indicator_state(position)}。{definition.description}",
    }


def latest_valid_observation(observations: list[IndicatorObservation], as_of: date) -> tuple[IndicatorObservation, bool]:
    candidates = [point for point in observations if point.date <= as_of and point.value is not None]
    if not candidates:
        raise ValueError("indicator has no valid observations")
    latest = max(candidates, key=lambda point: point.date)
    return latest, latest.date != as_of


def window_values(observations: list[IndicatorObservation], end: date, days: int) -> list[float]:
    start = end - timedelta(days=days)
    return [
        point.value
        for point in observations
        if point.value is not None and start <= point.date <= end
    ]


def weighted_mean(items: list[tuple[float, float]]) -> float:
    total_weight = sum(weight for _, weight in items)
    if total_weight <= 0:
        raise ValueError("total weight must be positive")
    return round(sum(value * weight for value, weight in items) / total_weight, 2)


def score_indicator(
    definition: IndicatorDefinition,
    observations: list[IndicatorObservation],
    as_of: date,
    window: ScoreWindow,
) -> dict:
    latest, stale = latest_valid_observation(observations, as_of)
    values = window_values(observations, latest.date, window.days)
    opportunity_score = percentile_score(values, latest.value or 0, definition.direction)
    position = invert_score(opportunity_score)
    percentile = percentile_score(values, latest.value or 0, Direction.HIGH_GOOD)
    return {
        "id": definition.id,
        "name": definition.name,
        "dimension": definition.dimension.value,
        "value": round(latest.value or 0, 4),
        "unit": definition.unit,
        "score": position,
        "opportunity_score": opportunity_score,
        "percentile": percentile,
        "direction": definition.direction.value,
        "source": definition.source,
        "source_links": source_links_for(definition.source),
        "description": definition.description,
        "explanation": indicator_explanation(definition, latest.value or 0, percentile, position),
        "as_of_date": latest.date.isoformat(),
        "used_stale_value": stale,
    }


def score_market_window(dataset: MarketDataset, window: ScoreWindow) -> dict:
    indicator_scores: list[dict] = []
    unavailable_indicators: list[str] = []
    for indicator_id, observations in dataset.indicators.items():
        definition = INDICATORS_BY_ID[indicator_id]
        try:
            indicator_scores.append(score_indicator(definition, observations, dataset.as_of_date, window))
        except ValueError:
            unavailable_indicators.append(indicator_id)

    dimension_scores: dict[str, float] = {}
    missing_dimensions: list[str] = []
    for dimension in Dimension:
        members = [
            (item["score"], INDICATORS_BY_ID[item["id"]].weight)
            for item in indicator_scores
            if item["dimension"] == dimension.value
        ]
        if members:
            dimension_scores[dimension.value] = weighted_mean(members)
        else:
            missing_dimensions.append(dimension.value)

    total_inputs = [
        (dimension_scores[dimension.value], weight)
        for dimension, weight in DIMENSION_WEIGHTS.items()
        if dimension.value in dimension_scores
    ]
    if not total_inputs:
        raise ValueError(f"{dataset.market} has no scoreable real indicators for {dataset.as_of_date}")
    total_score = weighted_mean(total_inputs)
    band = position_band(total_score)

    return {
        "score": total_score,
        "dimension_scores": dimension_scores,
        "position_label": band["label"],
        "position_range_label": band["range_label"],
        "meaning": band["meaning"],
        "action_advice": band["action_advice"],
        "indicators": indicator_scores,
        "missing_dimensions": missing_dimensions,
        "unavailable_indicators": unavailable_indicators,
        "stale_indicators": [item["id"] for item in indicator_scores if item["used_stale_value"]],
    }


def historical_scores(dataset: MarketDataset, window: ScoreWindow) -> list[dict]:
    all_dates = sorted({point.date for series in dataset.indicators.values() for point in series if point.date <= dataset.as_of_date})
    history: list[dict] = []

    for history_date in all_dates:
        historical_dataset = MarketDataset(
            market=dataset.market,
            label=dataset.label,
            as_of_date=history_date,
            indicators=dataset.indicators,
        )
        scored = score_market_window(historical_dataset, window)
        history.append(
            {
                "date": history_date.isoformat(),
                "score": scored["score"],
                "dimension_scores": scored["dimension_scores"],
                "indicators": scored["indicators"],
            }
        )

    return history


def build_market_payload(dataset: MarketDataset, short_window: ScoreWindow, long_window: ScoreWindow) -> dict:
    short = score_market_window(dataset, short_window)
    long = score_market_window(dataset, long_window)
    all_indicators = {
        item["id"]: item
        for item in long["indicators"]
    }

    return {
        "market": dataset.market,
        "label": dataset.label,
        "as_of_date": dataset.as_of_date.isoformat(),
        "short_score": short["score"],
        "long_score": long["score"],
        "dimension_scores": long["dimension_scores"],
        "short_dimension_scores": short["dimension_scores"],
        "position_label": position_band(fmean([short["score"], long["score"]]))["label"],
        "position_range_label": position_band(fmean([short["score"], long["score"]]))["range_label"],
        "meaning": position_band(fmean([short["score"], long["score"]]))["meaning"],
        "action_advice": position_band(fmean([short["score"], long["score"]]))["action_advice"],
        "indicators": list(all_indicators.values()),
        "stale_indicators": sorted(set(short["stale_indicators"] + long["stale_indicators"])),
        "history": historical_scores(dataset, long_window),
    }
