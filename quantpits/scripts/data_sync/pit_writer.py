"""
Qlib PIT二进制格式写入器。

生成 financial/{symbol}/{field}_q.data + {field}_q.index 文件，
供qlib的LocalPITProvider读取。

PIT文件格式（与qlib dump_pit.py一致）：
  .index: [first_year(int32), index_0(int32), index_1(int32), ...]
  .data:  [date(int32), period(int32), value(float32), _next(int32)] 链表结构

每条记录按ann_date排序，同一period的多条修正公告通过_next指针链接。
"""

from quantpits.utils import env

import struct
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DATA_DTYPE = "=IIdI"
INDEX_DTYPE = "=I"
PERIOD_DTYPE = "=I"

NA_INDEX = 0xFFFFFFFF
NA_VALUE = float("nan")

DATA_DTYPE_SIZE = struct.calcsize(DATA_DTYPE)
INDEX_DTYPE_SIZE = struct.calcsize(INDEX_DTYPE)
PERIOD_DTYPE_SIZE = struct.calcsize(PERIOD_DTYPE)


def _period_to_int(period_str: str, quarterly: bool = True) -> int:
    """
    将报告期字符串转为qlib period整数。

    Args:
        period_str: 报告期，如 "20240930" 或 "2024"
        quarterly: True=季度格式(202403)，False=年度格式(2024)

    Returns:
        period整数，如 202403 或 2024
    """
    period_str = str(period_str).strip()
    if len(period_str) == 8 and period_str.isdigit():
        year = int(period_str[:4])
        month = int(period_str[4:6])
        if quarterly:
            return year * 100 + (month - 1) // 3 + 1
        return year
    if len(period_str) == 6 and period_str.isdigit():
        return int(period_str)
    if len(period_str) == 4 and period_str.isdigit():
        return int(period_str)
    raise ValueError(f"无法解析period: {period_str}")


def _ann_date_to_int(ann_date) -> int:
    """
    将ann_date转为YYYYMMDD整数。

    Args:
        ann_date: 公告日期，可能是int/float/str

    Returns:
        8位日期整数，如 20241028
    """
    try:
        return int(float(ann_date))
    except (ValueError, TypeError):
        raise ValueError(f"无法解析ann_date: {ann_date}")


def dump_pit_for_field(
    symbol: str,
    field: str,
    records: pd.DataFrame,
    qlib_dir: str,
    quarterly: bool = True,
    overwrite: bool = True,
) -> int:
    """
    将单只股票单字段的PIT记录写入.data+.index文件。

    Args:
        symbol: qlib格式股票代码，如 "sh600000"
        field: 字段名，必须以_q或_a结尾，如 "holder_change_ratio_q"
        records: DataFrame，必须包含列 [ann_date, period, value]
        qlib_dir: Qlib数据目录
        quarterly: True=季度，False=年度
        overwrite: True=全量覆盖，False=增量追加

    Returns:
        写入的记录数
    """
    if not (field.endswith("_q") or field.endswith("_a")):
        field = f"{field}_q"

    financial_dir = Path(qlib_dir) / "financial" / symbol.lower()
    financial_dir.mkdir(parents=True, exist_ok=True)

    data_path = financial_dir / f"{field}.data"
    index_path = financial_dir / f"{field}.index"

    if records.empty:
        return 0

    records = records.copy()
    records["ann_date_int"] = records["ann_date"].apply(_ann_date_to_int)
    records["period_int"] = records["period"].apply(
        lambda x: _period_to_int(x, quarterly)
    )
    records["value_float32"] = records["value"].astype(np.float32)

    records = records.sort_values("ann_date_int").reset_index(drop=True)

    if overwrite:
        _write_full(data_path, index_path, records)
    else:
        _write_update(data_path, index_path, records)

    return len(records)


def _write_full(
    data_path: Path,
    index_path: Path,
    records: pd.DataFrame,
) -> None:
    """
    全量写入.data+.index文件。

    Args:
        data_path: .data文件路径
        index_path: .index文件路径
        records: 已排序的记录DataFrame
    """
    if records.empty:
        return

    data_records: list[tuple] = []
    period_first_index: dict[int, int] = {}

    grouped = records.groupby("period_int", sort=True)
    for period_val, group_df in grouped:
        group_df = group_df.sort_values("ann_date_int")
        first_idx = len(data_records)
        period_first_index[period_val] = first_idx

        for i, (_, row) in enumerate(group_df.iterrows()):
            next_ptr = first_idx + i + 1 if i < len(group_df) - 1 else NA_INDEX
            data_records.append((
                int(row["ann_date_int"]),
                int(row["period_int"]),
                float(row["value_float32"]),
                next_ptr,
            ))

    all_period_keys = sorted(period_first_index.keys())
    first_year_val = all_period_keys[0] // 100 if all_period_keys[0] > 10000 else all_period_keys[0]

    with open(index_path, "wb") as fi:
        fi.write(struct.pack(PERIOD_DTYPE, first_year_val))

        if all_period_keys[0] > 10000:
            last_year = max(p // 100 for p in all_period_keys)
        else:
            last_year = max(all_period_keys)

        n_slots_per_year = 4 if all_period_keys[0] > 10000 else 1

        for year in range(first_year_val, last_year + 1):
            for q in range(n_slots_per_year):
                if all_period_keys[0] > 10000:
                    period_key = year * 100 + (q + 1)
                else:
                    period_key = year

                if period_key in period_first_index:
                    byte_offset = period_first_index[period_key] * DATA_DTYPE_SIZE
                    fi.write(struct.pack(INDEX_DTYPE, byte_offset))
                else:
                    fi.write(struct.pack(INDEX_DTYPE, NA_INDEX))

    with open(data_path, "wb") as fd:
        for date_int, period_int, value, next_ptr in data_records:
            fd.write(struct.pack(DATA_DTYPE, date_int, period_int, value, next_ptr))


def _write_update(
    data_path: Path,
    index_path: Path,
    records: pd.DataFrame,
) -> None:
    """
    增量追加PIT记录。

    读取已有.data文件末尾的ann_date，仅追加新日期的记录。

    Args:
        data_path: .data文件路径
        index_path: .index文件路径
        records: 已排序的记录DataFrame
    """
    if not data_path.exists():
        _write_full(data_path, index_path, records)
        return

    if records.empty:
        return

    existing_last_date = 0
    with open(data_path, "rb") as fd:
        fd.seek(-DATA_DTYPE_SIZE, 2)
        last_record = fd.read(DATA_DTYPE_SIZE)
        if len(last_record) == DATA_DTYPE_SIZE:
            existing_last_date, _, _, _ = struct.unpack(DATA_DTYPE, last_record)

    new_records = records[records["ann_date_int"] > existing_last_date]
    if new_records.empty:
        return

    _write_full(data_path, index_path, pd.concat([records.iloc[:0], new_records], ignore_index=True))
    logger.warning("PIT增量追加：重建data文件（qlib PIT暂不支持高效增量）")


def dump_pit_all(
    raw_dir: str,
    qlib_dir: str,
    stock_basic: pd.DataFrame,
    pit_fields_config: list[dict],
    overwrite: bool = True,
) -> int:
    """
    批量生成全部股票的PIT文件。

    Args:
        raw_dir: raw数据目录
        qlib_dir: Qlib数据目录
        stock_basic: stock_basic DataFrame
        pit_fields_config: PIT字段配置列表，每项含：
            - event_interface: 事件接口名
            - source_field: raw中的源字段
            - pit_field: PIT字段名（需含_q/_a后缀）
            - aggregate: 聚合方式 "latest"/"sum"/"max"
        overwrite: True=全量覆盖

    Returns:
        总写入记录数
    """
    from quantpits.scripts.data_sync import storage
    from quantpits.scripts.data_sync.ts_code_converter import tushare_to_qlib
    import pyarrow.parquet as pq

    total_records = 0

    for cfg in pit_fields_config:
        iface = cfg["event_interface"]
        source_field = cfg["source_field"]
        pit_field = cfg["pit_field"]
        aggregate = cfg.get("aggregate", "latest")

        logger.info(f"生成PIT字段 {pit_field} ← {iface}.{source_field}")

        all_dates = storage.get_all_synced_dates(iface, raw_dir)
        if not all_dates:
            logger.warning(f"事件接口 {iface} 无已同步数据，跳过")
            continue

        valid_codes = stock_basic[
            stock_basic["ts_code"].apply(lambda x: tushare_to_qlib(str(x)) is not None)
        ]["ts_code"].unique()
        valid_codes_set = set(valid_codes)

        all_frames: list[pd.DataFrame] = []
        for date_str in all_dates:
            path = os.path.join(raw_dir, iface, f"{date_str}.parquet")
            if not os.path.exists(path):
                continue
            try:
                df = pq.read_table(path).to_pandas()
                if not df.empty and "ts_code" in df.columns:
                    df = df[df["ts_code"].isin(valid_codes_set)]
                if not df.empty:
                    all_frames.append(df)
            except Exception as e:
                logger.warning(f"读取 {path} 失败：{e}")

        if not all_frames:
            continue

        event_df = pd.concat(all_frames, ignore_index=True)

        if "ann_date" not in event_df.columns or source_field not in event_df.columns:
            logger.warning(f"{iface}缺少ann_date或{source_field}列，跳过")
            continue

        event_df = event_df.dropna(subset=["ann_date", source_field])
        event_df["ann_date"] = event_df["ann_date"].apply(lambda x: f"{int(float(x)):08d}")
        event_df = event_df[event_df["ann_date"].str.len() == 8]

        if "end_date" not in event_df.columns:
            event_df["end_date"] = event_df["ann_date"]

        for ts_code, stock_events in event_df.groupby("ts_code"):
            qlib_code = tushare_to_qlib(str(ts_code))
            if qlib_code is None:
                continue

            if aggregate == "latest":
                stock_events = stock_events.sort_values("ann_date")
                stock_events = stock_events.drop_duplicates(
                    subset=["ann_date", "end_date"], keep="last"
                )
            elif aggregate == "sum":
                stock_events = stock_events.groupby(["ann_date", "end_date"], as_index=False)[source_field].sum()
            elif aggregate == "max":
                stock_events = stock_events.groupby(["ann_date", "end_date"], as_index=False)[source_field].max()

            pit_records = pd.DataFrame({
                "ann_date": stock_events["ann_date"],
                "period": stock_events["end_date"],
                "value": stock_events[source_field].astype(np.float32),
            })

            count = dump_pit_for_field(
                qlib_code, pit_field, pit_records, qlib_dir,
                quarterly=pit_field.endswith("_q"),
                overwrite=overwrite,
            )
            total_records += count

        logger.info(f"PIT字段 {pit_field} 完成，{len(event_df['ts_code'].unique())} 只股票")

    logger.info(f"PIT生成完成：{total_records} 条记录")
    return total_records
