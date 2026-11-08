from datetime import date
import json
import math
import numpy as np
import pandas as pd
from _bootstrap import ProjectTest
from data_quality import evaluate_quality,check_dataset_quality,parse_dates,infer_frequency
from dataset_io import import_dataset,write_canonical_csv,read_canonical_csv,load_dataset
from dataset_store import DatasetError,file_sha256


class QualityTests(ProjectTest):
    def test_missing_representations_and_zero_and_empty_policy(self):
        frame=pd.DataFrame({'x':pd.Series([None,float('nan'),pd.NA,'',0,'0'],dtype=object)})
        report=evaluate_quality(frame)
        column=report['missing']['columns']['x']
        self.assertEqual(column['count'],3);self.assertEqual(column['non_missing'],3)
        self.assertEqual(column['ratio'],0.5)
        for kind in ['none','nan','pandas_na','empty_string']:self.assertEqual(column['observed_representations'][kind],1)
        self.assertEqual(evaluate_quality(frame,empty_strings_are_missing=True)['missing']['total_missing_cells'],4)

    def test_full_duplicates_differ_from_composite_primary_key(self):
        frame=pd.DataFrame({'date':['2025-01-02']*3,'symbol':['A','A','B'],'value':[1,1,2]})
        report=evaluate_quality(frame,primary_key=['date'])
        self.assertEqual(report['duplicates']['full_rows']['duplicate_excess'],1)
        self.assertEqual(report['duplicates']['full_rows']['affected_rows'],2)
        self.assertEqual(report['duplicates']['primary_key']['duplicate_excess'],2)
        self.assertEqual(evaluate_quality(frame,primary_key=['date','symbol'])['duplicates']['primary_key']['duplicate_excess'],1)

    def test_iqr_and_zscore_have_independent_hand_checked_thresholds(self):
        report=evaluate_quality(pd.DataFrame({'x':[1,2,3,4,5,6,7,100]}),z_threshold=2)
        iqr=report['outliers']['x']['iqr'];z=report['outliers']['x']['z_score']
        self.assertEqual((iqr['q1'],iqr['q3'],iqr['upper']),(2.75,6.25,11.5))
        self.assertEqual(iqr['candidate_count'],1);self.assertEqual(iqr['candidate_ratio'],.125)
        self.assertEqual((z['mean'],z['std'],z['lower'],z['upper']),(16,34,-52,84))
        self.assertEqual(z['candidate_count'],1)

    def test_zero_std_insufficient_samples_and_non_numeric_rejection(self):
        self.assertEqual(evaluate_quality(pd.DataFrame({'x':[1]*8}))['outliers']['x']['z_score']['status'],'constant')
        self.assertEqual(evaluate_quality(pd.DataFrame({'x':[1,2,100]}))['outliers']['x']['iqr']['status'],'insufficient_samples')
        with self.assertRaises(DatasetError):evaluate_quality(pd.DataFrame({'text':['1','2']}),outlier_columns=['text'])

    def test_infinities_excluded_mixed_types_and_json_is_strict(self):
        frame=pd.DataFrame({'x':[1.,2,3,4,5,6,7,100,float('inf'),float('-inf')],
                            'mixed':pd.Series([1,'bad',None,3,'4',5,6,7,8,9],dtype=object)})
        report=evaluate_quality(frame,expected_types={'mixed':'number'})
        self.assertEqual(report['outliers']['x']['finite_count'],8)
        self.assertEqual(report['outliers']['x']['excluded_non_finite'],2)
        self.assertEqual(report['data_types']['x']['infinite_count'],2)
        self.assertTrue(report['data_types']['mixed']['mixed_python_types'])
        self.assertEqual(report['data_types']['mixed']['numeric_type_mismatch_count'],1)
        json.dumps(report,allow_nan=False)

    def test_dates_failure_descending_and_duplicate_are_separate(self):
        frame=pd.DataFrame({'d':['2025-01-03','bad','2025-01-02','2025-01-02']})
        report=evaluate_quality(frame,date_column='d')['time_series']
        self.assertEqual(report['parse_failure_count'],1);self.assertEqual(report['descending_pairs'],1)
        self.assertEqual(report['duplicate_date_excess'],1)
        self.assertIsNone(report['missing_trading_dates'])

    def test_supported_frequency_types_and_unknown_irregular(self):
        cases={
            'weekly':['2024-01-01','2024-01-08','2024-01-15','2024-01-22','2024-01-29'],
            'monthly':['2024-01-31','2024-02-29','2024-03-31','2024-04-30','2024-05-31'],
            'quarterly':['2023-03-31','2023-06-30','2023-09-30','2023-12-31','2024-03-31'],
            'yearly':['2020-12-31','2021-12-31','2022-12-31','2023-12-31','2024-12-31'],
            'irregular':['2020-01-01','2020-01-19','2020-05-07','2021-08-13'],
            'unknown':['2025-01-01','2025-01-02'],
        }
        for expected,values in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(infer_frequency(parse_dates(pd.Series(values)))['inferred'],expected)

    def test_weekends_and_holiday_are_not_asserted_missing_trading_days(self):
        dates=['2025-01-23','2025-01-24','2025-02-05','2025-02-06','2025-02-07','2025-02-10','2025-02-11','2025-02-12']
        temporal=evaluate_quality(pd.DataFrame({'d':dates}),date_column='d')['time_series']
        self.assertEqual(temporal['frequency']['inferred'],'daily')
        self.assertEqual(temporal['frequency']['calendar_status'],'unverified')
        self.assertIsNone(temporal['missing_trading_dates'])
        self.assertIn(12.0,temporal['possible_long_gaps_days'])

    def test_multi_stock_dates_are_checked_inside_explicit_groups(self):
        frame=pd.DataFrame({'d':['2025-01-02','2025-01-03']*2,'symbol':['A','A','B','B']})
        report=evaluate_quality(frame,date_column='d',group_by=['symbol'],primary_key=['symbol','d'])
        self.assertTrue(all(group['report']['duplicate_date_excess']==0 for group in report['time_series']['groups']))
        self.assertEqual(report['duplicates']['primary_key']['duplicate_excess'],0)

    def test_quality_report_persists_input_hash_and_changes_no_data(self):
        source=self.root/'input.csv';source.write_text('date,x\n2025-01-02,1\n2025-01-03,2\n')
        result=import_dataset(self.store,'input.csv');before=(self.root/result['current_file_path']).read_bytes()
        quality=check_dataset_quality(self.store,result['dataset_id'],expected_sha256=result['sha256'],date_column='date')
        saved=json.loads((self.root/quality['report_path']).read_text(encoding='utf8'))
        self.assertEqual(saved['input_sha256'],result['sha256'])
        self.assertEqual(quality['sha256'],file_sha256(self.root/quality['report_path']))
        self.assertEqual((self.root/result['current_file_path']).read_bytes(),before)

    def test_canonical_csv_preserves_null_empty_identifiers_and_mixed_scalar_values(self):
        frame=pd.DataFrame({'text':pd.Series(['',None,pd.NA,float('nan'),'NA','000001'],dtype=object),
                            'mixed':pd.Series([1,'1',True,date(2025,1,2),float('inf'),'尾'],dtype=object)})
        file=self.root/'canonical.csv';options=write_canonical_csv(frame,file)
        restored=read_canonical_csv(file,options)
        self.assertEqual(restored.iloc[0,0],'');self.assertIsNone(restored.iloc[1,0]);self.assertIs(restored.iloc[2,0],pd.NA)
        self.assertTrue(math.isnan(restored.iloc[3,0]));self.assertEqual(restored.iloc[5,0],'000001')
        self.assertEqual(type(restored.iloc[0,1]),int);self.assertEqual(type(restored.iloc[1,1]),str)
        self.assertEqual(restored.iloc[3,1],date(2025,1,2));self.assertTrue(math.isinf(restored.iloc[4,1]))

