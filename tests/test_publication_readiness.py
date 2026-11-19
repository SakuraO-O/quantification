"""Regression checks for dated readiness, partial failures and immutable publication."""
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from codex.trend_observer.dispatch import dispatch_morning_report
from codex.trend_observer.fundamental_sources import FundamentalSource, FundamentalSynchronizer
from codex.trend_observer.supabase_store import SupabaseSettings, SupabaseStore


class PublicationReadinessTest(unittest.TestCase):
    def test_publication_respects_explicit_closed_day_in_sparse_calendar(self):
        store = SupabaseStore(SupabaseSettings('https://example.invalid', 'test'))
        def calendar_rows(_table, **kwargs):
            if kwargs['filters'].get('trade_date') == 'eq.2026-11-19':
                return [{'is_trading_day': False}]
            return []
        store.select = Mock(side_effect=calendar_rows)
        now = datetime(2026, 11, 19, 22, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertEqual(store.last_completed_trading_date('CN', now).isoformat(), '2026-11-18')

    def test_latest_failed_morning_attempt_does_not_reuse_earlier_success(self):
        store = SupabaseStore(SupabaseSettings('https://example.invalid', 'test'))
        store.select = Mock(side_effect=[
            [{'batch_id': 'batch', 'tasks': {'sync-market:CN': 'succeeded'}}],
            [{'status': 'failed', 'dashboard_version_id': 'old-success'}],
        ])
        self.assertIsNone(store.ready_morning_version('2026-11-19'))
        self.assertEqual(store.select.call_count, 2)

    def test_unchanged_version_can_send_on_two_business_dates(self):
        store = Mock()
        store.ready_morning_version.return_value = {'dashboard_version_id': 'same', 'payload': {'assets': []}}
        store.claim_dispatch.return_value = True
        with patch('codex.trend_observer.dispatch.send_text_message', return_value=200):
            for day in (19, 20):
                result = dispatch_morning_report(store, datetime(2026, 11, day, 8, tzinfo=ZoneInfo('Asia/Shanghai')))
                self.assertEqual(result.status, 'succeeded')
        keys = [call.args[0] for call in store.claim_dispatch.call_args_list]
        self.assertEqual(keys, ['morning_report:2026-11-19', 'morning_report:2026-11-20'])

    def test_unknown_webhook_result_retains_claim_without_marking_retryable(self):
        store = Mock()
        store.ready_morning_version.return_value = {'dashboard_version_id': 'v', 'payload': {'assets': []}}
        store.claim_dispatch.return_value = True
        with patch('codex.trend_observer.dispatch.send_text_message', side_effect=requests.Timeout('unknown')):
            with self.assertRaisesRegex(RuntimeError, '结果未知'):
                dispatch_morning_report(store, datetime(2026, 11, 19, 8, tzinfo=ZoneInfo('Asia/Shanghai')))
        store.record_dispatch.assert_not_called()

    def test_listing_directory_is_requested_once_per_exchange(self):
        store = Mock()
        store.select.return_value = [
            {"security_id":"a", "symbol":"sh600938", "name":"中国海油"},
            {"security_id":"b", "symbol":"sh600941", "name":"中国移动"},
        ]
        store.upsert.return_value = [{"source_document_id":"official-listing"}]
        source = FundamentalSource(finance=lambda _:None, dividends=lambda _:None)
        directory = pd.DataFrame({"证券代码":["600938","600941"], "上市日期":["2022-04-21","2022-01-05"]})
        with patch("akshare.stock_info_sh_name_code", return_value=directory) as load:
            results = FundamentalSynchronizer(store,source).sync_listing_dates()
        self.assertEqual(load.call_count,1)
        self.assertTrue(all(result.status == "succeeded" for result in results))
        self.assertEqual(store.upsert.call_args.args[1]["source"], "sse")

    def test_listing_date_requires_matching_security_and_keeps_evidence(self):
        store = Mock()
        store.select.return_value = [{'security_id': 'stock', 'symbol': 'sh600938', 'name': '中国海油'}]
        store.upsert.return_value = [{'source_document_id': 'listing-document'}]
        source = FundamentalSource(finance=lambda _: None, dividends=lambda _: None)
        synchronizer = FundamentalSynchronizer(store, source)
        result = synchronizer.sync_listing_dates(loader=lambda _: pd.DataFrame({
            'item': ['股票代码', '上市时间'], 'value': ['600938', '20220421']}))
        self.assertEqual(result[0].status, 'succeeded')
        self.assertEqual(store.patch.call_args.args[2], {'listing_date': '2022-04-21', 'listing_source_document_id': 'listing-document'})
        store.patch.reset_mock()
        result = synchronizer.sync_listing_dates(loader=lambda _: pd.DataFrame({
            'item': ['股票代码', '上市时间'], 'value': ['000001', '20220421']}))
        self.assertEqual(result[0].status, 'failed')
        store.patch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
