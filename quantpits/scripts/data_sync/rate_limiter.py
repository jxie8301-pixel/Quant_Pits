"""
两级令牌桶限速器 — 全局账号级 + 单接口级。

线程安全，支持SHUTDOWN_EVENT优雅退出。
"""

import time
import threading
import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

SHUTDOWN_EVENT = threading.Event()


class RateLimiter:
    """
    令牌桶限速器（线程安全），支持SHUTDOWN_EVENT中断。

    Args:
        rate: 每分钟最大请求次数
        max_daily: 每日最大请求次数，None表示不限
        name: 限速器名称（用于日志）
    """

    def __init__(self, rate: int, max_daily: Optional[int], name: str) -> None:
        self.name = name
        self.rate = rate
        self.interval = 60.0 / rate
        self.max_daily = max_daily
        self._lock = threading.Lock()
        self._last_time = 0.0
        self._today = date.today()
        self._daily_count = 0

    def acquire(self) -> None:
        """
        阻塞直到可以安全发起请求。

        Raises:
            InterruptedError: 收到SHUTDOWN_EVENT退出信号
        """
        if SHUTDOWN_EVENT.is_set():
            raise InterruptedError(f"[{self.name}] 收到退出信号")
        with self._lock:
            self._reset_daily_if_needed()
            self._block_if_daily_exceeded()
            self._wait_interval()
            self._daily_count += 1

    def _reset_daily_if_needed(self) -> None:
        today = date.today()
        if today != self._today:
            self._today = today
            self._daily_count = 0

    def _block_if_daily_exceeded(self) -> None:
        if self.max_daily is None or self._daily_count < self.max_daily:
            return
        self._lock.release()
        try:
            midnight = time.mktime(date.today().timetuple()) + 86400
            wait_secs = midnight - time.time() + 1
            logger.warning(
                f"[{self.name}] 达到每日上限 {self.max_daily}，"
                f"等待 {wait_secs:.0f}s 至明日0点"
            )
            _interruptible_sleep(wait_secs)
        finally:
            self._lock.acquire()
        self._reset_daily_if_needed()

    def _wait_interval(self) -> None:
        need = self.interval - (time.time() - self._last_time)
        if need > 0:
            _interruptible_sleep(need)
        self._last_time = time.time()


def _interruptible_sleep(seconds: float) -> None:
    """
    可中断睡眠，每50ms检查SHUTDOWN_EVENT。

    Args:
        seconds: 睡眠秒数

    Raises:
        InterruptedError: 收到退出信号
    """
    end = time.time() + seconds
    while time.time() < end:
        if SHUTDOWN_EVENT.is_set():
            raise InterruptedError("收到退出信号")
        time.sleep(min(0.05, end - time.time()))


GLOBAL_LIMITER = RateLimiter(rate=280, max_daily=None, name="GLOBAL")


def acquire_both(interface_limiter: RateLimiter) -> None:
    """
    两级限速统一入口：先过全局桶，再过接口桶。

    Args:
        interface_limiter: 接口级限速器

    Raises:
        InterruptedError: 收到退出信号
    """
    GLOBAL_LIMITER.acquire()
    interface_limiter.acquire()


class RateLimiterRegistry:
    """接口级限速器注册表（线程安全）。"""

    def __init__(self) -> None:
        self._limiters: dict[str, RateLimiter] = {}
        self._lock = threading.Lock()

    def get(self, interface: str, rate: int, max_daily: Optional[int]) -> RateLimiter:
        """
        获取或创建接口限速器。

        Args:
            interface: 接口名称
            rate: 每分钟最大请求次数
            max_daily: 每日最大请求次数

        Returns:
            对应接口的RateLimiter实例
        """
        with self._lock:
            if interface not in self._limiters:
                self._limiters[interface] = RateLimiter(
                    rate=rate, max_daily=max_daily, name=interface
                )
            return self._limiters[interface]


LIMITER_REGISTRY = RateLimiterRegistry()
