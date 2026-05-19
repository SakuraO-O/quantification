import akshare as ak
import pandas as pd
import numpy as np

def fetch_with_retry(func, max_retries=3, *args, **kwargs):
    import time
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if i == max_retries - 1:
                raise e
            time.sleep(1)

print("=== 测试所有可能的估值数据源 ===\n")

# 方法1: 我们当前在用的
print("1. stock_zh_index_value_csindex (当前方法)...")
try:
    df1 = fetch_with_retry(ak.stock_zh_index_value_csindex, symbol="000688")
    print(f"   ✅ 成功，共 {len(df1)} 条")
    print(f"   列名: {list(df1.columns)}")
    if len(df1) > 0:
        print(f"   日期范围: {df1.iloc[0]['日期']} 至 {df1.iloc[-1]['日期']}")
except Exception as e:
    print(f"   ❌ 失败: {e}")

# 方法2: 乐咕的估值数据
print("\n2. stock_index_pe_lg (乐咕)...")
try:
    df2 = fetch_with_retry(ak.stock_index_pe_lg, symbol="科创50")
    print(f"   ✅ 成功，共 {len(df2)} 条")
    print(f"   列名: {list(df2.columns)}")
    if len(df2) > 0:
        print(f"   最后5条:")
        print(df2.tail())
except Exception as e:
    print(f"   ❌ 失败: {e}")
    try:
        df2 = fetch_with_retry(ak.stock_index_pe_lg, symbol="000688")
        print(f"   ✅ 用代码成功，共 {len(df2)} 条")
    except Exception as e2:
        print(f"   ❌ 重试也失败: {e2}")

# 方法3: 尝试其他指数看看
print("\n3. 尝试沪深300对比...")
try:
    df3 = fetch_with_retry(ak.stock_zh_index_value_csindex, symbol="000300")
    print(f"   ✅ 沪深300估值数据: {len(df3)} 条")
    if len(df3) > 0:
        print(f"   日期范围: {df3.iloc[0]['日期']} 至 {df3.iloc[-1]['日期']}")
except Exception as e:
    print(f"   ❌ 失败: {e}")

# 方法4: 看看有没有直接获取PE历史的其他方式
print("\n4. 搜索其他可能的函数...")
try:
    import inspect
    funcs = [name for name, _ in inspect.getmembers(ak) if callable(_) and 'index' in name.lower() and ('pe' in name.lower() or 'value' in name.lower())]
    print(f"   找到的可能函数: {funcs}")
except Exception as e:
    print(f"   ❌ 搜索失败: {e}")
