import akshare as ak
import inspect

print("=== 搜索东方财富相关函数 ===\n")

# 搜索所有包含"index"和"value"或"pe"的函数
for name, obj in inspect.getmembers(ak):
    if callable(obj) and 'index' in name.lower():
        if 'value' in name.lower() or 'pe' in name.lower() or 'pb' in name.lower() or 'em' in name.lower():
            print(f"找到: {name}")

print("\n=== 搜索所有包含fund的估值函数 ===\n")
for name, obj in inspect.getmembers(ak):
    if callable(obj) and ('fund' in name.lower() and ('value' in name.lower() or 'pe' in name.lower() or 'valuation' in name.lower())):
        print(f"找到: {name}")

print("\n=== 尝试调用东方财富指数估值接口 ===\n")

# 尝试几个可能的接口
test_codes = ["000688", "000300", "000905"]

for code in test_codes:
    print(f"测试 {code}:")
    
    # 方法1: stock_zh_index_value_em
    try:
        print(f"  stock_zh_index_value_em: ", end="")
        df = ak.stock_zh_index_value_em(symbol=code)
        print(f"✅ 成功，共 {len(df)} 条")
        if len(df) > 0:
            print(f"     列名: {list(df.columns)}")
            print(f"     最新: {df.iloc[-1].to_dict()}")
    except Exception as e:
        print(f"❌ 失败: {str(e)}")
    
    # 方法2: index_zh_a_hist
    try:
        print(f"  index_zh_a_hist: ", end="")
        df = ak.index_zh_a_hist(symbol=code, period="monthly")
        print(f"✅ 成功，共 {len(df)} 条")
    except Exception as e:
        print(f"❌ 失败: {str(e)}")
    
    print()
