# -*- coding: utf-8 -*-
"""
===================================
大盘复盘分析模块 (港股深度定制版)
===================================
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List

from src.config import get_config
from src.search_service import SearchService
from src.core.market_profile import get_profile, MarketProfile
from src.core.market_strategy import get_market_strategy_blueprint
from data_provider.base import DataFetcherManager

logger = logging.getLogger(__name__)

@dataclass
class MarketIndex:
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

@dataclass
class MarketOverview:
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
        overview.indices = self._get_main_indices()
        return overview

    def _get_main_indices(self) -> List[MarketIndex]:
        indices = []
        try:
            logger.info(f"[大盘] 获取主要指数实时行情 (区域: hk)...")
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
            if not indices:
                logger.warning("[大盘] 所有行情数据源失败，将依赖新闻搜索进行分析")
            else:
                logger.info(f"[大盘] 获取到 {len(indices)} 个指数行情")
        except Exception as e:
            logger.error(f"[大盘] 获取指数行情失败: {e}")
        return indices

    def search_market_news(self) -> List[Dict]:
        if not self.search_service:
            logger.warning("[大盘] 搜索服务未配置，跳过新闻搜索")
            return []
        
        all_news = []
        search_queries = self.profile.news_queries
        try:
            logger.info("[大盘] 开始搜索市场新闻...")
            for query in search_queries:
                response = self.search_service.search_stock_news(
                    stock_code="market",
                    stock_name="港股大盘",
                    max_results=3,
                    focus_keywords=query.split()
                )
                if response and response.results:
                    all_news.extend(response.results)
                    logger.info(f"[大盘] 搜索 '{query}' 获取 {len(response.results)} 条结果")
        except Exception as e:
            logger.error(f"[大盘] 搜索市场新闻失败: {e}")
        return all_news
    
    def generate_market_review(self, overview: MarketOverview, news: List) -> str:
        if not self.analyzer or not self.analyzer.is_available():
            return self._generate_template_review(overview, news)
        
        prompt = self._build_review_prompt(overview, news)
        logger.info("[大盘] 调用大模型生成复盘报告...")
        review = self.analyzer.generate_text(prompt, max_tokens=2048, temperature=0.7)

        if review:
            logger.info("[大盘] 复盘报告生成成功")
            return self._inject_data_into_review(review, overview)
        return self._generate_template_review(overview, news)
    
    def _inject_data_into_review(self, review: str, overview: MarketOverview) -> str:
        import re
        indices_block = self._build_indices_block(overview)
        if indices_block:
            match = re.search(r'###\s*二、指数点评', review)
            if match:
                start = match.end()
                next_heading = re.search(r'\n###\s', review[start:])
                insert_pos = start + next_heading.start() if next_heading else len(review)
                review = review[:insert_pos].rstrip() + '\n\n' + indices_block + '\n\n' + review[insert_pos:].lstrip('\n')
        return review

    def _build_indices_block(self, overview: MarketOverview) -> str:
        if not overview.indices:
            return ""
        lines = [
            "| 指数 | 最新 | 涨跌幅 | 成交额(亿) |",
            "|------|------|--------|-----------|"]
        for idx in overview.indices:
            arrow = "🔴" if idx.change_pct < 0 else "🟢" if idx.change_pct > 0 else "⚪"
            amount_raw = idx.amount or 0.0
            amount_str = f"{amount_raw / 1e8:.0f}" if amount_raw > 1e6 else (f"{amount_raw:.0f}" if amount_raw > 0 else "N/A")
            lines.append(f"| {idx.name} | {idx.current:.2f} | {arrow} {idx.change_pct:+.2f}% | {amount_str} |")
        return "\n".join(lines)

    def _build_review_prompt(self, overview: MarketOverview, news: List) -> str:
        indices_text = ""
        for idx in overview.indices:
            direction = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
            indices_text += f"- {idx.name}: {idx.current:.2f} ({direction}{abs(idx.change_pct):.2f}%)\n"
        
        news_text = ""
        for i, n in enumerate(news[:6], 1):
            title = n.title[:50] if hasattr(n, 'title') else n.get('title', '')[:50]
            snippet = n.snippet[:100] if hasattr(n, 'snippet') else n.get('snippet', '')[:100]
            news_text += f"{i}. {title}\n   {snippet}\n"
        
        indices_placeholder = indices_text if indices_text else "暂无指数数据"
        news_placeholder = news_text if news_text else "暂无相关新闻"
        data_no_indices_hint = "注意：由于行情数据获取失败，请主要根据【市场新闻】进行定性分析和总结，不要编造具体的指数点位。" if not indices_text else ""

        return f'''你是一位专业的港股市场分析师，请根据以下数据生成一份简洁的大盘复盘报告。

【重要】输出要求：
- 必须输出纯 Markdown 文本格式
- 禁止输出 JSON 格式
- 禁止输出代码块
- emoji 仅在标题处少量使用（每个标题最多1个）

---

# 今日市场数据

## 日期
{overview.date}

## 主要指数
{indices_placeholder}

## 市场新闻
{news_placeholder}

{data_no_indices_hint}

{self.strategy.to_prompt_block()}

---

# 输出格式模板（请严格按此格式输出）

## {overview.date} 大盘复盘

### 一、市场总结
（2-3句话概括今日港股市场整体表现，包括恒生指数涨跌变化）

### 二、指数点评
（{self.profile.prompt_index_hint}）

### 三、资金动向
（解读新闻中的市场交投情绪）

### 四、热点解读
（结合新闻分析当前港股市场的热点主线）

### 五、后市展望
（结合当前走势和新闻，给出明日港股市场预判）

### 六、风险提示
（需要关注的风险点）

### 七、策略计划
（给出进攻/均衡/防守结论，对应仓位建议，并给出一个触发失效条件；最后补充“建议仅供参考，不构成投资建议”。）

---

请直接输出复盘报告内容，绝不要提到A股、上证、深证、涨跌家数等字眼。
'''
    
    def _generate_template_review(self, overview: MarketOverview, news: List) -> str:
        return f"## {overview.date} 大盘复盘\n\n数据获取异常，请查阅新闻。"
    
    def run_daily_review(self) -> str:
        logger.info("========== 开始大盘复盘分析 ==========")
        overview = self.get_market_overview()
        news = self.search_market_news()
        report = self.generate_market_review(overview, news)
        logger.info("========== 大盘复盘分析完成 ==========")
        return report

if __name__ == "__main__":
    import sys
    sys.path.insert(0, '.')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
    analyzer = MarketAnalyzer()
    print(analyzer.run_daily_review())
