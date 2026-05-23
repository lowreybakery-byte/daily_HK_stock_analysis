# -*- coding: utf-8 -*-
"""
自动化每日港股复盘系统 (集成量化主声浪初筛)
================================================
本脚本用于 GitHub Actions 定时执行：
1. 扫描核心自选股的量化异动信号 (均线、发散、突破)
2. 结合异动情况生成每日市场复盘报告
"""

import os
import time
from datetime import datetime
# 引入我们刚刚写好的量化雷达模块
from HK_MainWaveDetector import HKMainWaveDetector

# 你的核心关注港股名单
MY_STOCK_POOL = [
    "03317.HK", "02865.HK", "01989.HK", "03986.HK", "06088.HK",
    "06181.HK", "01072.HK", "02513.HK", "02507.HK", "01860.HK",
    "06656.HK", "02476.HK", "06082.HK", "00068.HK", "01530.HK",
    "01879.HK", "03296.HK", "06869.HK"
]

def run_quantitative_scan():
    """运行量化雷达，扫描自选股"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🚀 开始进行港股量化技术面扫描...")
    print(f"待扫描股票池共 {len(MY_STOCK_POOL)} 只标的。")
    
    detector = HKMainWaveDetector()
    triggered_stocks = []
    
    for code in MY_STOCK_POOL:
        # 扫描每一只股票
        signal = detector.generate_enhanced_mainwave_signal(code, is_high_trend=False)
        
        if signal:
            print(f"  🔥 [异动发现] {code} 触发主声浪启动信号！当前价格: {signal['price']}, 量化评分: {signal['score']}")
            triggered_stocks.append(signal)
            
        # 停顿0.3秒防止请求过快
        time.sleep(0.3)
        
    return triggered_stocks

def generate_daily_report(triggered_stocks):
    """生成每日复盘Markdown报告"""
    date_str = datetime.now().strftime('%Y-%m-%d')
    report_lines = [
        f"# 📈 港股每日自动化复盘报告 ({date_str})",
        "",
        "## 🤖 量化雷达监控结果",
        ""
    ]
    
    if triggered_stocks:
        report_lines.append(f"**今日在自选股池中发现 {len(triggered_stocks)} 只标的出现主声浪技术面异动！**\n")
        report_lines.append("| 股票代码 | 触发价格 | 量化综合评分 | 信号类型 |")
        report_lines.append("| :--- | :--- | :--- | :--- |")
        for s in triggered_stocks:
            report_lines.append(f"| {s['code']} | {s['price']} | {s['score']} | {s['signal']} |")
            
        report_lines.append("\n*系统提示：建议结合今日公司公告及行业新闻对以上个股进行基本面重点确认。*")
    else:
        report_lines.append("今日量化监控未发现自选股出现主声浪级别的突破异动信号。建议继续耐心观察。")
        
    report_lines.append("\n## 📰 市场动态与分析")
    report_lines.append("> 自动化量化扫描执行完毕。")
    
    report_content = "\n".join(report_lines)
    
    report_filename = f"daily_report_{date_str}.md"
    with open(report_filename, "w", encoding="utf-8") as f:
        f.write(report_content)
        
    print(f"\n✅ 今日复盘报告已生成: {report_filename}")
    print(report_content)

def main():
    signals = run_quantitative_scan()
    generate_daily_report(signals)

if __name__ == "__main__":
    main()
