# CirclePredict

CirclePredict is a personal market-cycle scoring dashboard for A-shares, U.S.
equities, and crypto. It pulls real market data, converts each market into a
0-100 heat score, shows the historical score curve, and can send a daily
summary to WeCom.

This project is for personal allocation research only. It does not provide
investment advice, trading instructions, or a public advisory service.

## Features

- Scores A-shares, U.S. equities, and crypto with the same four-dimension model.
- Uses a fixed score formula:

  ```text
  total = valuation * 40% + sentiment * 30% + market * 20% + macro * 10%
  ```

- Higher scores mean the market is hotter, more expensive, or more crowded on
  a cycle basis.
- Shows short-cycle and long-cycle scores, dimension breakdowns, indicator
  explanations, source links, and historical curves.
- Supports market filtering and 3-month to 10-year chart ranges.
- Uses real runtime data only. If a source fails, the related indicator is
  omitted and reported instead of being replaced with fake data.
- Supports macOS LaunchAgent deployment and a daily 10:00 WeCom report.

## Repository Description

Use this for the GitHub repository description:

```text
Personal market-cycle dashboard for A-shares, U.S. equities, and crypto, with real-data scoring, historical curves, and WeCom daily reports.
```

## Score Model

The app scores each market with Howard Marks-inspired dimensions:

```text
total = valuation * 40% + sentiment * 30% + market * 20% + macro * 10%
```

Score semantics:

```text
0-30   extremely cold / undervalued zone
30-50  cold zone
50-70  reasonable zone
70-80  hot / overvalued zone
80-100 extremely hot / crowded zone
```

The score is a cycle-position signal, not a short-term price forecast.

## Requirements

- macOS or Linux
- Python 3.9+; Python 3.11+ recommended
- Network access to the configured data sources
- Optional: local HTTP proxy if your environment needs one
- Optional: WeCom group robot webhook for daily reports

## Run

Runtime config is read from `.env`:

```bash
CIRCLEPREDICT_HOST=0.0.0.0
CIRCLEPREDICT_PORT=15121
CIRCLEPREDICT_PROXY_URL=
CIRCLEPREDICT_HTTP_RETRIES=2
CIRCLEPREDICT_CURL_FALLBACK=1
CIRCLEPREDICT_CURL_RETRIES=2
CIRCLEPREDICT_FAILURE_RETRY_MINUTES=15
CIRCLEPREDICT_STOOQ_API_KEY=
CIRCLEPREDICT_YAHOO_COOKIE=
CIRCLEPREDICT_YAHOO_CURL_RETRIES=3
CIRCLEPREDICT_YAHOO_REQUEST_SPACING_SECONDS=1.5
CIRCLEPREDICT_YAHOO_IMPERSONATE=safari_ios,chrome
CIRCLEPREDICT_WECOM_WEBHOOK_URL=
CIRCLEPREDICT_PUBLIC_URL=http://127.0.0.1:15121/
```

For a new machine, copy `.env.example` to `.env`, then adjust the proxy,
WeCom webhook, and public dashboard URL as needed:

```bash
cp .env.example .env
```

If the machine can access the data sources directly, keep
`CIRCLEPREDICT_PROXY_URL=` empty. If it must use a local proxy, set the HTTP
proxy endpoint:

```bash
CIRCLEPREDICT_PROXY_URL=http://127.0.0.1:7890
```

SOCKS proxies are also supported:

```bash
CIRCLEPREDICT_PROXY_URL=socks5h://127.0.0.1:7890
```

You can also force direct access with:

```bash
CIRCLEPREDICT_PROXY_URL=direct
```

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

If Python HTTP requests fail but `curl -x ...` works in your terminal, keep
`CIRCLEPREDICT_CURL_FALLBACK=1`. The data client will try `requests` first and
then fall back to the system `curl` command with the same proxy. Generic curl
fallbacks retry `CIRCLEPREDICT_CURL_RETRIES` times to absorb transient empty
replies from sources such as Eastmoney.

Stooq may require an API key for CSV downloads. Keep
`CIRCLEPREDICT_STOOQ_API_KEY=` empty to disable Stooq fallback, or set it if
you have one.

Yahoo Finance may return `429 Too Many Requests` to clean server-side requests
even when the same URL works in Chrome. If that happens, copy the browser
cookie from a working Yahoo request and set it in `.env`:

```bash
CIRCLEPREDICT_YAHOO_COOKIE=A3=...; A1=...; A1S=...
```

The app uses this only for `query1.finance.yahoo.com` requests. Yahoo requests
are serialized globally and first use `curl_cffi` browser TLS impersonation
with `CIRCLEPREDICT_YAHOO_IMPERSONATE`; if that fails, the client falls back to
system `curl` with the same browser-like headers and cookie. This is necessary
because Chrome's copied curl does not include Chrome's TLS fingerprint, and
plain Python/curl requests can still hit 429. Do not commit this value.

To diagnose data-source access from the same code path used by the app:

```bash
python3 -m circle_predict.diagnostics
```

```bash
python3 -m circle_predict.server
```

By default the server listens on `0.0.0.0:15121`.

Open `http://127.0.0.1:15121` on the same Mac. From another device on the
same LAN, open `http://<mac-lan-ip>:15121`.

You can still override the bind address and port with environment variables:

```bash
CIRCLEPREDICT_HOST=127.0.0.1 CIRCLEPREDICT_PORT=8000 python3 -m circle_predict.server
```

The helper script starts the service with the production defaults:

```bash
./scripts/start_circle_predict.sh
```

Health check:

```bash
curl http://127.0.0.1:15121/health
```

Force a fresh data pull:

```bash
curl "http://127.0.0.1:15121/api/dashboard?refresh=1"
```

## macOS Auto Start

On another Mac, copy this project folder to the target machine, install Python
3 if needed, then run:

```bash
cd /path/to/CirclePredict
cp .env.example .env
./scripts/install_macos_launch_agent.sh
```

Edit `.env` before installing if the target Mac uses a different proxy port.
The LaunchAgent starts `scripts/start_circle_predict.sh`, and that script loads
`.env` every time the service starts.

This creates and loads the server LaunchAgent:

```text
~/Library/LaunchAgents/com.circlepredict.server.plist
```

The service will start when the current macOS user logs in, and `launchd` will
restart it if it exits.

The installer also creates and loads a second LaunchAgent for the daily WeCom
report:

```text
~/Library/LaunchAgents/com.circlepredict.daily-report.plist
```

It runs every day at 10:00 local macOS time and calls:

```bash
./scripts/send_daily_report.sh
```

Configure the WeCom group robot webhook in `.env` before enabling it:

```bash
CIRCLEPREDICT_WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
CIRCLEPREDICT_PUBLIC_URL=https://your-domain.example:15121/
```

Preview the report without sending:

```bash
./scripts/send_daily_report.sh --dry-run
```

Send once immediately:

```bash
./scripts/send_daily_report.sh
```

Useful commands:

```bash
launchctl print gui/$(id -u)/com.circlepredict.server
launchctl kickstart -k gui/$(id -u)/com.circlepredict.server
tail -f ~/Library/Logs/CirclePredict/server.out.log
tail -f ~/Library/Logs/CirclePredict/server.err.log
tail -f ~/Library/Logs/CirclePredict/daily-report.out.log
tail -f ~/Library/Logs/CirclePredict/daily-report.err.log
```

To remove auto start:

```bash
./scripts/uninstall_macos_launch_agent.sh
```

If other devices cannot open `http://<mac-lan-ip>:15121`, check macOS Firewall
and allow incoming connections for Python or port `15121`.

## Privacy And Secrets

Do not commit `.env`. It may contain:

- WeCom webhook URL
- Personal deployment URL
- Local proxy settings

The repository tracks `.env.example` only. Put real values in `.env` on each
machine.

## Test

```bash
python3 -m unittest discover -s tests
```

## Data

Runtime scoring uses real HTTP data only. If a source fails, the related
indicator is omitted and reported; the app does not generate replacement
market values.

Cache and retry behavior:

- A fully successful payload is cached in memory until the next Beijing-time
  06:00 rollover and is also written to `.cache/dashboard-last-success.json`.
- A degraded payload is cached only for `CIRCLEPREDICT_FAILURE_RETRY_MINUTES`
  minutes, so the service will retry during the same day.
- If a fresh pull fails but a previous successful payload exists, the app
  returns the previous successful real data and marks it as stale fallback.
- `GET /api/dashboard?refresh=1` bypasses the current in-memory cache and
  forces a fresh pull.

Current sources and fallbacks:

- A-shares: Eastmoney index K-line data; Yahoo Finance A-share index proxy if
  Eastmoney is unavailable; Yahoo Finance CNY exchange data.
- U.S. equities: Stooq ETF data for SPY/QQQ with Yahoo index fallback; Yahoo
  Finance VIX and 10-year yield data, with FRED DGS10 as the yield fallback.
- Crypto: Binance spot K-lines; CoinGecko market chart as a fallback; Binance
  futures funding, Alternative.me Fear & Greed, and Yahoo Finance DXY data.

`SampleMarketDataProvider` exists only for unit tests.

## Network Troubleshooting

If `./scripts/send_daily_report.sh --dry-run` shows SSL handshake timeouts,
HTTP 403, or `Remote end closed connection without response`, check whether
the proxy in `.env` matches the new machine:

```bash
curl -I https://query1.finance.yahoo.com
curl -x http://127.0.0.1:7890 -I https://query1.finance.yahoo.com
```

Use direct mode if the first command works:

```bash
CIRCLEPREDICT_PROXY_URL=
```

Use the proxy URL only if the second command works and the proxy app is running.
