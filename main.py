# -*- coding: utf-8 -*-
import os
from src.config import setup_env
setup_env()

if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url

import argparse
import logging
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from data_provider.base import canonical_stock_code
from src.core.pipeline import StockAnalysisPipeline
from src.core.market_review import run_market_review
from src.webui_frontend import prepare_webui_frontend_assets
from src.config import get_config, Config
from src.logging_config import setup_logging
from src.stock_name_resolver import StockNameResolver

logger = logging.getLogger(__name__)

def _build_stock_display_name_map(stock_codes: List[str]) -> dict:
    resolver = StockNameResolver()
    mapping = {}
    for code in stock_codes or []:
        norm_code = canonical_stock_code(code)
        canonical_name = resolver.get_canonical_name(norm_code, fallback=norm_code)
        mapping[norm_code] = canonical_name
    return mapping

def _repair_result_stock_names(results) -> None:
    resolver = StockNameResolver()
    if not results: return
    for r in results:
        try:
            code = canonical_stock_code(getattr(r, "code", "") or "")
            if not code: continue
            old_name = getattr(r, "name", None)
            canonical_name = resolver.get_canonical_name(code, fallback=old_name or code)
            if hasattr(r, "name") and old_name != canonical_name:
                r.name = canonical_name
            for attr in ["full_analysis", "analysis", "report", "content"]:
                if hasattr(r, attr):
                    old_text = getattr(r, attr, None)
                    if isinstance(old_text, str) and old_text.strip():
                        setattr(r, attr, resolver.repair_text(code, old_text))
        except Exception as e:
            logger.warning("修正股票名称时发生异常: %s", e)

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='港股自选股智能分析系统')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--stocks', type=str)
    parser.add_argument('--no-notify', action='store_true')
    parser.add_argument('--single-notify', action='store_true')
    parser.add_argument('--workers', type=int, default=1) # 强制单线程
    parser.add_argument('--schedule', action='store_true')
    parser.add_argument('--no-run-immediately', action='store_true')
    parser.add_argument('--market-review', action='store_true')
    parser.add_argument('--no-market-review', action='store_true')
    parser.add_argument('--force-run', action='store_true')
    return parser.parse_args()

def run_full_analysis(config: Config, args: argparse.Namespace, stock_codes: Optional[List[str]] = None):
    try:
        if stock_codes is None: config.refresh_stock_list()
        
        effective_codes = stock_codes if stock_codes is not None else config.stock_list
        logger.info(f"开始分批分析，目标股票: {len(effective_codes)} 只")

        # 核心限流逻辑：手动循环调用，确保每只之间有间隔
        results = []
        pipeline = StockAnalysisPipeline(config=config, max_workers=1, query_id=uuid.uuid4().hex)
        
        for code in effective_codes:
            logger.info(f"正在准备分析: {code}")
            # 每一只执行前强制等待 8 秒
            time.sleep(8) 
            single_result = pipeline.run(stock_codes=[code], dry_run=args.dry_run, send_notification=not args.no_notify)
            if single_result:
                results.extend(single_result)

        _repair_result_stock_names(results)
        
        # 大盘分析...
        if config.market_review_enabled and not args.no_market_review:
            run_market_review(notifier=pipeline.notifier, analyzer=pipeline.analyzer, search_service=pipeline.search_service)

        logger.info("\n任务执行完成")
    except Exception as e:
        logger.exception(f"分析流程执行失败: {e}")

def main() -> int:
    args = parse_arguments()
    config = get_config()
    setup_logging(log_prefix="stock_analysis", debug=args.debug, log_dir=config.log_dir)
    
    logger.info("港股自选股智能分析系统 启动")
    
    stock_codes = [canonical_stock_code(c) for c in args.stocks.split(',')] if args.stocks else None
    
    try:
        run_full_analysis(config, args, stock_codes)
        return 0
    except Exception as e:
        logger.exception(f"程序执行失败: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
