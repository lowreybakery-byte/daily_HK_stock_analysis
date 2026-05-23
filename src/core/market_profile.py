# -*- coding: utf-8 -*-
"""
大盘复盘市场区域配置

定义各市场区域的指数、新闻搜索词、Prompt 提示等元数据，
供 MarketAnalyzer 按 region 切换 港股/美股复盘行为。
"""

from dataclasses import dataclass
from typing import List


@dataclass
class MarketProfile:
    """大盘复盘市场区域配置"""

    region: str  # "hk" | "us"
    # 用于判断整体走势的指数代码，hk 用恒生指数 HSI，us 用标普 SPX
    mood_index_code: str
    # 新闻搜索关键词
    news_queries: List[str]
    # 指数点评 Prompt 提示语
    prompt_index_hint: str
    # 市场概况是否包含涨跌家数、涨停跌停
    has_market_stats: bool
    # 市场概况是否包含板块涨跌
    has_sector_rankings: bool


HK_PROFILE = MarketProfile(
    region="hk",
    mood_index_code="HSI",  # 恒生指数代码
    news_queries=[
        "港股 大盘 复盘",
        "恒生指数 行情 分析",
        "港股 市场 热点 板块",
    ],
    prompt_index_hint="分析恒生指数、恒生科技指数、国企指数等各核心指数走势特点",
    has_market_stats=True,
    has_sector_rankings=True,
)

US_PROFILE = MarketProfile(
    region="us",
    mood_index_code="SPX",
    news_queries=[
        "美股 大盘",
        "US stock market",
        "S&P 500 NASDAQ",
    ],
    prompt_index_hint="分析标普500、纳斯达克、道指等各指数走势特点",
    has_market_stats=False,
    has_sector_rankings=False,
)


def get_profile(region: str) -> MarketProfile:
    """根据 region 返回对应的 MarketProfile"""
    if region == "us":
        return US_PROFILE
    # 默认返回港股配置
    return HK_PROFILE
