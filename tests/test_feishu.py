import unittest

from codex.trend_observer.feishu import format_feishu_message


class FeishuTest(unittest.TestCase):
    def test_message_format_and_stock_filter(self):
        snapshot = {
            "generated_at": "2026-06-20 08:00",
            "assets": [
                {
                    "name": "沪深300",
                    "symbol": "000300",
                    "date": "2026-06-19",
                    "close": 4777.32,
                    "short_trend": "短期震荡",
                    "mid_trend": "中期上升",
                    "long_trend": "长期上升",
                    "overall_status": "健康上升",
                    "asset_type": "指数",
                    "pe": 14.42,
                    "pe_percentile": 77.82,
                    "valuation_status": "偏高",
                    "error": "",
                },
                {
                    "name": "高息股票",
                    "symbol": "sh000001",
                    "date": "2026-06-19",
                    "close": 10.0,
                    "short_trend": "短期强势",
                    "mid_trend": "中期上升",
                    "long_trend": "长期上升",
                    "overall_status": "强趋势",
                    "asset_type": "股票",
                    "dividend_yield": 5.1,
                    "error": "",
                },
                {
                    "name": "中性股票",
                    "symbol": "sh000002",
                    "date": "2026-06-19",
                    "close": 10.0,
                    "short_trend": "短期震荡",
                    "mid_trend": "中期上升",
                    "long_trend": "长期上升",
                    "overall_status": "健康上升",
                    "asset_type": "股票",
                    "dividend_yield": 4.0,
                    "error": "",
                },
                {
                    "name": "低息股票",
                    "symbol": "sh000003",
                    "date": "2026-06-19",
                    "close": 10.0,
                    "short_trend": "短期转弱",
                    "mid_trend": "中期下跌",
                    "long_trend": "长期下跌",
                    "overall_status": "下跌通道",
                    "asset_type": "股票",
                    "dividend_yield": 2.9,
                    "error": "",
                },
            ],
        }
        message = format_feishu_message(snapshot)
        self.assertIn("妙啊 | 趋势观察报告 | 2026-06-20 08:00", message)
        self.assertIn("沪深300（000300）｜2026-06-19｜收盘 4777.32", message)
        self.assertIn("综合：✅健康上升", message)
        self.assertIn("估值：PE 14.42｜百分位 77.82%｜偏高", message)
        self.assertIn("高息股票", message)
        self.assertIn("股息率：5.10%", message)
        self.assertIn("低息股票", message)
        self.assertIn("综合：❌下跌通道", message)
        self.assertNotIn("中性股票", message)


if __name__ == "__main__":
    unittest.main()

