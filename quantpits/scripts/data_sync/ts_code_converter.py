"""
股票代码转换 — Tushare格式 ↔ Qlib格式。

Tushare格式: 600519.SH（6位代码.交易所代码）
Qlib格式: sh600519（交易所小写前缀+6位代码）
北交所(.BJ)过滤，不写入Qlib数据。
"""


def tushare_to_qlib(ts_code: str) -> str | None:
    """
    Tushare代码转Qlib代码。

    Args:
        ts_code: Tushare格式股票代码，如 '600519.SH'

    Returns:
        Qlib格式代码（如 'sh600519'），北交所返回None

    Examples:
        >>> tushare_to_qlib('600519.SH')
        'sh600519'
        >>> tushare_to_qlib('000001.SZ')
        'sz000001'
        >>> tushare_to_qlib('830001.BJ')
        None
    """
    if not ts_code or '.' not in ts_code:
        return None
    code, suffix = ts_code.split('.', 1)
    suffix_upper = suffix.upper()
    if suffix_upper == 'SH':
        return f'sh{code}'
    elif suffix_upper == 'SZ':
        return f'sz{code}'
    else:
        return None


def qlib_to_tushare(qlib_code: str) -> str:
    """
    Qlib代码转Tushare代码。

    Args:
        qlib_code: Qlib格式股票代码，如 'sh600519'

    Returns:
        Tushare格式代码，如 '600519.SH'

    Raises:
        ValueError: qlib_code格式不正确

    Examples:
        >>> qlib_to_tushare('sh600519')
        '600519.SH'
        >>> qlib_to_tushare('sz000001')
        '000001.SZ'
    """
    if not qlib_code or len(qlib_code) < 3:
        raise ValueError(f"无效的Qlib代码: {qlib_code}")
    prefix = qlib_code[:2].lower()
    code = qlib_code[2:]
    if prefix == 'sh':
        return f'{code}.SH'
    elif prefix == 'sz':
        return f'{code}.SZ'
    else:
        raise ValueError(f"不支持的交易所前缀: {prefix}")
