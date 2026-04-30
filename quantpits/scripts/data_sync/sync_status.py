"""
同步状态查询 — 辅助用户判断数据同步进度，不自动触发任何操作。

输出各tier的同步进度、全局raw统计、Workspace状态。
"""

from quantpits.utils import env

import os
import logging
from dataclasses import dataclass

from quantpits.scripts.data_sync import storage
from quantpits.scripts.data_sync.path_resolver import DataPaths
from quantpits.scripts.data_sync.tier_config import TIER_DEFINITIONS

logger = logging.getLogger(__name__)


@dataclass
class TierStatus:
    """
    单个tier的同步状态。

    Attributes:
        tier_name: tier名称
        window: 时间窗口描述
        interfaces: 接口列表
        latest_date: 最新同步日期
        interface_ranges: 各接口的日期范围
    """
    tier_name: str
    window: str
    interfaces: list[str]
    latest_date: str | None
    interface_ranges: dict[str, tuple[str | None, str | None]]


@dataclass
class GlobalRawStatus:
    """
    全局raw数据统计。

    Attributes:
        raw_path: raw目录路径
        total_interfaces: 接口数
        date_range: 日期范围
        total_files: 文件总数
    """
    raw_path: str
    total_interfaces: int
    date_range: tuple[str | None, str | None]
    total_files: int


@dataclass
class WorkspaceStatus:
    """
    Workspace状态。

    Attributes:
        workspace_dir: Workspace目录
        bin_stock_count: bin文件股票数
        qlib_dir_exists: qlib_data目录是否存在
    """
    workspace_dir: str
    bin_stock_count: int
    qlib_dir_exists: bool


def get_sync_status(
    data_paths: DataPaths,
    workspace_dir: str | None = None,
) -> tuple[list[TierStatus], GlobalRawStatus, WorkspaceStatus | None]:
    """
    获取同步状态信息。

    Args:
        data_paths: DataPaths实例
        workspace_dir: 可选，Workspace目录（指定时输出Workspace状态）

    Returns:
        (tier_statuses, global_status, ws_status) 元组
    """
    raw_dir = data_paths.raw_read_dir

    tier_statuses = []
    all_latest_dates = []
    for td in TIER_DEFINITIONS:
        iface_ranges = {}
        iface_latest = []
        for iface in td.interfaces:
            dates = storage.get_all_synced_dates(iface, raw_dir)
            if dates:
                iface_ranges[iface] = (dates[0], dates[-1])
                iface_latest.append(dates[-1])
            else:
                iface_ranges[iface] = (None, None)

        tier_latest = max(iface_latest) if iface_latest else None
        if tier_latest:
            all_latest_dates.append(tier_latest)

        tier_statuses.append(TierStatus(
            tier_name=td.name,
            window=td.window,
            interfaces=list(td.interfaces),
            latest_date=tier_latest,
            interface_ranges=iface_ranges,
        ))

    global_status = _get_global_raw_status(raw_dir)

    ws_status = None
    if workspace_dir:
        ws_status = _get_workspace_status(workspace_dir)

    return tier_statuses, global_status, ws_status


def format_status_report(
    tier_statuses: list[TierStatus],
    global_status: GlobalRawStatus,
    ws_status: WorkspaceStatus | None,
) -> str:
    """
    格式化状态报告为可读文本。

    Args:
        tier_statuses: 各tier状态
        global_status: 全局raw状态
        ws_status: Workspace状态（可选）

    Returns:
        格式化文本
    """
    lines = []
    lines.append("=" * 60)
    lines.append("data_sync 同步状态")
    lines.append("=" * 60)

    lines.append(f"\n全局raw数据: {global_status.raw_path}")
    lines.append(f"  接口数: {global_status.total_interfaces}")
    if global_status.date_range[0]:
        lines.append(f"  日期范围: {global_status.date_range[0]} ~ {global_status.date_range[1]}")
    else:
        lines.append("  日期范围: 无数据")
    lines.append(f"  文件总数: {global_status.total_files}")

    for ts in tier_statuses:
        lines.append(f"\n--- [{ts.tier_name}] ({ts.window}) ---")
        lines.append(f"  最新日期: {ts.latest_date or '无数据'}")
        for iface, (first, last) in ts.interface_ranges.items():
            if first:
                lines.append(f"  {iface}: {first} ~ {last}")
            else:
                lines.append(f"  {iface}: 无数据")

    if ws_status:
        lines.append(f"\n--- Workspace ---")
        lines.append(f"  目录: {ws_status.workspace_dir}")
        lines.append(f"  bin股票数: {ws_status.bin_stock_count}")
        lines.append(f"  qlib_data: {'已生成' if ws_status.qlib_dir_exists else '未生成'}")

    return "\n".join(lines)


def _get_global_raw_status(raw_dir: str) -> GlobalRawStatus:
    """
    计算全局raw数据统计。

    Args:
        raw_dir: raw目录路径

    Returns:
        GlobalRawStatus
    """
    if not os.path.exists(raw_dir):
        return GlobalRawStatus(
            raw_path=raw_dir,
            total_interfaces=0,
            date_range=(None, None),
            total_files=0,
        )

    all_dates = []
    total_files = 0
    interface_count = 0

    for entry in os.listdir(raw_dir):
        entry_path = os.path.join(raw_dir, entry)
        if os.path.isdir(entry_path):
            parquet_files = [
                f for f in os.listdir(entry_path)
                if f.endswith(".parquet")
            ]
            if parquet_files:
                interface_count += 1
                total_files += len(parquet_files)
                for f in parquet_files:
                    if f[:8].isdigit() and len(f[:8]) == 8:
                        all_dates.append(f[:8])
        elif entry.endswith(".parquet"):
            total_files += 1
            interface_count += 1

    date_range = (min(all_dates), max(all_dates)) if all_dates else (None, None)

    return GlobalRawStatus(
        raw_path=raw_dir,
        total_interfaces=interface_count,
        date_range=date_range,
        total_files=total_files,
    )


def _get_workspace_status(workspace_dir: str) -> WorkspaceStatus:
    """
    计算Workspace状态。

    Args:
        workspace_dir: Workspace目录

    Returns:
        WorkspaceStatus
    """
    qlib_dir = os.path.join(workspace_dir, "data", "qlib_data")
    feat_dir = os.path.join(qlib_dir, "features")

    stock_count = 0
    if os.path.exists(feat_dir):
        stock_count = sum(
            1 for entry in os.listdir(feat_dir)
            if os.path.isdir(os.path.join(feat_dir, entry))
        )

    return WorkspaceStatus(
        workspace_dir=workspace_dir,
        bin_stock_count=stock_count,
        qlib_dir_exists=os.path.exists(qlib_dir),
    )
