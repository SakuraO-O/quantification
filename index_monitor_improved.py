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
HISTORY_DATA_DIR = "data/history"

MIN_HISTORY_YEARS = 5  # 最小需要5年历史数据
MIN_HISTORY_RECORDS = 5 * 252  # 约5年交易日数据
MIN_VALUATION_RECORDS = 5 * 12  # 至少需要5年 * 12个月 = 60条估值数据

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

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"feishu_webhook": ""}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def fetch_with_retry(func, max_retries=3, *args, **kwargs):
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception:
            if i == max_retries - 1:
                raise
            time.sleep(1)

def ensure_dir(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)

def save_valuation_history(idx_code, idx_name, valuation_data):
    """保存历史估值数据到本地文件"""
    if valuation_data is None or len(valuation_data) == 0:
        return
    
    ensure_dir(HISTORY_DATA_DIR)
    
    filename = os.path.join(HISTORY_DATA_DIR, f"valuation_{idx_code}.json")
    
    existing_data = {}
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except:
            existing_data = {"records": [], "last_update": ""}
    else:
        existing_data = {"records": [], "last_update": ""}
    
    for _, row in valuation_data.iterrows():
        record = {
            "date": str(row.get("日期", "")),
            "pe": row.get("市盈率1", row.get("市盈率2")),
            "pb": row.get("市净率1", row.get("市净率2")),
            "dividend": row.get("股息率1", row.get("股息率2"))
        }
        existing_data["records"].append(record)
    
    existing_data["last_update"] = datetime.now().strftime("%Y-%m-%d")
    existing_data["total_records"] = len(existing_data["records"])
    
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)
    
    print(f"  💾 已保存 {idx_name} 估值数据到 {filename}，共 {len(existing_data['records'])} 条")

def get_index_valuation_multi_source(code, market):
    """
    尝试多个数据源获取估值数据
    返回：(数据, 来源, 数据量是否足够)
    """
    all_sources = []
    
    # 方法1: 中证指数公司
    try:
        print(f"  尝试中证指数公司数据源...")
        df1 = fetch_with_retry(ak.stock_zh_index_value_csindex, symbol=code)
        if df1 is not None and len(df1) > 0:
            all_sources.append(("中证指数公司", df1))
            print(f"    ✅ 获取成功，共 {len(df1)} 条")
    except Exception as e:
        print(f"    ❌ 中证指数公司失败: {str(e)}")
    
    # 方法2: 东方财富 - 尝试获取更多历史数据
    try:
        print(f"  尝试东方财富数据源...")
        # 东方财富可能有更多历史数据
        df2 = ak.stock_zh_index_value_em(symbol=code)
        if df2 is not None and len(df2) > 0:
            all_sources.append(("东方财富", df2))
            print(f"    ✅ 获取成功，共 {len(df2)} 条")
    except AttributeError:
        # stock_zh_index_value_em 可能不存在
        print(f"    ⚠️ 东方财富接口不可用")
    except Exception as e:
        print(f"    ❌ 东方财富失败: {str(e)}")
    
    # 方法3: 理杏仁或其他源
    # 可以继续添加其他数据源
    
    # 选择最佳数据源
    if not all_sources:
        return None, "无", False
    
    # 优先选择数据量最多的源
    best_source = max(all_sources, key=lambda x: len(x[1]))
    source_name, df = best_source
    
    is_sufficient = len(df) >= MIN_VALUATION_RECORDS
    
    return df, source_name, is_sufficient

def get_csi_index_data(code):
    try:
        df = fetch_with_retry(ak.stock_zh_index_hist_csindex, symbol=code)
        return df
    except Exception:
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
    except Exception:
        return None

def format_value(val, decimals=2):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "None"
    if isinstance(val, str):
        return val
    return f"{val:.{decimals}f}"

def calculate_indicators(index_data, valuation_data, data_source="", data_sufficient=False):
    result = {
        "date": None,
        "close": "None",
        "ma10": "-",
        "ma20": "-",
        "ma60": "-",
        "historical_high": "None",
        "drop_from_high": "-",
        "pe": "None",
        "pe_percentile": "None",
        "pe_data_source": "",
        "pe_data_span": "",
        "pb": "None",
        "pb_percentile": "None",
        "errors": []
    }
    
    if index_data is None or len(index_data) == 0:
        result["errors"].append("指数行情数据获取失败")
        return result
    
    if len(index_data) < MIN_HISTORY_RECORDS:
        result["errors"].append(f"行情数据不足: 仅{len(index_data)}条，需要至少{MIN_HISTORY_RECORDS}条")
    
    try:
        latest = index_data.iloc[-1]
        result["date"] = latest.get("日期", latest.get("date"))
        if isinstance(result["date"], pd.Timestamp) or hasattr(result["date"], "strftime"):
            result["date"] = result["date"].strftime("%Y-%m-%d")
        
        close_col = "收盘" if "收盘" in latest else "close"
        if close_col in latest:
            result["close"] = format_value(float(latest[close_col]))
        
        if len(index_data) >= 10:
            ma10 = index_data[close_col].iloc[-10:].mean()
            result["ma10"] = format_value(ma10)
        if len(index_data) >= 20:
            ma20 = index_data[close_col].iloc[-20:].mean()
            result["ma20"] = format_value(ma20)
        if len(index_data) >= 60:
            ma60 = index_data[close_col].iloc[-60:].mean()
            result["ma60"] = format_value(ma60)
        
        high_col = "最高" if "最高" in index_data.columns else "high"
        if high_col in index_data.columns:
            historical_high = index_data[high_col].max()
            result["historical_high"] = format_value(historical_high)
            
            if result["close"] != "None" and not np.isnan(historical_high):
                close_val = float(result["close"])
                drop_pct = (historical_high - close_val) / historical_high * 100
                result["drop_from_high"] = format_value(drop_pct, 1)
        
    except Exception as e:
        result["errors"].append(f"行情指标计算失败: {str(e)}")
    
    # 估值数据处理
    if valuation_data is not None and len(valuation_data) > 0:
        result["pe_data_source"] = data_source
        
        # 检查数据量是否足够
        if not data_sufficient:
            result["errors"].append(f"估值数据不足: 仅{len(valuation_data)}条，需要至少{MIN_VALUATION_RECORDS}条（{MIN_HISTORY_YEARS}年）")
        else:
            # 数据量足够，可以计算百分位
            try:
                latest_val = valuation_data.iloc[-1]
                pe_col = None
                pb_col = None
                for col in valuation_data.columns:
                    if "市盈率" in str(col) or "PE" in str(col):
                        if pe_col is None:
                            pe_col = col
                    if "市净率" in str(col) or "PB" in str(col):
                        if pb_col is None:
                            pb_col = col
                
                if pe_col is not None:
                    pe_val = pd.to_numeric(latest_val[pe_col], errors="coerce")
                    if not np.isnan(pe_val):
                        result["pe"] = format_value(pe_val)
                        
                        all_pes = pd.to_numeric(valuation_data[pe_col], errors="coerce").dropna()
                        if len(all_pes) > 0:
                            percentile = (all_pes < pe_val).mean() * 100
                            result["pe_percentile"] = format_value(percentile)
                            
                            if len(valuation_data) >= 1:
                                start_date = valuation_data.iloc[0]["日期"] if "日期" in valuation_data.columns else valuation_data.iloc[0].get("date")
                                if isinstance(start_date, pd.Timestamp) or hasattr(start_date, "strftime"):
                                    start_date = start_date.strftime("%Y-%m-%d")
                                try:
                                    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                                except Exception:
                                    try:
                                        start_dt = datetime.strptime(start_date, "%Y%m%d")
                                    except Exception:
                                        start_dt = datetime.now()
                                
                                days_span = (datetime.now() - start_dt).days
                                years = days_span // 365
                                months = (days_span % 365) // 30
                                if years > 0:
                                    result["pe_data_span"] = f"({years}年{months}个月)"
                                else:
                                    result["pe_data_span"] = f"({months}个月)"
                
                if pb_col is not None:
                    pb_val = pd.to_numeric(latest_val[pb_col], errors="coerce")
                    if not np.isnan(pb_val):
                        result["pb"] = format_value(pb_val)
                        
                        all_pbs = pd.to_numeric(valuation_data[pb_col], errors="coerce").dropna()
                        if len(all_pbs) > 0:
                            percentile = (all_pbs < pb_val).mean() * 100
                            result["pb_percentile"] = format_value(percentile)
            except Exception as e:
                result["errors"].append(f"估值指标计算失败: {str(e)}")
    else:
        result["errors"].append("估值数据获取失败")
    
    return result

def check_conditions(indicators, index_name):
    signals = {
        "valuation": None,
        "ma": None,
        "is_error": False,
        "error_fields": []
    }
    
    if len(indicators["errors"]) > 0:
        signals["is_error"] = True
        signals["error_fields"] = indicators["errors"]
        return signals
    
    pe_pct = None
    if indicators["pe_percentile"] != "None":
        try:
            pe_pct = float(indicators["pe_percentile"])
        except Exception:
            pass
    
    if pe_pct is not None:
        if 0 <= pe_pct <= 7:
            signals["valuation"] = "📉跌幅进入最后一击"
        elif 7 < pe_pct <= 13:
            signals["valuation"] = "📉跌幅进入击球区深处"
        elif 13 < pe_pct <= 20:
            signals["valuation"] = "📉跌幅进入击球区"
        elif 20 < pe_pct <= 25:
            signals["valuation"] = "📉跌幅进入观察区"
        elif 65 < pe_pct <= 75:
            signals["valuation"] = "📈涨幅进入警示区"
        elif 75 < pe_pct <= 83:
            signals["valuation"] = "📈涨幅考虑卖出一小网"
        elif 83 < pe_pct <= 90:
            signals["valuation"] = "📈涨幅考虑卖出一中网"
        elif 90 < pe_pct <= 95:
            signals["valuation"] = "📈涨幅考虑卖出一中网"
        elif 95 < pe_pct <= 100:
            signals["valuation"] = "📈涨幅考虑卖出全部"
    
    if indicators["close"] != "None" and indicators["ma10"] != "-" and indicators["ma20"] != "-" and indicators["ma60"] != "-":
        try:
            close = float(indicators["close"])
            ma10 = float(indicators["ma10"])
            ma20 = float(indicators["ma20"])
            ma60 = float(indicators["ma60"])
            
            ma_sigs = []
            if close < ma10 and close > ma20 and close > ma60:
                ma_sigs.append("⚠️注意下跌可能")
            elif close < ma10 and close < ma20 and close > ma60:
                ma_sigs.append("⚠️下跌趋势形成中")
            elif close < ma10 and close < ma20 and close < ma60:
                ma_sigs.append("⚠️下跌趋势确认")
            elif close > ma10 and close < ma20 and close < ma60:
                ma_sigs.append("⚠️注意上涨可能")
            elif close > ma10 and close > ma20 and close < ma60:
                ma_sigs.append("🌟上涨趋势形成中")
            elif close > ma10 and close > ma20 and close > ma60:
                ma_sigs.append("🌟上涨趋势确认")
            
            if ma_sigs:
                signals["ma"] = " ".join(ma_sigs)
        
        except Exception:
            pass
    
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
    except Exception:
        return False

def save_daily_data(date_str, all_data):
    ensure_dir(DATA_DIR)
    filename = os.path.join(DATA_DIR, f"index_data_{date_str}.json")
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

def main():
    try:
        config = load_config()
        today = datetime.now()
        
        print(f"📅 今日日期: {today.strftime('%Y-%m-%d')}")
        print(f"📊 最小历史数据要求: {MIN_HISTORY_YEARS}年 ({MIN_VALUATION_RECORDS}条)\n")
        
        all_data = []
        under_valuation = []
        over_valuation = []
        ma_signals = []
        error_indices = []
        
        for idx_info in INDEX_LIST:
            name = idx_info["name"]
            code = idx_info["code"]
            print(f"\n处理: {name} ({code})")
            
            try:
                if idx_info["market"] == "csi":
                    index_data = get_csi_index_data(code)
                else:
                    index_data = get_hk_index_data(code)
                
                # 获取估值数据（尝试多个数据源）
                valuation_data, data_source, data_sufficient = get_index_valuation_multi_source(code, idx_info["market"])
                
                indicators = calculate_indicators(index_data, valuation_data, data_source, data_sufficient)
                
                # 在获取数据后保存历史数据（无论数据是否足够）
                if valuation_data is not None and len(valuation_data) > 0:
                    save_valuation_history(code, name, valuation_data)
                
                record = {
                    "name": name,
                    "code": code,
                    **indicators
                }
                all_data.append(record)
                
                signals = check_conditions(indicators, name)
                
                if signals["is_error"]:
                    error_indices.append((record, signals))
                    print(f"  ⚠️ {name} 存在异常: {signals['error_fields']}")
                else:
                    if signals["valuation"]:
                        if "📉" in signals["valuation"]:
                            under_valuation.append((record, signals))
                        elif "📈" in signals["valuation"]:
                            over_valuation.append((record, signals))
                    if signals["ma"] and not signals["valuation"]:
                        ma_signals.append((record, signals))
            
            except Exception as e:
                import traceback
                error_record = {
                    "name": idx_info["name"],
                    "code": idx_info["code"],
                    "errors": [str(e)]
                }
                error_indices.append((error_record, {"is_error": True, "error_fields": [str(e)]}))
                all_data.append(error_record)
                print(f"  ❌ 处理失败: {str(e)}")
                print(f"  详细错误: {traceback.format_exc()}")
        
        save_daily_data(today.strftime("%Y-%m-%d"), all_data)
        
        # 生成报告
        total_triggers = len(under_valuation) + len(over_valuation) + len(ma_signals) + len(error_indices)
        
        if total_triggers > 0:
            report = f"📊A股指数每日监控报告 | 统计日期：{today.strftime('%Y-%m-%d')}\n"
            report += f"📌触发提醒总数量：{total_triggers} 个丨估值提醒：{len(under_valuation)+len(over_valuation)}个，{len(under_valuation)}个低估，{len(over_valuation)}个高估\n\n"
            
            if under_valuation:
                report += f"📉低估信号：{len(under_valuation)}个\n"
                for rec, sig in under_valuation:
                    pe_pct = rec.get('pe_percentile', '')
                    pe_span = rec.get('pe_data_span', '')
                    pe_src = rec.get('pe_data_source', '')
                    report += f"{rec['name']} ({rec['code']})收盘价：{rec['close']} | PE：{rec['pe']}丨{pe_pct}百分位{pe_span}【{pe_src}】 | 距历史高点跌幅：{rec['drop_from_high']}% {sig['valuation']}\n"
                report += "\n"
            
            if over_valuation:
                report += f"📈高估信号：{len(over_valuation)}个\n"
                for rec, sig in over_valuation:
                    pe_pct = rec.get('pe_percentile', '')
                    pe_span = rec.get('pe_data_span', '')
                    pe_src = rec.get('pe_data_source', '')
                    report += f"{rec['name']} ({rec['code']})收盘价：{rec['close']} | PE：{rec['pe']}丨{pe_pct}百分位{pe_span}【{pe_src}】 | 距历史高点跌幅：{rec['drop_from_high']}% {sig['valuation']}\n"
                report += "\n"
            
            if ma_signals:
                report += f"⚠️均线趋势信号：{len(ma_signals)}个\n"
                for rec, sig in ma_signals:
                    report += f"{rec['name']} ({rec['code']})收盘价：{rec['close']} | 10日均线：{rec['ma10']} | 20日均线：{rec['ma20']} | 60日均线：{rec['ma60']} {sig['ma']}\n"
                report += "\n"
            
            if error_indices:
                report += f"⚠️数据异常：{len(error_indices)}个\n"
                for rec, sig in error_indices:
                    error_desc = "、".join(sig["error_fields"])
                    report += f"{rec['name']} ({rec['code']})：{error_desc}\n"
                report += "\n"
            
            report += "日富一日，今天也要元气满满，秒啊妙啊🍻"
        else:
            report = f"📊A股指数每日监控报告 | 统计日期：{today.strftime('%Y-%m-%d')}\n"
            report += "✅所有监控指数运行平稳，无任何估值、均线条件触发提醒\n"
            report += "日富一日，今天也要元气满满，秒啊妙啊🍻"
        
        print("\n" + "="*80)
        print(report)
        print("="*80)
        
        if config.get("feishu_webhook"):
            send_feishu_message(config["feishu_webhook"], report)
            print("✅ 飞书通知已发送")
    
    except Exception as e:
        print(f"❌ 执行异常: {e}")
        import traceback
        traceback.print_exc()
        try:
            config = load_config()
            if config.get("feishu_webhook"):
                send_feishu_message(config["feishu_webhook"], "⚠️ 监控任务执行异常")
        except Exception:
            pass

if __name__ == "__main__":
    main()
