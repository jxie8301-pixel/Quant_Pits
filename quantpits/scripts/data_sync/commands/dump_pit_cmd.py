"""
dump-pit子命令 — 将raw事件数据生成qlib PIT二进制文件。

forward_fill型事件字段走PIT存储，由qlib的P()运算符读取。
indicator/event_day_only型字段仍走日频bin（dump-bin生成）。
"""

from quantpits.utils import env

import logging


PIT_FIELDS_CONFIG = [
    {"event_interface": "stk_holdertrade", "source_field": "change_ratio", "pit_field": "holder_change_ratio_q", "aggregate": "latest"},
    {"event_interface": "share_float", "source_field": "float_ratio", "pit_field": "float_ratio_q", "aggregate": "latest"},
    {"event_interface": "forecast_vip", "source_field": "p_change_max", "pit_field": "forecast_net_ratio_q", "aggregate": "latest"},
    {"event_interface": "express_vip", "source_field": "diluted_roe", "pit_field": "express_roe_q", "aggregate": "latest"},
    {"event_interface": "express_vip", "source_field": "yoy_net_profit", "pit_field": "express_yoy_net_q", "aggregate": "latest"},
]


def run_dump_pit(
    workspace_dir: str,
    project_root: str | None,
    log_level: str,
    overwrite: bool = True,
    target_stocks: list[str] | None = None,
) -> None:
    """
    执行PIT数据生成。

    Args:
        workspace_dir: Workspace根目录
        project_root: 项目根目录
        log_level: 日志级别
        overwrite: True=全量覆盖，False=增量追加
        target_stocks: 指定股票ts_code列表，None时处理全市场
    """
    from quantpits.scripts.data_sync.pit_writer import dump_pit_all
    from quantpits.scripts.data_sync import storage
    from quantpits.scripts.data_sync.path_resolver import resolve_raw_dir
    import os

    logger = logging.getLogger(__name__)

    raw_dir = resolve_raw_dir(workspace_dir, project_root)
    qlib_dir = os.path.join(workspace_dir, "data", "qlib_data")

    try:
        stock_basic = storage.load_static("stock_basic", raw_dir)
    except FileNotFoundError:
        logger.error("stock_basic静态表不存在")
        return

    logger.info(f"=== 生成PIT数据 (overwrite={overwrite}) ===")

    if target_stocks is not None:
        stock_basic = stock_basic[stock_basic["ts_code"].isin(target_stocks)]
        logger.info(f"指定股票模式：{len(stock_basic)} 只股票")

    total = dump_pit_all(
        raw_dir=raw_dir,
        qlib_dir=qlib_dir,
        stock_basic=stock_basic,
        pit_fields_config=PIT_FIELDS_CONFIG,
        overwrite=overwrite,
    )

    logger.info(f"PIT数据生成完成：{total} 条记录")
