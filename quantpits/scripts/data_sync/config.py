"""
data_sync全局配置 — 限速参数、起始日期、重试配置。

Token仅从环境变量TUSHARE_TOKEN获取，路径通过path_resolver动态解析。
"""

from quantpits.utils import env

START_DATES: dict[str, str | None] = {
    "stock_basic": None,
    "trade_cal": None,
    "stk_factor_pro": "20180101",
    "cyq_perf": "20180101",
    "moneyflow": "20180101",
    "margin_detail": "20180101",
    "stk_limit": "20180101",
    "suspend_d": "20180101",
    "top_list": "20180101",
    "stk_holdertrade": "20180101",
    "share_float": "20180101",
    "forecast_vip": "20180101",
    "express_vip": "20180101",
}

RATE_CONFIG: dict[str, dict] = {
    "stk_factor_pro": {"rate": 30, "max_daily": None, "workers": 4},
    "cyq_perf": {"rate": 30, "max_daily": 20000, "workers": 4},
    "forecast_vip": {"rate": 30, "max_daily": None, "workers": 2},
    "express_vip": {"rate": 30, "max_daily": None, "workers": 2},
    "moneyflow": {"rate": 150, "max_daily": None, "workers": 8},
    "margin_detail": {"rate": 150, "max_daily": None, "workers": 8},
    "stk_limit": {"rate": 150, "max_daily": None, "workers": 8},
    "suspend_d": {"rate": 150, "max_daily": None, "workers": 8},
    "top_list": {"rate": 150, "max_daily": None, "workers": 8},
    "stk_holdertrade": {"rate": 150, "max_daily": None, "workers": 4},
    "share_float": {"rate": 150, "max_daily": None, "workers": 4},
    "stock_basic": {"rate": 150, "max_daily": None, "workers": 1},
    "trade_cal": {"rate": 150, "max_daily": None, "workers": 1},
}

RETRY_TIMES: int = 3
RETRY_DELAY: int = 5
GLOBAL_RATE_PER_MINUTE: int = 280
