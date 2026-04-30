"""
status子命令 — 同步状态查询。
"""

from quantpits.utils import env

import logging


def run_status(
    workspace_dir: str,
    project_root: str | None,
    log_level: str,
) -> None:
    """
    查询并打印同步状态。

    Args:
        workspace_dir: Workspace根目录
        project_root: 项目根目录
        log_level: 日志级别
    """
    from quantpits.scripts.data_sync.path_resolver import resolve_data_paths
    from quantpits.scripts.data_sync.sync_status import (
        get_sync_status,
        format_status_report,
    )

    data_paths = resolve_data_paths(workspace_dir, project_root)

    tier_statuses, global_status, ws_status = get_sync_status(
        data_paths, workspace_dir
    )

    report_text = format_status_report(tier_statuses, global_status, ws_status)
    print(report_text)
