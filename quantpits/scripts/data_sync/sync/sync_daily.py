"""
日频接口多线程增量同步。

核心调度逻辑：
1. 接口间并行（ThreadPoolExecutor）
2. 接口内按日并行（ThreadPoolExecutor）
3. 两级限速（全局桶 + 接口桶）
4. 增量检测（跳过已同步日期）
5. 指数退避重试
"""

from quantpits.utils import env

import os
import time
import logging
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import pandas as pd

from quantpits.scripts.data_sync.config import RATE_CONFIG, START_DATES, RETRY_TIMES, RETRY_DELAY
from quantpits.scripts.data_sync.rate_limiter import LIMITER_REGISTRY, RateLimiter, SHUTDOWN_EVENT


class FatalSyncError(Exception):
    """致命同步错误（IP超限、网络不可达等），应中断整个同步流程。"""


FATAL_ERROR_KEYWORDS = [
    "每分钟访问限制",
    "ip限制",
    "ip超限",
    "exceed",
    "rate limit",
    "ConnectionError",
    "MaxRetryError",
    "NewConnectionError",
    "网络",
    "timeout",
    "Timed out",
    "connect timeout",
    "Connection refused",
    "Name or service not known",
    "HTTP 5",
    "500",
    "502",
    "503",
    "504",
]


def _is_fatal_error(e: Exception) -> bool:
    """
    判断异常是否为致命错误（IP超限、网络不可达等）。

    Args:
        e: 异常对象

    Returns:
        True表示致命错误，应中断整个流程
    """
    msg = str(e).lower()
    return any(kw.lower() in msg for kw in FATAL_ERROR_KEYWORDS)


from quantpits.scripts.data_sync.storage import (
    load_static, get_pending_dates, is_date_synced, save_daily
)
from quantpits.scripts.data_sync.fetchers.daily import (
    fetch_stk_factor_pro, fetch_cyq_perf, fetch_moneyflow,
    fetch_margin_detail, fetch_stk_limit, fetch_suspend_d, fetch_top_list,
)
from quantpits.scripts.data_sync.sync.sync_pipeline import DailyReport

logger = logging.getLogger(__name__)

DAILY_FETCH_FUNCS: dict[str, Callable] = {
    "stk_factor_pro": fetch_stk_factor_pro,
    "cyq_perf": fetch_cyq_perf,
    "moneyflow": fetch_moneyflow,
    "margin_detail": fetch_margin_detail,
    "stk_limit": fetch_stk_limit,
    "suspend_d": fetch_suspend_d,
    "top_list": fetch_top_list,
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


def _sync_one_day(interface: str,
                  trade_date: str,
                  fetch_fn: Callable,
                  limiter: RateLimiter,
                  token: str,
                  project_root: str,
                  force: bool = False) -> tuple[str, str, bool]:
    """
    同步单接口单日，失败自动指数退避重试。

    Args:
        interface: 接口名称
        trade_date: 交易日期YYYYMMDD
        fetch_fn: 拉取函数
        limiter: 接口限速器
        token: Tushare API Token
        project_root: 项目根目录
        force: True时即使文件已存在也重新拉取并覆盖

    Returns:
        (接口名, 日期, 是否成功) 元组

    Raises:
        FatalSyncError: 遇到IP超限、网络不可达等致命错误
    """
    raw_dir = os.path.join(project_root, "data", "raw")

    if not force and is_date_synced(interface, trade_date, raw_dir):
        return interface, trade_date, True

    for attempt in range(1, RETRY_TIMES + 1):
        if SHUTDOWN_EVENT.is_set():
            return interface, trade_date, False
        try:
            pro = _get_pro(token)
            df = fetch_fn(pro, trade_date, limiter)
            if df is not None and not df.empty:
                save_daily(df, interface, trade_date, raw_dir)
            else:
                logger.debug(f"[{interface}] {trade_date} 无数据，跳过保存")
            return interface, trade_date, True

        except InterruptedError:
            logger.info(f"[{interface}] {trade_date} 收到退出信号，中止")
            return interface, trade_date, False

        except Exception as e:
            if _is_fatal_error(e):
                logger.error(
                    f"[{interface}] {trade_date} 遇到致命错误，中断同步：{e}"
                )
                raise FatalSyncError(
                    f"[{interface}] {trade_date} 致命错误：{e}"
                ) from e

            wait = RETRY_DELAY * (2 ** (attempt - 1))
            if attempt < RETRY_TIMES:
                logger.warning(
                    f"[{interface}] {trade_date} 第{attempt}次失败，"
                    f"{wait}s 后重试：{e}"
                )
                for _ in range(int(wait * 10)):
                    if SHUTDOWN_EVENT.is_set():
                        return interface, trade_date, False
                    time.sleep(0.1)
            else:
                logger.error(
                    f"[{interface}] {trade_date} 已失败 {RETRY_TIMES} 次：{e}",
                    exc_info=True
                )
                return interface, trade_date, False

    return interface, trade_date, False


def sync_one_interface(interface: str,
                       pro_token: str,
                       trade_dates: list[str],
                       project_root: str,
                       mode: str = "daily",
                       repair: bool = False) -> DailyReport:
    """
    同步单个日频接口的所有待补日期。

    Args:
        interface: 接口名称
        pro_token: Tushare API Token
        trade_dates: 全量交易日列表
        project_root: 项目根目录
        mode: 同步模式，full=全量覆盖，daily=增量
        repair: True时扫描历史缺口并只补缺失文件

    Returns:
        DailyReport 同步报告
    """
    raw_dir = os.path.join(project_root, "data", "raw")
    report = DailyReport(interface=interface)

    if SHUTDOWN_EVENT.is_set():
        return report

    cfg = RATE_CONFIG[interface]
    limiter = LIMITER_REGISTRY.get(interface, cfg['rate'], cfg['max_daily'])
    fetch_fn = DAILY_FETCH_FUNCS[interface]
    workers = cfg['workers']
    start_fb = START_DATES.get(interface, '20100101')

    today = datetime.today().strftime('%Y%m%d')
    candidates = [d for d in trade_dates if start_fb <= d <= today]
    if mode == "full":
        pending = candidates
        force = True
    elif repair:
        pending = [d for d in candidates if not is_date_synced(interface, d, raw_dir)]
        force = False
    else:
        pending = get_pending_dates(interface, trade_dates, start_fb, raw_dir)
        pending = [d for d in pending if d <= today]
        force = False

    if not pending:
        logger.info(f"[{interface}] 已是最新，无需同步")
        return report

    est_minutes = len(pending) / cfg['rate'] if cfg['rate'] > 0 else 0
    sync_kind = "全量覆盖" if force else ("历史修复" if repair else "增量")
    logger.info(f"[{interface}] {sync_kind}待同步 {len(pending)} 天，"
                f"workers={workers}，{cfg['rate']}次/分钟，"
                f"预计 ~{est_minutes:.0f}分钟")

    success_count = 0
    failed_count = 0
    failed_dates: list[str] = []

    executor = ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix=interface)
    try:
        futures = {
            executor.submit(
                _sync_one_day,
                interface, trade_date, fetch_fn, limiter, pro_token, project_root, force
            ): trade_date
            for trade_date in pending
        }

        for future in as_completed(futures):
            if SHUTDOWN_EVENT.is_set():
                for f in futures:
                    f.cancel()
                break

            try:
                _, date_str, ok = future.result()
            except FatalSyncError as e:
                logger.error(f"致命错误，中断所有接口同步：{e}")
                SHUTDOWN_EVENT.set()
                for f in futures:
                    f.cancel()
                break

            if ok:
                success_count += 1
            else:
                failed_count += 1
                failed_dates.append(date_str)

            done = success_count + failed_count
            progress_pct = done / len(pending) * 100
            log_interval = max(1, min(50, len(pending) // 20))
            if done % log_interval == 0 or done == len(pending):
                logger.info(
                    f"[{interface}] {done}/{len(pending)} "
                    f"({progress_pct:.0f}%) ✓{success_count} ✗{failed_count}"
                )
    finally:
        executor.shutdown(wait=False)

    if failed_dates:
        logger.warning(f"[{interface}] 失败日期：{sorted(failed_dates)}")

    report.success = success_count
    report.failed = failed_count
    report.failed_dates = sorted(failed_dates)
    return report


def sync_all_daily(token: str,
                   project_root: str,
                   interfaces: list[str] | None = None,
                   mode: str = "daily",
                   tier_names: list[str] | None = None,
                   repair: bool = False) -> list[DailyReport]:
    """
    同步所有日频接口（接口之间并行）。

    Args:
        token: Tushare API Token
        project_root: 项目根目录
        interfaces: 指定接口列表，None时同步全部
        mode: 同步模式，"full"或"daily"
        tier_names: tier名称列表，当interfaces为None时用于筛选接口
        repair: True时补齐历史缺失日期但不覆盖已有raw

    Returns:
        各接口的DailyReport列表
    """
    logger.info("═" * 60)
    logger.info("开始同步日频接口")
    logger.info("═" * 60)

    raw_dir = os.path.join(project_root, "data", "raw")

    try:
        cal = load_static("trade_cal", raw_dir)
    except FileNotFoundError:
        logger.error("交易日历不存在，请先运行 sync_static_tables()")
        return []

    trade_dates = cal[cal['is_open'] == 1]['cal_date'].sort_values().tolist()

    if interfaces is None and tier_names is not None:
        from quantpits.scripts.data_sync.tier_config import get_interfaces_for_tiers
        tier_ifaces = get_interfaces_for_tiers(tier_names)
        interfaces = [i for i in tier_ifaces if i in DAILY_FETCH_FUNCS]

    target = interfaces or list(DAILY_FETCH_FUNCS.keys())
    results: list[DailyReport] = []

    executor = ThreadPoolExecutor(max_workers=len(target),
                                  thread_name_prefix="iface")
    try:
        futures = {
            executor.submit(
                sync_one_interface, iface, token, trade_dates, project_root, mode, repair
            ): iface
            for iface in target
        }

        for future in as_completed(futures):
            if SHUTDOWN_EVENT.is_set():
                for f in futures:
                    f.cancel()
                break
            result = future.result()
            results.append(result)
            logger.info(f"[{result.interface}] ✓{result.success} ✗{result.failed}")
    finally:
        executor.shutdown(wait=False)

    logger.info("═" * 60)
    total_ok = sum(r.success for r in results)
    total_err = sum(r.failed for r in results)
    for r in sorted(results, key=lambda x: x.interface):
        mark = "✓" if r.failed == 0 else "✗"
        logger.info(f"  {mark} {r.interface:20s} ✓{r.success:4d} ✗{r.failed:3d}")
    logger.info(f"合计：✓{total_ok}  ✗{total_err}")
    logger.info("═" * 60)
    return results
