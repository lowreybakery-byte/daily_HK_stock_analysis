# -*- coding: utf-8 -*-
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any
import pandas as pd
import numpy as np

# 导入映射表
from src.data.stock_mapping import STOCK_NAME_MAP

logger = logging.getLogger(__name__)
STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']

# --- 辅助工具函数 (修复 ImportError 的关键) ---
def normalize_stock_code(stock_code: str) -> str:
    code = stock_code.strip().upper()
    if code.endswith('.HK'): return code
    if code.startswith('HK'): return code[2:] + '.HK'
    return code

def is_bse_code(code: str) -> bool:
    c = (code or "").strip().split(".")[0]
    return len(c) == 6 and c.isdigit() and (c.startswith(("8", "4")) or c.startswith("92"))

def is_st_stock(name: str) -> bool:
    return 'ST' in (name or "").upper()

def is_kc_cy_stock(code: str) -> bool:
    c = (code or "").strip().split(".")[0]
    return c.startswith("688") or c.startswith("30")

def canonical_stock_code(code: str) -> str:
    return (code or "").strip().upper()

class DataFetchError(Exception): pass

# --- 核心类 ---
class BaseFetcher(ABC):
    @abstractmethod
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame: pass
    @abstractmethod
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame: pass

    def get_daily_data(self, stock_code: str, start_date: Optional[str] = None, end_date: Optional[str] = None, days: int = 30) -> pd.DataFrame:
        if end_date is None: end_date = datetime.now().strftime('%Y-%m-%d')
        if start_date is None:
            start_date = (datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=days * 2)).strftime('%Y-%m-%d')
        raw_df = self._fetch_raw_data(stock_code, start_date, end_date)
        if raw_df is None or raw_df.empty: raise DataFetchError("无数据")
        df = self._normalize_data(raw_df, stock_code)
        return df.dropna(subset=['close', 'volume']).sort_values('date').reset_index(drop=True)

class DataFetcherManager:
    def __init__(self):
        from .efinance_fetcher import EfinanceFetcher
        from .akshare_fetcher import AkshareFetcher
        from .yfinance_fetcher import YfinanceFetcher
        self._fetchers = [YfinanceFetcher(), EfinanceFetcher(), AkshareFetcher()]
    
    def get_stock_name(self, stock_code: str) -> str:
        """补丁方法：修复 AttributeError"""
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
