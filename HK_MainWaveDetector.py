# -*- coding: utf-8 -*-
"""
主声浪识别模型系统 (港股适配版 - 雅虎财经数据源)
================================================
改造说明：
1. 数据源已替换为 yfinance (雅虎财经)，完美适配 GitHub Actions 云端环境。
2. 参数根据港股 T+0 及无涨跌幅限制的特性进行了调优。
3. 包含防仙股/老千股的流动性过滤机制。
"""

import numpy as np
import pandas as pd
import yfinance as yf
import time
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# ============================================================
# 港股主声浪核心参数配置（放宽波动限制，适应港股特征）
# ============================================================
HK_MAINWAVE_CONFIG = {
    'ma_periods': [5, 10, 20, 30],
    'divergence_days': 3,
    'divergence_ratio_optimal': 2.80,           
    'divergence_range': [2.00, 4.00],           
    
    'enable_multi_timeframe': True,
    'weekly_ma_periods': [5, 10, 20],
    
    'enable_volume_confirm': True,
    'volume_ratio_threshold': 1.3,              
    'volume_ma_period': 5,
    
    'enable_momentum': True,
    'macd_fast': 12, 'macd_slow': 26, 'macd_signal': 9,
    'rsi_period': 14, 'rsi_range': [35, 75],    
    'kdj_k': 9, 'kdj_d': 3, 'kdj_j': 3,
    
    'enable_breakout_check': True,
    'breakout_low_pct': 92.00,                  
    'breakout_high_pct': 108.00,                
    'breakout_lookback_days': 120,
    
    'bias_period': 20,
    'bias_threshold_upper': 20.0,               
    'bias_threshold_lower': -15.0,
    
    'trend_strength_weights': {
        'ma_alignment': 0.25,
        'divergence': 0.20,
        'momentum': 0.20,
        'volume': 0.15,
        'breakout': 0.10,
        'multi_timeframe': 0.10,
    },
    
    'entry_signal_strength': 60,                
    'min_liquidity_vol': 2000000,               
}

class HKMainWaveDetector:
    def __init__(self, config: Dict = None):
        self.config = config or HK_MAINWAVE_CONFIG.copy()

    def get_kline_data(self, code: str, count: int = 250, period: str = '1d'):
        """使用 yfinance 获取数据"""
        # 映射 yfinance 支持的周期
        interval_map = {'1d': '1d', '1w': '1wk', '1m': '1mo'}
        yf_interval = interval_map.get(period, '1d')
        
        # 为了确保拿到足够的 count，直接拉取过去 2 年的数据，然后再截取最后 count 行
        try:
            ticker = yf.Ticker(code)
            df = ticker.history(period="2y", interval=yf_interval)
            
            if df is not None and not df.empty:
                return df.tail(count)
            return None
        except Exception as e:
            print(f"[ERROR] 获取K线失败: {code}, {e}")
            return None

    def _extract_series(self, df, field='Close'):
        try:
            if isinstance(df, dict) and field in df:
                target_df = df[field]
            elif hasattr(df, field):
                target_df = getattr(df, field)
            else:
                return None

            if isinstance(target_df, pd.DataFrame):
                return target_df.iloc[:, 0] if len(target_df.columns) > 0 else None
            elif hasattr(target_df, 'iloc'):
                return target_df.iloc[:, 0]
            else:
                return pd.Series(target_df)
        except:
            return None

    def calculate_ma(self, df, periods: List[int]) -> Dict[str, pd.Series]:
        close = self._extract_series(df, 'Close')
        return {f'MA{p}': close.rolling(window=p).mean() for p in periods} if close is not None else {}

    def check_ma_bullish_alignment(self, ma_dict: Dict[str, pd.Series], idx: int) -> Tuple[bool, float]:
        periods = self.config['ma_periods']
        ma_values = [(p, ma_dict[f'MA{p}'].iloc[idx]) for p in periods if f'MA{p}' in ma_dict and idx < len(ma_dict[f'MA{p}']) and not np.isnan(ma_dict[f'MA{p}'].iloc[idx])]
        if len(ma_values) < 2: return False, 0.0
        
        is_bullish = all(ma_values[i][1] > ma_values[i+1][1] for i in range(len(ma_values)-1))
        strength = 50.0 if is_bullish else 0.0
        if is_bullish:
            gaps = [(ma_values[i][1] - ma_values[i+1][1]) / ma_values[i+1][1] * 100 for i in range(len(ma_values)-1)]
            if gaps: strength = min(100, max(0, (np.mean(gaps) / (np.std(gaps) + 0.01)) * 10))
        return is_bullish, strength

    def check_liquidity(self, df, idx: int) -> bool:
        vol = self._extract_series(df, 'Volume')
        if vol is None or idx < 20: return False
        avg_vol = vol.iloc[idx-20:idx].mean()
        return avg_vol >= self.config['min_liquidity_vol']

    def generate_enhanced_mainwave_signal(self, code: str, is_high_trend: bool = False) -> Optional[Dict]:
        df = self.get_kline_data(code, count=250)
        if df is None: return None
        
        close = self._extract_series(df, 'Close')
        if close is None or len(close) < 30: return None
        
        current_idx = len(close) - 1
        
        if not self.check_liquidity(df, current_idx):
            return None

        ma_dict = self.calculate_ma(df, self.config['ma_periods'])
        if not ma_dict: return None

        is_aligned, alignment_score = self.check_ma_bullish_alignment(ma_dict, current_idx)
        
        total_score = alignment_score * 0.4 + (50 if is_aligned else 0)
        if is_high_trend: total_score += 10
        
        if total_score >= self.config['entry_signal_strength'] and is_aligned:
            return {
                'code': code,
                'price': round(float(close.iloc[current_idx]), 2),
                'score': round(total_score, 2),
                'signal': 'HK_MAINWAVE_START'
            }
        return None
