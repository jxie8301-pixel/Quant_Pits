"""
事件型接口多线程增量同步。

核心特性：
1. limiter 传入 fetch_fn，分页循环每次都限速
2. SHUTDOWN_EVENT 支持，Ctrl+C 可立即中断
3. 增量同步从最后同步日期-3天开始拉取
4. 合并写入时按主键去重
"""

from quantpits.utils import env

import os
import time
import logging
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import pandas as pd

from quantpits.scripts.data_sync.config import RATE_CONFIG, START_DATES, RETRY_TIMES, RETRY_DELAY
from quantpits.scripts.data_sync.rate_limiter import LIMITER_REGISTRY, RateLimiter, SHUTDOWN_EVENT
from quantpits.scripts.data_sync.sync.sync_daily import FatalSyncError, _is_fatal_error
from quantpits.scripts.data_sync.storage import (
    get_last_synced_date, save_daily, is_date_synced, load_one_day
)
from quantpits.scripts.data_sync.fetchers.event import (
    fetch_stk_holdertrade, fetch_share_float,
    fetch_forecast_vip, fetch_express_vip,
)
from quantpits.scripts.data_sync.sync.sync_pipeline import EventReport

logger = logging.getLogger(__name__)

EVENT_FETCH_FUNCS: dict[str, Callable] = {
    "stk_holdertrade": fetch_stk_holdertrade,
    "share_float": fetch_share_float,
    "forecast_vip": fetch_forecast_vip,
    "express_vip": fetch_express_vip,
}

EVENT_PRIMARY_KEYS: dict[str, list[str]] = {
    "stk_holdertrade": ["ts_code", "ann_date", "holder_name", "begin_date"],
    "share_float": ["ts_code", "ann_date", "float_date", "holder_name"],
    "forecast_vip": ["ts_code", "ann_date", "end_date", "type"],
    "express_vip": ["ts_code", "ann_date", "end_date"],
}

_thread_local = threading.local()


def _get_pro(token: str) -> object:
    """
    获取线程本地Tushare pro_api对象。

    Args:
        token: Tushare API Token

    Returns:
        Tushare pro_api 对象
    """
    if not hasattr(_thread_local, 'pro'):
        import tushare as ts

        ts.set_token(token)
        _thread_local.pro = ts.pro_api()
    return _thread_local.pro


def _generate_date_chunks(start_date: str,
                           end_date: str,
                           chunk_days: int = 90) -> list[tuple[str, str]]:
    """
    将日期范围按chunk_days天切分为多个时间段。

    Args:
        start_date: 起始日期YYYYMMDD
        end_date: 结束日期YYYYMMDD
        chunk_days: 每段天数，默认90

    Returns:
        [(start, end), ...] 时间段列表
    """
    chunks: list[tuple[str, str]] = []
    current = datetime.strptime(start_date, '%Y%m%d')
    end = datetime.strptime(end_date, '%Y%m%d')
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        chunks.append((current.strftime('%Y%m%d'), chunk_end.strftime('%Y%m%d')))
        current = chunk_end + timedelta(days=1)
    return chunks


def _sync_one_chunk(interface: str,
                    chunk_start: str,
                    chunk_end: str,
                    fetch_fn: Callable,
                    limiter: RateLimiter,
                    token: str,
                    project_root: str) -> tuple[str, int, int, list[str]]:
    """
    同步事件型接口的一个时间段，合并写入时按主键去重。

    Args:
        interface: 接口名称
        chunk_start: 时间段起始日期
        chunk_end: 时间段结束日期
        fetch_fn: 拉取函数
        limiter: 接口限速器
        token: Tushare API Token
        project_root: 项目根目录

    Returns:
        (接口名, 新增数, 更新数, 失败段列表) 元组
    """
    if SHUTDOWN_EVENT.is_set():
        return interface, 0, 0, []

    raw_dir = os.path.join(project_root, "data", "raw")

    for attempt in range(1, RETRY_TIMES + 1):
        if SHUTDOWN_EVENT.is_set():
            return interface, 0, 0, []
        try:
            pro = _get_pro(token)
            df = fetch_fn(pro, chunk_start, chunk_end, limiter)

            if df.empty:
                return interface, 0, 0, []

            pk_cols = EVENT_PRIMARY_KEYS.get(interface)
            written = 0
            skipped = 0
            for ann_date, group in df.groupby('ann_date'):
                group = group.reset_index(drop=True)
                if is_date_synced(interface, ann_date, raw_dir):
                    existing = load_one_day(interface, ann_date, raw_dir)
                    if not existing.empty:
                        merged = pd.concat([existing, group], ignore_index=True)
                        if pk_cols and all(c in merged.columns for c in pk_cols):
                            merged = merged.drop_duplicates(subset=pk_cols, keep='last')
                        else:
                            merged = merged.drop_duplicates()
                        save_daily(merged, interface, ann_date, raw_dir)
                    else:
                        save_daily(group, interface, ann_date, raw_dir)
                    skipped += 1
                else:
                    save_daily(group, interface, ann_date, raw_dir)
                    written += 1

            return interface, written, skipped, []

        except InterruptedError:
            logger.info(f"[{interface}] {chunk_start}~{chunk_end} 收到退出信号")
            return interface, 0, 0, []

        except Exception as e:
            if _is_fatal_error(e):
                logger.error(
                    f"[{interface}] {chunk_start}~{chunk_end} 遇到致命错误，中断同步：{e}"
                )
                raise FatalSyncError(
                    f"[{interface}] {chunk_start}~{chunk_end} 致命错误：{e}"
                ) from e

            wait = RETRY_DELAY * (2 ** (attempt - 1))
            if attempt < RETRY_TIMES:
                logger.warning(
                    f"[{interface}] {chunk_start}~{chunk_end} "
                    f"第{attempt}次失败，{wait}s 后重试：{e}"
                )
                for _ in range(int(wait * 10)):
                    if SHUTDOWN_EVENT.is_set():
                        return interface, 0, 0, []
                    time.sleep(0.1)
            else:
                logger.error(
                    f"[{interface}] {chunk_start}~{chunk_end} "
                    f"失败 {RETRY_TIMES} 次：{e}",
                    exc_info=True
                )
                return interface, 0, 0, [f"{chunk_start}~{chunk_end}"]

    return interface, 0, 0, []


def sync_one_event_interface(interface: str,
                              token: str,
                              project_root: str) -> EventReport:
    """
    同步单个事件型接口，增量同步从最后同步日期-3天开始拉取。

    Args:
        interface: 接口名称
        token: Tushare API Token
        project_root: 项目根目录

    Returns:
        EventReport 同步报告
    """
    report = EventReport(interface=interface)

    if SHUTDOWN_EVENT.is_set():
        return report

    raw_dir = os.path.join(project_root, "data", "raw")

    cfg = RATE_CONFIG.get(interface, {"rate": 150, "max_daily": None, "workers": 4})
    limiter = LIMITER_REGISTRY.get(interface, cfg['rate'], cfg['max_daily'])
    fetch_fn = EVENT_FETCH_FUNCS[interface]
    workers = cfg['workers']
    start_fb = START_DATES.get(interface, '20100101')
    today = datetime.today().strftime('%Y%m%d')

    last_date = get_last_synced_date(interface, raw_dir)
    if last_date:
        sync_from = (datetime.strptime(last_date, '%Y%m%d')
                     - timedelta(days=3)).strftime('%Y%m%d')
    else:
        sync_from = start_fb

    if sync_from > today:
        logger.info(f"[{interface}] 已是最新，无需同步")
        return report

    chunks = _generate_date_chunks(sync_from, today, chunk_days=90)
    logger.info(f"[{interface}] 从 {sync_from} 开始，{len(chunks)} 个时间段，"
                f"workers={workers}，{cfg['rate']}次/分钟")

    total_written = 0
    total_skipped = 0
    all_failed: list[str] = []
    failed_chunks: list[tuple[str, str]] = []

    executor = ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix=interface)
    try:
        futures = {
            executor.submit(
                _sync_one_chunk,
                interface, cs, ce, fetch_fn, limiter, token, project_root
            ): (cs, ce)
            for cs, ce in chunks
        }

        for future in as_completed(futures):
            if SHUTDOWN_EVENT.is_set():
                for f in futures:
                    f.cancel()
                break

            try:
                _, written, skipped, failed = future.result()
            except FatalSyncError as e:
                logger.error(f"致命错误，中断所有事件同步：{e}")
                SHUTDOWN_EVENT.set()
                for f in futures:
                    f.cancel()
                break

            total_written += written
            total_skipped += skipped
            all_failed.extend(failed)

            done_count = sum(1 for f in futures if f.done())
            logger.info(
                f"[{interface}] {done_count}/{len(chunks)} 段完成，"
                f"新增={total_written} 更新={total_skipped}"
            )
    finally:
        executor.shutdown(wait=False)

    if all_failed and not SHUTDOWN_EVENT.is_set():
        logger.warning(
            f"[{interface}] {len(all_failed)} 段首次失败，开始重试..."
        )
        for failed_range in all_failed:
            parts = failed_range.split('~')
            if len(parts) != 2:
                continue
            cs, ce = parts[0], parts[1]
            for retry in range(1, RETRY_TIMES + 1):
                if SHUTDOWN_EVENT.is_set():
                    break
                try:
                    pro = _get_pro(token)
                    df = fetch_fn(pro, cs, ce, limiter)
                    if not df.empty:
                        pk_cols = EVENT_PRIMARY_KEYS.get(interface)
                        for ann_date, group in df.groupby('ann_date'):
                            group = group.reset_index(drop=True)
                            if is_date_synced(interface, ann_date, raw_dir):
                                existing = load_one_day(interface, ann_date, raw_dir)
                                if not existing.empty:
                                    merged = pd.concat([existing, group], ignore_index=True)
                                    if pk_cols and all(c in merged.columns for c in pk_cols):
                                        merged = merged.drop_duplicates(subset=pk_cols, keep='last')
                                    else:
                                        merged = merged.drop_duplicates()
                                    save_daily(merged, interface, ann_date, raw_dir)
                                else:
                                    save_daily(group, interface, ann_date, raw_dir)
                            else:
                                save_daily(group, interface, ann_date, raw_dir)
                    logger.info(f"[{interface}] 重试 {cs}~{ce} 成功")
                    all_failed.remove(failed_range)
                    break
                except Exception as e:
                    wait = RETRY_DELAY * (2 ** (retry - 1))
                    if retry < RETRY_TIMES:
                        logger.warning(
                            f"[{interface}] 重试 {cs}~{ce} "
                            f"第{retry}次失败，{wait}s 后重试：{e}"
                        )
                        time.sleep(wait)
                    else:
                        logger.error(
                            f"[{interface}] 重试 {cs}~{ce} "
                            f"最终失败：{e}"
                        )

    report.written = total_written
    report.skipped = total_skipped
    report.failed = all_failed
    return report


def sync_all_events(token: str,
                    project_root: str,
                    interfaces: list[str] | None = None,
                    mode: str = "daily",
                    tier_names: list[str] | None = None) -> list[EventReport]:
    """
    同步所有事件型接口。

    Args:
        token: Tushare API Token
        project_root: 项目根目录
        interfaces: 指定接口列表，None时同步全部
        mode: 同步模式，"full"或"daily"
        tier_names: tier名称列表，当interfaces为None时用于筛选接口

    Returns:
        各接口的EventReport列表
    """
    logger.info("═" * 60)
    logger.info("开始同步事件型接口")
    logger.info("═" * 60)

    if interfaces is None and tier_names is not None:
        from quantpits.scripts.data_sync.tier_config import get_interfaces_for_tiers
        tier_ifaces = get_interfaces_for_tiers(tier_names)
        interfaces = [i for i in tier_ifaces if i in EVENT_FETCH_FUNCS]

    target = interfaces or list(EVENT_FETCH_FUNCS.keys())
    results: list[EventReport] = []

    executor = ThreadPoolExecutor(max_workers=len(target),
                                  thread_name_prefix="event")
    try:
        futures = {
            executor.submit(sync_one_event_interface, iface, token, project_root): iface
            for iface in target
        }
        for future in as_completed(futures):
            if SHUTDOWN_EVENT.is_set():
                for f in futures:
                    f.cancel()
                break
            result = future.result()
            results.append(result)
            mark = "✓" if not result.failed else "✗"
            logger.info(f"[{result.interface}] {mark} "
                        f"新增={result.written} 更新={result.skipped} "
                        f"失败段={len(result.failed)}")
    finally:
        executor.shutdown(wait=False)

    logger.info("═" * 60)
    return results
