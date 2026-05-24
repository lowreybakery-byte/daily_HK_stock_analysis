# -*- coding: utf-8 -*-
"""
===================================
数据源基类与管理器 - 修复版
===================================
"""

import logging
import random
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List, Tuple, Dict, Any

import pandas as pd
import numpy as np
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.data.stock_mapping import STOCK_NAME_MAP

logger = logging.getLogger(__name__)

STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']

def unwrap_exception(exc: Exception) -> Exception:
    current = exc
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        next_exc = current.__cause__ or current.__context__
        if next_exc is None:
            break
        current = next_exc
    return current

def summarize_exception(exc: Exception) -> Tuple[str, str]:
    root = unwrap_exception(exc)
    error_type = type(root).__name__
    message = str(exc).strip() or str(root).strip() or error_type
    return error_type, " ".join(message.split())

def normalize_stock_code(stock_code: str) -> str:
    code = stock_code.strip().upper()
    if code.endswith('.HK'): return code
    if code.startswith('HK'): return code[2:] + '.HK'
    return code

def canonical_stock_code(code: str) -> str:
    return (code or "").strip().upper()

class DataFetchError(Exception): pass

class BaseFetcher(ABC):
    name: str = "BaseFetcher"
    priority: int = 99
    
    @abstractmethod
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        pass
    
    @abstractmethod
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        pass

    def get_daily_data(self, stock_code: str, start_date: Optional[str] = None, end_date: Optional[str] = None, days: int = 30) -> pd.DataFrame:
        if end_date is None: end_date = datetime.now().strftime('%Y-%m-%d')
        if start_date is None:
            from datetime import timedelta
            start_date = (datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=days * 2)).strftime('%Y-%m-%d')
        try:
            raw_df = self._fetch_raw_data(stock_code, start_date, end_date)
            if raw_df is None or raw_df.empty: raise DataFetchError("无数据")
            df = self._normalize_data(raw_df, stock_code)
            df = df.dropna(subset=['close', 'volume']).sort_values('date').reset_index(drop=True)
            df['ma5'] = df['close'].rolling(5, min_periods=1).mean()
            df['ma10'] = df['close'].rolling(10, min_periods=1).mean()
            df['ma20'] = df['close'].rolling(20, min_periods=1).mean()
            return df
        except Exception as e:
            raise DataFetchError(f"[{self.name}] {stock_code}: {e}")

class DataFetcherManager:
    def __init__(self, fetchers: Optional[List[BaseFetcher]] = None):
        if fetchers:
            self._fetchers = sorted(fetchers, key=lambda f: f.priority)
        else:
            self._init_default_fetchers()
    
    def _init_default_fetchers(self) -> None:
        from .yfinance_fetcher import YfinanceFetcher
        from .efinance_fetcher import EfinanceFetcher
        from .akshare_fetcher import AkshareFetcher
        # 核心优化：yfinance 放在第一位，确保 Action 环境下最稳
        self._fetchers = [YfinanceFetcher(), EfinanceFetcher(), AkshareFetcher()]
        self._fetchers.sort(key=lambda f: f.priority)
    
    def get_stock_name(self, stock_code: str) -> str:
        """从映射表中获取股票名称，处理大小写"""
        code = stock_code.strip().upper()
        return STOCK_NAME_MAP.get(code, code)

    def get_daily_data(self, stock_code: str, start_date: Optional[str] = None, end_date: Optional[str] = None, days: int = 30) -> Tuple[pd.DataFrame, str]:
        stock_code = normalize_stock_code(stock_code)
        for fetcher in self._fetchers:
            try:
                df = fetcher.get_daily_data(stock_code, start_date, end_date, days)
                if df is not None and not df.empty: return df, fetcher.name
            except Exception: continue
        raise DataFetchError(f"所有数据源均无法获取 {stock_code}")

    def get_realtime_quote(self, stock_code: str):
        stock_code = normalize_stock_code(stock_code)
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, 'get_realtime_quote'):
                    quote = fetcher.get_realtime_quote(stock_code)
                    if quote: return quote
            except Exception: continue
        return None
