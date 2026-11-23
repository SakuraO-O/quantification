import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from codex.trend_observer.verification import (
    compare_reference, parse_wind_receipt, WindReference, run_verification_worker,
    run_publish_request,
)
from codex.trend_observer.review_dependencies import bind_dependencies
from codex.trend_observer.quarterly import build_quarterly


def candidate():
    return dict(kind='financial',symbol='sh600000',report_period='2024-12-31',metric_code='net_profit',
                unit='CNY',period_type='annual',value='4218000000.123456',metadata_complete=True,
                source='eastmoney',fact_hash='fact-v1',content_hash='source-v1',review_data={})


def reference():
    return dict(wind_code='600000.SH',report_period='2024-12-31',unit='CNY',currency='CNY',
                period_type='annual',revision_basis='latest_available',value='4218000000.123456',source='Wind')


def receipt(value='42.18'):
    return {'content':[{'text':json.dumps({'data':{'data':[{'columns':[{'name':'Wind代码'},
        {'name':'2024年度归母净利润','unit':'亿元','currency':'CNY','revision_basis':'最新调整后'}],
        'rows':[['600000.SH',value]]}]}})}]}


class VerificationTest(unittest.TestCase):
    def test_equal_decimal_sources_verify_without_rounding_candidate(self):
        item=candidate(); result=compare_reference(item,reference())
        self.assertEqual(result['status'],'auto');self.assertEqual(item['value'],'4218000000.123456')
        self.assertEqual(result['reference']['difference'],'0.000000')

    def test_missing_identity_metadata_and_nonfinite_values_do_not_verify(self):
        for key in ('wind_code','report_period','unit','currency','period_type','revision_basis'):
            ref=reference();ref.pop(key)
            self.assertEqual(compare_reference(candidate(),ref)['status'],'basis',key)
        for value in (None,'NaN','Infinity','-Infinity'):
            self.assertEqual(compare_reference(candidate(),dict(reference(),value=value))['status'],'failed')
        self.assertEqual(compare_reference(dict(candidate(),source='Wind'),reference())['status'],'basis')

    def test_rounding_requires_receipt_precision_not_percent_tolerance(self):
        ref=dict(reference(),value='4218000000',tolerance='500000',precision_evidence={'unit':'亿元','display_value':'42.18'})
        self.assertEqual(compare_reference(candidate(),ref)['status'],'auto')
        ref.pop('precision_evidence');self.assertEqual(compare_reference(candidate(),ref)['status'],'diff')
        ref.update(value='0');result=compare_reference(candidate(),ref)
        self.assertIsNone(result['reference']['difference_ratio'])

    def test_receipt_metadata_is_not_invented_from_request(self):
        raw=receipt();ref=parse_wind_receipt(raw,candidate())
        self.assertEqual(Decimal(ref['value']),Decimal('4218000000'))
        self.assertEqual(Decimal(ref['tolerance']),Decimal('500000'))
        body=json.loads(raw['content'][0]['text']);column=body['data']['data'][0]['columns'][1]
        column.pop('currency');column.pop('revision_basis');raw['content'][0]['text']=json.dumps(body)
        ref=parse_wind_receipt(raw,candidate())
        self.assertIsNone(ref['currency']);self.assertIsNone(ref['revision_basis'])
        body['data']['data'][0]['rows']*=2;raw['content'][0]['text']=json.dumps(body)
        with self.assertRaisesRegex(ValueError,'多条'):parse_wind_receipt(raw,candidate())

    def test_cached_period_is_shared_but_retry_fetches_new_evidence(self):
        with TemporaryDirectory() as temp:
            runner=Mock(return_value=SimpleNamespace(returncode=0,stdout=json.dumps(receipt())))
            provider=WindReference(Path(temp)/'bin/cli.js',Path(temp)/'cache',runner=runner)
            first=provider(candidate());provider(dict(candidate(),fact_hash='another-fact'))
            self.assertEqual(runner.call_count,1);self.assertEqual(len(first['receipt_sha256']),64)
            provider(dict(candidate(),attempt_seq=2));self.assertEqual(runner.call_count,2)

    def test_worker_passes_attempt_and_stops_on_source_failure(self):
        store=Mock();item=candidate();job=dict(job_id='j',attempt_seq=3,lease_token='lease',item=item)
        store.rpc.side_effect=[None,None,job,None]
        provider=Mock(side_effect=RuntimeError('授权失效'));provider.pause_on_failure=False
        self.assertEqual(run_verification_worker(store,provider,limit=10),1)
        result=store.rpc.call_args_list[-1].args
        self.assertEqual(result[0],'review_complete');self.assertEqual(result[1]['p_attempt'],3)
        self.assertEqual(result[1]['p_result']['status'],'failed');provider.assert_called_once()

    def test_publish_failure_is_not_reported_as_success(self):
        store=Mock();store.rpc.return_value={'attempt_seq':2,'lease_token':'lease'}
        publisher=Mock();publisher.publish.side_effect=RuntimeError('输入版本已变化')
        with self.assertRaises(RuntimeError):run_publish_request(store,'job',2,publisher)
        self.assertEqual(store.rpc.call_args.args[1]['p_result']['status'],'failed')

    def test_dependencies_include_previous_cumulative_period_and_yoy(self):
        docs=[{'source_document_id':'doc','document_type':'annual_report','report_period':'2024-12-31'}]
        facts=[dict(report_period=p,metric_code='net_profit',value=v,period_type=b,
                    source_document_id='doc',is_current=True,verification_status='verified')
               for p,v,b in [('2024-09-30','100','year_to_date'),('2024-12-31','150','annual')]]
        payload=bind_dependencies(build_quarterly(facts,[],docs,[]),'sid',[])
        column=payload['metrics']['net_profit']['years'][-1]
        self.assertEqual(column['quarters'][3]['value'],50)
        self.assertIn('financial:sid:2024-09-30:net_profit',column['quarters'][3]['review_dependencies'])
        self.assertIn('financial:sid:2023-12-31:net_profit',column['comparison']['review_dependencies'])

    def test_dividend_identity_and_annual_scope_cannot_be_guessed(self):
        item=dict(candidate(),kind='dividend',metric_code='cash_dividend_per_share',value='1.2',unit='CNY/share',period_type='event',fiscal_year=2024,
          fields={'plan_key':'2024-final','event_stage':'implemented','ex_date':'2025-06-01','approval_basis':None,
                  'cash_dividend_total':'100','bonus_shares_per_share':'0','transfer_shares_per_share':'0'})
        ref=dict(reference(),value='1.2',unit='CNY/share',period_type='event',fiscal_year=2024,**item['fields'])
        self.assertEqual(compare_reference(item,ref)['status'],'auto')
        for key in ('plan_key','ex_date','fiscal_year'):
            broken=deepcopy(ref);broken.pop(key);self.assertEqual(compare_reference(item,broken)['status'],'basis')


if __name__=='__main__':unittest.main()
