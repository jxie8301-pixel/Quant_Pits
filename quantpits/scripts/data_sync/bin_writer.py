"""
bin文件底层写入 — Qlib bin格式读写操作。

bin文件格式：[float32 start_index, float32 v0, v1, ...]，小端字节序(<f)。
start_index为该股票在calendar中的起始位置，后续为每日特征值。
"""

from quantpits.utils import env

import logging
import os
import struct

import numpy as np

logger = logging.getLogger(__name__)

_FLOAT32_STRUCT = struct.Struct("<f")
_FLOAT32_SIZE = 4
_MIN_BIN_BYTES = _FLOAT32_SIZE * 2


class BinWriter:
    """Qlib bin文件写入器，提供新建、追加、重写三种路径。"""

    @staticmethod
    def write_new(
        file_path: str, start_index: int, data: np.ndarray
    ) -> None:
        """
        新建bin文件，写入[start_index, v0, v1, ...]。

        Args:
            file_path: bin文件路径
            start_index: 起始索引
            data: 特征值数组
        """
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        arr = np.concatenate([[np.float32(start_index)], data.astype(np.float32)])
        trimmed = _trim_trailing_nan(arr)
        trimmed.tofile(file_path)

    @staticmethod
    def append_fast(
        file_path: str,
        old_end_index: int,
        new_start_index: int,
        new_data: np.ndarray,
    ) -> None:
        """
        快速追加路径 — 当old_end_index == new_start_index时直接追加。

        Args:
            file_path: bin文件路径
            old_end_index: 旧数据的结束索引（下一个写入位置）
            new_start_index: 新数据的起始索引
            new_data: 新增特征值数组
        """
        if old_end_index != new_start_index:
            raise ValueError(
                f"快速追加要求 old_end_index == new_start_index，"
                f"但 {old_end_index} != {new_start_index}"
            )
        new_arr = new_data.astype(np.float32)
        trimmed = _trim_trailing_nan(new_arr)
        with open(file_path, "ab") as f:
            trimmed.tofile(f)

    @staticmethod
    def rewrite_with_merge(
        file_path: str,
        old_start_index: int,
        old_data: np.ndarray,
        new_start_index: int,
        new_data: np.ndarray,
    ) -> None:
        """
        慢速重写路径 — 合并旧数据和新数据后重写bin文件。

        合并策略：new_value.fillna(old_value) — 新值优先，NaN处保留旧值。
        修正Qlib缺陷：确保文件头部4字节为start_index值。

        Args:
            file_path: bin文件路径
            old_start_index: 旧数据起始索引
            old_data: 旧特征值数组
            new_start_index: 新数据起始索引
            new_data: 新特征值数组
        """
        old_end = old_start_index + len(old_data)
        new_end = new_start_index + len(new_data)
        merged_start = min(old_start_index, new_start_index)
        merged_end = max(old_end, new_end)
        merged_len = merged_end - merged_start

        merged = np.full(merged_len, np.nan, dtype=np.float32)

        old_offset = old_start_index - merged_start
        merged[old_offset : old_offset + len(old_data)] = old_data.astype(np.float32)

        new_offset = new_start_index - merged_start
        new_arr = new_data.astype(np.float32)
        new_slice = merged[new_offset : new_offset + len(new_data)]
        not_nan_mask = ~np.isnan(new_arr)
        new_slice[not_nan_mask] = new_arr[not_nan_mask]

        arr = np.concatenate([[np.float32(merged_start)], merged])
        trimmed = _trim_trailing_nan(arr)
        trimmed.tofile(file_path)

    @staticmethod
    def read_bin_metadata(file_path: str) -> tuple[int, int, int]:
        """
        读取bin文件元数据。

        Args:
            file_path: bin文件路径

        Returns:
            (start_index, end_index, data_count) 元组
            start_index: 起始索引
            end_index: 结束索引（start_index + data_count - 1）
            data_count: 数据值个数（不含start_index）

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件格式不正确
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"bin文件不存在：{file_path}")

        file_size = os.path.getsize(file_path)
        if file_size < _MIN_BIN_BYTES:
            raise ValueError(f"bin文件过小（{file_size}字节）：{file_path}")

        total_values = file_size // _FLOAT32_SIZE
        with open(file_path, "rb") as f:
            start_index = _FLOAT32_STRUCT.unpack(f.read(_FLOAT32_SIZE))[0]

        data_count = total_values - 1
        end_index = int(start_index) + data_count - 1
        return int(start_index), end_index, data_count

    @staticmethod
    def write_feature_bin(
        file_path: str,
        calendar: list[str],
        dates: list[str],
        values: np.ndarray,
        mode: str = "daily",
        calendar_index_map: dict[str, int] | None = None,
    ) -> None:
        """
        统一写入入口 — 根据模式选择新建/追加/重写。

        Args:
            file_path: bin文件路径
            calendar: 完整交易日历
            dates: 本次数据对应的交易日期列表
            values: 特征值数组，长度与dates一致
            mode: "full"强制重写，"daily"增量写入
            calendar_index_map: 可选日期索引映射，传入后可避免O(n)查找
        """
        if len(dates) == 0 or len(values) == 0:
            return

        new_start_index = _find_start_index(
            dates[0], calendar, calendar_index_map=calendar_index_map
        )
        if new_start_index < 0:
            logger.warning(f"日期 {dates[0]} 不在日历中，跳过写入 {file_path}")
            return

        if not os.path.exists(file_path):
            BinWriter.write_new(file_path, new_start_index, values)
            return

        file_size = os.path.getsize(file_path)
        if file_size < _MIN_BIN_BYTES:
            logger.warning(f"损坏bin文件（{file_size}字节），删除重建：{file_path}")
            os.remove(file_path)
            BinWriter.write_new(file_path, new_start_index, values)
            return

        try:
            old_start_index, old_end_index, old_data_count = BinWriter.read_bin_metadata(file_path)
        except (ValueError, struct.error) as e:
            logger.warning(f"读取bin元数据失败（{e}），删除重建：{file_path}")
            os.remove(file_path)
            BinWriter.write_new(file_path, new_start_index, values)
            return

        with open(file_path, "rb") as f:
            f.seek(_FLOAT32_SIZE)
            old_data = np.frombuffer(f.read(), dtype=np.float32)

        next_write_index = old_end_index + 1

        if new_start_index == next_write_index:
            BinWriter.append_fast(file_path, next_write_index, new_start_index, values)
        else:
            BinWriter.rewrite_with_merge(
                file_path, old_start_index, old_data, new_start_index, values
            )


def _trim_trailing_nan(arr: np.ndarray) -> np.ndarray:
    """
    截断末尾NaN值。

    Args:
        arr: float32数组

    Returns:
        截断后的数组（至少保留前2个元素：start_index + v0）
    """
    last_valid = len(arr) - 1
    while last_valid > 1 and np.isnan(arr[last_valid]):
        last_valid -= 1
    return arr[: last_valid + 1]


def _find_start_index(
    date_str: str,
    calendar: list[str],
    calendar_index_map: dict[str, int] | None = None,
) -> int:
    """
    查找日期在日历中的索引。

    Args:
        date_str: 日期YYYYMMDD
        calendar: 交易日历列表
        calendar_index_map: 可选的日期索引映射，传入后可避免O(n)查找

    Returns:
        索引位置，未找到返回-1
    """
    if calendar_index_map is not None:
        return calendar_index_map.get(date_str, -1)

    try:
        return calendar.index(date_str)
    except ValueError:
        return -1
