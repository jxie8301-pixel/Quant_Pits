"""
按股票逐个生成bin文件 — 直接从raw数据生成Qlib bin。

核心思路：跳过宽表Parquet中间步骤，按股票逐只从raw数据读取、合并、转换、写入bin。

事件字段处理策略：
- indicator/event_day_only型：直接从raw事件数据生成日频bin（每个交易日一个值）
- forward_fill型：由dump-pit生成PIT文件，训练时通过P($$field_q)读取，不在此处理
"""

from quantpits.utils import env

import logging
import os
import shutil

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from quantpits.scripts.data_sync import storage, unit_converter
from quantpits.scripts.data_sync.bin_converter import (
    FieldMapping,
    compute_bin_values,
    write_calendar_files,
    write_instruments_files,
    write_index_daily_bin,
)
from quantpits.scripts.data_sync.bin_writer import BinWriter
from quantpits.scripts.data_sync.ts_code_converter import tushare_to_qlib
from quantpits.scripts.data_sync.sync.sync_pipeline import BinConvertReport

logger = logging.getLogger(__name__)

DAILY_BASE_INTERFACE: str = "stk_factor_pro"

DAILY_JOIN_INTERFACES: list[str] = [
    "cyq_perf",
    "moneyflow",
    "margin_detail",
    "stk_limit",
    "suspend_d",
]

EVENT_INDICATOR_FIELDS: list[dict] = [
    {"event_interface": "stk_holdertrade", "source_field": "change_vol", "bin_field": "holder_change_flag", "aggregate": "max"},
    {"event_interface": "share_float", "source_field": "float_share", "bin_field": "float_flag", "aggregate": "max"},
    {"event_interface": "forecast_vip", "source_field": "p_change_max", "bin_field": "forecast_flag", "aggregate": "max"},
    {"event_interface": "express_vip", "source_field": "diluted_roe", "bin_field": "express_flag", "aggregate": "max"},
]

EVENT_DAY_ONLY_FIELDS: list[dict] = [
    {"event_interface": "stk_holdertrade", "source_field": "change_vol", "bin_field": "holder_change", "aggregate": "sum"},
    {"event_interface": "share_float", "source_field": "float_share", "bin_field": "float_vol", "aggregate": "sum"},
]

JOIN_KEYS: list[str] = ["ts_code", "trade_date"]


def _load_daily_with_trade_date(
    interface: str,
    start_date: str,
    end_date: str,
    raw_dir: str,
    target_stocks: list[str] | None = None,
) -> pd.DataFrame:
    """
    读取日期范围的日频Parquet，确保trade_date列存在。

    Args:
        interface: 接口名称
        start_date: 起始日期YYYYMMDD
        end_date: 结束日期YYYYMMDD
        raw_dir: raw数据目录
        target_stocks: 指定股票ts_code列表，None时读取全市场

    Returns:
        合并后的DataFrame，保证包含trade_date列
    """
    folder = os.path.join(raw_dir, interface)
    if not os.path.exists(folder):
        return pd.DataFrame()

    dfs: list[pd.DataFrame] = []
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".parquet"):
            continue
        date_str = fn[:8]
        if not date_str.isdigit() or len(date_str) != 8:
            continue
        if date_str < start_date or date_str > end_date:
            continue

        path = os.path.join(folder, fn)
        try:
            day_df = pq.read_table(path).to_pandas()
            if "trade_date" not in day_df.columns:
                day_df["trade_date"] = date_str
            if target_stocks is not None and "ts_code" in day_df.columns:
                day_df = day_df[day_df["ts_code"].isin(target_stocks)]
            if not day_df.empty:
                dfs.append(day_df)
        except Exception as e:
            logger.warning(f"读取 {path} 失败：{e}")
            continue

    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


def _preload_merged_daily(
    raw_dir: str,
    start_date: str,
    end_date: str,
    target_stocks: list[str] | None = None,
) -> pd.DataFrame:
    """
    一次加载所有日频接口，合并、转换单位，以(ts_code, trade_date)为索引。

    Args:
        raw_dir: raw数据目录
        start_date: 起始日期YYYYMMDD
        end_date: 结束日期YYYYMMDD
        target_stocks: 指定股票ts_code列表，None时读取全市场

    Returns:
        合并后的DataFrame，索引=[ts_code, trade_date]，已排序
    """
    base = _load_daily_with_trade_date(DAILY_BASE_INTERFACE, start_date, end_date, raw_dir, target_stocks)
    if base.empty:
        return pd.DataFrame()

    base = unit_converter.apply_unit_conversion(base, DAILY_BASE_INTERFACE)
    base = base.set_index(["ts_code", "trade_date"]).sort_index()

    for iface in DAILY_JOIN_INTERFACES:
        other = _load_daily_with_trade_date(iface, start_date, end_date, raw_dir, target_stocks)
        if other.empty:
            continue

        other = unit_converter.apply_unit_conversion(other, iface)
        other = other.set_index(["ts_code", "trade_date"])

        other_cols = [c for c in other.columns if c not in base.columns]
        if not other_cols:
            continue

        base = base.join(other[other_cols], how="left")

    return base


def _load_raw_events(
    interface: str,
    raw_dir: str,
    target_stocks: list[str] | None = None,
) -> pd.DataFrame:
    """
    加载事件接口全量raw数据。

    Args:
        interface: 事件接口名
        raw_dir: raw数据目录
        target_stocks: 指定股票ts_code列表，None时读取全市场

    Returns:
        事件DataFrame
    """
    all_dates = storage.get_all_synced_dates(interface, raw_dir)
    if not all_dates:
        return pd.DataFrame()

    dfs: list[pd.DataFrame] = []
    for date_str in all_dates:
        path = os.path.join(raw_dir, interface, f"{date_str}.parquet")
        if not os.path.exists(path):
            continue
        try:
            df = pq.read_table(path).to_pandas()
            if target_stocks is not None and "ts_code" in df.columns:
                df = df[df["ts_code"].isin(target_stocks)]
            if not df.empty:
                dfs.append(df)
        except Exception as e:
            logger.warning(f"读取 {path} 失败：{e}")

    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


def _build_indicator_daily(
    event_interface: str,
    source_field: str,
    bin_field: str,
    aggregate: str,
    raw_dir: str,
    calendar: list[str],
    valid_codes: list[str],
    target_stocks: list[str] | None = None,
) -> pd.DataFrame:
    """
    从raw事件数据构建indicator型日频DataFrame。

    indicator语义：公告日=1.0，非公告日=0.0。

    Args:
        event_interface: 事件接口名
        source_field: 源字段
        bin_field: 输出bin字段名
        aggregate: 聚合方式
        raw_dir: raw数据目录
        calendar: 交易日历
        valid_codes: 有效ts_code列表
        target_stocks: 指定股票ts_code列表，None时读取全市场

    Returns:
        DataFrame，索引=[ts_code, trade_date]，列=[bin_field]
    """
    event_df = _load_raw_events(event_interface, raw_dir, target_stocks)
    if event_df.empty:
        return pd.DataFrame()

    if "ann_date" not in event_df.columns:
        return pd.DataFrame()

    event_df = event_df.dropna(subset=["ann_date"])
    event_df["ann_date"] = event_df["ann_date"].apply(lambda x: f"{int(float(x)):08d}")
    event_df = event_df[event_df["ann_date"].str.len() == 8]
    event_df = event_df[event_df["ts_code"].isin(valid_codes)]

    cal_set = set(calendar)
    event_df["cal_date"] = event_df["ann_date"].apply(
        lambda d: d if d in cal_set else None
    )
    event_df = event_df.dropna(subset=["cal_date"])

    stock_dates = event_df.groupby("ts_code")["cal_date"].apply(set).to_dict()

    rows: list[dict] = []
    for ts_code, dates in stock_dates.items():
        for d in dates:
            if d in cal_set:
                rows.append({"ts_code": ts_code, "trade_date": d, bin_field: 1.0})

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)
    return result.set_index(["ts_code", "trade_date"])


def _build_event_day_only_daily(
    event_interface: str,
    source_field: str,
    bin_field: str,
    aggregate: str,
    raw_dir: str,
    calendar: list[str],
    valid_codes: list[str],
    target_stocks: list[str] | None = None,
) -> pd.DataFrame:
    """
    从raw事件数据构建event_day_only型日频DataFrame。

    event_day_only语义：仅公告日有值，其余=NaN。

    Args:
        event_interface: 事件接口名
        source_field: 源字段
        bin_field: 输出bin字段名
        aggregate: 聚合方式
        raw_dir: raw数据目录
        calendar: 交易日历
        valid_codes: 有效ts_code列表
        target_stocks: 指定股票ts_code列表，None时读取全市场

    Returns:
        DataFrame，索引=[ts_code, trade_date]，列=[bin_field]
    """
    event_df = _load_raw_events(event_interface, raw_dir, target_stocks)
    if event_df.empty:
        return pd.DataFrame()

    if "ann_date" not in event_df.columns or source_field not in event_df.columns:
        return pd.DataFrame()

    event_df = event_df.dropna(subset=["ann_date", source_field])
    event_df["ann_date"] = event_df["ann_date"].apply(lambda x: f"{int(float(x)):08d}")
    event_df = event_df[event_df["ann_date"].str.len() == 8]
    event_df = event_df[event_df["ts_code"].isin(valid_codes)]

    cal_set = set(calendar)
    event_df["cal_date"] = event_df["ann_date"].apply(
        lambda d: d if d in cal_set else None
    )
    event_df = event_df.dropna(subset=["cal_date"])

    if aggregate == "sum":
        agg_df = event_df.groupby(["ts_code", "cal_date"], as_index=False)[source_field].sum()
    elif aggregate == "max":
        agg_df = event_df.groupby(["ts_code", "cal_date"], as_index=False)[source_field].max()
    else:
        agg_df = event_df.groupby(["ts_code", "cal_date"], as_index=False)[source_field].last()

    agg_df = agg_df.rename(columns={"cal_date": "trade_date", source_field: bin_field})
    return agg_df.set_index(["ts_code", "trade_date"])


def _preload_event_bin_fields(
    raw_dir: str,
    calendar: list[str],
    valid_codes: list[str],
    target_stocks: list[str] | None = None,
) -> pd.DataFrame:
    """
    构建所有indicator/event_day_only型事件字段的日频DataFrame。

    Args:
        raw_dir: raw数据目录
        calendar: 交易日历
        valid_codes: 有效ts_code列表
        target_stocks: 指定股票ts_code列表，None时读取全市场

    Returns:
        合并后的DataFrame，索引=[ts_code, trade_date]
    """
    parts: list[pd.DataFrame] = []

    for cfg in EVENT_INDICATOR_FIELDS:
        df = _build_indicator_daily(
            cfg["event_interface"], cfg["source_field"], cfg["bin_field"],
            cfg["aggregate"], raw_dir, calendar, valid_codes, target_stocks,
        )
        if not df.empty:
            parts.append(df)
            logger.info(f"indicator字段 {cfg['bin_field']}：{len(df)} 条非零记录")

    for cfg in EVENT_DAY_ONLY_FIELDS:
        df = _build_event_day_only_daily(
            cfg["event_interface"], cfg["source_field"], cfg["bin_field"],
            cfg["aggregate"], raw_dir, calendar, valid_codes, target_stocks,
        )
        if not df.empty:
            parts.append(df)
            logger.info(f"event_day_only字段 {cfg['bin_field']}：{len(df)} 条记录")

    if not parts:
        return pd.DataFrame()

    result = parts[0]
    for part in parts[1:]:
        new_cols = [c for c in part.columns if c not in result.columns]
        if new_cols:
            result = result.join(part[new_cols], how="outer")

    return result


def _determine_date_range(
    raw_dir: str,
    start_date: str | None,
    end_date: str | None,
) -> tuple[str, str]:
    """
    确定日期范围。

    Args:
        raw_dir: raw数据目录
        start_date: 指定起始日期，None时自动推断
        end_date: 指定结束日期，None时自动推断

    Returns:
        (start_date, end_date) 元组

    Raises:
        RuntimeError: 无已同步数据时
    """
    if start_date and end_date:
        return start_date, end_date

    all_dates = storage.get_all_synced_dates(DAILY_BASE_INTERFACE, raw_dir)
    if not all_dates:
        raise RuntimeError("stk_factor_pro无已同步数据，无法确定日期范围")

    if not start_date:
        start_date = all_dates[0]
    if not end_date:
        end_date = all_dates[-1]

    return start_date, end_date


def _infer_workspace_dir(qlib_dir: str) -> str:
    """
    从qlib_dir反推workspace_dir。

    Args:
        qlib_dir: Qlib数据目录

    Returns:
        Workspace根目录
    """
    return os.path.dirname(os.path.dirname(qlib_dir))


def generate_bins_per_stock(
    raw_dir: str,
    qlib_dir: str,
    calendar: list[str],
    stock_basic: pd.DataFrame,
    field_mappings: list[FieldMapping],
    mode: str = "daily",
    start_date: str | None = None,
    end_date: str | None = None,
    target_stocks: list[str] | None = None,
) -> BinConvertReport:
    """
    按股票逐个生成bin文件。

    流程：
    1. 确定日期范围
    2. 加载日频数据合并
    3. 构建indicator/event_day_only事件字段的日频数据
    4. 写入calendars/instruments文件
    5. 遍历股票写入bin（仅处理alignment=daily/indicator/event_day_only的字段）
    6. 写入指数bin

    Args:
        raw_dir: raw数据目录
        qlib_dir: Qlib数据输出目录
        calendar: 交易日历列表
        stock_basic: stock_basic静态表DataFrame
        field_mappings: 字段映射配置列表
        mode: "full"全量写入，"daily"增量写入
        start_date: 起始日期YYYYMMDD
        end_date: 结束日期YYYYMMDD
        target_stocks: 指定股票ts_code列表，None时处理全市场

    Returns:
        BinConvertReport 转换报告
    """
    report = BinConvertReport()

    valid_start, valid_end = _determine_date_range(raw_dir, start_date, end_date)
    all_synced_dates = storage.get_all_synced_dates(DAILY_BASE_INTERFACE, raw_dir)
    if not all_synced_dates:
        report.errors.append(f"{DAILY_BASE_INTERFACE}无已同步数据，无法确定bin索引日历")
        return report

    data_start = all_synced_dates[0]
    data_end = all_synced_dates[-1]
    index_calendar = [d for d in calendar if data_start <= d <= data_end]
    date_range = [d for d in index_calendar if valid_start <= d <= valid_end]
    if not date_range:
        report.errors.append(f"日期范围内无交易日：{valid_start}~{valid_end}")
        return report

    logger.info(
        f"bin生成日期范围：{valid_start}~{valid_end}，{len(date_range)} 个交易日；"
        f"索引日历：{data_start}~{data_end}，{len(index_calendar)} 个交易日"
    )

    workspace_dir = _infer_workspace_dir(qlib_dir)
    write_calendar_files(qlib_dir, calendar, first_synced_date=data_start, last_synced_date=data_end)

    if target_stocks is not None:
        stock_basic = stock_basic[stock_basic["ts_code"].isin(target_stocks)]
        logger.info(f"指定股票模式：{len(stock_basic)} 只股票")

    write_instruments_files(workspace_dir, stock_basic)

    enabled_mappings = [m for m in field_mappings if m.enabled and m.alignment != "pit"]
    report.fields_count = len(enabled_mappings)

    valid_codes = stock_basic[
        stock_basic["ts_code"].apply(lambda x: tushare_to_qlib(str(x)) is not None)
    ]["ts_code"].tolist()
    report.stocks_count = len(valid_codes)

    merged_daily = _preload_merged_daily(raw_dir, valid_start, valid_end, target_stocks)

    event_bin_fields = _preload_event_bin_fields(raw_dir, index_calendar, valid_codes, target_stocks)

    if merged_daily.empty and event_bin_fields.empty:
        report.errors.append("预加载后无可用数据")
        return report

    if merged_daily.empty:
        full_data = event_bin_fields
    elif event_bin_fields.empty:
        full_data = merged_daily
    else:
        new_cols = [c for c in event_bin_fields.columns if c not in merged_daily.columns]
        if new_cols:
            full_data = merged_daily.join(event_bin_fields[new_cols], how="left")
        else:
            full_data = merged_daily

    for col in full_data.columns:
        if col in ("trade_date", "ts_code"):
            continue
        is_indicator = any(cfg["bin_field"] == col for cfg in EVENT_INDICATOR_FIELDS)
        if is_indicator:
            full_data[col] = full_data[col].fillna(0.0).astype(np.float32)

    logger.info(f"预加载合并完成：{len(full_data)} 行，{len(full_data.columns)} 列")

    feat_dir = os.path.join(qlib_dir, "features")

    if mode == "full" and os.path.exists(feat_dir):
        shutil.rmtree(feat_dir)
        logger.info(f"full模式：清除已有bin文件 {feat_dir}")

    for i, ts_code in enumerate(valid_codes):
        qlib_code = tushare_to_qlib(ts_code)
        if qlib_code is None:
            continue

        try:
            try:
                stock_df = full_data.loc[[ts_code]]
            except KeyError:
                continue

            stock_df = stock_df.reset_index(level="ts_code", drop=True).reset_index()

            stock_dir = os.path.join(feat_dir, qlib_code)
            os.makedirs(stock_dir, exist_ok=True)

            for mapping in enabled_mappings:
                dates, values = compute_bin_values(stock_df, mapping)
                if len(values) == 0:
                    continue
                file_path = os.path.join(stock_dir, f"{mapping.bin_field}.day.bin")
                BinWriter.write_feature_bin(file_path, index_calendar, dates, values, mode)

        except Exception as e:
            logger.warning(f"股票 {ts_code} bin生成失败：{e}")
            report.errors.append(f"{ts_code}: {e}")
            continue

        if (i + 1) % 500 == 0 or (i + 1) == len(valid_codes) or len(valid_codes) <= 50:
            logger.info(f"bin生成进度 {i + 1}/{len(valid_codes)}")

    report.dates_count = len(date_range)

    index_count = write_index_daily_bin(workspace_dir, index_calendar, mode)
    report.index_count = index_count

    logger.info(
        f"bin生成完成：{report.stocks_count} 股 × {report.fields_count} 字段 × "
        f"{report.dates_count} 天，指数 {report.index_count} 个，"
        f"错误 {len(report.errors)} 个"
    )
    return report
