from __future__ import annotations

import json
import os
import subprocess

import requests

from circle_predict.data_provider import DataClient, USER_AGENT, YAHOO_IMPERSONATE, curl_requests, yahoo_browser_headers
from circle_predict.env import load_env


TARGETS = {
    "Yahoo S&P 500": "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=5d&interval=1d",
    "Stooq SPY": "https://stooq.com/q/d/l/?s=spy.us&i=d",
    "Eastmoney CSI300": (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        "?secid=1.000300&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&beg=20260101&end=20500101"
    ),
    "Binance BTC": "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=5",
}


def configured_proxy() -> str:
    return os.getenv("CIRCLEPREDICT_PROXY_URL", "").strip()


def print_result(label: str, ok: bool, detail: str) -> None:
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {label}: {detail}")


def test_requests(url: str, proxy_url: str) -> None:
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else {}
    session = requests.Session()
    session.trust_env = False
    headers = yahoo_browser_headers() if "query1.finance.yahoo.com" in url else {"User-Agent": USER_AGENT}
    response = session.get(url, headers=headers, proxies=proxies, timeout=12)
    response.raise_for_status()
    print_result("requests", True, f"status={response.status_code}, bytes={len(response.text)}")


def test_curl(url: str, proxy_url: str) -> None:
    headers = yahoo_browser_headers() if "query1.finance.yahoo.com" in url else {"User-Agent": USER_AGENT}
    command = ["curl", "-fsSL", "--compressed", "--max-time", "12", "-A", headers["User-Agent"]]
    if proxy_url:
        command.extend(["-x", proxy_url])
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
        raise RuntimeError(result.stderr.strip() or f"curl exited with {result.returncode}")
    print_result("curl", True, f"bytes={len(result.stdout)}")


def test_curl_cffi(url: str, proxy_url: str) -> None:
    if curl_requests is None:
        raise RuntimeError("curl_cffi is not installed")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else {}
    headers = yahoo_browser_headers() if "query1.finance.yahoo.com" in url else {"User-Agent": USER_AGENT}
    last_error: Exception | None = None
    for profile in YAHOO_IMPERSONATE or ["chrome"]:
        try:
            response = curl_requests.get(url, headers=headers, proxies=proxies, timeout=12, impersonate=profile)
            response.raise_for_status()
            print_result("curl_cffi", True, f"profile={profile}, status={response.status_code}, bytes={len(response.text)}")
            return
        except Exception as error:
            last_error = error
    if last_error is not None:
        raise last_error
    raise RuntimeError("curl_cffi failed without an error")


def test_data_client(url: str) -> None:
    client = DataClient()
    text = client.text(url, timeout=12)
    detail = f"transport={client.last_transport}, bytes={len(text)}"
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "chart" in parsed:
            error = parsed["chart"].get("error")
            detail += f", yahoo_error={error}"
    except json.JSONDecodeError:
        pass
    print_result("DataClient", True, detail)


def main() -> None:
    load_env()
    proxy_url = configured_proxy()
    print(f"proxy={proxy_url or 'direct'}")
    print(f"curl_fallback={os.getenv('CIRCLEPREDICT_CURL_FALLBACK', '1')}")
    for name, url in TARGETS.items():
        print(f"\n== {name} ==")
        testers = (
            ("requests", lambda: test_requests(url, proxy_url)),
            ("curl", lambda: test_curl(url, proxy_url)),
            ("DataClient", lambda: test_data_client(url)),
        )
        if "query1.finance.yahoo.com" in url:
            testers = (
                ("DataClient", lambda: test_data_client(url)),
                ("curl_cffi", lambda: test_curl_cffi(url, proxy_url)),
                ("curl", lambda: test_curl(url, proxy_url)),
                ("requests", lambda: test_requests(url, proxy_url)),
            )
        for label, tester in testers:
            try:
                tester()
            except Exception as error:
                print_result(label, False, str(error))


if __name__ == "__main__":
    main()
