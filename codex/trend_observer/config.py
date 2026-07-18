"""Static configuration for the trend observer."""

from pathlib import Path
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parents[1]
CSV_OUTPUT = BASE_DIR / "trend_observer_report.csv"
MARKDOWN_OUTPUT = BASE_DIR / "trend_observer_report.md"
DASHBOARD_OUTPUT = BASE_DIR / "dashboard_data.json"
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_OUTPUT = DATA_DIR / "trend_snapshot.json"
MARKET_VALUATION_OUTPUT = DATA_DIR / "market_valuation_snapshot.json"
HISTORY_JSON_OUTPUT = BASE_DIR / "trend_history.json"
HISTORY_CSV_OUTPUT = BASE_DIR / "trend_history.csv"
HISTORY_DIR = BASE_DIR / "history"
HISTORY_MANIFEST_OUTPUT = HISTORY_DIR / "manifest.json"
DIVIDENDS_CONFIG = BASE_DIR / "dividends.json"

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
HTTP_TIMEOUT = (5, 20)
MIN_HISTORY_ROWS = 260
TENCENT_LOOKBACK_ROWS = 1400
TENCENT_LOOKBACK_DAYS = 365 * 5 + 30
PE_MIN_PERIODS = 252
PE_WINDOW_ROWS = 2520
SNAPSHOT_SCHEMA_VERSION = 1
DATABASE_SCHEMA_VERSION = 1
FEISHU_KEYWORD = "妙啊"
DISCLAIMER = "本结果仅用于趋势观察，不构成投资建议。均线信号存在滞后性和假突破风险。"

# The dashboard always uses this explicit registry.  It deliberately contains
# 12 indices and the current 9 stock holdings; adding a historical file alone
# must not make a security appear in calculations or notifications.
PORTFOLIO_CATEGORIES = ("海外", "红利", "成长", "债券", "大宗商品", "现金")
STYLE_COMPASS_PAIRS = (
    ("红利低波", "创业板100"),
    ("国证自由现金流", "科创50"),
    ("沪深300", "中证500"),
)

ASSETS = [
    {"name": "沪深300", "symbol": "000300", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "中证A500", "symbol": "000510", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "中证500", "symbol": "000905", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "创业板100", "symbol": "sz399006", "market": "CN", "asset_type": "指数", "provider": "tencent"},
    {"name": "科创50", "symbol": "000688", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "恒生指数", "symbol": "hkHSI", "market": "HK", "asset_type": "指数", "provider": "tencent"},
    {"name": "红利低波", "symbol": "H30269", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "国证自由现金流", "symbol": "980092", "market": "CN", "asset_type": "指数", "provider": "cnindex"},
    {"name": "中证消费", "symbol": "000932", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "全指医药", "symbol": "000991", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "纳斯达克100", "symbol": "NDX100", "market": "US","asset_type": "指数","provider": "global_index","eastmoney_symbol": "100.NDX100","nasdaq_symbol": "NDX","yahoo_symbol": "^NDX"},
    {
        "name": "标普500",
        "symbol": "SPX",
        "market": "US",
        "asset_type": "指数",
        "provider": "global_index",
        "eastmoney_symbol": "100.SPX",
        "yahoo_symbol": "^GSPC",
    },
    {"name": "长江电力", "symbol": "sh600900", "market": "CN", "asset_type": "股票", "provider": "tencent", "industry_template": "utility_concession"},
    {"name": "招商银行", "symbol": "sh600036", "market": "CN", "asset_type": "股票", "provider": "tencent", "industry_template": "bank"},
    {"name": "中国神华", "symbol": "sh601088", "market": "CN", "asset_type": "股票", "provider": "tencent", "industry_template": "resource_cycle"},
    {"name": "中国海油", "symbol": "sh600938", "market": "CN", "asset_type": "股票", "provider": "tencent", "industry_template": "resource_cycle"},
    {"name": "美的集团", "symbol": "sz000333", "market": "CN", "asset_type": "股票", "provider": "tencent", "industry_template": "durable_manufacturing"},
    {"name": "格力电器", "symbol": "sz000651", "market": "CN", "asset_type": "股票", "provider": "tencent", "industry_template": "durable_manufacturing"},
    {"name": "粤高速A", "symbol": "sz000429", "market": "CN", "asset_type": "股票", "provider": "tencent", "industry_template": "utility_concession"},
    {"name": "国电电力", "symbol": "sh600795", "market": "CN", "asset_type": "股票", "provider": "tencent", "industry_template": "utility_concession"},
    {"name": "云铝股份", "symbol": "sz000807", "market": "CN", "asset_type": "股票", "provider": "tencent", "industry_template": "resource_cycle"},
]

REPORT_COLUMNS = [
    "name",
    "symbol",
    "market",
    "asset_type",
    "date",
    "close",
    "daily_return",
    "return_ytd",
    "return_1w",
    "return_1m",
    "return_1y",
    "return_3y",
    "MA20",
    "MA60",
    "MA120",
    "MA200",
    "ma20_slope_5d",
    "ma60_slope_10d",
    "ma120_slope_20d",
    "ma200_slope_40d",
    "short_trend",
    "mid_trend",
    "long_trend",
    "overall_status",
    "investment_advice",
    "signal_tags",
    "last_year_dividend",
    "dividend_yield",
    "pe",
    "pe_percentile",
    "pe_percentile_period",
    "valuation_status",
    "error",
]
