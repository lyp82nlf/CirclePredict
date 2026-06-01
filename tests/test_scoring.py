from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from circle_predict.config import LONG_WINDOW, SHORT_WINDOW
from circle_predict.data_provider import SampleMarketDataProvider
from circle_predict.daily_report import build_daily_report
from circle_predict.dashboard import build_dashboard_payload, next_cache_expiry
from circle_predict.models import Direction, IndicatorObservation, MarketDataset, ScoreWindow
from circle_predict.scoring import build_market_payload, percentile_score, score_market_window


UTC = timezone.utc


class ScoringTests(unittest.TestCase):
    def test_percentile_score_handles_direction(self) -> None:
        values = [10, 20, 30, 40]

        self.assertEqual(percentile_score(values, 40, Direction.HIGH_GOOD), 100)
        self.assertEqual(percentile_score(values, 40, Direction.HIGH_BAD), 0)

    def test_weighted_total_uses_dimension_formula(self) -> None:
        as_of = date(2026, 5, 25)
        observations = [IndicatorObservation(as_of - timedelta(days=day), float(day + 1)) for day in range(10)]
        dataset = MarketDataset(
            market="cn_equity",
            label="A 股",
            as_of_date=as_of,
            indicators={
                "cn_valuation_position": observations,
                "cn_turnover_heat": observations,
                "cn_trend_200d": observations,
                "cn_fx_pressure": observations,
            },
        )

        scored = score_market_window(dataset, ScoreWindow(name="test", days=30))
        dims = scored["dimension_scores"]
        expected = (
            dims["valuation"] * 0.4
            + dims["sentiment"] * 0.3
            + dims["market"] * 0.2
            + dims["macro"] * 0.1
        )

        self.assertAlmostEqual(scored["score"], round(expected, 2))

    def test_missing_current_value_uses_latest_valid_and_marks_stale(self) -> None:
        as_of = date(2026, 5, 25)
        dataset = MarketDataset(
            market="cn_equity",
            label="A 股",
            as_of_date=as_of,
            indicators={
                "cn_valuation_position": [
                    IndicatorObservation(as_of - timedelta(days=2), 20),
                    IndicatorObservation(as_of - timedelta(days=1), 18),
                    IndicatorObservation(as_of, None),
                ],
                "cn_turnover_heat": [
                    IndicatorObservation(as_of - timedelta(days=2), 30),
                    IndicatorObservation(as_of - timedelta(days=1), 35),
                    IndicatorObservation(as_of, 40),
                ],
            },
        )

        payload = build_market_payload(dataset, SHORT_WINDOW, LONG_WINDOW)
        stale = [item for item in payload["indicators"] if item["id"] == "cn_valuation_position"][0]

        self.assertTrue(stale["used_stale_value"])
        self.assertEqual(stale["as_of_date"], "2026-05-24")
        self.assertIn("cn_valuation_position", payload["stale_indicators"])

    def test_dashboard_payload_contains_three_markets(self) -> None:
        payload = build_dashboard_payload(SampleMarketDataProvider())
        markets = {market["market"] for market in payload["markets"]}

        self.assertEqual(markets, {"cn_equity", "us_equity", "crypto"})
        for market in payload["markets"]:
            self.assertIn("short_score", market)
            self.assertIn("long_score", market)
            self.assertIn("dimension_scores", market)
            self.assertIn("position_label", market)
            self.assertIn("action_advice", market)
            self.assertNotIn("recommendation_label", market)
            self.assertIn("indicators", market)
            self.assertIn("history", market)
        self.assertIn("data_mode", payload)
        self.assertIn("data_notes", payload)

    def test_indicator_payload_includes_source_links(self) -> None:
        payload = build_dashboard_payload(SampleMarketDataProvider())
        linked_indicators = [
            indicator
            for market in payload["markets"]
            for indicator in market["indicators"]
            if indicator["source_links"]
        ]

        self.assertTrue(linked_indicators)
        self.assertTrue(all("url" in link and "label" in link for item in linked_indicators for link in item["source_links"]))

    def test_position_score_is_inverse_of_opportunity_score(self) -> None:
        payload = build_dashboard_payload(SampleMarketDataProvider())
        indicator = payload["markets"][0]["indicators"][0]

        self.assertAlmostEqual(indicator["score"], 100 - indicator["opportunity_score"])

    def test_cache_expires_at_next_beijing_6am(self) -> None:
        before_rollover = datetime(2026, 5, 25, 20, 0, tzinfo=UTC)
        after_rollover = datetime(2026, 5, 25, 23, 0, tzinfo=UTC)

        self.assertEqual(next_cache_expiry(before_rollover), datetime(2026, 5, 25, 22, 0, tzinfo=UTC))
        self.assertEqual(next_cache_expiry(after_rollover), datetime(2026, 5, 26, 22, 0, tzinfo=UTC))

    def test_dashboard_keeps_unavailable_market_card(self) -> None:
        class PartialProvider:
            mode = "test"
            notes = []
            failures = {"cn_equity": "A 股接口超时"}

            def load(self):
                return [dataset for dataset in SampleMarketDataProvider().load() if dataset.market != "cn_equity"]

        payload = build_dashboard_payload(PartialProvider())
        markets = {market["market"]: market for market in payload["markets"]}

        self.assertEqual(set(markets), {"cn_equity", "us_equity", "crypto"})
        self.assertFalse(markets["cn_equity"]["available"])
        self.assertEqual(markets["cn_equity"]["position_label"], "数据不可用")
        self.assertIn("A 股接口超时", markets["cn_equity"]["unavailable_reason"])

    def test_daily_report_contains_market_scores(self) -> None:
        payload = build_dashboard_payload(SampleMarketDataProvider())
        report = build_daily_report(payload)

        self.assertIn("CirclePredict 每日市场周期评分", report)
        self.assertIn("A 股", report)
        self.assertIn("美股", report)
        self.assertIn("虚拟货币", report)
        self.assertIn("短周期", report)
        self.assertIn("四维", report)


if __name__ == "__main__":
    unittest.main()
