from __future__ import annotations

import unittest
from importlib import reload
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import circle_predict.data_provider as data_provider_module
from circle_predict.config import LONG_WINDOW, SHORT_WINDOW
from circle_predict.data_provider import (
    DataClient,
    RealMarketDataProvider,
    SampleMarketDataProvider,
    cn_equity_completed_through,
    normalize_proxy_url,
    trim_series_through,
)
from circle_predict.daily_report import build_daily_report
import circle_predict.dashboard as dashboard_module
from circle_predict.dashboard import (
    build_dashboard_payload,
    clear_dashboard_cache,
    get_dashboard_payload,
    next_cache_expiry,
    payload_has_partial_failures,
    payload_is_degraded,
)
from circle_predict.models import Direction, IndicatorObservation, MarketDataset, ScoreWindow
from circle_predict.scoring import build_market_payload, percentile_score, score_market_window


UTC = timezone.utc


class ScoringTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_dashboard_cache()

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

    def test_degraded_payload_is_detected(self) -> None:
        payload = build_dashboard_payload(SampleMarketDataProvider())
        self.assertFalse(payload_is_degraded(payload))
        self.assertFalse(payload_has_partial_failures(payload))

        payload["data_notes"].append("Yahoo 获取失败，宏观维度缺失。")
        self.assertFalse(payload_is_degraded(payload))
        self.assertTrue(payload_has_partial_failures(payload))

        payload["markets"][0]["available"] = False
        self.assertTrue(payload_is_degraded(payload))

    def test_failed_refresh_uses_recent_success_cache(self) -> None:
        class FailingProvider:
            mode = "test"
            notes = ["_load_cn_equity 失败：HTTP Error 429: Too Many Requests"]
            failures = {"cn_equity": "HTTP Error 429: Too Many Requests"}

            def load(self):
                return [dataset for dataset in SampleMarketDataProvider().load() if dataset.market != "cn_equity"]

        original_disk_cache_path = dashboard_module.DISK_CACHE_PATH
        dashboard_module.DISK_CACHE_PATH = original_disk_cache_path.parent / "test-dashboard-last-success.json"
        try:
            success_payload = get_dashboard_payload(SampleMarketDataProvider(), force_refresh=True)
            failed_payload = get_dashboard_payload(FailingProvider(), force_refresh=True)

            self.assertFalse(success_payload.get("using_stale_success_cache", False))
            self.assertTrue(failed_payload["using_stale_success_cache"])
            self.assertEqual(failed_payload["cache"]["status"], "stale_fallback")
            self.assertTrue(all(market.get("available") is not False for market in failed_payload["markets"]))
        finally:
            dashboard_module.DISK_CACHE_PATH.unlink(missing_ok=True)
            dashboard_module.DISK_CACHE_PATH = original_disk_cache_path

    def test_partial_refresh_returns_current_payload_without_stale_cache(self) -> None:
        class PartialProvider(SampleMarketDataProvider):
            notes = ["Yahoo 获取失败，宏观维度缺失。"]

        clear_dashboard_cache()
        payload = get_dashboard_payload(PartialProvider(), force_refresh=True)

        self.assertFalse(payload.get("using_stale_success_cache", False))
        self.assertEqual(payload["cache"]["status"], "partial_miss")
        self.assertTrue(all(market.get("available") is not False for market in payload["markets"]))

    def test_daily_report_contains_market_scores(self) -> None:
        payload = build_dashboard_payload(SampleMarketDataProvider())
        report = build_daily_report(payload)

        self.assertIn("CirclePredict 每日市场周期评分", report)
        self.assertIn("A 股", report)
        self.assertIn("美股", report)
        self.assertIn("虚拟货币", report)
        self.assertIn("短周期", report)
        self.assertIn("四维", report)

    def test_proxy_url_can_be_disabled(self) -> None:
        self.assertEqual(normalize_proxy_url(""), "")
        self.assertEqual(normalize_proxy_url("direct"), "")
        self.assertEqual(normalize_proxy_url("none"), "")
        self.assertEqual(normalize_proxy_url("http://127.0.0.1:7890"), "http://127.0.0.1:7890")

    def test_curl_fallback_uses_configured_proxy(self) -> None:
        client = DataClient(proxy_url="http://127.0.0.1:7890", curl_fallback=True)
        completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")

        with patch("circle_predict.data_provider.subprocess.run", return_value=completed) as run:
            self.assertEqual(client._curl_text("https://example.com", 8, {"User-Agent": "UA"}), "{}")

        command = run.call_args.args[0]
        self.assertIn("-x", command)
        self.assertIn("http://127.0.0.1:7890", command)

    def test_curl_uses_cookie_jar_flag_for_cookie_header(self) -> None:
        client = DataClient(proxy_url="http://127.0.0.1:7890", curl_fallback=True)
        completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")

        with patch("circle_predict.data_provider.subprocess.run", return_value=completed) as run:
            client._curl_text("https://example.com", 8, {"User-Agent": "UA", "Cookie": "A1=abc"})

        command = run.call_args.args[0]
        self.assertIn("-b", command)
        self.assertIn("A1=abc", command)
        self.assertNotIn("Cookie: A1=abc", command)

    def test_yahoo_headers_include_configured_cookie(self) -> None:
        with patch.dict("os.environ", {"CIRCLEPREDICT_YAHOO_COOKIE": "A1=abc"}):
            reloaded = reload(data_provider_module)

        self.assertEqual(reloaded.yahoo_browser_headers()["Cookie"], "A1=abc")
        reload(data_provider_module)

    def test_data_client_records_curl_transport(self) -> None:
        client = DataClient(proxy_url="http://127.0.0.1:7890", curl_fallback=True)
        completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")

        with patch.object(client.session, "get", side_effect=RuntimeError("requests failed")):
            with patch("circle_predict.data_provider.subprocess.run", return_value=completed):
                self.assertEqual(client.text("https://example.com"), "{}")

        self.assertEqual(client.last_transport, "curl")

    def test_generic_curl_fallback_retries_empty_reply(self) -> None:
        client = DataClient(proxy_url="http://127.0.0.1:7890", curl_fallback=True)
        failed = SimpleNamespace(returncode=52, stdout="", stderr="curl: (52) Empty reply from server")
        completed = SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

        with patch.object(client.session, "get", side_effect=RuntimeError("requests failed")):
            with patch("circle_predict.data_provider.time.sleep"):
                with patch("circle_predict.data_provider.subprocess.run", side_effect=[failed, completed]) as run:
                    self.assertEqual(client.text("https://push2his.eastmoney.com/example"), '{"ok": true}')

        self.assertEqual(run.call_count, 2)
        self.assertEqual(client.last_transport, "curl")

    def test_yahoo_requests_prefer_curl_transport(self) -> None:
        client = DataClient(proxy_url="http://127.0.0.1:7890", curl_fallback=True)
        completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")

        with patch.object(client.session, "get") as get:
            with patch.object(data_provider_module, "curl_requests", None):
                with patch("circle_predict.data_provider.subprocess.run", return_value=completed):
                    self.assertEqual(client.text("https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"), "{}")

        get.assert_not_called()
        self.assertEqual(client.last_transport, "curl")

    def test_yahoo_uses_curl_cffi_when_available(self) -> None:
        client = DataClient(proxy_url="http://127.0.0.1:7890", curl_fallback=True)
        response = SimpleNamespace(text="{}", status_code=200, raise_for_status=lambda: None)
        fake_curl_requests = SimpleNamespace(get=lambda *args, **kwargs: response)

        with patch.object(client.session, "get") as get:
            with patch.object(data_provider_module, "curl_requests", fake_curl_requests):
                with patch("circle_predict.data_provider.subprocess.run") as run:
                    self.assertEqual(client.text("https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"), "{}")

        get.assert_not_called()
        run.assert_not_called()
        self.assertTrue(client.last_transport.startswith("curl_cffi:"))

    def test_yahoo_curl_retries_before_failing(self) -> None:
        client = DataClient(proxy_url="http://127.0.0.1:7890", curl_fallback=True)
        failed = SimpleNamespace(returncode=56, stdout="", stderr="curl: (56) The requested URL returned error: 429")
        completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")

        with patch.object(client.session, "get") as get:
            with patch.object(data_provider_module, "curl_requests", None):
                with patch("circle_predict.data_provider.time.sleep"):
                    with patch("circle_predict.data_provider.subprocess.run", side_effect=[failed, failed, completed]) as run:
                        self.assertEqual(client.text("https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"), "{}")

        self.assertEqual(run.call_count, 3)
        get.assert_not_called()

    def test_us_equity_uses_yahoo_tnx_before_fred(self) -> None:
        provider = RealMarketDataProvider()
        base = date(2026, 1, 1)
        series = [IndicatorObservation(base + timedelta(days=index), float(100 + index)) for index in range(260)]

        with patch.object(provider, "_stooq_daily", return_value=series):
            with patch.object(provider, "_yahoo_chart", return_value=series) as yahoo:
                with patch.object(provider, "_fred_csv", return_value=series) as fred:
                    dataset = provider._load_us_equity()

        self.assertEqual(dataset.market, "us_equity")
        self.assertIn("us_10y_yield", dataset.indicators)
        yahoo.assert_any_call("^VIX")
        yahoo.assert_any_call("^TNX")
        fred.assert_not_called()

    def test_cn_equity_completed_through_uses_previous_day_before_close(self) -> None:
        morning = datetime(2026, 6, 3, 10, 0, tzinfo=data_provider_module.BEIJING_TZ)
        after_close = datetime(2026, 6, 3, 15, 45, tzinfo=data_provider_module.BEIJING_TZ)

        self.assertEqual(cn_equity_completed_through(morning), date(2026, 6, 2))
        self.assertEqual(cn_equity_completed_through(after_close), date(2026, 6, 3))

    def test_trim_series_through_excludes_intraday_current_date(self) -> None:
        series = [
            IndicatorObservation(date(2026, 6, 1), 10),
            IndicatorObservation(date(2026, 6, 2), 20),
            IndicatorObservation(date(2026, 6, 3), 1),
        ]

        trimmed = trim_series_through(series, date(2026, 6, 2))

        self.assertEqual([point.date for point in trimmed], [date(2026, 6, 1), date(2026, 6, 2)])

    def test_cn_equity_loader_excludes_unfinished_current_day(self) -> None:
        provider = RealMarketDataProvider()
        index_data = {
            "close": [
                IndicatorObservation(date(2026, 6, 1), 100),
                IndicatorObservation(date(2026, 6, 2), 110),
                IndicatorObservation(date(2026, 6, 3), 120),
            ],
            "amount": [
                IndicatorObservation(date(2026, 6, 1), 1000),
                IndicatorObservation(date(2026, 6, 2), 1100),
                IndicatorObservation(date(2026, 6, 3), 100),
            ],
        }

        with patch.object(provider, "_eastmoney_index", return_value=index_data):
            with patch.object(provider, "_yahoo_chart", return_value=[]):
                with patch("circle_predict.data_provider.cn_equity_completed_through", return_value=date(2026, 6, 2)):
                    dataset = provider._load_cn_equity()

        self.assertEqual(dataset.as_of_date, date(2026, 6, 2))
        self.assertTrue(all(point.date <= date(2026, 6, 2) for series in dataset.indicators.values() for point in series))
        self.assertIn("A 股已排除未收盘日线", " ".join(provider.notes))


if __name__ == "__main__":
    unittest.main()
