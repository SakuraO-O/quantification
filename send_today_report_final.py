#!/usr/bin/env python3
import json
import requests
import os
from datetime import datetime

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def send_feishu_message(webhook, content):
    if not webhook:
        print("⚠️ 没有配置飞书webhook")
        return False
    try:
        data = {
            "msg_type": "text",
            "content": {
                "text": content
            }
        }
        print(f"📤 正在发送飞书通知...")
        response = requests.post(webhook, json=data, timeout=10)
        print(f"📤 响应状态码: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ 发送失败: {e}")
        return False

def analyze_data(data):
    """分析数据并生成报告"""
    under_valuation = []
    over_valuation = []
    ma_signals = []
    
    for item in data:
        name = item["name"]
        code = item["code"]
        close = item.get("close", "N/A")
        pe_percentile = item.get("pe_percentile", "N/A")
        drop_from_high = item.get("drop_from_high", "N/A")
        ma10 = item.get("ma10", "N/A")
        ma20 = item.get("ma20", "N/A")
        ma60 = item.get("ma60", "N/A")
        signals = item.get("signals", [])
        
        # 分类信号
        has_valuation_signal = False
        has_ma_signal = False
        
        for sig in signals:
            if "📉" in sig or "📈" in sig:
                under_valuation.append((item, sig)) if "📉" in sig else over_valuation.append((item, sig))
                has_valuation_signal = True
            elif "⚠️" in sig or "🌟" in sig:
                if not has_valuation_signal:  # 只在没有估值信号时添加均线信号
                    ma_signals.append((item, sig))
                    has_ma_signal = True
    
    return under_valuation, over_valuation, ma_signals

def generate_report(data, today_str):
    """生成完整报告"""
    under_valuation, over_valuation, ma_signals = analyze_data(data)
    
    total_triggers = len(under_valuation) + len(over_valuation) + len(ma_signals)
    
    report = f"📊A股指数每日监控报告 | 统计日期：{today_str}\n"
    
    if total_triggers > 0:
        report += f"📌触发提醒总数量：{total_triggers} 个丨估值提醒：{len(under_valuation)+len(over_valuation)}个，{len(under_valuation)}个低估，{len(over_valuation)}个高估\n\n"
        
        if under_valuation:
            report += f"📉低估信号：{len(under_valuation)}个\n"
            for item, sig in under_valuation:
                report += f"{item['name']} ({item['code']})收盘价：{item['close']}丨10年PE百分位：{item['pe_percentile']}%丨距历史高点跌幅：{item['drop_from_high']}%丨10日均线：{item['ma10']} | 20日均线：{item['ma20']} | 60日均线：{item['ma60']} {sig}\n"
            report += "\n"
        
        if over_valuation:
            report += f"📈高估信号：{len(over_valuation)}个\n"
            for item, sig in over_valuation:
                report += f"{item['name']} ({item['code']})收盘价：{item['close']}丨10年PE百分位：{item['pe_percentile']}%丨距历史高点跌幅：{item['drop_from_high']}%丨10日均线：{item['ma10']} | 20日均线：{item['ma20']} | 60日均线：{item['ma60']} {sig}\n"
            report += "\n"
        
        if ma_signals:
            report += f"⚠️均线趋势信号：{len(ma_signals)}个\n"
            for item, sig in ma_signals:
                report += f"{item['name']} ({item['code']})收盘价：{item['close']}丨10年PE百分位：{item['pe_percentile']}%丨距历史高点跌幅：{item['drop_from_high']}%丨10日均线：{item['ma10']} | 20日均线：{item['ma20']} | 60日均线：{item['ma60']} {sig}\n"
            report += "\n"
        
        report += "日富一日，今天也要元气满满，秒啊妙啊🍻"
    else:
        report += "✅所有监控指数运行平稳，无任何估值、均线条件触发提醒\n"
        report += "日富一日，今天也要元气满满，秒啊妙啊🍻"
    
    return report

if __name__ == "__main__":
    config = load_config()
    
    # 获取今日日期
    today = datetime.now()
    today_str = today.strftime('%Y-%m-%d')
    
    # 使用昨天的数据
    data_file = "data/index_data_2026-05-18.json"
    
    print(f"📅 今日日期: {today_str}")
    
    if os.path.exists(data_file):
        print(f"✅ 使用数据文件: {data_file}")
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"✅ 数据包含 {len(data)} 个指数")
        
        report = generate_report(data, today_str)
        print(f"\n📄 报告内容:\n{report}\n")
        
        success = send_feishu_message(config["feishu_webhook"], report)
        if success:
            print("✅ 飞书通知发送成功！")
        else:
            print("❌ 飞书通知发送失败！")
    else:
        print(f"❌ 未找到数据文件")
