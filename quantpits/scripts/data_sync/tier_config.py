"""
接口分层配置 — 按Tushare数据可用时间将接口分为4个tier。

tier仅为调度效率工具，不用于自动判断数据是否到位。
用户自行决定何时同步哪个tier。
"""

from quantpits.utils import env

from dataclasses import dataclass

VALID_TIERS: tuple[str, ...] = ("post_market", "capital_flow", "evening", "event")


@dataclass(frozen=True)
class TierDef:
    """
    Tier定义。

    Attributes:
        name: tier名称
        window: 数据可用时间窗口（描述性，crontab调度参考）
        interfaces: 该tier包含的Tushare接口列表
        is_event: 是否为事件型tier
    """
    name: str
    window: str
    interfaces: tuple[str, ...]
    is_event: bool


TIER_DEFINITIONS: tuple[TierDef, ...] = (
    TierDef(
        name="post_market",
        window="15:00-16:00",
        interfaces=("stk_factor_pro", "suspend_d", "stk_limit"),
        is_event=False,
    ),
    TierDef(
        name="capital_flow",
        window="16:00-19:00",
        interfaces=("moneyflow", "margin_detail", "cyq_perf"),
        is_event=False,
    ),
    TierDef(
        name="evening",
        window="19:00+",
        interfaces=("top_list", "express_vip", "forecast_vip"),
        is_event=False,
    ),
    TierDef(
        name="event",
        window="T+1",
        interfaces=("share_float", "stk_holdertrade"),
        is_event=True,
    ),
)

_INTERFACE_TO_TIER: dict[str, str] = {}
for _td in TIER_DEFINITIONS:
    for _iface in _td.interfaces:
        _INTERFACE_TO_TIER[_iface] = _td.name


def get_tier(name: str) -> TierDef:
    """
    获取tier定义。

    Args:
        name: tier名称

    Returns:
        TierDef

    Raises:
        ValueError: tier名称无效
    """
    for td in TIER_DEFINITIONS:
        if td.name == name:
            return td
    raise ValueError(
        f"无效tier: {name}，有效值为: {', '.join(VALID_TIERS)}"
    )


def get_interfaces_for_tiers(tier_names: list[str] | None) -> list[str]:
    """
    获取指定tier包含的接口列表。

    Args:
        tier_names: tier名称列表，None返回所有接口

    Returns:
        接口名称列表

    Raises:
        ValueError: 任何tier名称无效
    """
    if tier_names is None:
        result = []
        for td in TIER_DEFINITIONS:
            result.extend(td.interfaces)
        return result

    result = []
    for name in tier_names:
        td = get_tier(name)
        result.extend(td.interfaces)
    return result


def get_tier_for_interface(interface: str) -> str | None:
    """
    获取接口所属的tier名称。

    Args:
        interface: 接口名称

    Returns:
        tier名称，未找到返回None
    """
    return _INTERFACE_TO_TIER.get(interface)


def is_event_tier(tier_name: str) -> bool:
    """
    判断tier是否为事件型。

    Args:
        tier_name: tier名称

    Returns:
        True为事件型tier

    Raises:
        ValueError: tier名称无效
    """
    td = get_tier(tier_name)
    return td.is_event
