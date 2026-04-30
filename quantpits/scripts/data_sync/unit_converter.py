"""
数据单位统一转换 — 在宽表Parquet合并阶段执行。

统一标准：金额→元，数量→股，市值→元，股本→股。
raw/目录保留Tushare原始单位不变，仅feature/宽表Parquet统一。
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

UNIT_CONVERSION_RULES: dict[str, dict[str, dict]] = {
    "stk_factor_pro": {
        "amount": {"factor": 1000.0, "source_unit": "千元", "target_unit": "元"},
        "total_mv": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "circ_mv": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "free_share": {"factor": 10000.0, "source_unit": "万股", "target_unit": "股"},
    },
    "moneyflow": {
        "net_mf_amount": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "buy_elg_amount": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "sell_elg_amount": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "buy_lg_amount": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "sell_lg_amount": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "buy_md_amount": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "sell_md_amount": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "buy_sm_amount": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "sell_sm_amount": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
    },
    "top_list": {
        "net_amount": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "l_amount": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "l_sell": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
        "l_buy": {"factor": 10000.0, "source_unit": "万元", "target_unit": "元"},
    },
}


def get_conversion_rules(interface: str) -> dict[str, float]:
    """
    获取某接口的单位转换规则。

    Args:
        interface: 接口名称

    Returns:
        字段名→转换系数的映射，如 {'amount': 1000.0}
    """
    rules = UNIT_CONVERSION_RULES.get(interface, {})
    return {field: info["factor"] for field, info in rules.items()}


def apply_unit_conversion(df: pd.DataFrame, interface: str) -> pd.DataFrame:
    """
    对DataFrame中需要单位转换的列执行乘法转换。

    仅在宽表合并阶段调用，raw/目录原始Parquet不做转换。
    责任边界：宽表合并阶段统一单位，bin转换阶段仅做字段映射+后复权计算。

    Args:
        df: 原始数据DataFrame
        interface: 接口名称，用于匹配转换规则

    Returns:
        转换后的DataFrame（原地修改副本）
    """
    rules = get_conversion_rules(interface)
    if not rules:
        return df

    for field_name, factor in rules.items():
        if field_name not in df.columns:
            logger.warning(f"单位转换字段 {field_name} 在 {interface} 中不存在，跳过")
            continue

        df[field_name] = df[field_name] * factor
        source_unit = UNIT_CONVERSION_RULES[interface][field_name]["source_unit"]
        target_unit = UNIT_CONVERSION_RULES[interface][field_name]["target_unit"]
        logger.debug(
            f"[{interface}] {field_name}: {source_unit}→{target_unit} (×{factor:.0f})"
        )

        max_val = df[field_name].max()
        if max_val > 5e11:
            logger.warning(
                f"[{interface}] {field_name} 最大值 {max_val:.2e} 超过5e11，"
                f"float32可能精度不足"
            )

    return df
