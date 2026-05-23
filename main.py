# -*- coding: utf-8 -*-
"""
===================================
港股自选股智能分析系统 - 主调度程序
===================================
集成量化主声浪初筛与 AI 智能复盘
"""
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
    if not results:
        return
    for r in results:
        try:
            code = canonical_stock_code(getattr(r, "code", "") or "")
            if not code:
                continue
            old_name = getattr(r, "name", None)
            canonical_name = resolver.get_canonical_name(code, fallback=old_name or code)
            if hasattr(r, "name") and old_name != canonical_name:
                r.name = canonical_name
            for attr in ["full_analysis", "analysis", "report", "content"]:
                if hasattr(r, attr):
                    old_text = getattr(r, attr, None)
                    if isinstance(old_text, str) and old_text.strip():
                        new_text = resolver.repair_text(code, old_text)
                        setattr(r, attr, new_text)
        except Exception as e:
            logger.warning("修正股票名称时发生异常，已忽略: %s", e)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='港股自选股智能分析系统',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--stocks', type=str)
    parser.add_argument('--no-notify', action='store_true')
    parser.add_argument('--single-notify', action='store_true')
    parser.add_argument('--workers', type=int, default=None)
    parser.add_argument('--schedule', action='store_true')
    parser.add_argument('--no-run-immediately', action='store_true')
    parser.add_argument('--market-review', action='store_true')
    parser.add_argument('--no-market-review', action='store_true')
    parser.add_argument('--force-run', action='store_true')
    parser.add_argument('--webui', action='store_true')
    parser.add_argument('--webui-only', action='store_true')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--serve-only', action='store_true')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--host', type=str, default='0.0.0.0')
    parser.add_argument('--no-context-snapshot', action='store_true')
    parser.add_argument('--backtest', action='store_true')
    parser.add_argument('--backtest-code', type=str, default=None)
    parser.add_argument('--backtest-days', type=int, default=None)
    parser.add_argument('--backtest-force', action='store_true')
    return parser.parse_args()


def _compute_trading_day_filter(
    config: Config,
    args: argparse.Namespace,
    stock_codes: List[str],
) -> Tuple[List[str], Optional[str], bool]:
    force_run = getattr(args, 'force_run', False)
    if force_run or not getattr(config, 'trading_day_check_enabled', True):
        return (stock_codes, None, False)

    from src.core.trading_calendar import (
        get_market_for_stock,
        get_open_markets_today,
        compute_effective_region,
    )

    open_markets = get_open_markets_today()
    filtered_codes = []
    for code in stock_codes:
        mkt = get_market_for_stock(code)
        if mkt in open_markets or mkt is None:
            filtered_codes.append(code)

    if config.market_review_enabled and not getattr(args, 'no_market_review', False):
        effective_region = compute_effective_region(
            getattr(config, 'market_review_region', 'hk') or 'hk', open_markets
        )
    else:
        effective_region = None

    should_skip_all = (not filtered_codes) and (effective_region or '') == ''
    return (filtered_codes, effective_region, should_skip_all)


def run_full_analysis(
    config: Config,
    args: argparse.Namespace,
    stock_codes: Optional[List[str]] = None
):
    try:
        if stock_codes is None:
            config.refresh_stock_list()

        effective_codes = stock_codes if stock_codes is not None else config.stock_list
        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(
            config, args, effective_codes
        )
        if should_skip:
            logger.info("今日所有相关市场均为非交易日，跳过执行。可使用 --force-run 强制执行。")
            return
        if set(filtered_codes) != set(effective_codes):
            skipped = set(effective_codes) - set(filtered_codes)
            logger.info("今日休市股票已跳过: %s", skipped)
        stock_codes = filtered_codes

        stock_name_map = _build_stock_display_name_map(stock_codes)
        
        if getattr(args, 'single_notify', False):
            config.single_stock_notify = True

        merge_notification = (
            getattr(config, 'merge_email_notification', False)
            and config.market_review_enabled
            and not getattr(args, 'no_market_review', False)
            and not config.single_stock_notify
        )

        save_context_snapshot = None
        if getattr(args, 'no_context_snapshot', False):
            save_context_snapshot = False
        query_id = uuid.uuid4().hex
        pipeline = StockAnalysisPipeline(
            config=config,
            max_workers=args.workers,
            query_id=query_id,
            query_source="cli",
            save_context_snapshot=save_context_snapshot
        )

        # 1. 运行个股 AI 分析
        results = pipeline.run(
            stock_codes=stock_codes,
            dry_run=args.dry_run,
            send_notification=not args.no_notify,
            merge_notification=merge_notification
        )

        _repair_result_stock_names(results)

        analysis_delay = getattr(config, 'analysis_delay', 0)
        if analysis_delay > 0 and config.market_review_enabled and not args.no_market_review and effective_region != '':
            logger.info(f"等待 {analysis_delay} 秒后执行大盘复盘（避免API限流）...")
            time.sleep(analysis_delay)

        # 2. 运行大盘复盘
        market_report = ""
        if config.market_review_enabled and not args.no_market_review and effective_region != '':
            review_result = run_market_review(
                notifier=pipeline.notifier,
                analyzer=pipeline.analyzer,
                search_service=pipeline.search_service,
                send_notification=not args.no_notify,
                merge_notification=merge_notification,
                override_region=effective_region,
            )
            if review_result:
                market_report = review_result

        # ============================================================
        # 3. 运行量化雷达扫描 (新增核心逻辑)
        # ============================================================
        radar_report = ""
        try:
            from HK_MainWaveDetector import HKMainWaveDetector
            logger.info("🤖 启动港股主声浪量化雷达扫描...")
            detector = HKMainWaveDetector()
            triggered_stocks = []
            
            for code in (stock_codes or []):
                # 转换格式：hk00700 -> 00700.HK
                yf_code = code.upper()
                if yf_code.startswith("HK"):
                    yf_code = yf_code[2:] + ".HK"
                elif not yf_code.endswith(".HK"):
                    yf_code = yf_code + ".HK"

                signal = detector.generate_enhanced_mainwave_signal(yf_code, is_high_trend=False)
                if signal:
                    triggered_stocks.append(signal)
                time.sleep(0.3)
                
            if triggered_stocks:
                radar_lines = [
                    "**🚨 今日在自选股池中发现以下标的出现主声浪技术面异动！**\n",
                    "| 股票代码 | 触发价格 | 量化综合评分 | 信号类型 |",
                    "| :--- | :--- | :--- | :--- |"
                ]
                for s in triggered_stocks:
                    radar_lines.append(f"| {s['code']} | {s['price']} | {s['score']} | {s['signal']} |")
                radar_lines.append("\n*💡 系统提示：请结合下方的大盘复盘与个股 AI 分析，重点确认上述异动股票的买点。*")
                radar_report = "\n".join(radar_lines)
                logger.info(f"✅ 量化雷达发现 {len(triggered_stocks)} 只异动股。")
            else:
                radar_report = "今日量化监控未发现自选股出现主声浪级别的突破异动信号。建议继续耐心观察。"
                logger.info("✅ 量化雷达扫描完毕，未发现异动股。")
        except ImportError:
            logger.warning("⚠️ 未找到 HK_MainWaveDetector 模块，跳过量化雷达扫描。")
        except Exception as e:
            logger.error(f"❌ 量化雷达扫描失败: {e}")
        # ============================================================

        # 4. 合并推送（量化雷达 + 大盘复盘 + 个股分析）
        if merge_notification and (results or market_report or radar_report) and not args.no_notify:
            parts = []
            if radar_report:
                parts.append(f"# 🤖 量化雷达初筛\n\n{radar_report}")
            if market_report:
                parts.append(f"# 📈 大盘复盘\n\n{market_report}")
            if results:
                dashboard_content = pipeline.notifier.generate_aggregate_report(
                    results,
                    getattr(config, 'report_type', 'simple'),
                )
                parts.append(f"# 🚀 个股决策仪表盘\n\n{dashboard_content}")
            
            if parts:
                combined_content = "\n\n---\n\n".join(parts)
                if pipeline.notifier.is_available():
                    if pipeline.notifier.send(combined_content, email_send_to_all=True):
                        logger.info("已完成全景合并推送（包含雷达、大盘、个股）")
                    else:
                        logger.warning("合并推送失败")

        # 5. 生成飞书云文档
        try:
            from src.feishu_doc import FeishuDocManager
            feishu_doc = FeishuDocManager()
            if feishu_doc.is_configured() and (results or market_report or radar_report):
                logger.info("正在创建飞书云文档...")

                tz_cn = timezone(timedelta(hours=8))
                now = datetime.now(tz_cn)
                doc_title = f"{now.strftime('%Y-%m-%d %H:%M')} 港股全景复盘"

                full_content = ""
                
                if radar_report:
                    full_content += f"# 🤖 量化雷达初筛\n\n{radar_report}\n\n---\n\n"

                if market_report:
                    full_content += f"# 📈 大盘复盘\n\n{market_report}\n\n---\n\n"

                if results:
                    dashboard_content = pipeline.notifier.generate_aggregate_report(
                        results,
                        getattr(config, 'report_type', 'simple'),
                    )
                    full_content += f"# 🚀 个股决策仪表盘\n\n{dashboard_content}"

                doc_url = feishu_doc.create_daily_doc(doc_title, full_content)
                if doc_url:
                    logger.info(f"飞书云文档创建成功: {doc_url}")
                    if not args.no_notify:
                        pipeline.notifier.send(f"[{now.strftime('%Y-%m-%d %H:%M')}] 复盘文档创建成功: {doc_url}")
        except Exception as e:
            logger.error(f"飞书文档生成失败: {e}")

        # 6. 自动回测
        try:
            if getattr(config, 'backtest_enabled', False):
                from src.services.backtest_service import BacktestService
                logger.info("开始自动回测...")
                service = BacktestService()
                stats = service.run_backtest(
                    force=False,
                    eval_window_days=getattr(config, 'backtest_eval_window_days', 10),
                    min_age_days=getattr(config, 'backtest_min_age_days', 14),
                    limit=200,
                )
        except Exception as e:
            logger.warning(f"自动回测失败（已忽略）: {e}")

    except Exception as e:
        logger.exception(f"分析流程执行失败: {e}")


def start_api_server(host: str, port: int, config: Config) -> None:
    import threading
    import uvicorn
    def run_server():
        level_name = (config.log_level or "INFO").lower()
        uvicorn.run("api.app:app", host=host, port=port, log_level=level_name, log_config=None)
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    logger.info(f"FastAPI 服务已启动: http://{host}:{port}")


def start_bot_stream_clients(config: Config) -> None:
    if config.dingtalk_stream_enabled:
        try:
            from bot.platforms import start_dingtalk_stream_background, DINGTALK_STREAM_AVAILABLE
            if DINGTALK_STREAM_AVAILABLE:
                start_dingtalk_stream_background()
        except Exception as exc:
            logger.error(f"[Main] Failed to start Dingtalk Stream client: {exc}")

    if getattr(config, 'feishu_stream_enabled', False):
        try:
            from bot.platforms import start_feishu_stream_background, FEISHU_SDK_AVAILABLE
            if FEISHU_SDK_AVAILABLE:
                start_feishu_stream_background()
        except Exception as exc:
            logger.error(f"[Main] Failed to start Feishu Stream client: {exc}")


def main() -> int:
    args = parse_arguments()
    config = get_config()
    setup_logging(log_prefix="stock_analysis", debug=args.debug, log_dir=config.log_dir)

    logger.info("=" * 60)
    logger.info("港股自选股智能分析系统 (全景强化版) 启动")
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    stock_codes = None
    if args.stocks:
        stock_codes = [canonical_stock_code(c) for c in args.stocks.split(',') if (c or "").strip()]

    if args.webui: args.serve = True
    if args.webui_only: args.serve_only = True
    if config.webui_enabled and not (args.serve or args.serve_only): args.serve = True

    start_serve = (args.serve or args.serve_only) and os.getenv("GITHUB_ACTIONS") != "true"

    if start_serve:
        if args.host == '0.0.0.0' and os.getenv('WEBUI_HOST'): args.host = os.getenv('WEBUI_HOST')
        if args.port == 8000 and os.getenv('WEBUI_PORT'): args.port = int(os.getenv('WEBUI_PORT'))

    bot_clients_started = False
    if start_serve:
        prepare_webui_frontend_assets()
        try:
            start_api_server(host=args.host, port=args.port, config=config)
            bot_clients_started = True
        except Exception as e:
            logger.error(f"启动 FastAPI 服务失败: {e}")

    if bot_clients_started:
        start_bot_stream_clients(config)

    if args.serve_only:
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            return 0

    try:
        if getattr(args, 'backtest', False):
            from src.services.backtest_service import BacktestService
            service = BacktestService()
            service.run_backtest(code=getattr(args, 'backtest_code', None), force=getattr(args, 'backtest_force', False), eval_window_days=getattr(args, 'backtest_days', None))
            return 0

        if args.market_review:
            from src.analyzer import GeminiAnalyzer
            from src.core.market_review import run_market_review
            from src.notification import NotificationService
            from src.search_service import SearchService

            effective_region = None
            if not getattr(args, 'force_run', False) and getattr(config, 'trading_day_check_enabled', True):
                from src.core.trading_calendar import get_open_markets_today, compute_effective_region as _compute_region
                effective_region = _compute_region(getattr(config, 'market_review_region', 'hk') or 'hk', get_open_markets_today())
                if effective_region == '': return 0

            notifier = NotificationService()
            search_service = None
            analyzer = None

            if config.bocha_api_keys or config.tavily_api_keys or config.serpapi_keys:
                search_service = SearchService(bocha_keys=config.bocha_api_keys, tavily_keys=config.tavily_api_keys, serpapi_keys=config.serpapi_keys)

            if config.gemini_api_key or config.openai_api_key:
                analyzer = GeminiAnalyzer(api_key=config.gemini_api_key)

            run_market_review(notifier=notifier, analyzer=analyzer, search_service=search_service, send_notification=not args.no_notify, override_region=effective_region)
            return 0

        if args.schedule or config.schedule_enabled:
            from src.scheduler import run_with_schedule
            should_run_immediately = config.schedule_run_immediately
            if getattr(args, 'no_run_immediately', False): should_run_immediately = False
            run_with_schedule(task=lambda: run_full_analysis(config, args, stock_codes), schedule_time=config.schedule_time, run_immediately=should_run_immediately)
            return 0

        if config.run_immediately:
            run_full_analysis(config, args, stock_codes)

        keep_running = start_serve and not (args.schedule or config.schedule_enabled)
        if keep_running:
            try:
                while True: time.sleep(1)
            except KeyboardInterrupt:
                pass
        return 0

    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.exception(f"程序执行失败: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
