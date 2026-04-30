"""
字段映射与bin值计算。

将field_mapping.yaml配置加载为FieldMapping对象，
并提供compute_bin_values()按字段类型计算bin值。
"""

from quantpits.utils import env

import logging
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
import yaml

from quantpits.scripts.data_sync import storage, config
from quantpits.scripts.data_sync.ts_code_converter import tushare_to_qlib, qlib_to_tushare
from quantpits.scripts.data_sync.bin_writer import BinWriter

logger = logging.getLogger(__name__)


@dataclass
class FieldMapping:
    """
    字段映射配置。

    Attributes:
        bin_field: Qlib bin文件中的字段名
        source: 宽表Parquet中的源字段名或表达式字段列表
        transform: 转换方式 (direct/expression/indicator)
        alignment: 对齐方式 (daily/indicator/event_day_only/pit)
            - daily: 日频字段，直接写入bin
            - indicator: 事件标志，公告日=1.0其余=0.0，写入日频bin
            - event_day_only: 仅公告日有值，写入日频bin
            - pit: forward_fill型事件字段，由dump-pit生成PIT文件
        enabled: 是否启用
        source_interface: 源接口名称
    """

    bin_field: str
    source: str | list[str]
    transform: str
    alignment: str
    enabled: bool = True
    source_interface: str = ""


def load_field_mapping(
    field_mapping_path: str, workspace_dir: str
) -> list[FieldMapping]:
    """
    加载YAML字段映射配置。

    Args:
        field_mapping_path: YAML文件路径（相对于workspace_dir或绝对路径）
        workspace_dir: Workspace根目录

    Returns:
        FieldMapping列表

    Raises:
        FileNotFoundError: YAML文件不存在
    """
    if os.path.isabs(field_mapping_path):
        path = field_mapping_path
    else:
        path = os.path.join(workspace_dir, field_mapping_path)

    if not os.path.exists(path):
        raise FileNotFoundError(f"字段映射配置不存在：{path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    mappings: list[FieldMapping] = []
    for item in raw.get("bin_fields", []):
        source = item.get("source", "")
        if isinstance(source, list):
            source = [str(s) for s in source]
        else:
            source = str(source)

        mappings.append(
            FieldMapping(
                bin_field=item["bin_field"],
                source=source,
                transform=item.get("transform", "direct"),
                alignment=item.get("alignment", "daily"),
                enabled=item.get("enabled", True),
                source_interface=item.get("source_interface", ""),
            )
        )

    logger.info(f"加载字段映射 {len(mappings)} 个，来自 {path}")
    return mappings


def validate_field_mapping(mappings: list[FieldMapping]) -> None:
    """
    校验字段映射配置，确保bin_field不重复。

    Args:
        mappings: FieldMapping列表

    Raises:
        ValueError: 存在重复bin_field
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for m in mappings:
        if not m.enabled:
            continue
        if m.bin_field in seen:
            duplicates.add(m.bin_field)
        seen.add(m.bin_field)

    if duplicates:
        raise ValueError(f"重复的bin_field：{sorted(duplicates)}")


def compute_bin_values(
    stock_df: pd.DataFrame, mapping: FieldMapping
) -> tuple[list[str], np.ndarray]:
    """
    计算单个字段的bin值。

    Args:
        stock_df: 单只股票的宽表DataFrame（按trade_date排序）
        mapping: 字段映射配置

    Returns:
        (日期列表, 特征值数组) 元组
    """
    dates = stock_df["trade_date"].tolist() if "trade_date" in stock_df.columns else []

    if mapping.transform == "direct":
        source_col = mapping.source[0] if isinstance(mapping.source, list) else mapping.source

        if source_col not in stock_df.columns:
            return dates, np.full(len(dates), np.nan, dtype=np.float32)

        values = stock_df[source_col].values.astype(np.float32)
        return dates, values

    elif mapping.transform == "indicator":
        source_col = mapping.source if isinstance(mapping.source, str) else mapping.source[0]
        if source_col not in stock_df.columns:
            return dates, np.zeros(len(dates), dtype=np.float32)
        raw_vals = stock_df[source_col].values
        values = np.where(raw_vals == "S", 1.0, 0.0).astype(np.float32)
        return dates, values

    elif mapping.transform == "expression" or (
        mapping.transform not in ("direct", "indicator")
    ):
        expr = mapping.source if mapping.transform == "expression" else mapping.transform
        if isinstance(mapping.source, list):
            local_vars = {s: stock_df[s].values.astype(np.float32) for s in mapping.source if s in stock_df.columns}
        elif isinstance(mapping.source, str) and mapping.source in stock_df.columns:
            local_vars = {mapping.source: stock_df[mapping.source].values.astype(np.float32)}
        else:
            local_vars = {col: stock_df[col].values.astype(np.float32)
                         for col in stock_df.columns
                         if col not in ("trade_date", "ts_code")}

        try:
            values = eval(expr, {"__builtins__": {}}, local_vars).astype(np.float32)
        except Exception as e:
            logger.warning(f"表达式计算失败 {expr}: {e}")
            values = np.full(len(stock_df), np.nan, dtype=np.float32)
        return dates, values

    else:
        source_col = mapping.source if isinstance(mapping.source, str) else mapping.source[0]
        if source_col not in stock_df.columns:
            return dates, np.full(len(dates), np.nan, dtype=np.float32)
        values = stock_df[source_col].values.astype(np.float32)
        return dates, values


def write_calendar_files(
    qlib_dir: str,
    calendar_dates: list[str],
    first_synced_date: str | None = None,
    last_synced_date: str | None = None,
) -> None:
    """
    生成calendars/day.txt（数据历史交易日）和day_future.txt（数据开始→未来）。

    day.txt: 从first_synced_date到last_synced_date的交易日，Qlib官方%Y-%m-%d格式。
    day_future.txt: 从first_synced_date到calendar_dates末尾的全部交易日，
    供order_gen.py的D.calendar(future=True)查询下一交易日。

    Args:
        qlib_dir: Qlib数据目录（如 workspace_dir/data/qlib_data）
        calendar_dates: trade_cal全部交易日列表（YYYYMMDD，含已公布的未来日期）
        first_synced_date: 最早有raw数据的日期，None时取calendar_dates[0]
        last_synced_date: 最后有raw数据的日期，None时取calendar_dates[-1]
    """
    cal_dir = os.path.join(qlib_dir, "calendars")
    os.makedirs(cal_dir, exist_ok=True)

    if first_synced_date is None:
        first_synced_date = calendar_dates[0]
    if last_synced_date is None:
        last_synced_date = calendar_dates[-1]

    historical = [d for d in calendar_dates if first_synced_date <= d <= last_synced_date]
    historical_formatted = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in historical]

    day_path = os.path.join(cal_dir, "day.txt")
    with open(day_path, "w", encoding="utf-8") as f:
        for d in historical_formatted:
            f.write(f"{d}\n")
    logger.info(f"写入day.txt {len(historical)}天 → {day_path}")

    future_dates = [d for d in calendar_dates if d >= first_synced_date]
    future_formatted = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in future_dates]
    future_path = os.path.join(cal_dir, "day_future.txt")
    with open(future_path, "w", encoding="utf-8") as f:
        for d in future_formatted:
            f.write(f"{d}\n")
    future_extra = len(future_dates) - len(historical)
    logger.info(f"写入day_future.txt {len(future_dates)}天(历史{len(historical)}+未来{future_extra}) → {future_path}")


def write_instruments_files(workspace_dir: str, stock_basic: pd.DataFrame) -> None:
    """
    生成instruments/all.txt和指数文件。

    Args:
        workspace_dir: Workspace根目录
        stock_basic: stock_basic静态表DataFrame
    """
    qlib_dir = os.path.join(workspace_dir, "data", "qlib_data")
    inst_dir = os.path.join(qlib_dir, "instruments")
    os.makedirs(inst_dir, exist_ok=True)

    all_path = os.path.join(inst_dir, "all.txt")
    valid = stock_basic[stock_basic["ts_code"].apply(lambda x: tushare_to_qlib(str(x)) is not None)]

    with open(all_path, "w", encoding="utf-8") as f:
        for _, row in valid.iterrows():
            qlib_code = tushare_to_qlib(str(row["ts_code"]))
            if qlib_code is None:
                continue
            list_date = str(row.get("list_date", "19900101"))
            delist_date = str(row.get("delist_date", "20991231"))
            if list_date == "None" or list_date == "nan":
                list_date = "19900101"
            if delist_date == "None" or delist_date == "nan":
                delist_date = "20991231"
            f.write(f"{qlib_code}\t{list_date}\t{delist_date}\n")

    logger.info(f"写入instruments/all.txt {len(valid)} 只股票")


def write_index_daily_bin(
    workspace_dir: str, calendar: list[str], mode: str = "daily"
) -> int:
    """
    写入指数日线bin（factor=1.0）。

    Args:
        workspace_dir: Workspace根目录
        calendar: 交易日历列表
        mode: 写入模式

    Returns:
        写入的指数数量
    """
    qlib_dir = os.path.join(workspace_dir, "data", "qlib_data")
    indices = {"sh000300": "沪深300", "sh000905": "中证500", "sh000852": "中证1000"}

    feat_dir = os.path.join(qlib_dir, "features")
    count = 0

    for qlib_code, name in indices.items():
        idx_dir = os.path.join(feat_dir, qlib_code)
        os.makedirs(idx_dir, exist_ok=True)

        file_path = os.path.join(idx_dir, "$close.day.bin")
        values = np.ones(len(calendar), dtype=np.float32)
        BinWriter.write_feature_bin(file_path, calendar, calendar, values, mode)
        count += 1

    logger.info(f"写入指数日线bin {count} 个")
    return count



