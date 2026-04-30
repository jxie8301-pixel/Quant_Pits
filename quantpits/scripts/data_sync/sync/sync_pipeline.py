"""
全流程编排 — 静态表→日频→事件→dump-pit→dump-bin。

报告数据模型与编排入口。
"""

from quantpits.utils import env

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Literal

from quantpits.scripts.data_sync.rate_limiter import SHUTDOWN_EVENT


@dataclass
class StaticReport:
    """静态表同步报告。"""
    stock_basic_count: int = 0
    trade_cal_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class DailyReport:
    """日频接口同步报告。"""
    interface: str = ""
    success: int = 0
    failed: int = 0
    failed_dates: list[str] = field(default_factory=list)


@dataclass
class EventReport:
    """事件型接口同步报告。"""
    interface: str = ""
    written: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)


@dataclass
class BinConvertReport:
    """Qlib bin格式转换报告。"""
    stocks_count: int = 0
    fields_count: int = 0
    dates_count: int = 0
    index_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class SyncReport:
    """全流程同步总报告。"""
    mode: Literal["full", "daily"] = "daily"
    static: StaticReport = field(default_factory=StaticReport)
    daily: list[DailyReport] = field(default_factory=list)
    event: list[EventReport] = field(default_factory=list)
    bin: BinConvertReport = field(default_factory=BinConvertReport)
    total_seconds: float = 0.0


logger = logging.getLogger(__name__)


def ensure_workspace_dirs(workspace_dir: str) -> None:
    """
    创建Workspace必要的目录结构。

    Args:
        workspace_dir: Workspace根目录
    """
    for subdir in ["data/raw", "data/qlib_data", "data/logs"]:
        os.makedirs(os.path.join(workspace_dir, subdir), exist_ok=True)


def run_pipeline(
    mode: Literal["full", "daily"],
    workspace_dir: str,
    interfaces: list[str] | None = None,
    field_mapping_path: str | None = None,
    log_level: str = "INFO",
    data_paths: "DataPaths | None" = None,
) -> SyncReport:
    """
    全流程编排入口。

    Args:
        mode: full=全量同步+全量bin转换, daily=增量同步+增量bin转换
        workspace_dir: Workspace根目录路径
        interfaces: 指定接口列表，None=全部
        field_mapping_path: 字段映射YAML路径，None=使用默认
        log_level: 日志级别
        data_paths: DataPaths路径集合，None=自动解析

    Returns:
        SyncReport: 包含各阶段执行结果的报告对象

    Raises:
        ValueError: mode不是full/daily时
        RuntimeError: TUSHARE_TOKEN未设置时
    """
    if mode not in ("full", "daily"):
        raise ValueError(f"不支持的模式: {mode}，仅支持 full 和 daily")

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError("环境变量TUSHARE_TOKEN未设置")

    if data_paths is None:
        from quantpits.scripts.data_sync.path_resolver import resolve_data_paths
        data_paths = resolve_data_paths(env.ROOT_DIR)

    report = SyncReport(mode=mode)
    start_time = time.time()

    ensure_workspace_dirs(workspace_dir)

    from quantpits.scripts.data_sync.sync.sync_static import sync_static_tables
    from quantpits.scripts.data_sync.sync.sync_daily import sync_all_daily
    from quantpits.scripts.data_sync.sync.sync_event import sync_all_events
    from quantpits.scripts.data_sync.stock_bin_generator import generate_bins_per_stock
    from quantpits.scripts.data_sync import storage as pipeline_storage

    try:
        logger.info(f"=== 阶段1: 静态表同步 (mode={mode}) ===")
        report.static = sync_static_tables(token, workspace_dir)
        if SHUTDOWN_EVENT.is_set():
            return report

        logger.info(f"=== 阶段2: 日频接口同步 (mode={mode}) ===")
        report.daily = sync_all_daily(token, workspace_dir, interfaces, mode)
        if SHUTDOWN_EVENT.is_set():
            return report

        logger.info(f"=== 阶段3: 事件型接口同步 (mode={mode}) ===")
        report.event = sync_all_events(token, workspace_dir, interfaces, mode)
        if SHUTDOWN_EVENT.is_set():
            return report

        logger.info(f"=== 阶段4: Qlib bin生成 (mode={mode}) ===")

        raw_read_dir = data_paths.raw_read_dir if data_paths else os.path.join(
            workspace_dir, "data", "raw"
        )
        qlib_dir = os.path.join(workspace_dir, "data", "qlib_data")

        try:
            stock_basic = pipeline_storage.load_static("stock_basic", raw_read_dir)
            cal_df = pipeline_storage.load_static("trade_cal", raw_read_dir)
        except FileNotFoundError as e:
            logger.error(f"静态表缺失：{e}")
            report.static.errors.append(str(e))
            return report

        calendar = cal_df[cal_df["is_open"] == 1]["cal_date"].sort_values().tolist()

        if field_mapping_path is None:
            field_mapping_path = "config/field_mapping.yaml"

        from quantpits.scripts.data_sync.bin_converter import (
            load_field_mapping, validate_field_mapping,
        )
        try:
            mappings = load_field_mapping(field_mapping_path, workspace_dir)
        except FileNotFoundError:
            logger.error(f"字段映射配置不存在：{field_mapping_path}")
            report.static.errors.append(f"field_mapping not found: {field_mapping_path}")
            return report
        validate_field_mapping(mappings)

        report.bin = generate_bins_per_stock(
            raw_dir=raw_read_dir,
            qlib_dir=qlib_dir,
            calendar=calendar,
            stock_basic=stock_basic,
            field_mappings=mappings,
            mode=mode,
        )
        if SHUTDOWN_EVENT.is_set():
            return report

    except Exception as e:
        logger.error(f"流水线执行异常: {e}", exc_info=True)
        report.static.errors.append(str(e))

    report.total_seconds = time.time() - start_time
    logger.info(f"流水线完成，耗时 {report.total_seconds:.1f}s")
    return report
