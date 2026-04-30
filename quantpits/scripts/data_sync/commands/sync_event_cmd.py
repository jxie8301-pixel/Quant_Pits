"""
sync-event子命令 — 事件型接口同步。
"""

from quantpits.utils import env

import os
import logging


def run_sync_event(
    workspace_dir: str,
    mode: str,
    tier_names: list[str] | None,
    interfaces: list[str] | None,
    project_root: str | None,
    log_level: str,
) -> None:
    """
    执行事件型接口同步。

    Args:
        workspace_dir: Workspace根目录
        mode: 同步模式 "full" 或 "daily"
        tier_names: tier名称列表
        interfaces: 接口列表
        project_root: 项目根目录
        log_level: 日志级别
    """
    from quantpits.scripts.data_sync.path_resolver import resolve_project_root
    from quantpits.scripts.data_sync.sync.sync_event import sync_all_events
    from quantpits.scripts.data_sync.storage import load_static

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError("环境变量TUSHARE_TOKEN未设置")

    project_root = resolve_project_root(project_root)
    global_raw = os.path.join(project_root, "data", "raw")

    logger = logging.getLogger(__name__)

    try:
        load_static("trade_cal", global_raw)
    except FileNotFoundError:
        logger.error("交易日历不存在，请先运行 sync-static")
        return

    if interfaces is None and tier_names:
        from quantpits.scripts.data_sync.tier_config import get_interfaces_for_tiers
        interfaces = get_interfaces_for_tiers(tier_names)

    logger.info(f"=== 事件型接口同步 (mode={mode}) ===")

    results = sync_all_events(
        token, project_root, interfaces, mode, tier_names=tier_names
    )

    total_written = sum(r.written for r in results)
    total_skipped = sum(r.skipped for r in results)
    logger.info(f"事件同步完成: 新增={total_written}, 更新={total_skipped}")
