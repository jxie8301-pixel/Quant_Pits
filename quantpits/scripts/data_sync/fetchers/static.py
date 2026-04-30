"""
静态表拉取函数：stock_basic / trade_cal
"""

from quantpits.utils import env

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def fetch_stock_basic(pro) -> pd.DataFrame:
    """
    拉取全量股票基础信息，按L/D/P状态分批拉取后合并去重。

    接口：stock_basic，积分：2000

    Args:
        pro: Tushare pro_api 对象

    Returns:
        全量股票基础信息DataFrame，按ts_code去重
    """
    logger.info("[stock_basic] 开始拉取全量数据...")

    frames: list[pd.DataFrame] = []
    for status in ['L', 'D', 'P']:
        df = pro.stock_basic(
            exchange='',
            list_status=status,
            fields=(
                'ts_code,symbol,name,area,industry,fullname,'
                'enname,cnspell,market,exchange,curr_type,'
                'list_status,list_date,delist_date,is_hs,'
                'act_name,act_ent_type'
            )
        )
        frames.append(df)
        logger.info(f"[stock_basic] list_status={status}，获取 {len(df)} 条")

    result = pd.concat(frames, ignore_index=True)
    result = result.drop_duplicates(subset=['ts_code'])
    logger.info(f"[stock_basic] 合并完成，共 {len(result)} 条")
    return result


def fetch_trade_cal(pro: object,
                    start_date: str = '20050101',
                    end_date: str = '20301231') -> pd.DataFrame:
    """
    拉取交易日历（A股，上交所）。

    接口：trade_cal，积分：2000

    Args:
        pro: Tushare pro_api 对象
        start_date: 起始日期，默认'20050101'
        end_date: 结束日期，默认'20301231'

    Returns:
        交易日历DataFrame
    """
    logger.info("[trade_cal] 开始拉取交易日历...")

    df = pro.trade_cal(
        exchange='SSE',
        start_date=start_date,
        end_date=end_date,
        fields='exchange,cal_date,is_open,pretrade_date'
    )
    logger.info(f"[trade_cal] 获取 {len(df)} 条，"
                f"{df['cal_date'].min()} ~ {df['cal_date'].max()}")
    return df
