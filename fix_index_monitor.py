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

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"feishu_webhook": ""}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def fetch_with_retry(func, max_retries=3, *args, **kwargs):
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if i == max_retries - 1:
                raise e
            time.sleep(1)

def test_data_fetch():
    """测试数据获取功能"""
    print("=== 测试数据获取 ===")
    
    for idx_info in INDEX_LIST[:3]:  # 只测试前3个指数
        print(f"\n--- 测试 {idx_info['name']} ({idx_info['code']}) ---")
        try:
            # 测试行情数据
            if idx_info["market"] == "csi":
                print("方法1: stock_zh_index_daily_em...")
                try:
                    df1 = fetch_with_retry(ak.stock_zh_index_daily_em, symbol=f"sh{idx_info['code']}" if idx_info['code'].startswith("000") or idx_info['code'].startswith("0006") else f"sz{idx_info['code']}")
                    print(f"  ✅ 成功，共 {len(df1)} 条")
                    print(f"  最新: {df1.iloc[-1].to_dict()}")
                except Exception as e:
                    print(f"  ❌ 失败: {e}")
                
                print("方法2: stock_zh_index_hist_csindex...")
                try:
                    df2 = fetch_with_retry(ak.stock_zh_index_hist_csindex, symbol=idx_info['code'])
                    print(f"  ✅ 成功，共 {len(df2)} 条")
                    print(f"  最新: {df2.iloc[-1].to_dict()}")
                except Exception as e:
                    print(f"  ❌ 失败: {e}")
            
            # 测试估值数据
            print("估值数据: stock_zh_index_value_csindex...")
            try:
                df_val = fetch_with_retry(ak.stock_zh_index_value_csindex, symbol=idx_info['code'])
                print(f"  ✅ 成功，共 {len(df_val)} 条")
                print(f"  最新: {df_val.iloc[-1].to_dict()}")
                print(f"  列名: {list(df_val.columns)}")
            except Exception as e:
                print(f"  ❌ 失败: {e}")
                
        except Exception as e:
            print(f"  ❌ 总失败: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    test_data_fetch()
