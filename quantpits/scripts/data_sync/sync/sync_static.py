"""
静态表同步：stock_basic / trade_cal。
单线程，全量覆盖写，每次运行都重新拉取。
"""

from quantpits.utils import env

import logging
import os

from quantpits.scripts.data_sync.fetchers.static import fetch_stock_basic, fetch_trade_cal
from quantpits.scripts.data_sync.storage import save_static
from quantpits.scripts.data_sync.sync.sync_pipeline import StaticReport

logger = logging.getLogger(__name__)


def sync_static_tables(token: str, project_root: str) -> StaticReport:
    """
    同步所有静态表，建议每周运行一次，保证退市、新股信息最新。

    Args:
        token: Tushare API Token
        project_root: 项目根目录

    Returns:
        StaticReport 同步报告
    """
    logger.info("═" * 60)
    logger.info("开始同步静态表")
    logger.info("═" * 60)

    report = StaticReport()

    raw_dir = os.path.join(project_root, "data", "raw")

    import tushare as ts

    ts.set_token(token)
    pro = ts.pro_api()

    try:
        df_basic = fetch_stock_basic(pro)
        save_static(df_basic, "stock_basic", raw_dir)
        report.stock_basic_count = len(df_basic)
        logger.info(f"[stock_basic] ✓ 完成，共 {len(df_basic)} 只股票")
    except Exception as e:
        report.errors.append(f"stock_basic: {e}")
        logger.error(f"[stock_basic] ✗ 失败：{e}", exc_info=True)

    try:
        df_cal = fetch_trade_cal(pro)
        save_static(df_cal, "trade_cal", raw_dir)
        open_days = df_cal[df_cal['is_open'] == 1]
        report.trade_cal_count = len(open_days)
        logger.info(f"[trade_cal] ✓ 完成，共 {len(open_days)} 个交易日")
    except Exception as e:
        report.errors.append(f"trade_cal: {e}")
        logger.error(f"[trade_cal] ✗ 失败：{e}", exc_info=True)

    logger.info("静态表同步完成")
    return report
