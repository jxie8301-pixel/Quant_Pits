"""
Parquet读写与增量检测。

raw操作参数改为 raw_read_dir/raw_write_dir（由path_resolver提供）。
写入使用原子操作（.tmp + os.replace），防止半写文件。

文件命名规范：
- 日频接口: raw/{interface}/YYYYMMDD.parquet
- 事件型接口: raw/{interface}/YYYYMMDD.parquet (按ann_date)
- 静态表: raw/{name}.parquet
"""

import logging
import os
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


def save_daily(
    df: pd.DataFrame, interface: str, date_str: str, raw_write_dir: str
) -> str:
    """
    写入日频/事件型Parquet（原子写入）。

    Args:
        df: 数据DataFrame
        interface: 接口名称
        date_str: 日期字符串YYYYMMDD
        raw_write_dir: raw写入目录

    Returns:
        写入文件的绝对路径
    """
    folder = os.path.join(raw_write_dir, interface)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{date_str}.parquet")
    _atomic_write_parquet(df, path)
    logger.debug(f"[{interface}] 写入 {path}，{len(df)} 行")
    return path


def save_static(df: pd.DataFrame, name: str, raw_write_dir: str) -> str:
    """
    写入静态表Parquet（全量覆盖，原子写入）。

    Args:
        df: 数据DataFrame
        name: 表名（如stock_basic、trade_cal）
        raw_write_dir: raw写入目录

    Returns:
        写入文件的绝对路径
    """
    os.makedirs(raw_write_dir, exist_ok=True)
    path = os.path.join(raw_write_dir, f"{name}.parquet")
    _atomic_write_parquet(df, path)
    logger.info(f"[{name}] 静态表写入完成，{len(df)} 行 → {path}")
    return path


def load_daily(
    interface: str,
    start_date: str,
    end_date: str,
    raw_read_dir: str,
    columns: Optional[list] = None,
) -> pd.DataFrame:
    """
    读取日期范围的日频Parquet。

    Args:
        interface: 接口名称
        start_date: 起始日期YYYYMMDD
        end_date: 结束日期YYYYMMDD
        raw_read_dir: raw读取目录
        columns: 可选，仅读取指定列

    Returns:
        合并后的DataFrame，无数据时返回空DataFrame
    """
    folder = os.path.join(raw_read_dir, interface)
    if not os.path.exists(folder):
        return pd.DataFrame()
    files = sorted(
        f for f in (os.path.join(folder, fn) for fn in os.listdir(folder))
        if f.endswith(".parquet")
        and os.path.basename(f)[:8].isdigit()
        and len(os.path.basename(f)[:8]) == 8
        and start_date <= os.path.basename(f)[:8] <= end_date
    )
    if not files:
        return pd.DataFrame()
    dfs = [pq.read_table(f, columns=columns).to_pandas() for f in files]
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


def load_one_day(
    interface: str,
    date_str: str,
    raw_read_dir: str,
    columns: Optional[list] = None,
) -> pd.DataFrame:
    """
    读取单个日期的日频Parquet。

    Args:
        interface: 接口名称
        date_str: 日期字符串YYYYMMDD
        raw_read_dir: raw读取目录
        columns: 可选，仅读取指定列

    Returns:
        DataFrame，文件不存在时返回空DataFrame
    """
    path = os.path.join(raw_read_dir, interface, f"{date_str}.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pq.read_table(path, columns=columns).to_pandas()


def load_static(
    name: str, raw_read_dir: str, columns: Optional[list] = None
) -> pd.DataFrame:
    """
    读取静态表Parquet。

    Args:
        name: 表名
        raw_read_dir: raw读取目录
        columns: 可选，仅读取指定列

    Returns:
        DataFrame

    Raises:
        FileNotFoundError: 静态表不存在
    """
    path = os.path.join(raw_read_dir, f"{name}.parquet")
    if not os.path.exists(path):
        raise FileNotFoundError(f"静态表不存在：{path}，请先运行静态表同步")
    return pq.read_table(path, columns=columns).to_pandas()


def is_date_synced(interface: str, date_str: str, raw_read_dir: str) -> bool:
    """
    判断某日期数据是否已同步。

    Args:
        interface: 接口名称
        date_str: 日期字符串YYYYMMDD
        raw_read_dir: raw读取目录

    Returns:
        True表示已同步
    """
    path = os.path.join(raw_read_dir, interface, f"{date_str}.parquet")
    return os.path.exists(path)


def get_last_synced_date(interface: str, raw_read_dir: str) -> Optional[str]:
    """
    获取某接口最后同步的日期。

    Args:
        interface: 接口名称
        raw_read_dir: raw读取目录

    Returns:
        最后同步日期YYYYMMDD，无数据时返回None
    """
    folder = os.path.join(raw_read_dir, interface)
    if not os.path.exists(folder):
        return None
    dates = sorted(
        fn[:8]
        for fn in os.listdir(folder)
        if fn.endswith(".parquet") and fn[:8].isdigit() and len(fn[:8]) == 8
    )
    return dates[-1] if dates else None


def get_pending_dates(
    interface: str,
    all_trade_dates: list[str],
    start_fallback: str,
    raw_read_dir: str,
) -> list[str]:
    """
    计算缺失日期列表。

    Args:
        interface: 接口名称
        all_trade_dates: 全部交易日列表
        start_fallback: 无已同步数据时的起始日期
        raw_read_dir: raw读取目录

    Returns:
        需要同步的日期列表
    """
    last_date = get_last_synced_date(interface, raw_read_dir) or start_fallback
    candidates = [d for d in all_trade_dates if d > last_date]
    pending = [d for d in candidates if not is_date_synced(interface, d, raw_read_dir)]
    return pending


def get_all_synced_dates(interface: str, raw_read_dir: str) -> list[str]:
    """
    获取某接口所有已同步的日期。

    Args:
        interface: 接口名称
        raw_read_dir: raw读取目录

    Returns:
        已同步日期列表（排序后）
    """
    folder = os.path.join(raw_read_dir, interface)
    if not os.path.exists(folder):
        return []
    return sorted(
        fn[:8]
        for fn in os.listdir(folder)
        if fn.endswith(".parquet") and fn[:8].isdigit() and len(fn[:8]) == 8
    )


def _atomic_write_parquet(df: pd.DataFrame, path: str) -> None:
    """
    原子写入Parquet文件（.tmp + os.replace）。

    Args:
        df: 数据DataFrame
        path: 目标文件路径
    """
    tmp_path = path + ".tmp"
    try:
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, tmp_path, compression="snappy")
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
