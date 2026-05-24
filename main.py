# -*- coding: utf-8 -*-
import os
import argparse
import logging
import time
import uuid
from datetime import datetime

from src.config import get_config, Config
from src.logging_config import setup_logging
from src.core.pipeline import StockAnalysisPipeline
from src.core.market_review import run_market_review
from data_provider.base import canonical_stock_code

logger = logging.getLogger(__name__)

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='港股自选股智能分析系统')
    parser.add_argument('--stocks', type=str, help='指定股票代码')
    parser.add_argument('--no-notify', action='store_true')
    parser.add_argument('--no-market-review', action='store_true')
    parser.add_argument('--force-run', action='store_true')
    return parser.parse_args()

def run_full_analysis(config: Config, args: argparse.Namespace, stock_codes: Optional[List[str]] = None):
    """主调度逻辑：单线程循环 + 强制延迟"""
    try:
        if stock_codes is None:
            config.refresh_stock_list()
            stock_codes = config.stock_list
        
        logger.info(f"开始分析，共 {len(stock_codes)} 只股票")

        # 实例化 Pipeline
        pipeline = StockAnalysisPipeline(
            config=config,
            max_workers=1, # 强制单线程
            query_id=uuid.uuid4().hex
        )
        
        all_results = []

        # 逐个分析，防止 RateLimitError
        for code in stock_codes:
            try:
                logger.info(f"正在处理股票: {code}")
                # 核心：每只之间强制等待 15 秒，给 API 留出缓冲
                time.sleep(15)
                
                # 单独运行一只股票
                res = pipeline.run(
                    stock_codes=[code], 
                    dry_run=False, 
                    send_notification=not args.no_notify,
                    merge_notification=False
                )
                if res:
                    all_results.extend(res)
                    logger.info(f"完成: {code}")
            except Exception as e:
                logger.error(f"分析 {code} 失败，已跳过: {e}")
                continue

        # 汇总报告
        if all_results and not args.no_notify:
            dashboard = pipeline.notifier.generate_aggregate_report(all_results, 'simple')
            pipeline.notifier.send(f"# 🚀 港股决策仪表盘\n\n{dashboard}", email_send_to_all=True)

        # 大盘复盘
        if config.market_review_enabled and not args.no_market_review:
            run_market_review(
                notifier=pipeline.notifier,
                analyzer=pipeline.analyzer,
                search_service=pipeline.search_service
            )
            
        logger.info("所有分析任务执行完成")

    except Exception as e:
        logger.exception(f"分析流程总调度失败: {e}")

def main() -> int:
    args = parse_arguments()
    config = get_config()
    setup_logging(log_prefix="stock_analysis", debug=False, log_dir=config.log_dir)
    
    logger.info("港股分析系统已启动")
    
    stock_codes = [canonical_stock_code(c) for c in args.stocks.split(',')] if args.stocks else None
    
    try:
        run_full_analysis(config, args, stock_codes)
        return 0
    except Exception as e:
        logger.error(f"程序执行失败: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
