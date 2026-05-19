#!/usr/bin/env python3
import json
import requests

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
        print(f"📤 响应内容: {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ 发送失败: {e}")
        return False

if __name__ == "__main__":
    config = load_config()
    
    # 尝试加载最新的数据文件
    import os
    from datetime import datetime
    today = datetime.now()
    data_file = f"data/index_data_{today.strftime('%Y-%m-%d')}.json"
    
    if os.path.exists(data_file):
        print(f"✅ 找到今日数据文件: {data_file}")
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"✅ 数据包含 {len(data)} 个指数")
        
        # 构建一个简单的测试报告
        report = f"📊 指数监控测试通知\n📅 日期: {today.strftime('%Y-%m-%d')}\n✅ 监控系统运行正常！\n\n日富一日，今天也要元气满满，秒啊妙啊🍻"
    else:
        report = f"📊 指数监控通知\n📅 日期: {today.strftime('%Y-%m-%d')}\n⚠️ 正在重新获取实时数据...\n\n日富一日，今天也要元气满满，秒啊妙啊🍻"
    
    print(f"📄 报告内容:\n{report}\n")
    success = send_feishu_message(config["feishu_webhook"], report)
    if success:
        print("✅ 飞书通知发送成功！")
    else:
        print("❌ 飞书通知发送失败！")
