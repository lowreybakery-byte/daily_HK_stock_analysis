# -*- coding: utf-8 -*-
"""
===================================
大盘复盘分析模块 (港股深度定制版)
===================================
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List

import pandas as pd

from src.config import get_config
from src.search_service import SearchService
from src.core.market_profile import get_profile, MarketProfile
from src.core.market_strategy import get_market_strategy_blueprint
from data_provider.base import DataFetcherManager

logger = logging.getLogger(__name__)


@dataclass
class MarketIndex:
    """大盘指数数据"""
    code: str                    
    name: str                    
    current: float = 0.0         
    change: float = 0.0          
    change_pct: float = 0.0      
    open: float = 0.0            
    high: float = 0.0            
    low: float = 0.0             
    prev_close: float = 0.0      
    volume: float = 0.0          
    amount: float = 0.0          
    amplitude: float = 0.0       
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'name': self.name,
            'current': self.current,
            'change': self.change,
            'change_pct': self.change_pct,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'volume': self.volume,
            'amount': self.amount,
            'amplitude': self.amplitude,
        }


@dataclass
class MarketOverview:
    """市场概览数据"""
    date: str                           
    indices: List[MarketIndex] = field(default_factory=list)  
    up_count: int = 0                   
    down_count: int = 0                 
    flat_count: int = 0                 
    limit_up_count: int = 0             
    limit_down_count: int = 0           
    total_amount: float = 0.0           
    
    top_sectors: List[Dict] = field(default_factory=list)     
    bottom_sectors: List[Dict] = field(default_factory=list)  


class MarketAnalyzer:
    
    def __init__(
        self,
        search_service: Optional[SearchService] = None,
        analyzer=None,
        region: str = "hk",
    ):
        self.config = get_config()
        self.search_service = search_service
        self.analyzer = analyzer
        self.data_manager = DataFetcherManager()
        self.region = region if region in ("hk", "us") else "hk"
        self.profile: MarketProfile = get_profile(self.region)
        self.strategy = get_market_strategy_blueprint(self.region)

    def get_market_overview(self) -> MarketOverview:
        today = datetime.now().strftime('%Y-%m-%d')
        overview = MarketOverview(date=today)
        
        # 获取主要指数行情
        overview.indices = self._get_main_indices()

        # 获取涨跌统计 (如果是港股，强制跳过，防止抓到A股数据)
        if self.profile.has_market_stats and self.region != 'hk':
            self._get_market_statistics(overview)

        # 获取板块涨跌榜 (如果是港股，强制跳过，防止抓到A股板块)
        if self.profile.has_sector_rankings and self.region != 'hk':
            self._get_sector_rankings(overview)
        
        return overview

    
    def _get_main_indices(self) -> List[MarketIndex]:
        """获取主要指数实时行情（强行适配港股）"""
        indices = []
        try:
            logger.info(f"[大盘] 获取主要指数实时行情 (区域: {self.region})...")

            if self.region == 'hk':
                # 港股：底层接口默认写死了A股，这里强制改用个股接口查港股指数
                hk_index_codes = [
                    {'code': 'hkHSI', 'name': '恒生指数'},
                    {'code': 'hkHSTECH', 'name': '恒生科技指数'},
                    {'code': 'hkHSCEI', 'name': '国企指数'}
                ]
                for item in hk_index_codes:
                    quote = self.data_manager.get_realtime_quote(item['code'])
                    if quote:
                        index = MarketIndex(
                            code=item['code'],
                            name=item['name'],
                            current=getattr(quote, 'current', getattr(quote, 'close', 0.0)),
                            change=getattr(quote, 'change', 0.0),
                            change_pct=getattr(quote, 'pct_chg', getattr(quote, 'change_pct', 0.0)),
                            volume=getattr(quote, 'volume', 0.0),
                            amount=getattr(quote, 'amount', 0.0)
                        )
                        indices.append(index)
            else:
                data_list = self.data_manager.get_main_indices(region=self.region)
                if data_list:
                    for item in data_list:
                        index = MarketIndex(
                            code=item['code'], name=item['name'],
                            current=item['current'], change=item['change'],
                            change_pct=item['change_pct'], open=item.get('open', 0),
                            high=item.get('high', 0), low=item.get('low', 0),
                            prev_close=item.get('prev_close', 0), volume=item.get('volume', 0),
                            amount=item.get('amount', 0), amplitude=item.get('amplitude', 0)
                        )
                        indices.append(index)

            if not indices:
                logger.warning("[大盘] 所有行情数据源失败，将依赖新闻搜索进行分析")
            else:
                logger.info(f"[大盘] 获取到 {len(indices)} 个指数行情")

        except Exception as e:
            logger.error(f"[大盘] 获取指数行情失败: {e}")

        return indices

    def _get_market_statistics(self, overview: MarketOverview):
        """获取市场涨跌统计"""
        if self.region == 'hk':
            logger.info("[大盘] 港股模式屏蔽全市场涨跌统计，防止抓取到A股数据")
            return
            
        try:
            logger.info("[大盘] 获取市场涨跌统计...")
            stats = self.data_manager.get_market_stats()
            if stats:
                overview.up_count = stats.get('up_count', 0)
                overview.down_count = stats.get('down_count', 0)
                overview.flat_count = stats.get('flat_count', 0)
                overview.limit_up_count = stats.get('limit_up_count', 0)
                overview.limit_down_count = stats.get('limit_down_count', 0)
                overview.total_amount = stats.get('total_amount', 0.0)
        except Exception as e:
            logger.error(f"[大盘] 获取涨跌统计失败: {e}")

    def _get_sector_rankings(self, overview: MarketOverview):
        """获取板块涨跌榜"""
        if self.region == 'hk':
            logger.info("[大盘] 港股模式屏蔽板块排行，防止抓取到A股板块")
            return
            
        try:
            logger.info("[大盘] 获取板块涨跌榜...")
            top_sectors, bottom_sectors = self.data_manager.get_sector_rankings(5)
            if top_sectors or bottom_sectors:
                overview.top_sectors = top_sectors
                overview.bottom_sectors = bottom_sectors
        except Exception as e:
            logger.error(f"[大盘] 获取板块涨跌榜失败: {e}")
    
    def search_market_news(self) -> List[Dict]:
        if not self.search_service:
            logger.warning("[大盘] 搜索服务未配置，跳过新闻搜索")
            return []
        
        all_news = []
        today = datetime.now()
        date_str = today.strftime('%Y年%m月%d日')

        search_queries = self.profile.news_queries
        try:
            logger.info("[大盘] 开始搜索市场新闻...")
            market_name = "港股大盘" if self.region == "hk" else "US market"
            for query in search_queries:
                response = self.search_service.search_stock_news(
                    stock_code="market",
                    stock_name=market_name,
                    max_results=3,
                    focus_keywords=query.split()
                )
                if response and response.results:
                    all_news.extend(response.results)
                    logger.info(f"[大盘] 搜索 '{query}' 获取 {len(response.results)} 条结果")
            
            logger.info(f"[大盘] 共获取 {len(all_news)} 条市场新闻")
        except Exception as e:
            logger.error(f"[大盘] 搜索市场新闻失败: {e}")
        
        return all_news
    
    def generate_market_review(self, overview: MarketOverview, news: List) -> str:
        if not self.analyzer or not self.analyzer.is_available():
            logger.warning("[大盘] AI分析器未配置或不可用，使用模板生成报告")
            return self._generate_template_review(overview, news)
        
        prompt = self._build_review_prompt(overview, news)
        logger.info("[大盘] 调用大模型生成复盘报告...")
        review = self.analyzer.generate_text(prompt, max_tokens=2048, temperature=0.7)

        if review:
            logger.info("[大盘] 复盘报告生成成功，长度: %d 字符", len(review))
            return self._inject_data_into_review(review, overview)
        else:
            logger.warning("[大盘] 大模型返回为空，使用模板报告")
            return self._generate_template_review(overview, news)
    
    def _inject_data_into_review(self, review: str, overview: MarketOverview) -> str:
        import re
        stats_block = self._build_stats_block(overview)
        indices_block = self._build_indices_block(overview)
        sector_block = self._build_sector_block(overview)

        if stats_block:
            review = self._insert_after_section(review, r'###\s*一、市场总结', stats_block)
        if indices_block:
            review = self._insert_after_section(review, r'###\s*二、指数点评', indices_block)
        if sector_block:
            review = self._insert_after_section(review, r'###\s*四、热点解读', sector_block)
        return review

    @staticmethod
    def _insert_after_section(text: str, heading_pattern: str, block: str) -> str:
        import re
        match = re.search(heading_pattern, text)
        if not match:
            return text
        start = match.end()
        next_heading = re.search(r'\n###\s', text[start:])
        if next_heading:
            insert_pos = start + next_heading.start()
        else:
            insert_pos = len(text)
        return text[:insert_pos].rstrip() + '\n\n' + block + '\n\n' + text[insert_pos:].lstrip('\n')

    def _build_stats_block(self, overview: MarketOverview) -> str:
        has_stats = overview.up_count or overview.down_count or overview.total_amount
        if not has_stats:
            return ""
        lines = [
            f"> 📈 上涨 **{overview.up_count}** 家 / 下跌 **{overview.down_count}** 家 / "
            f"平盘 **{overview.flat_count}** 家 | "
            f"涨停 **{overview.limit_up_count}** / 跌停 **{overview.limit_down_count}** | "
            f"成交额 **{overview.total_amount:.0f}** 亿"
        ]
        return "\n".join(lines)

    def _build_indices_block(self, overview: MarketOverview) -> str:
        if not overview.indices:
            return ""
        lines = [
            "| 指数 | 最新 | 涨跌幅 | 成交额(亿) |",
            "|------|------|--------|-----------|"]
        for idx
