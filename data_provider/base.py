# -*- coding: utf-8 -*-
"""
===================================
数据源基类与管理器
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
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

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
    """
    强制将港股代码保留 .HK 后缀，并统一格式。
    """
    code = stock_code.strip().upper()
    
    # 如果是港股后缀格式 (例如 00700.HK)，直接返回
    if code.endswith('.HK'):
        return code
        
    # 如果是前缀格式 (例如 HK00700)，转换为后缀格式 (00700.HK)
    if code.startswith('HK'):
        return code[2:] + '.HK'
        
    # 原有的 A 股清洗逻辑
    upper = code.upper()
    if upper.startswith(('SH', 'SZ')) and not upper.startswith('SH.') and not upper.startswith('SZ.'):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) in (5, 6):
            return candidate
    if upper.startswith('BJ') and not upper.startswith('BJ.'):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) == 6:
            return candidate
    if '.' in code:
        base, suffix = code.rsplit('.', 1)
        if suffix.upper() in ('SH', 'SZ', 'SS', 'BJ') and base.isdigit():
            return base
    return code

def is_bse_code(code: str) -> bool:
    c = (code or "").strip().split(".")[0]
    if len(c) != 6 or not c.isdigit():
        return False
    return c.startswith(("8", "4")) or c.startswith("92")

def is_st_stock(name: str) -> bool:
    n = (name or "").upper()
    return 'ST' in n

def is_kc_cy_stock(code: str) -> bool:
    c = (code or "").strip().split(".")[0]
    return c.startswith("688") or c.startswith("30")

def canonical_stock_code(code: str) -> str:
    return (code or "").strip().upper()

class DataFetchError(Exception): pass
class RateLimitError(DataFetchError): pass
class DataSourceUnavailableError(DataFetchError): pass

class BaseFetcher(ABC):
    name: str = "BaseFetcher"
    priority: int = 99
    
    @abstractmethod
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        pass
    
    @abstractmethod
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        pass

    def get_main_indices(self, region: str = "hk") -> Optional[List[Dict[str, Any]]]:
        return None

    def get_market_stats(self) -> Optional[Dict[str, Any]]:
        return None

    def get_sector_rankings(self, n: int = 5) -> Optional[Tuple[List[Dict], List[Dict]]]:
        return None

    def get_daily_data(
        self,
        stock_code: str, 
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> pd.DataFrame:
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')
        if start_date is None:
            from datetime import timedelta
            start_dt = datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=days * 2)
            start_date = start_dt.strftime('%Y-%m-%d')
        try:
            raw_df = self._fetch_raw_data(stock_code, start_date, end_date)
            if raw_df is None or raw_df.empty:
                raise DataFetchError(f"[{self.name}] 未获取到 {stock_code} 的数据")
            df = self._normalize_data(raw_df, stock_code)
            df = self._clean_data(df)
            df = self._calculate_indicators(df)
            return df
        except Exception as e:
            error_type, error_reason = summarize_exception(e)
            raise DataFetchError(f"[{self.name}] {stock_code}: {error_reason}") from e
    
    def _clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['close', 'volume'])
        df = df.sort_values('date', ascending=True).reset_index(drop=True)
        return df
    
    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['ma5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['ma10'] = df['close'].rolling(window=10, min_periods=1).mean()
        df['ma20'] = df['close'].rolling(window=20, min_periods=1).mean()
        avg_volume_5 = df['volume'].rolling(window=5, min_periods=1).mean()
        df['volume_ratio'] = df['volume'] / avg_volume_5.shift(1)
        df['volume_ratio'] = df['volume_ratio'].fillna(1.0)
        return df

class DataFetcherManager:
    def __init__(self, fetchers: Optional[List[BaseFetcher]] = None):
        self._fetchers: List[BaseFetcher] = []
        if fetchers:
            self._fetchers = sorted(fetchers, key=lambda f: f.priority)
        else:
            self._init_default_fetchers()
    
    def _init_default_fetchers(self) -> None:
        from .efinance_fetcher import EfinanceFetcher
        from .akshare_fetcher import AkshareFetcher
        from .yfinance_fetcher import YfinanceFetcher
        self._fetchers = [EfinanceFetcher(), AkshareFetcher(), YfinanceFetcher()]
        self._fetchers.sort(key=lambda f: f.priority)
    
    def get_daily_data(self, stock_code: str, start_date: Optional[str] = None, end_date: Optional[str] = None, days: int = 30) -> Tuple[pd.DataFrame, str]:
        stock_code = normalize_stock_code(stock_code)
        for fetcher in self._fetchers:
            try:
                df = fetcher.get_daily_data(stock_code, start_date, end_date, days)
                if df is not None and not df.empty:
                    return df, fetcher.name
            except Exception: continue
        raise DataFetchError(f"所有数据源均无法获取 {stock_code}")

    def get_realtime_quote(self, stock_code: str):
        stock_code = normalize_stock_code(stock_code)
        for fetcher in self._fetchers:
            if hasattr(fetcher, 'get_realtime_quote'):
                try:
                    quote = fetcher.get_realtime_quote(stock_code)
                    if quote: return quote
                except Exception: continue
        return None
