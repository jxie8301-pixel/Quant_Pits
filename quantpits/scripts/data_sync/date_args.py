"""
日期参数解析 — merge/bin子命令的--dates/--start-date/--end-date参数支持。

--dates与--start-date/--end-date互斥。
"""

from quantpits.utils import env

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def parse_date_args(
    dates_str: str | None,
    start_date: str | None,
    end_date: str | None,
    calendar: list[str] | None = None,
) -> list[str] | None:
    """
    解析merge/bin的日期参数。

    Args:
        dates_str: --dates参数值，逗号分隔离散日期（如"20241230,20241231"）
        start_date: --start-date参数值
        end_date: --end-date参数值
        calendar: 交易日历列表，用于--start-date/--end-date范围过滤

    Returns:
        解析后的日期列表，None表示全量

    Raises:
        ValueError: 参数互斥、格式无效、日期范围无效
    """
    if dates_str is None and start_date is None and end_date is None:
        return None

    if dates_str is not None and (start_date is not None or end_date is not None):
        raise ValueError("--dates 与 --start-date/--end-date 互斥，不可同时指定")

    if dates_str is not None:
        dates = [d.strip() for d in dates_str.split(",") if d.strip()]
        for d in dates:
            _validate_date_format(d)
        return dates

    if start_date is not None and end_date is None:
        raise ValueError("--start-date 和 --end-date 必须同时指定")
    if start_date is None and end_date is not None:
        raise ValueError("--start-date 和 --end-date 必须同时指定")

    _validate_date_format(start_date)
    _validate_date_format(end_date)

    if start_date > end_date:
        raise ValueError(
            f"--start-date ({start_date}) 不得晚于 --end-date ({end_date})"
        )

    if calendar is not None:
        result = [d for d in calendar if start_date <= d <= end_date]
        if not result:
            logger.warning(
                f"日期范围 [{start_date}, {end_date}] 内无交易日"
            )
        return result

    return [start_date, end_date]


def _validate_date_format(date_str: str) -> None:
    """
    校验日期格式为YYYYMMDD且为有效日期。

    Args:
        date_str: 日期字符串

    Raises:
        ValueError: 格式无效或日期不存在
    """
    if not date_str or len(date_str) != 8 or not date_str.isdigit():
        raise ValueError(f"日期格式无效：{date_str}，应为YYYYMMDD")

    try:
        datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        raise ValueError(f"日期值无效：{date_str}，不存在该日期")
