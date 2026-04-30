"""
事件型接口拉取函数。

每次 API 调用使用 acquire_both(limiter)，同时过全局桶和接口桶。
"""

from quantpits.utils import env

import logging

import pandas as pd

from quantpits.scripts.data_sync.rate_limiter import RateLimiter, acquire_both

logger = logging.getLogger(__name__)


def _clean_ann_date(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗公告日期字段，过滤空值和非8位日期。

    Args:
        df: 待清洗DataFrame

    Returns:
        清洗后的DataFrame
    """
    if df.empty:
        return df
    df = df.dropna(subset=['ann_date'])
    df = df[df['ann_date'].astype(str).str.len() == 8]
    return df.reset_index(drop=True)


def fetch_stk_holdertrade(pro: object,
                          start_date: str,
                          end_date: str,
                          limiter: RateLimiter) -> pd.DataFrame:
    """
    大股东增减持，金额单位：元，股数：股，每页3000条，分页拉取。

    Args:
        pro: Tushare pro_api 对象
        start_date: 起始日期YYYYMMDD
        end_date: 结束日期YYYYMMDD
        limiter: 接口限速器

    Returns:
        大股东增减持DataFrame，无数据时返回空DataFrame
    """
    all_frames: list[pd.DataFrame] = []
    offset = 0
    limit = 3000
    while True:
        acquire_both(limiter)
        df = pro.stk_holdertrade(
            start_date=start_date, end_date=end_date,
            offset=offset, limit=limit,
            fields=(
                'ts_code,ann_date,end_date,holder_name,holder_type,'
                'in_de,change_vol,change_ratio,'
                'after_share,after_ratio,'
                'avg_price,total_share,'
                'begin_date,close_date'
            )
        )
        if df is None or df.empty:
            break
        all_frames.append(df)
        if len(df) < limit:
            break
        offset += limit
    if not all_frames:
        return pd.DataFrame()
    result = _clean_ann_date(pd.concat(all_frames, ignore_index=True))
    logger.info(f"[stk_holdertrade] {start_date}~{end_date} 获取 {len(result)} 条")
    return result


def fetch_share_float(pro: object,
                      start_date: str,
                      end_date: str,
                      limiter: RateLimiter) -> pd.DataFrame:
    """
    限售股解禁，按ann_date逐日查询。

    Tushare share_float的start_date/end_date是解禁日(float_date)筛选，
    不适合按时间段批量拉取。改为按ann_date逐日查询。

    Args:
        pro: Tushare pro_api 对象
        start_date: 起始日期YYYYMMDD（ann_date范围）
        end_date: 结束日期YYYYMMDD（ann_date范围）
        limiter: 接口限速器

    Returns:
        限售股解禁DataFrame，无数据时返回空DataFrame
    """
    from datetime import datetime, timedelta

    all_frames: list[pd.DataFrame] = []
    current = datetime.strptime(start_date, '%Y%m%d')
    end = datetime.strptime(end_date, '%Y%m%d')

    while current <= end:
        date_str = current.strftime('%Y%m%d')
        acquire_both(limiter)
        try:
            df = pro.share_float(
                ann_date=date_str,
                fields=(
                    'ts_code,ann_date,float_date,'
                    'float_share,float_ratio,'
                    'holder_name,share_type'
                )
            )
            if df is not None and not df.empty:
                all_frames.append(df)
        except Exception as e:
            logger.warning(f"[share_float] {date_str} 拉取失败: {e}")
            raise
        current += timedelta(days=1)

    if not all_frames:
        return pd.DataFrame()
    result = _clean_ann_date(pd.concat(all_frames, ignore_index=True))
    logger.info(f"[share_float] {start_date}~{end_date} 获取 {len(result)} 条")
    return result


def fetch_forecast_vip(pro: object,
                       start_date: str,
                       end_date: str,
                       limiter: RateLimiter) -> pd.DataFrame:
    """
    业绩预告，金额单位：万元，变动幅度：% ，每页3000条，分页拉取。

    Args:
        pro: Tushare pro_api 对象
        start_date: 起始日期YYYYMMDD
        end_date: 结束日期YYYYMMDD
        limiter: 接口限速器

    Returns:
        业绩预告DataFrame，无数据时返回空DataFrame
    """
    all_frames: list[pd.DataFrame] = []
    offset = 0
    limit = 3000
    while True:
        acquire_both(limiter)
        df = pro.forecast_vip(
            start_date=start_date, end_date=end_date,
            offset=offset, limit=limit,
            fields=(
                'ts_code,ann_date,end_date,type,'
                'p_change_min,p_change_max,'
                'net_profit_min,net_profit_max,'
                'last_parent_net,first_ann_date,summary'
            )
        )
        if df is None or df.empty:
            break
        all_frames.append(df)
        if len(df) < limit:
            break
        offset += limit
    if not all_frames:
        return pd.DataFrame()
    result = _clean_ann_date(pd.concat(all_frames, ignore_index=True))
    logger.info(f"[forecast_vip] {start_date}~{end_date} 获取 {len(result)} 条")
    return result


def fetch_express_vip(pro: object,
                      start_date: str,
                      end_date: str,
                      limiter: RateLimiter) -> pd.DataFrame:
    """
    业绩快报，金额单位：元（注意与forecast的万元不同），每页3000条，分页拉取。

    Args:
        pro: Tushare pro_api 对象
        start_date: 起始日期YYYYMMDD
        end_date: 结束日期YYYYMMDD
        limiter: 接口限速器

    Returns:
        业绩快报DataFrame，无数据时返回空DataFrame
    """
    all_frames: list[pd.DataFrame] = []
    offset = 0
    limit = 3000
    while True:
        acquire_both(limiter)
        df = pro.express_vip(
            start_date=start_date, end_date=end_date,
            offset=offset, limit=limit,
            fields=(
                'ts_code,ann_date,end_date,'
                'revenue,operate_profit,total_profit,'
                'n_income,total_assets,'
                'total_hldr_eqy_exc_min_int,'
                'diluted_eps,diluted_roe,'
                'yoy_net_profit,bps,yoy_sales,yoy_op'
            )
        )
        if df is None or df.empty:
            break
        all_frames.append(df)
        if len(df) < limit:
            break
        offset += limit
    if not all_frames:
        return pd.DataFrame()
    result = _clean_ann_date(pd.concat(all_frames, ignore_index=True))
    logger.info(f"[express_vip] {start_date}~{end_date} 获取 {len(result)} 条")
    return result
