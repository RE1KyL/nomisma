import json,os,subprocess
from unittest.mock import patch
import numpy as np
import pandas as pd
from _bootstrap import AnalysisTest
from finance_common import publish_finance
from finance_returns import analyze_returns
from finance_risk import analyze_risk
from finance_timeseries import rolling_statistics
from analysis_store import read_analysis,save_analysis
from analysis_result import make_analysis_result
from dataset_store import ProjectDatasetStore


class FinanceSafetyTests(AnalysisTest):
    def fixture(self):
        source=self.dataset(pd.DataFrame({'date':['2025-01-01','2025-01-10','2025-01-20'],'price':[100.,110.,99.]}))
        return analyze_returns(self.store,source.metadata['dataset_id'],expected_sha256=source.metadata['sha256'],date_column='date',
            price_column='price',frequency='irregular',price_adjustment='unknown')

    def test_calendar_gaps_are_visible_and_not_annualized(self):
        a=self.fixture();self.assertEqual(a.tables['returns'].elapsed_calendar_days.iloc[1:].tolist(),[9.,10.])
        source=a.sample.metadata
        self.code('INVALID_FINANCIAL_CONVENTION',lambda:analyze_returns(self.store,source['dataset_id'],expected_sha256=source['sha256'],
            date_column='date',price_column='price',frequency='irregular',periods_per_year=252,price_adjustment='unknown'))

    def test_modified_result_input_or_table_cannot_be_published(self):
        a=self.fixture();a.tables['returns'].loc[1,'return']=888
        self.code('STALE_ANALYSIS',lambda:publish_finance(self.store,a))
        a=self.fixture();source=self.store.path(a.sample.metadata['current_file_path']);source.write_bytes(source.read_bytes()+b'\n')
        self.code('STALE_DATASET',lambda:publish_finance(self.store,a))

    def test_existing_result_unsafe_namespace_and_disguised_method_are_rejected(self):
        a=self.fixture();receipt=publish_finance(self.store,a);result,_=read_analysis(self.store,receipt['data']['analysis_id'],namespace='finance')
        self.code('FILE_EXISTS',lambda:save_analysis(self.store,result))
        self.code('INVALID_PATH',lambda:read_analysis(self.store,result['analysis_id'],namespace='../raw'))
        result['analysis_domain']='shared';result['method']='describe'
        self.code('ANALYSIS_INVALID',lambda:save_analysis(self.store,result))

    def test_same_mode_cross_project_identity_is_checked(self):
        import shutil
        a=self.fixture();receipt=publish_finance(self.store,a);identity=receipt['data']['analysis_id']
        another=self.base/'another_finance';another.mkdir()
        registry=json.loads(self.registry.read_text());registry.append({'id':'another_finance','mode':'finance','status':'active','path':str(another)})
        self.registry.write_text(json.dumps(registry),encoding='utf8')
        (another/'analyses').mkdir();(another/'analyses/finance').mkdir()
        shutil.copytree(self.root/'analyses/finance'/identity,another/'analyses/finance'/identity)
        with patch.dict(os.environ,{'NOMISMA_PROJECT_ID':'another_finance','NOMISMA_WORKSPACE':str(another)}):
            other=ProjectDatasetStore();self.code('PROJECT_CONTEXT_INVALID',lambda:read_analysis(other,identity,namespace='finance'))

    def test_round_trip_failure_cannot_commit_finance_metadata(self):
        a=self.fixture();r=make_analysis_result(a.sample,a.computation)
        with patch('analysis_store.read_canonical_csv',return_value=pd.DataFrame({'wrong':[1]})):
            self.code('ROUNDTRIP_MISMATCH',lambda:save_analysis(self.store,r,tables={'check':pd.DataFrame({'value':[1.]})}))
        self.assertFalse((self.root/'analyses/finance'/r['analysis_id']).exists())
        self.assertEqual(list((self.root/'analyses/finance/.staging').iterdir()),[])

    def test_real_finance_junction_is_rejected(self):
        if os.name!='nt':self.skipTest('Windows junction test')
        a=self.fixture();(self.root/'analyses').mkdir();link=self.root/'analyses/finance'
        subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
            'New-Item -ItemType Junction -Path $env:NMS_TEST_LINK -Target $env:NMS_TEST_TARGET -ErrorAction Stop | Out-Null'],
            env={**os.environ,'NMS_TEST_LINK':str(link),'NMS_TEST_TARGET':str(self.other)},check=True,capture_output=True)
        try:self.code('LINK_NOT_ALLOWED',lambda:publish_finance(self.store,a));self.assertEqual(list(self.other.iterdir()),[])
        finally:link.rmdir()

    def test_log_underflow_and_rolling_numeric_failure_are_not_hidden_nulls(self):
        source=self.dataset(pd.DataFrame({'date':['2025-01-01','2025-01-02'],'r':[-1000.,.1]}))
        self.code('NUMERICAL_FAILURE',lambda:analyze_risk(self.store,source.metadata['dataset_id'],expected_sha256=source.metadata['sha256'],
            date_column='date',return_column='r',return_kind='log',frequency='daily',metrics=['max_drawdown']))
        source=self.dataset(pd.DataFrame({'date':['2025-01-01','2025-01-02','2025-01-03'],'x':[1e200,-1e200,1e200]}))
        self.code('NUMERICAL_FAILURE',lambda:rolling_statistics(self.store,source.metadata['dataset_id'],expected_sha256=source.metadata['sha256'],
            date_column='date',value_column='x',frequency='daily',value_kind='measurement',window=2,min_periods=2,statistic='std'))

    def test_very_small_tail_probability_remains_worst_observation(self):
        source=self.dataset(pd.DataFrame({'date':pd.date_range('2025-01-01',periods=20),'r':[-.1]*19+[-.2]}))
        a=analyze_risk(self.store,source.metadata['dataset_id'],expected_sha256=source.metadata['sha256'],date_column='date',
            return_column='r',return_kind='simple',frequency='daily',metrics=['es'],alpha_loss=1-1e-15)
        self.assertAlmostEqual(a.computation['assets'][0]['es']['loss'],.2)
