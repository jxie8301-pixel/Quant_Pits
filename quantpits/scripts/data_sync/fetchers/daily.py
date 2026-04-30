"""
日频接口拉取函数。

每次 API 调用使用 acquire_both(limiter)，同时过全局桶和接口桶。
"""

from quantpits.utils import env

import logging

import pandas as pd

from quantpits.scripts.data_sync.rate_limiter import RateLimiter, acquire_both

logger = logging.getLogger(__name__)


def fetch_stk_factor_pro(pro: object,
                         trade_date: str,
                         limiter: RateLimiter) -> pd.DataFrame:
    """
    技术面因子，全A一次取完，无需分页。

    Args:
        pro: Tushare pro_api 对象
        trade_date: 交易日期YYYYMMDD
        limiter: 接口限速器

    Returns:
        技术面因子DataFrame，无数据时返回空DataFrame
    """
    acquire_both(limiter)
    df = pro.stk_factor_pro(
        trade_date=trade_date,
        fields=(
            'ts_code,trade_date,'
            'open_qfq,high_qfq,low_qfq,close_qfq,pre_close,'
            'open_hfq,high_hfq,low_hfq,close_hfq,'
            'pct_chg,vol,amount,'
            'turnover_rate,turnover_rate_f,volume_ratio,'
            'pe_ttm,pb,ps_ttm,dv_ttm,'
            'total_share,float_share,free_share,'
            'total_mv,circ_mv,adj_factor,'
            'macd_bfq,macd_dif_bfq,macd_dea_bfq,'
            'kdj_k_bfq,kdj_d_bfq,kdj_bfq,'
            'rsi_bfq_6,rsi_bfq_12,rsi_bfq_24,'
            'ma_bfq_5,ma_bfq_10,ma_bfq_20,ma_bfq_60,ma_bfq_250,'
            'ema_bfq_5,ema_bfq_10,ema_bfq_20,ema_bfq_60,'
            'boll_upper_bfq,boll_mid_bfq,boll_lower_bfq,'
            'obv_bfq,mfi_bfq,'
            'roc_bfq,mtm_bfq,bias1_bfq,bias2_bfq,bias3_bfq,'
            'updays,downdays,topdays,lowdays'
        )
    )
    if df is None or df.empty:
        logger.warning(f"[stk_factor_pro] {trade_date} 无数据")
        return pd.DataFrame()
    return df


def fetch_cyq_perf(pro: object,
                   trade_date: str,
                   limiter: RateLimiter) -> pd.DataFrame:
    """
    筹码胜率，每页1000条，分页拉取。

    Args:
        pro: Tushare pro_api 对象
        trade_date: 交易日期YYYYMMDD
        limiter: 接口限速器

    Returns:
        筹码胜率DataFrame，无数据时返回空DataFrame
    """
    all_frames: list[pd.DataFrame] = []
    offset = 0
    limit = 1000
    while True:
        acquire_both(limiter)
        df = pro.cyq_perf(
            trade_date=trade_date, offset=offset, limit=limit,
            fields=(
                'ts_code,trade_date,'
                'his_low,his_high,'
                'cost_5pct,cost_15pct,cost_50pct,'
                'cost_85pct,cost_95pct,'
                'weight_avg,winner_rate'
            )
        )
        if df is None or df.empty:
            break
        all_frames.append(df)
        if len(df) < limit:
            break
        offset += limit
    if not all_frames:
        logger.warning(f"[cyq_perf] {trade_date} 无数据")
        return pd.DataFrame()
    return pd.concat(all_frames, ignore_index=True)


def fetch_moneyflow(pro: object,
                    trade_date: str,
                    limiter: RateLimiter) -> pd.DataFrame:
    """
    个股资金流向，金额单位：万元，每页5000条，分页拉取。

    Args:
        pro: Tushare pro_api 对象
        trade_date: 交易日期YYYYMMDD
        limiter: 接口限速器

    Returns:
        资金流向DataFrame，无数据时返回空DataFrame
    """
    all_frames: list[pd.DataFrame] = []
    offset = 0
    limit = 5000
    while True:
        acquire_both(limiter)
        df = pro.moneyflow(
            trade_date=trade_date, offset=offset, limit=limit,
            fields=(
                'ts_code,trade_date,'
                'buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,'
                'buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,'
                'buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,'
                'buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,'
                'net_mf_vol,net_mf_amount'
            )
        )
        if df is None or df.empty:
            break
        all_frames.append(df)
        if len(df) < limit:
            break
        offset += limit
    if not all_frames:
        logger.warning(f"[moneyflow] {trade_date} 无数据")
        return pd.DataFrame()
    return pd.concat(all_frames, ignore_index=True)


def fetch_margin_detail(pro: object,
                        trade_date: str,
                        limiter: RateLimiter) -> pd.DataFrame:
    """
    融资融券明细，金额单位：元（原始保留），每页5000条，分页拉取。

    Args:
        pro: Tushare pro_api 对象
        trade_date: 交易日期YYYYMMDD
        limiter: 接口限速器

    Returns:
        融资融券明细DataFrame，无数据时返回空DataFrame
    """
    all_frames: list[pd.DataFrame] = []
    offset = 0
    limit = 5000
    while True:
        acquire_both(limiter)
        df = pro.margin_detail(
            trade_date=trade_date, offset=offset, limit=limit,
            fields='ts_code,trade_date,rzye,rzmre,rzche,rqye,rqmcl,rqchl,rqyl'
        )
        if df is None or df.empty:
            break
        all_frames.append(df)
        if len(df) < limit:
            break
        offset += limit
    if not all_frames:
        logger.warning(f"[margin_detail] {trade_date} 无数据")
        return pd.DataFrame()
    return pd.concat(all_frames, ignore_index=True)


def fetch_stk_limit(pro: object,
                    trade_date: str,
                    limiter: RateLimiter) -> pd.DataFrame:
    """
    涨跌停价，全量一次取完。

    Args:
        pro: Tushare pro_api 对象
        trade_date: 交易日期YYYYMMDD
        limiter: 接口限速器

    Returns:
        涨跌停价DataFrame，无数据时返回空DataFrame
    """
    acquire_both(limiter)
    df = pro.stk_limit(
        trade_date=trade_date,
        fields='ts_code,trade_date,up_limit,down_limit'
    )
    if df is None or df.empty:
        return pd.DataFrame()
    return df


def fetch_suspend_d(pro: object,
                    trade_date: str,
                    limiter: RateLimiter) -> pd.DataFrame:
    """
    停复牌，无停牌时返回空属正常，全量一次取完。

    Args:
        pro: Tushare pro_api 对象
        trade_date: 交易日期YYYYMMDD
        limiter: 接口限速器

    Returns:
        停复牌DataFrame，无数据时返回空DataFrame
    """
    acquire_both(limiter)
    df = pro.suspend_d(
        trade_date=trade_date,
        fields='ts_code,trade_date,suspend_type,suspend_reason'
    )
    if df is None or df.empty:
        return pd.DataFrame()
    return df


def fetch_top_list(pro: object,
                   trade_date: str,
                   limiter: RateLimiter) -> pd.DataFrame:
    """
    龙虎榜，非触发日返回空属正常，金额单位：元，全量一次取完。

    Args:
        pro: Tushare pro_api 对象
        trade_date: 交易日期YYYYMMDD
        limiter: 接口限速器

    Returns:
        龙虎榜DataFrame，无数据时返回空DataFrame
    """
    acquire_both(limiter)
    df = pro.top_list(
        trade_date=trade_date,
        fields=(
            'trade_date,ts_code,name,'
            'close,pct_chg,turnover_rate,'
            'l_sell,l_buy,l_amount,'
            'net_amount,net_rate,amount_rate,'
            'float_values,reason'
        )
    )
    if df is None or df.empty:
        return pd.DataFrame()
    return df
