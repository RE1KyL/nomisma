import asyncio,hashlib,json,os,sys,unittest,uuid
from unittest.mock import patch
import pandas as pd
from _bootstrap import AnalysisTest,HERE
from dataset_store import DatasetError,ProjectDatasetStore,file_sha256
from dataset_io import load_dataset
from mcp_contract import SPECS,validate,bounded
from mcp_jobs import run_job,inputs,lock,read_json,write_json,recover,changed_files,SnapshotStore,validate_candidate
from mcp_operations import execute
from analysis_result import encode_result
from jsonschema import Draft202012Validator


class IntegrationTests(AnalysisTest):
    def call(self,short,source=None,parameters=None,**extra):
        name='nomisma_'+short
        args={'parameters':parameters or {},'request_id':str(uuid.uuid4()),**extra}
        if source is not None:args['dataset']={'dataset_id':source.metadata['dataset_id'],'sha256':source.metadata['sha256']}
        if short=='akshare_catalog':args={}
        if short=='dataset_inspect':args.pop('request_id')
        validate(name,args,self.store.context['mode'])
        execution=uuid.uuid4().hex
        result=asyncio.run(run_job(self.store,name,args,execution))
        bounded(result);self.assertTrue(result['ok']);self.assertEqual(result['data']['execution_id'],execution)
        return result,args

    def test_schema_manifest_modes_and_unknown_branches(self):
        manifest=json.loads((HERE.parents[1]/'launcher/research_tool_manifest.json').read_text('utf8'))
        from mcp_contract import visible
        for mode in ('economic','finance','big_data_explore'):
            self.assertEqual(sorted(visible(mode)),manifest[mode])
        self.assertEqual(set(SPECS),set().union(*map(set,manifest.values())))
        for spec in SPECS.values():Draft202012Validator.check_schema(spec['inputSchema'])
        ref={'dataset_id':'ds_'+'a'*32,'sha256':'b'*64}
        invalid=[('nomisma_dataset_quality',{'dataset':ref,'parameters':{'iqr_multiplier':.1},'save_report':False}),
                 ('nomisma_fin_timeseries',{'dataset':ref,'parameters':{'action':'rolling','date_column':'date','value_column':'x','frequency':'daily','value_kind':'measurement','window':253,'min_periods':2,'statistic':'mean'}})]
        for name,args in invalid:
            with self.assertRaises(DatasetError):validate(name,dict(args,request_id=str(uuid.uuid4())),'finance')
        for name,args,mode in [('nomisma_econ_ols',{},'finance'),('nomisma_fin_risk',{},'economic'),
            ('nomisma_health_check',{'code':'exec'},'finance'),('nomisma_analysis_plot',{},'finance')]:
            with self.assertRaises(DatasetError):validate(name,args,mode)

    def test_catalog_inspect_and_source_hash_unchanged(self):
        catalog,_=self.call('akshare_catalog');self.assertEqual(len(catalog['data']['verified']),1)
        source=self.dataset(pd.DataFrame({'x':[2,4,6]}));before=inputs(self.store,{'dataset':{'dataset_id':source.metadata['dataset_id'],'sha256':source.metadata['sha256']}})
        result,_=self.call('dataset_inspect',source,{'sample_rows':2});self.assertEqual(result['data']['row_count'],3)
        self.assertEqual(len(result['data']['sample']),2)
        for r,digest in before.items():self.assertEqual(file_sha256(self.store.path(r)),digest)

    def test_quality_readonly_and_saved_no_cleaning(self):
        source=self.dataset(pd.DataFrame({'x':[2.,None,6.]}))
        r,_=self.call('dataset_quality',source,{},save_report=False);self.assertEqual(r['artifacts'],[])
        r,_=self.call('dataset_quality',source,{},save_report=True);self.assertEqual(len(r['artifacts']),1)
        self.assertTrue(self.store.path(r['data']['report_path']).is_file())
        self.assertFalse((self.root/'datasets/processed').exists())

    def test_import_probe_commit_and_stale_source(self):
        file=self.root/'upload.csv';file.write_text('x,y\n2,3\n4,5\n',encoding='utf8')
        p={'source_path':'upload.csv','expected_sha256':file_sha256(file),'encoding':'utf-8','delimiter':'comma'}
        r,_=self.call('dataset_import',parameters=p,action='probe');self.assertFalse(r['data']['committed'])
        self.assertFalse((self.root/'datasets/raw').exists())
        r,_=self.call('dataset_import',parameters=p,action='commit');self.assertEqual(r['data']['source_type'],'user_provided')
        file.write_text('changed',encoding='utf8')
        with self.assertRaises(DatasetError):self.call('dataset_import',parameters=p,action='commit')

    def test_clean_export_csv_xlsx_and_no_overwrite(self):
        source=self.dataset(pd.DataFrame({'x':[2.,2.,6.]}))
        before=file_sha256(self.store.path(source.metadata['current_file_path']))
        q,_=self.call('dataset_quality',source,{},save_report=True)
        r,_=self.call('dataset_clean',source,{'steps':[{'operation':'drop_duplicates','subset':['x'],'keep':'first'}]},
            quality_report={'quality_report_id':q['data']['quality_report_id'],'sha256':q['data']['sha256']},
            cleaning_plan={'reason':'Remove exact duplicate x values','expected_impact':'3 rows to 2; preserve raw'})
        self.assertEqual(r['data']['row_count'],2)
        self.assertEqual(file_sha256(self.store.path(source.metadata['current_file_path'])),before)
        for fmt in ('csv','xlsx'):
            r,_=self.call('dataset_export',source,{'format':fmt,'file_name':'fixed.'+fmt})
            self.assertTrue(self.store.path(r['data']['output_file']).is_file())
            self.assertTrue(r['data']['roundtrip_verified'])
        with self.assertRaises(DatasetError) as caught:self.call('dataset_export',source,{'format':'csv','file_name':'fixed.csv'})
        self.assertEqual(caught.exception.code,'FILE_EXISTS')

    def test_shared_statistics_fixed_values_and_replay(self):
        source=self.dataset(pd.DataFrame({'x':[2,4,6],'y':[-2,-4,-6]}))
        r,args=self.call('analysis_describe',source,{'columns':['x'],'missing_policy':'reject'})
        result_path=next(a for a in r['artifacts'] if a['path'].endswith('/results.json'))
        full=read_json(self.store.path(result_path['path']))
        self.assertIn('4.0',json.dumps(full['results']))
        again=asyncio.run(run_job(self.store,'nomisma_analysis_describe',args,uuid.uuid4().hex));self.assertEqual(r,again)
        args['parameters']['columns']=['y']
        with self.assertRaises(DatasetError) as caught:asyncio.run(run_job(self.store,'nomisma_analysis_describe',args,uuid.uuid4().hex))
        self.assertEqual(caught.exception.code,'REQUEST_CONFLICT')
        r,_=self.call('analysis_correlate',source,{'columns':['x','y'],'method':'pearson','missing_policy':'reject','inference':'none'})
        self.assertAlmostEqual(r['data']['facts']['pairs'][0]['coefficient'],-1.)

    def test_economic_ols_reference_diagnostics_and_cross_mode_rejection(self):
        with patch.dict(os.environ,{'NOMISMA_MODE':'economic','NOMISMA_PROJECT_ID':'project_economic','NOMISMA_WORKSPACE':str(self.other)}):
            self.store=ProjectDatasetStore()
            source=self.dataset(pd.DataFrame({'x':[0.,1.,2.,3.],'y':[1.,2.,2.,4.]}))
            r,_=self.call('econ_ols',source,{'y':'y','x_numeric':['x'],'intercept':True,'missing_policy':'reject','covariance':'classical'})
            self.assertAlmostEqual(r['data']['facts']['coefficients'][1]['estimate'],.9)
            path=next(a for a in r['artifacts'] if a['path'].endswith('results.json'))
            ref={'analysis_id':r['data']['analysis_id'],'sha256':path['sha256']}
            r,_=self.call('econ_diagnostics',parameters={'tests':['design']},analysis=ref)
            self.assertEqual(r['data']['facts']['n'],4)

    def test_finance_returns_risk_timeseries_branches(self):
        source=self.dataset(pd.DataFrame({'date':['2025-01-01','2025-01-02','2025-01-03'],'p':[100.,110.,99.],'r':[.1,-.1,.0]}))
        r,_=self.call('fin_returns',source,{'date_column':'date','price_column':'p','frequency':'daily','price_adjustment':'unknown','return_kind':'simple','missing_policy':'reject'})
        self.assertEqual(r['data']['method'],'fin_returns')
        path=next(a for a in r['artifacts'] if a['path'].endswith('results.json'))
        risk,_=self.call('fin_risk',parameters={'metrics':['max_drawdown']},analysis={'analysis_id':r['data']['analysis_id'],'sha256':path['sha256']})
        self.assertAlmostEqual(risk['data']['facts']['assets'][0]['max_drawdown']['loss'],.1)
        r,_=self.call('fin_risk',source,{'date_column':'date','return_column':'r','frequency':'daily','return_kind':'simple','metrics':['max_drawdown']})
        self.assertEqual(r['data']['method'],'fin_risk')
        common={'date_column':'date','frequency':'daily','value_column':'p','value_kind':'price','window':2,'min_periods':2}
        for p in [dict(common,action='rolling',statistic='mean'),dict(common,action='moving_average',kind='sma'),
                  {'action':'distribution','date_column':'date','frequency':'daily','return_column':'r','return_kind':'simple','bins':2}]:
            r,_=self.call('fin_timeseries',source,p);self.assertEqual(r['data']['method'],'fin_timeseries')

    def test_multiasset_factor_portfolio_and_correlation(self):
        rows=[]
        for day,date in enumerate(pd.date_range('2025-01-01',periods=6)):
            for i in range(6):rows.append({'date':date,'asset':str(i),'factor':float(i+1),'price':100*(1+(i+1)*.01)**day,'available':date,'r':.01*(i+1)})
        source=self.dataset(pd.DataFrame(rows));assets=[str(i) for i in range(6)]
        p={'date_column':'date','asset_column':'asset','factor_column':'factor','price_column':'price','available_at_column':'available',
           'assets':assets,'universe_status':'declared_ex_ante','frequency':'daily','price_adjustment':'unknown','entry_lag':1,'horizon':1,'groups':3}
        r,_=self.call('fin_factor',source,p);self.assertEqual(r['data']['method'],'fin_factor')
        p={'date_column':'date','asset_column':'asset','return_column':'r','return_kind':'simple','assets':assets,'universe_status':'declared_ex_ante',
           'frequency':'daily','weighting':'equal','metrics':['max_drawdown']}
        r,_=self.call('fin_portfolio',source,p);self.assertEqual(r['data']['method'],'fin_portfolio')
        p={'action':'asset_correlations','date_column':'date','asset_column':'asset','return_column':'r','return_kind':'simple','frequency':'daily',
           'assets':['0','1'],'method':'pearson','missing_policy':'reject'}
        r,_=self.call('fin_timeseries',source,p);self.assertEqual(r['data']['method'],'fin_timeseries')

    def test_validation_path_hash_and_result_budgets(self):
        source=self.dataset(pd.DataFrame({'x':[2,4,6]}));ref={'dataset_id':source.metadata['dataset_id'],'sha256':source.metadata['sha256']}
        with self.assertRaises(DatasetError):inputs(self.store,{'parameters':{'source_path':'../outside.csv','expected_sha256':'a'*64}})
        with self.assertRaises(DatasetError):inputs(self.store,{'dataset':dict(ref,sha256='a'*64)})
        for p in [{'steps':[{'operation':'eval','code':'1+1'}]}, {'steps':[]}, {'steps':[{'operation':'sort_values','by':['x'],'ascending':True,'shell':'bad'}]}]:
            with self.assertRaises(DatasetError):validate('nomisma_dataset_clean',{'dataset':ref,'parameters':p,'request_id':str(uuid.uuid4())},'finance')
        with self.assertRaises(DatasetError):bounded({'ok':True,'summary':'x'*21000,'data':{},'artifacts':[],'warnings':[],'error':None})
        with self.assertRaises(DatasetError):encode_result({'x':float('nan')})

    def test_project_commit_lock_and_interrupted_request_recovery(self):
        with lock(self.store,'.nomisma/commit.lock'):
            with self.assertRaises(DatasetError):
                with lock(self.store,'.nomisma/commit.lock'):pass
        ledger=self.store.directory('.nomisma/requests/'+str(uuid.uuid4()))
        write_json(ledger/'request.json',{'fingerprint':'test','execution_id':'a'*32})
        result=recover(self.store,ledger,'test');self.assertEqual(result['error']['code'],'REQUEST_INTERRUPTED')

    def test_cancel_before_worker_completion_cannot_publish(self):
        source=self.dataset(pd.DataFrame({'x':[2,4,6]}));args={'dataset':{'dataset_id':source.metadata['dataset_id'],'sha256':source.metadata['sha256']},
            'parameters':{'columns':['x'],'missing_policy':'reject'},'request_id':str(uuid.uuid4())}
        async def cancel():
            import anyio
            with anyio.move_on_after(.05) as scope:
                await run_job(self.store,'nomisma_analysis_describe',args,uuid.uuid4().hex)
            self.assertTrue(scope.cancelled_caught)
        asyncio.run(cancel())
        self.assertFalse(list(self.root.glob('analyses/*/an_*/metadata.json')))
        self.assertEqual(list((self.root/'.nomisma/jobs').iterdir()),[])


if __name__=='__main__':unittest.main(verbosity=2)
