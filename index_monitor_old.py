import akshare as ak
import pandas as pd
import numpy as np
import requests
import json
import os
from datetime import datetime, timedelta
import time

CONFIG_FILE = "config.json"
DATA_DIR = "data"

INDEX_LIST = [
    {"name": "沪深300", "code": "000300", "market": "csi"},
    {"name": "中证500", "code": "000905", "market": "csi"},
    {"name": "中证2000", "code": "932000", "market": "csi"},
    {"name": "创业板指100", "code": "399102", "market": "csi"},
    {"name": "科创50", "code": "000688", "market": "csi"},
    {"name": "恒生指数", "code": "HSI", "market": "hk"},
    {"name": "恒生科技指数", "code": "HSTECH", "market": "hk"},
    {"name": "中证消费", "code": "000932", "market": "csi"},
    {"name": "全指医药", "code": "000991", "market": "csi"},
    {"name": "中证医疗", "code": "399989", "market": "csi"},
    {"name": "港股通创新药", "code": "931250", "market": "csi"},
    {"name": "中证环保", "code": "000827", "market": "csi"},
    {"name": "中证传媒", "code": "399971", "market": "csi"},
    {"name": "养老产业", "code": "399481", "market": "csi"}
]

CN_HOLIDAYS = [
    "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-01", "2025-02-02", "2025-02-03", "2025-02-04", "2025-04-04",
    "2025-04-05", "2025-04-06", "2025-05-01", "2025-05-02", "2025-05-03",
    "2025-05-31", "2025-06-01", "2025-06-02", "2025-10-01", "2025-10-02",
    "2025-10-03", "2025-10-04", "2025-10-05", "2025-10-06", "2025-10-07",
    "2025-10-08"
]

HK_HOLIDAYS = [
    "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-01", "2025-02-02", "2025-02-03", "2025-02-04", "2025-04-04",
    "2025-04-05", "2025-04-06", "2025-05-01", "2025-05-02", "2025-05-03",
    "2025-06-02", "2025-10-01", "2025-10-02", "2025-10-03", "2025-12-25",
    "2025-12-26"
]


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"feishu_webhook": ""}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def is_trading_day(date):
    if date.weekday() in (5, 6):
        return False
    date_str = date.strftime("%Y-%m-%d")
    if date_str in CN_HOLIDAYS or date_str in HK_HOLIDAYS:
        return False
    return True


def get_yesterday():
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    return yesterday


def fetch_with_retry(func, max_retries=3, *args, **kwargs):
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if i == max_retries - 1:
                raise e
            time.sleep(1)


def get_csi_index_data(code):
    try:
        df = fetch_with_retry(ak.stock_zh_index_daily_em, symbol=f"sh{code}" if code.startswith("000") or code.startswith("0006") else f"sz{code}")
        return df
    except:
        try:
            df = fetch_with_retry(ak.stock_zh_index_hist_csindex, symbol=code)
            return df
        except:
            try:
                df = fetch_with_retry(ak.stock_zh_index_daily, symbol=code)
                return df
            except:
                return None


def get_hk_index_data(code):
    try:
        if code == "HSI":
            df = fetch_with_retry(ak.stock_hk_index_daily_em, symbol="HSI")
            return df
        elif code == "HSTECH":
            df = fetch_with_retry(ak.stock_hk_index_daily_em, symbol="HSTECH")
            return df
        return None
    except:
        try:
            df = fetch_with_retry(ak.stock_hk_index_daily_sina, symbol=code)
            return df
        except:
            return None


def get_index_pe(code, market):
    try:
        if market == "csi":
            df = fetch_with_retry(ak.stock_zh_index_value_csindex, symbol=code)
            return df
        else:
            return None
    except:
        try:
            df = fetch_with_retry(ak.stock_index_pe_lg, symbol=code)
            return df
        except:
            return None


def calculate_indicators(index_data, pe_data):
    if index_data is None or len(index_data) == 0:
        return None
    
    latest = index_data.iloc[-1]
    close_price = round(float(latest["收盘"]) if "收盘" in latest else float(latest["close"]), 2)
    
    ma10 = None
    ma20 = None
    ma60 = None
    
    if len(index_data) >= 10:
        ma10 = round(index_data["收盘"].iloc[-10:].mean() if "收盘" in index_data.columns else index_data["close"].iloc[-10:].mean(), 2)
    if len(index_data) >= 20:
        ma20 = round(index_data["收盘"].iloc[-20:].mean() if "收盘" in index_data.columns else index_data["close"].iloc[-20:].mean(), 2)
    if len(index_data) >= 60:
        ma60 = round(index_data["收盘"].iloc[-60:].mean() if "收盘" in index_data.columns else index_data["close"].iloc[-60:].mean(), 2)
    
    historical_high = round(index_data["最高"].max() if "最高" in index_data.columns else index_data["high"].max(), 2)
    drop_from_high = round((historical_high - close_price) / historical_high * 100, 1)
    
    pe_percentile = None
    if pe_data is not None and len(pe_data) > 0:
        pe_col = None
        for col in pe_data.columns:
            if "市盈率" in col or "PE" in col:
                pe_col = col
                break
        if pe_col:
            latest_pe = float(pe_data.iloc[-1][pe_col])
            all_pes = pd.to_numeric(pe_data[pe_col], errors="coerce").dropna()
            if len(all_pes) > 0:
                percentile = (all_pes < latest_pe).mean() * 100
                pe_percentile = round(percentile, 1)
    
    return {
        "close": close_price,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "historical_high": historical_high,
        "drop_from_high": drop_from_high,
        "pe_percentile": pe_percentile
    }


def check_conditions(indicators):
    signals = []
    
    if indicators["pe_percentile"] is not None:
        pe_pct = indicators["pe_percentile"]
        if pe_pct <= 7:
            signals.append("📉跌幅进入最后一击")
        elif pe_pct <= 13:
            signals.append("📉跌幅进入击球区深处")
        elif pe_pct <= 20:
            signals.append("📉跌幅进入击球区")
        elif pe_pct <= 25:
            signals.append("📉跌幅进入观察区")
        elif pe_pct >= 96:
            signals.append("📈卖出全部")
        elif pe_pct >= 91:
            signals.append("📈卖出一中网")
        elif pe_pct >= 85:
            signals.append("📈卖出一中网")
        elif pe_pct >= 78:
            signals.append("📈卖出一小网")
        elif pe_pct >= 70:
            signals.append("📈卖出一小网")
        elif pe_pct >= 65:
            signals.append("📈涨幅进入警示区")
    
    if indicators["ma10"] is not None and indicators["close"] < indicators["ma10"]:
        signals.append("⚠️注意下跌可能")
    elif indicators["ma10"] is not None and indicators["close"] > indicators["ma10"]:
        signals.append("⚠️注意上涨可能")
    
    if indicators["ma20"] is not None and indicators["close"] < indicators["ma20"]:
        signals.append("⚠️下跌趋势形成中")
    elif indicators["ma20"] is not None and indicators["close"] > indicators["ma20"]:
        signals.append("⚠️上涨趋势形成中")
    
    if indicators["ma60"] is not None and indicators["close"] < indicators["ma60"]:
        signals.append("⚠️下跌趋势确认")
    elif indicators["ma60"] is not None and indicators["close"] > indicators["ma60"]:
        signals.append("⚠️上涨趋势确认")
    
    return signals


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
    except:
        return False


def save_daily_data(date_str, all_data):
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    filename = os.path.join(DATA_DIR, f"index_data_{date_str}.json")
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)


def main():
    config = load_config()
    yesterday = get_yesterday()
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    
    if not is_trading_day(yesterday):
        msg = "妙啊妙啊，日富一日"
        print(msg)
        if config.get("feishu_webhook"):
            send_feishu_message(config["feishu_webhook"], msg)
        return
    
    all_index_data = []
    triggered_indices = []
    under_valuation_signals = []
    over_valuation_signals = []
    ma_trend_signals = []
    
    for idx in INDEX_LIST:
        try:
            if idx["market"] == "csi":
                index_data = get_csi_index_data(idx["code"])
            else:
                index_data = get_hk_index_data(idx["code"])
            
            pe_data = get_index_pe(idx["code"], idx["market"])
            indicators = calculate_indicators(index_data, pe_data)
            
            if indicators is None:
                continue
            
            signals = check_conditions(indicators)
            
            index_info = {
                "name": idx["name"],
                "code": idx["code"],
                **indicators,
                "signals": signals
            }
            
            all_index_data.append(index_info)
            
            if len(signals) > 0:
                triggered_indices.append(index_info)
                
                for signal in signals:
                    if "📉" in signal:
                        under_valuation_signals.append((index_info, signal))
                    elif "📈" in signal:
                        over_valuation_signals.append((index_info, signal))
                    elif "⚠️" in signal:
                        ma_trend_signals.append((index_info, signal))
        
        except Exception as e:
            continue
    
    save_daily_data(yesterday_str, all_index_data)
    
    if len(triggered_indices) > 0:
        report = f"📊指数每日监控报告 | 昨日统计日期：{yesterday_str}\n触发指数总数量：{len(triggered_indices)} 个\n\n"
        
        under_valuation_indices = {}
        for info, signal in under_valuation_signals:
            key = info['code']
            if key not in under_valuation_indices:
                under_valuation_indices[key] = {'info': info, 'signals': []}
            under_valuation_indices[key]['signals'].append(signal)
        
        over_valuation_indices = {}
        for info, signal in over_valuation_signals:
            key = info['code']
            if key not in over_valuation_indices:
                over_valuation_indices[key] = {'info': info, 'signals': []}
            over_valuation_indices[key]['signals'].append(signal)
        
        ma_trend_indices = {}
        for info, signal in ma_trend_signals:
            key = info['code']
            if key not in ma_trend_indices:
                ma_trend_indices[key] = {'info': info, 'signals': []}
            ma_trend_indices[key]['signals'].append(signal)
        
        if under_valuation_indices:
            report += "📉低估击球区信号\n"
            for code, data in under_valuation_indices.items():
                info = data['info']
                report += f"{info['name']} ({code})\n"
                report += f"收盘价：{info['close']:.2f} | 10 年 TTM PE 百分位：{info['pe_percentile']:.1f}% | 距历史高点跌幅：{info['drop_from_high']:.1f}%\n"
                report += "\n".join(data['signals']) + "\n\n"
        
        if over_valuation_indices:
            report += "📈高估卖出区信号\n"
            for code, data in over_valuation_indices.items():
                info = data['info']
                report += f"{info['name']} ({code})\n"
                report += f"收盘价：{info['close']:.2f} | 10 年 TTM PE 百分位：{info['pe_percentile']:.1f}% | 距历史高点跌幅：{info['drop_from_high']:.1f}%\n"
                report += "\n".join(data['signals']) + "\n\n"
        
        if ma_trend_indices:
            report += "⚠️均线趋势风险信号\n"
            for code, data in ma_trend_indices.items():
                info = data['info']
                report += f"{info['name']} ({code})\n"
                ma10_str = f"{info['ma10']:.2f}" if info['ma10'] is not None else "数据暂不可用"
                ma20_str = f"{info['ma20']:.2f}" if info['ma20'] is not None else "数据暂不可用"
                report += f"收盘价：{info['close']:.2f} | 10 日均线：{ma10_str} | 20 日均线：{ma20_str}\n"
                report += "\n".join(data['signals']) + "\n\n"
        
        report += f"📌整体汇总统计\n"
        report += f"观察击球类信号：{len(under_valuation_indices)} 个\n"
        report += f"高估卖出类信号：{len(over_valuation_indices)} 个\n"
        report += f"均线趋势风险信号：{len(ma_trend_indices)} 个\n"
        report += f"\n妙啊妙啊，日富一日"
    else:
        report = f"📊指数每日监控报告 | 昨日统计日期：{yesterday_str}\n✅昨日所有监控指数运行平稳，无任何估值、均线条件触发提醒\n\n妙啊妙啊，日富一日"
    
    print(report)
    if config.get("feishu_webhook"):
        send_feishu_message(config["feishu_webhook"], report)


if __name__ == "__main__":
    main()
