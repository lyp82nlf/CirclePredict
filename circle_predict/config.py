from __future__ import annotations

from circle_predict.models import Dimension, Direction, IndicatorDefinition, ScoreWindow


DIMENSION_WEIGHTS: dict[Dimension, float] = {
    Dimension.VALUATION: 0.40,
    Dimension.SENTIMENT: 0.30,
    Dimension.MARKET: 0.20,
    Dimension.MACRO: 0.10,
}

SHORT_WINDOW = ScoreWindow(name="short", days=365 * 2)
LONG_WINDOW = ScoreWindow(name="long", days=365 * 10)

POSITION_BANDS = (
    {
        "min": 80,
        "max": 100,
        "range_label": "80-100分",
        "label": "极度高估区",
        "meaning": "市场极度狂热，泡沫明显",
        "action_advice": "清仓或只留10%以下，远离市场",
    },
    {
        "min": 70,
        "max": 80,
        "range_label": "70-80分",
        "label": "高估区",
        "meaning": "市场偏乐观，风险开始累积",
        "action_advice": "逐步减仓，仓位20%-30%",
    },
    {
        "min": 50,
        "max": 70,
        "range_label": "50-70分",
        "label": "合理区",
        "meaning": "市场平稳，没有明显偏差",
        "action_advice": "持有为主，仓位30%-50%",
    },
    {
        "min": 30,
        "max": 50,
        "range_label": "30-50分",
        "label": "低估区",
        "meaning": "市场偏悲观，但有些机会",
        "action_advice": "逐步建仓，仓位50%-70%",
    },
    {
        "min": 0,
        "max": 30,
        "range_label": "0-30分",
        "label": "极度低估区",
        "meaning": "市场极度悲观，遍地便宜货",
        "action_advice": "大胆买入，仓位可以到80%-90%",
    },
)

MARKETS = {
    "cn_equity": "A 股",
    "us_equity": "美股",
    "crypto": "虚拟货币",
}

SOURCE_LINKS = {
    "Tushare": "https://tushare.pro/document/2",
    "AKShare": "https://akshare.akfamily.xyz/",
    "Yahoo Finance": "https://query1.finance.yahoo.com/",
    "CoinGlass": "https://docs.coinglass.com/v4.0/reference/cryptofear-greedindex",
    "CoinMarketCap": "https://coinmarketcap.com/charts/fear-and-greed-index/",
    "Binance": "https://academy.binance.com/en/articles/what-is-the-crypto-fear-and-greed-index",
    "Alternative.me": "https://alternative.me/crypto/fear-and-greed-index/",
    "Eastmoney": "https://quote.eastmoney.com/center/",
    "Binance Spot": "https://api.binance.com/api/v3/klines",
    "Binance Futures": "https://fapi.binance.com/fapi/v1/fundingRate",
    "Stooq": "https://stooq.com/q/d/l/",
    "FRED": "https://fred.stlouisfed.org/graph/fredgraph.csv",
    "CoinGecko": "https://docs.coingecko.com/reference/coins-id-market-chart-range",
    "Derived": "",
}


def source_links_for(source: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for label, url in SOURCE_LINKS.items():
        if label in source and url:
            links.append({"label": label, "url": url})
    return links

INDICATORS: list[IndicatorDefinition] = [
    IndicatorDefinition("cn_valuation_position", "cn_equity", "A 股指数位置代理", Dimension.VALUATION, Direction.HIGH_BAD, 1, "Eastmoney / Yahoo Finance / Derived", "index", "沪深300与中证500价格在历史区间越高，位置分越高"),
    IndicatorDefinition("cn_turnover_heat", "cn_equity", "A 股成交热度", Dimension.SENTIMENT, Direction.HIGH_BAD, 1, "Eastmoney / Derived", "index", "成交额处于历史高位会推高位置分"),
    IndicatorDefinition("cn_trend_200d", "cn_equity", "A 股 200 日趋势", Dimension.MARKET, Direction.HIGH_GOOD, 1, "Eastmoney / Yahoo Finance / Derived", "%", "趋势越弱，市场位置分越高"),
    IndicatorDefinition("cn_momentum_60d", "cn_equity", "A 股 60 日动量", Dimension.MARKET, Direction.HIGH_GOOD, 1, "Eastmoney / Yahoo Finance / Derived", "%", "中期动量代理"),
    IndicatorDefinition("cn_volatility", "cn_equity", "A 股波动率", Dimension.MARKET, Direction.HIGH_BAD, 1, "Eastmoney / Yahoo Finance / Derived", "%", "波动越高，市场位置分越高"),
    IndicatorDefinition("cn_fx_pressure", "cn_equity", "美元兑人民币压力", Dimension.MACRO, Direction.HIGH_BAD, 1, "Yahoo Finance", "index", "美元兑人民币处于高位时，宏观位置分更高"),

    IndicatorDefinition("us_valuation_position", "us_equity", "美股指数位置代理", Dimension.VALUATION, Direction.HIGH_BAD, 1, "Stooq / Yahoo Finance / Derived", "index", "标普500与纳指在历史区间越高，位置分越高"),
    IndicatorDefinition("us_vix", "us_equity", "VIX 位置", Dimension.SENTIMENT, Direction.HIGH_BAD, 1, "Yahoo Finance", "index", "VIX 越高，风险压力越高"),
    IndicatorDefinition("us_trend_200d", "us_equity", "美股 200 日趋势", Dimension.MARKET, Direction.HIGH_GOOD, 1, "Stooq / Yahoo Finance / Derived", "%", "趋势越弱，市场位置分越高"),
    IndicatorDefinition("us_momentum_60d", "us_equity", "美股 60 日动量", Dimension.MARKET, Direction.HIGH_GOOD, 1, "Stooq / Yahoo Finance / Derived", "%", "中期动量代理"),
    IndicatorDefinition("us_volatility", "us_equity", "美股波动率", Dimension.MARKET, Direction.HIGH_BAD, 1, "Stooq / Yahoo Finance / Derived", "%", "波动越高，市场位置分越高"),
    IndicatorDefinition("us_10y_yield", "us_equity", "美国 10 年期国债收益率", Dimension.MACRO, Direction.HIGH_BAD, 1, "Yahoo Finance / FRED", "%", "长端利率越高，宏观位置分越高"),

    IndicatorDefinition("crypto_price_position", "crypto", "BTC/ETH 价格位置", Dimension.VALUATION, Direction.HIGH_BAD, 1, "Binance Spot / CoinGecko / Derived", "index", "BTC 与 ETH 价格越接近历史高位，位置分越高"),
    IndicatorDefinition("crypto_volume_heat", "crypto", "加密成交热度", Dimension.SENTIMENT, Direction.HIGH_BAD, 1, "Binance Spot / CoinGecko / Derived", "index", "成交过热会推高位置分"),
    IndicatorDefinition("crypto_fear_greed", "crypto", "加密恐惧贪婪", Dimension.SENTIMENT, Direction.HIGH_BAD, 1, "Alternative.me", "index", "贪婪越高，位置分越高"),
    IndicatorDefinition("crypto_funding", "crypto", "BTC 永续资金费率", Dimension.SENTIMENT, Direction.HIGH_BAD, 1, "Binance Futures", "%", "多头拥挤会推高位置分"),
    IndicatorDefinition("crypto_trend_200d", "crypto", "加密 200 日趋势", Dimension.MARKET, Direction.HIGH_GOOD, 1, "Binance Spot / CoinGecko / Derived", "%", "趋势越弱，市场位置分越高"),
    IndicatorDefinition("crypto_momentum_60d", "crypto", "加密 60 日动量", Dimension.MARKET, Direction.HIGH_GOOD, 1, "Binance Spot / CoinGecko / Derived", "%", "中期动量代理"),
    IndicatorDefinition("crypto_volatility", "crypto", "加密波动率", Dimension.MARKET, Direction.HIGH_BAD, 1, "Binance Spot / CoinGecko / Derived", "%", "波动越高，市场位置分越高"),
    IndicatorDefinition("crypto_dxy", "crypto", "美元指数位置", Dimension.MACRO, Direction.HIGH_BAD, 1, "Yahoo Finance", "index", "美元走强通常压制风险资产"),
]


INDICATORS_BY_ID = {indicator.id: indicator for indicator in INDICATORS}
