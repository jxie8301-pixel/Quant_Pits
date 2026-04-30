"""
dump-bin子命令 — 按股票逐个生成Qlib bin文件（替代宽表merge流程）。
"""

from quantpits.utils import env

import logging


def run_dump_bin(
    workspace_dir: str,
    mode: str,
    start_date: str | None,
    end_date: str | None,
    field_mapping_path: str | None,
    project_root: str | None,
    log_level: str,
    target_stocks: list[str] | None = None,
) -> None:
    """
    执行按股票逐个bin生成。

    Args:
        workspace_dir: Workspace根目录
        mode: 转换模式 "full" 或 "daily"
        start_date: 起始日期YYYYMMDD
        end_date: 结束日期YYYYMMDD
        field_mapping_path: 字段映射YAML路径
        project_root: 项目根目录
        log_level: 日志级别
        target_stocks: 指定股票ts_code列表，None时处理全市场
    """
    from quantpits.scripts.data_sync.stock_bin_generator import generate_bins_per_stock
    from quantpits.scripts.data_sync.bin_converter import load_field_mapping, validate_field_mapping
    from quantpits.scripts.data_sync import storage
    from quantpits.scripts.data_sync.path_resolver import resolve_raw_dir
    import os

    logger = logging.getLogger(__name__)

    if field_mapping_path is None:
        field_mapping_path = "config/field_mapping.yaml"

    raw_dir = resolve_raw_dir(workspace_dir, project_root)
    qlib_dir = os.path.join(workspace_dir, "data", "qlib_data")

    try:
        stock_basic = storage.load_static("stock_basic", raw_dir)
    except FileNotFoundError:
        logger.error("stock_basic静态表不存在")
        return

    try:
        cal_df = storage.load_static("trade_cal", raw_dir)
    except FileNotFoundError:
        logger.error("trade_cal静态表不存在")
        return

    calendar = cal_df[cal_df["is_open"] == 1]["cal_date"].sort_values().tolist()

    try:
        mappings = load_field_mapping(field_mapping_path, workspace_dir)
    except FileNotFoundError:
        logger.error(f"字段映射配置不存在：{field_mapping_path}")
        return

    validate_field_mapping(mappings)

    logger.info(f"=== 按股票逐个生成bin (mode={mode}) ===")

    report = generate_bins_per_stock(
        raw_dir=raw_dir,
        qlib_dir=qlib_dir,
        calendar=calendar,
        stock_basic=stock_basic,
        field_mappings=mappings,
        mode=mode,
        start_date=start_date,
        end_date=end_date,
        target_stocks=target_stocks,
    )

    logger.info(
        f"bin生成完成: {report.stocks_count}股×{report.fields_count}字段"
    )
    if report.errors:
        for err in report.errors:
            logger.error(f"错误: {err}")
