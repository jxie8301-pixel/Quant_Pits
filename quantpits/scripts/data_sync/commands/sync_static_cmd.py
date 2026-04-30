"""
sync-static子命令 — 静态表同步。
"""

from quantpits.utils import env

import os
import logging


def run_sync_static(
    project_root: str | None,
    workspace_dir: str,
    log_level: str,
) -> None:
    """
    执行静态表同步。

    Args:
        project_root: 项目根目录
        workspace_dir: Workspace根目录
        log_level: 日志级别
    """
    from quantpits.scripts.data_sync.path_resolver import resolve_project_root
    from quantpits.scripts.data_sync.sync.sync_static import sync_static_tables

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError("环境变量TUSHARE_TOKEN未设置")

    project_root = resolve_project_root(project_root)

    logger = logging.getLogger(__name__)
    logger.info("=== 静态表同步 ===")

    report = sync_static_tables(token, project_root)

    logger.info(
        f"静态表同步完成: stock_basic={report.stock_basic_count}, "
        f"trade_cal={report.trade_cal_count}"
    )
    if report.errors:
        for err in report.errors:
            logger.error(f"错误: {err}")
