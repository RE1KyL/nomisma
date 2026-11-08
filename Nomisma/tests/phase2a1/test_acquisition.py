from datetime import date
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pandas as pd
from support import ProjectTest
from akshare_adapter import fetch_hs300, _validate_sina_frame, _download_once
from akshare_registry import source_catalog, SINA_ID, SINA_COLUMNS, SourceStatus
from dataset_store import DatasetError, file_sha256, frame_schema, utc_now


class AcquisitionTests(ProjectTest):
    def frame(self):
        return pd.DataFrame({'date':[date(2025,1,2),date(2025,1,3),date(2025,1,6)],
                             'open':[10.0,11,12],'high':[12.0,13,14],'low':[9.0,10,11],
                             'close':[11.0,12,13],'volume':[100,200,300]})

    def download(self,frame=None):
        frame=self.frame() if frame is None else frame
        def mocked(store,target):
            frame.to_csv(target,index=False,mode='x',encoding='utf-8-sig')
            return {'fetched_at':utc_now(),'package_version':'1.20.18',
                    'function_name':'stock_zh_index_daily','signature':"(symbol: str = 'sh000922')",
                    'original_row_count':len(frame),'original_column_schema':frame_schema(frame),
                    'original_missing_counts':{name:int(value) for name,value in frame.isna().sum().items()}}
        return mocked

    def test_registry_verified_disabled_and_planned_enum(self):
        catalog=source_catalog();self.assertEqual(len(catalog),3)
        required={'provider','interface_name','function_name','status','supported_symbols','output_schema',
                  'package_version','verified_at','verification_status','notes'}
        self.assertTrue(all(required<=set(item) for item in catalog))
        self.assertEqual(sum(item['status']=='verified' for item in catalog),1)
        self.assertEqual(sum(item['status']=='disabled_candidate' for item in catalog),2)
        self.assertEqual(SourceStatus.PLANNED.value,'planned')

    def test_both_eastmoney_candidates_and_unregistered_source_never_execute(self):
        for interface in ['cn_equity_eastmoney_history','cn_equity_eastmoney_profile','arbitrary_function']:
            with self.subTest(interface=interface),patch('akshare_adapter._download_once') as download:
                with self.assertRaises(DatasetError):fetch_hs300(self.store,interface_name=interface)
                download.assert_not_called()

    def test_invalid_symbol_dates_and_retry_budget_never_execute(self):
        for options in [{'symbol':'sh000001'},{'symbol':'000300'},{'start_date':'2025-02-30'},
                        {'start_date':'20250101'},{'start_date':'2025-01-03','end_date':'2025-01-01'},
                        {'attempts':0},{'attempts':3},{'attempts':True}]:
            with self.subTest(options=options),patch('akshare_adapter._download_once') as download:
                with self.assertRaises(DatasetError):fetch_hs300(self.store,**options)
                download.assert_not_called()

    def test_date_filter_original_structure_hash_and_metadata_match_real_saved_files(self):
        with patch('akshare_adapter._download_once',side_effect=self.download()) as download:
            result=fetch_hs300(self.store,start_date='2025-01-03',end_date='2025-01-06')
        download.assert_called_once()
        self.assertEqual(result['row_count'],2);self.assertEqual(result['original_row_count'],3)
        original=pd.read_csv(self.root/result['original_file_path']);selected=pd.read_csv(self.root/result['current_file_path'])
        self.assertEqual(tuple(original.columns),SINA_COLUMNS)
        self.assertEqual(original['date'].tolist(),['2025-01-02','2025-01-03','2025-01-06'])
        self.assertEqual(selected['date'].tolist(),['2025-01-03','2025-01-06'])
        meta=self.store.metadata(result['dataset_id'])
        self.assertEqual(meta['row_count'],len(selected));self.assertEqual(meta['original_row_count'],len(original))
        self.assertEqual(meta['sha256'],file_sha256(self.root/result['current_file_path']))
        self.assertEqual(meta['original_sha256'],file_sha256(self.root/result['original_file_path']))
        self.assertEqual(meta['source_type'],'akshare');self.assertEqual(meta['source_name'],'sina')
        self.assertEqual(meta['source_function'],'stock_zh_index_daily')
        self.assertEqual(meta['request_parameters']['actual_function_parameters'],{'symbol':'sh000300'})
        self.assertIsNotNone(meta['fetched_at']);self.assertIsNotNone(meta['imported_at'])
        self.assertEqual(meta['data_start'],'2025-01-03');self.assertIsInstance(meta['package_version'],dict)

    def test_empty_upstream_empty_selection_and_schema_change_leave_no_dataset(self):
        for frame,options,code in [
            (self.frame().iloc[:0],{},'EMPTY_DATA'),
            (self.frame(),{'start_date':'2026-01-01'},'EMPTY_DATA'),
            (self.frame().drop(columns=['high']),{},'UPSTREAM_SCHEMA_CHANGED'),
        ]:
            with self.subTest(code=code),patch('akshare_adapter._download_once',side_effect=self.download(frame)):
                with self.assertRaises(DatasetError) as caught:fetch_hs300(self.store,**options)
                self.assertEqual(caught.exception.code,code)
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])
        self.assertFalse((self.root/'datasets/metadata').exists())

    def test_network_failure_not_replaced_with_another_source(self):
        with patch('akshare_adapter._download_once',side_effect=DatasetError('NETWORK_PROXY_ERROR','fixed')) as download:
            with self.assertRaises(DatasetError) as caught:fetch_hs300(self.store,attempts=2)
            self.assertEqual(caught.exception.code,'NETWORK_PROXY_ERROR');self.assertEqual(download.call_count,1)

    def test_only_transient_errors_retry_at_most_twice(self):
        real=self.download();counter=0
        def temporary(store,target):
            nonlocal counter
            counter+=1
            if counter==1:raise DatasetError('NETWORK_TIMEOUT','fixed')
            return real(store,target)
        with patch('akshare_adapter._download_once',side_effect=temporary),patch('akshare_adapter.time.sleep'):
            result=fetch_hs300(self.store,attempts=2)
            self.assertEqual(result['network_attempts'],2)
        with patch('akshare_adapter._download_once',side_effect=DatasetError('NETWORK_UNREACHABLE','fixed')) as download,patch('akshare_adapter.time.sleep'):
            with self.assertRaises(DatasetError):fetch_hs300(self.store,attempts=2)
            self.assertEqual(download.call_count,2)

    def test_worker_deadline_is_explicit_and_uses_no_shell(self):
        with self.store.transaction() as transaction:
            with patch('akshare_adapter.subprocess.run',side_effect=subprocess.TimeoutExpired('fixed',25)) as run:
                with self.assertRaises(DatasetError) as caught:_download_once(self.store,transaction.file('original.csv'))
                self.assertEqual(caught.exception.code,'NETWORK_TIMEOUT')
                self.assertEqual(run.call_args.kwargs['timeout'],25)
                self.assertFalse(run.call_args.kwargs.get('shell',False))
                self.assertFalse(any(key.upper() in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY') for key in run.call_args.kwargs['env']))

    def test_version_change_is_rejected_before_network(self):
        with patch('akshare_adapter.importlib.metadata.version',return_value='9.0.0'),patch('akshare_adapter._download_once') as download:
            with self.assertRaises(DatasetError) as caught:fetch_hs300(self.store)
            self.assertEqual(caught.exception.code,'VERSION_MISMATCH');download.assert_not_called()

    def test_missing_dates_infinite_values_and_nonframe_are_rejected(self):
        bad=self.frame();bad.loc[0,'date']=None
        with self.assertRaises(DatasetError):_validate_sina_frame(bad)
        bad=self.frame();bad.loc[0,'close']=float('inf')
        with self.assertRaises(DatasetError):_validate_sina_frame(bad)
        with self.assertRaises(DatasetError):_validate_sina_frame(None)

