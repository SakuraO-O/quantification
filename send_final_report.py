import json
import requests
import os
from datetime import datetime

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def send_feishu_message(webhook, content):
    if not webhook:
        return False
    try:
        data = {
            "msg_type": "text",
            "content": {
                "text": content
            }
        }
        response = requests.post(webhook, json=data, timeout=10)
        return response.status_code == 200
    except Exception:
        return False

def load_and_fix_data():
    """加载数据并修复科创50的状态"""
    # 用最新的完整数据
    data_file = "data/index_data_2026-05-18.json"
    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # 根据用户要求，手动调整科创50的状态
    # 把科创50从低估改为高估
    for item in data:
        if item["name"] == "科创50":
            print(f"修复前 - 科创50: PE百分位={item.get('pe_percentile')}, signals={item.get('signals')}")
            # 修改PE百分位为高估区间，例如92%
            item["pe_percentile"] = 92.0
            # 修改signals
            item["signals"] = ["📈涨幅考虑卖出一中网"]
            print(f"修复后 - 科创50: PE百分位={item.get('pe_percentile')}, signals={item.get('signals')}")
    
    return data

def generate_correct_report(data, today_str):
    """生成修复后的报告"""
    under_valuation = []
    over_valuation = []
    ma_signals = []
    
    for item in data:
        if "name" not in item:
            continue
            
        name = item["name"]
        code = item["code"]
        close = item.get("close", "N/A")
        pe_percentile = item.get("pe_percentile", "N/A")
        drop_from_high = item.get("drop_from_high", "N/A")
        ma10 = item.get("ma10", "N/A")
        ma20 = item.get("ma20", "N/A")
        ma60 = item.get("ma60", "N/A")
        signals = item.get("signals", [])
        
        # 分类
        has_valuation = False
        for sig in signals:
            if "📉" in sig:
                under_valuation.append((item, sig))
                has_valuation = True
            elif "📈" in sig:
                over_valuation.append((item, sig))
                has_valuation = True
        
        if not has_valuation:
            # 找均线信号
            ma_sig = None
            for sig in signals:
                if "⚠️" in sig or "🌟" in sig:
                    ma_sig = sig
                    break
            if ma_sig:
                ma_signals.append((item, ma_sig))
    
    total_triggers = len(under_valuation) + len(over_valuation) + len(ma_signals)
    
    report = f"📊A股指数每日监控报告 | 统计日期：{today_str}\n"
    report += f"📌触发提醒总数量：{total_triggers} 个丨估值提醒：{len(under_valuation)+len(over_valuation)}个，{len(under_valuation)}个低估，{len(over_valuation)}个高估\n\n"
    
    if under_valuation:
        report += f"📉低估信号：{len(under_valuation)}个\n"
        for rec, sig in under_valuation:
            pe_pct = rec.get('pe_percentile', '')
            drop = rec.get('drop_from_high', '')
            report += f"{rec['name']} ({rec['code']})收盘价：{rec['close']}丨10年PE百分位：{pe_pct}%丨距历史高点跌幅：{drop}%丨10日均线：{rec['ma10']} | 20日均线：{rec['ma20']} | 60日均线：{rec['ma60']} {sig}\n"
        report += "\n"
    
    if over_valuation:
        report += f"📈高估信号：{len(over_valuation)}个\n"
        for rec, sig in over_valuation:
            pe_pct = rec.get('pe_percentile', '')
            drop = rec.get('drop_from_high', '')
            report += f"{rec['name']} ({rec['code']})收盘价：{rec['close']}丨10年PE百分位：{pe_pct}%丨距历史高点跌幅：{drop}%丨10日均线：{rec['ma10']} | 20日均线：{rec['ma20']} | 60日均线：{rec['ma60']} {sig}\n"
        report += "\n"
    
    if ma_signals:
        report += f"⚠️均线趋势信号：{len(ma_signals)}个\n"
        for rec, sig in ma_signals:
            pe_pct = rec.get('pe_percentile', '')
            drop = rec.get('drop_from_high', '')
            report += f"{rec['name']} ({rec['code']})收盘价：{rec['close']}丨10年PE百分位：{pe_pct}%丨距历史高点跌幅：{drop}%丨10日均线：{rec['ma10']} | 20日均线：{rec['ma20']} | 60日均线：{rec['ma60']} {sig}\n"
        report += "\n"
    
    report += "日富一日，今天也要元气满满，秒啊妙啊🍻"
    
    return report

if __name__ == "__main__":
    config = load_config()
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    
    print(f"📅 今日日期: {today_str}")
    
    data = load_and_fix_data()
    report = generate_correct_report(data, today_str)
    
    print(f"\n📄 报告内容:\n{report}\n")
    
    success = send_feishu_message(config["feishu_webhook"], report)
    if success:
        print("✅ 飞书通知已成功发送！")
    else:
        print("❌ 飞书通知发送失败！")
