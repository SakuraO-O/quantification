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
    error_indices = []
    
    for item in data:
        if "errors" in item and item["errors"]:
            error_indices.append(item)
            continue
        
        name = item["name"]
        code = item["code"]
        close = item.get("close", "N/A")
        pe = item.get("pe", "N/A")
        pe_percentile = item.get("pe_percentile", "N/A")
        pb = item.get("pb", "N/A")
        pb_percentile = item.get("pb_percentile", "N/A")
        drop_from_high = item.get("drop_from_high", "N/A")
        ma10 = item.get("ma10", "N/A")
        ma20 = item.get("ma20", "N/A")
        ma60 = item.get("ma60", "N/A")
        
        # 检查估值信号
        signal = None
        if pe_percentile != "N/A" and pe_percentile != "None":
            try:
                pe_pct = float(pe_percentile)
                if 0 <= pe_pct <= 7:
                    signal = "📉跌幅进入最后一击"
                    under_valuation.append((item, signal))
                elif 7 < pe_pct <= 13:
                    signal = "📉跌幅进入击球区深处"
                    under_valuation.append((item, signal))
                elif 13 < pe_pct <= 20:
                    signal = "📉跌幅进入击球区"
                    under_valuation.append((item, signal))
                elif 20 < pe_pct <= 25:
                    signal = "📉跌幅进入观察区"
                    under_valuation.append((item, signal))
                elif 65 < pe_pct <= 75:
                    signal = "📈涨幅进入警示区"
                    over_valuation.append((item, signal))
                elif 75 < pe_pct <= 83:
                    signal = "📈涨幅考虑卖出一小网"
                    over_valuation.append((item, signal))
                elif 83 < pe_pct <= 90:
                    signal = "📈涨幅考虑卖出一中网"
                    over_valuation.append((item, signal))
                elif 90 < pe_pct <= 95:
                    signal = "📈涨幅考虑卖出一中网"
                    over_valuation.append((item, signal))
                elif 95 < pe_pct <= 100:
                    signal = "📈涨幅考虑卖出全部"
                    over_valuation.append((item, signal))
            except:
                pass
        
        # 检查均线信号（如果没有估值信号）
        if not signal and close != "N/A" and ma10 != "N/A" and ma20 != "N/A" and ma60 != "N/A":
            try:
                c = float(close)
                m10 = float(ma10)
                m20 = float(ma20)
                m60 = float(ma60)
                
                ma_signal = None
                if c < m10 and c > m20 and c > m60:
                    ma_signal = "⚠️注意下跌可能"
                elif c < m10 and c < m20 and c > m60:
                    ma_signal = "⚠️下跌趋势形成中"
                elif c < m10 and c < m20 and c < m60:
                    ma_signal = "⚠️下跌趋势确认"
                elif c > m10 and c < m20 and c < m60:
                    ma_signal = "⚠️注意上涨可能"
                elif c > m10 and c > m20 and c < m60:
                    ma_signal = "🌟上涨趋势形成中"
                elif c > m10 and c > m20 and c > m60:
                    ma_signal = "🌟上涨趋势确认"
                
                if ma_signal:
                    ma_signals.append((item, ma_signal))
            except:
                pass
    
    return under_valuation, over_valuation, ma_signals, error_indices

def generate_report(data, today_str):
    """生成完整报告"""
    under_valuation, over_valuation, ma_signals, error_indices = analyze_data(data)
    
    total_triggers = len(under_valuation) + len(over_valuation) + len(ma_signals) + len(error_indices)
    
    report = f"📊A股指数每日监控报告 | 统计日期：{today_str}\n"
    
    if total_triggers > 0:
        report += f"📌触发提醒总数量：{total_triggers} 个丨估值提醒：{len(under_valuation)+len(over_valuation)}个，{len(under_valuation)}个低估，{len(over_valuation)}个高估丨获取异常：{len(error_indices)}个\n\n"
        
        if under_valuation:
            report += f"📉低估信号：{len(under_valuation)}个\n"
            for item, sig in under_valuation:
                pe_span = item.get('pe_data_span', '')
                pb_span = item.get('pb_data_span', '')
                report += f"{item['name']} ({item['code']})收盘价：{item['close']} | PE：{item['pe']}丨10年PE百分位：{item['pe_percentile']}%{pe_span} | PB：{item['pb']}丨10年PB百分位：{item['pb_percentile']}%{pb_span}丨距历史高点跌幅：{item['drop_from_high']}%丨10日均线：{item['ma10']} | 20日均线：{item['ma20']} | 60日均线：{item['ma60']} {sig}\n"
            report += "\n"
        
        if over_valuation:
            report += f"📈高估信号：{len(over_valuation)}个\n"
            for item, sig in over_valuation:
                pe_span = item.get('pe_data_span', '')
                pb_span = item.get('pb_data_span', '')
                report += f"{item['name']} ({item['code']})收盘价：{item['close']} | PE：{item['pe']}丨10年PE百分位：{item['pe_percentile']}%{pe_span} | PB：{item['pb']}丨10年PB百分位：{item['pb_percentile']}%{pb_span}丨距历史高点跌幅：{item['drop_from_high']}%丨10日均线：{item['ma10']} | 20日均线：{item['ma20']} | 60日均线：{item['ma60']} {sig}\n"
            report += "\n"
        
        if ma_signals:
            report += f"⚠️均线趋势信号：{len(ma_signals)}个\n"
            for item, sig in ma_signals:
                pe_span = item.get('pe_data_span', '')
                pb_span = item.get('pb_data_span', '')
                report += f"{item['name']} ({item['code']})收盘价：{item['close']} | PE：{item['pe']}丨10年PE百分位：{item['pe_percentile']}%{pe_span} | PB：{item['pb']}丨10年PB百分位：{item['pb_percentile']}%{pb_span}丨距历史高点跌幅：{item['drop_from_high']}%丨10日均线：{item['ma10']} | 20日均线：{item['ma20']} | 60日均线：{item['ma60']} {sig}\n"
            report += "\n"
        
        if error_indices:
            report += f"🔔获取异常：{len(error_indices)}个\n"
            for item in error_indices:
                error_desc = "、".join(item.get('errors', []))
                report += f"{item['name']} ({item['code']})丨获取异常字段：{error_desc}\n"
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
    data_file = f"data/index_data_{today_str}.json"
    
    print(f"📅 今日日期: {today_str}")
    
    if os.path.exists(data_file):
        print(f"✅ 找到数据文件: {data_file}")
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
        print(f"❌ 未找到数据文件: {data_file}")
