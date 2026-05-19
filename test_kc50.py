import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def fetch_with_retry(func, max_retries=3, *args, **kwargs):
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if i == max_retries - 1:
                raise e
            import time
            time.sleep(1)

print("=== 测试科创50数据 ===\n")

# 获取行情数据
print("1. 获取科创50行情数据...")
try:
    df_kline = fetch_with_retry(ak.stock_zh_index_hist_csindex, symbol="000688")
    print(f"   ✅ 成功，共 {len(df_kline)} 条")
    print(f"   最新数据: {df_kline.iloc[-1].to_dict()}")
except Exception as e:
    print(f"   ❌ 失败: {e}")

# 获取估值数据
print("\n2. 获取科创50估值数据...")
try:
    df_val = fetch_with_retry(ak.stock_zh_index_value_csindex, symbol="000688")
    print(f"   ✅ 成功，共 {len(df_val)} 条")
    print(f"   列名: {list(df_val.columns)}")
    print(f"   最新估值: {df_val.iloc[-1].to_dict()}")
    
    # 计算PE百分位
    pe_col = None
    for col in df_val.columns:
        if "市盈率" in str(col) or "PE" in str(col):
            pe_col = col
            break
    
    if pe_col:
        print(f"\n3. 分析PE数据 (列: {pe_col})...")
        all_pes = pd.to_numeric(df_val[pe_col], errors="coerce").dropna()
        print(f"   有效PE数据: {len(all_pes)} 条")
        latest_pe = all_pes.iloc[-1]
        print(f"   最新PE: {latest_pe}")
        
        percentile = (all_pes < latest_pe).mean() * 100
        print(f"   当前PE百分位: {percentile:.1f}%")
        print(f"   PE最小值: {all_pes.min():.2f}, 最大值: {all_pes.max():.2f}, 平均值: {all_pes.mean():.2f}")
        
        print(f"\n   PE数据样本:")
        print(f"   {all_pes.tail(10).to_string()}")
        
        # 判断高估/低估
        if percentile <= 25:
            print(f"\n   📉 判断: 低估 (百分位 {percentile:.1f}% <= 25%)")
        elif percentile >= 75:
            print(f"\n   📈 判断: 高估 (百分位 {percentile:.1f}% >= 75%)")
        else:
            print(f"\n   ➡️ 判断: 正常 (百分位 {percentile:.1f}%)")
except Exception as e:
    print(f"   ❌ 失败: {e}")
    import traceback
    traceback.print_exc()
