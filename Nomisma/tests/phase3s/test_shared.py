import asyncio,copy,datetime,json,os,unittest,uuid
from unittest.mock import patch
import numpy as np
import pandas as pd
from openpyxl import Workbook,load_workbook
from _bootstrap import ProjectTest,HERE,market
from dataset_store import DatasetError,file_sha256,utc_now,ProjectDatasetStore
from dataset_io import import_dataset,write_canonical_csv
from shared_service import handle
from shared_store import register_artifact,read_artifact,capture_experiment,reproduction_check,replay_plan,read_experiment
from shared_contract import validate as contract
from spreadsheet_inspector import inspect
from chart_store import create_chart,save_chart,open_chart,validate_config
from research_control import control,resolve
from mcp_jobs import run_job
from mcp_contract import validate,SPECS
from viewer_store import ViewerStore
from quant_experiments import read_result

class SharedTests(ProjectTest):
    def publish(self,frame):
        with self.store.transaction() as t:
            p=t.file('source.csv');serial=write_canonical_csv(frame,p)
            r=self.store.commit(t,frame,dataset_name='3S synthetic fixture',original_name='source.csv',current_name='source.csv',
              provenance={'source_type':'user_provided','source_name':'synthetic_fixture','source_function':None,'request_parameters':{},'fetched_at':None,'imported_at':utc_now(),'import_history':[]},parser_options=serial)
        return {'dataset_id':r['dataset_id'],'sha256':r['sha256']}
    def csv(self):
        p=self.root/'table.csv';p.write_text('date,value,other\n2025-01-01,1,3\n2025-01-02,2,4\n2025-01-03,3,5\n',encoding='utf8')
        return register_artifact(self.store,'table.csv',file_sha256(p))
    def task(self,key,name,args,deps=None,outputs=None):return {'task_id':key,'task_name':key,'objective':'Synthetic shared integration','tool':name,'arguments':args,'dependencies':deps or [],'expected_outputs':outputs or ['receipt'],'validation_criteria':['receipt','artifacts','warnings']}
    def research(self,tasks):
        plan={'objective':'Synthetic shared integration','mode':self.store.context['mode'],'locked_constraints':{'scope':'synthetic associational / research scenario only'},'tasks':tasks}
        s=control(self.store,'create',{'plan':plan});return control(self.store,'confirm',{'research_id':s['research_id']})
    def execute(self,s,key):
        t=s['tasks'][key];args=resolve(t['arguments'],s['tasks']);cid=uuid.uuid4().hex;pid=uuid.uuid4().hex
        control(self.store,'admit',{'tool':t['tool'],'arguments':args,'tool_call_id':cid,'permission_id':pid});control(self.store,'decision',{'permission_id':pid,'allow':True})
        r=asyncio.run(run_job(self.store,t['tool'],args,uuid.uuid4().hex));return control(self.store,'result',{'tool_call_id':cid,'receipt':r})
    def test_contract_registry_domains_and_schema_versions(self):
        rows=handle(self.store,'catalog',{},'read_only');self.assertTrue(rows)
        for row in rows:
            if row['available']:contract('ToolDescriptor',row)
        self.assertIn('nomisma_fin_scenario',{r['tool_id'] for r in rows});self.assertNotIn('nomisma_econ_panel',{r['tool_id'] for r in rows})
        with self.assertRaises(DatasetError):contract('DatasetRef',{'dataset_id':'file.csv','sha256':'x'})
        with self.assertRaises(DatasetError):validate('nomisma_econ_panel',{},'finance')
    def test_read_only_inspector_formula_cache_and_formats(self):
        p=self.root/'book.xlsx';wb=Workbook();s=wb.active;s.title='Data';s.append(['name','value','date','percent','formula']);s.append(['long text '*40,1234.56789,datetime.date(2025,1,1),.125,'=B2*2']);s['B2'].number_format='#,##0.00';s['D2'].number_format='0.0%';wb.create_sheet('Second').append(['second']);wb.save(p);wb.close();before=file_sha256(p)
        page=inspect(self.store,{'path':'book.xlsx'});cells=page['rows'][1]['cells'];self.assertEqual(cells[1]['display'],'1,234.57');self.assertEqual(cells[3]['display'],'12.5%');self.assertEqual(cells[4]['formula'],'=B2*2');self.assertIsNone(cells[4]['cached_value']);self.assertGreater(len(cells[0]['value']),256);self.assertEqual(len(page['sheets']),2);self.assertEqual(file_sha256(p),before)
    def test_csv_paging_search_filter_sort_preserves_source_coordinates(self):
        p=self.root/'large.csv';p.write_text('x,y\n'+''.join(f'{i},name {i}\n' for i in range(20000)),encoding='utf8');before=file_sha256(p)
        page=inspect(self.store,{'path':'large.csv','start_row':10000,'row_count':200});self.assertEqual(page['rows'][0]['source_row'],10000);self.assertEqual(len(page['rows']),200)
        page=inspect(self.store,{'path':'large.csv','search':'name 19999'});self.assertEqual(page['rows'][0]['source_row'],20001)
        page=inspect(self.store,{'path':'large.csv','filter':{'column':2,'contains':'name 19999'},'sort':{'column':1,'direction':'descending'}});self.assertEqual(page['rows'][0]['cells'][0]['value'],'19999');self.assertEqual(file_sha256(p),before)
    def test_csv_formula_is_literal_and_bad_page_rejected(self):
        (self.root/'literal.csv').write_text('x,y\n=1+2,\n',encoding='utf8');page=inspect(self.store,{'path':'literal.csv'})
        self.assertEqual(page['rows'][1]['cells'][0]['kind'],'text');self.assertIn('missing',page['rows'][1]['cells'][1]['flags'])
        with self.assertRaises(DatasetError):inspect(self.store,{'path':'literal.csv','row_count':100000})
        with self.assertRaises(DatasetError):inspect(self.store,{'path':'../literal.csv'})
    def test_saved_quality_overlay_and_declared_types_are_nonmutating(self):
        ref=self.publish(pd.DataFrame({'x':[1.,1.,None,4.,5.,6.,7.,10000.],'date':['2025-01-01']*7+['wrong']}));meta=self.store.metadata(ref['dataset_id'])
        from data_quality import check_dataset_quality
        check_dataset_quality(self.store,ref['dataset_id'],expected_sha256=ref['sha256'],date_column='date')
        page=inspect(self.store,{'path':meta['current_file_path'],'expected_types':{'B':'date'}})
        self.assertTrue(page['quality_reports']);self.assertIn('duplicate',page['rows'][1]['cells'][0]['flags']);self.assertIn('date_error',page['rows'][-1]['cells'][1]['flags']);self.assertEqual(file_sha256(self.store.path(meta['current_file_path'])),ref['sha256'])
        self.assertIsNone(page['rows'][3]['cells'][0]['value']);self.assertEqual(page['rows'][1]['cells'][0]['kind'],'number')
    def test_wide_page_retains_source_first_column_and_never_accepts_edits(self):
        path=self.root/'wide.csv';path.write_text(','.join('c'+str(i) for i in range(60))+'\n'+','.join(str(i) for i in range(60))+'\n',encoding='utf8')
        page=inspect(self.store,{'path':'wide.csv','start_column':51,'column_count':10})
        self.assertEqual(page['rows'][1]['first_cell']['address'],'A2');self.assertEqual(page['rows'][1]['cells'][0]['address'],'AY2')
        with self.assertRaises(DatasetError):inspect(self.store,{'path':'wide.csv','write':'changed'})
    def test_bad_binary_signature_and_active_svg_export_are_rejected(self):
        path=self.root/'fake.png';path.write_text('not a PNG',encoding='utf8')
        with self.assertRaises(DatasetError):register_artifact(self.store,'fake.png',file_sha256(path))
        a=self.csv();chart=create_chart(self.store,a['artifact_id']);before=set((self.root/'datasets/exports').iterdir())
        import base64
        with self.assertRaises(DatasetError):handle(self.store,'chart.publish_export',{'chart_id':chart['chart']['chart_id'],'version':1,'format':'svg','content_base64':base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"><script>bad</script></svg>').decode()},'user_confirmed')
        self.assertEqual(set((self.root/'datasets/exports').iterdir()),before)
    def test_rejected_experiment_is_preserved_and_never_replayed(self):
        ref=self.publish(pd.DataFrame({'x':[1,2,3]}));s=self.research([self.task('inspect','nomisma_dataset_inspect',{'dataset':ref,'parameters':{'sample_rows':2}})])
        t=s['tasks']['inspect'];control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'denied','permission_id':'denied'})
        control(self.store,'decision',{'permission_id':'denied','allow':False});e=capture_experiment(self.store,s['research_id'])
        self.assertEqual(e['execution_status'],'FAILED');self.assertFalse(reproduction_check(self.store,e['experiment_id'])['conditions_match'])
        with self.assertRaises(DatasetError):replay_plan(self.store,e['experiment_id'])
    def test_unauthorized_chart_write_denied_and_audited(self):
        a=self.csv()
        with self.assertRaises(DatasetError):handle(self.store,'chart.create',{'source_artifact_id':a['artifact_id']},'read_only')
        events=handle(self.store,'audit',{});self.assertEqual(events[-1]['error'],'APPROVAL_REQUIRED');self.assertFalse((self.root/'.nomisma/shared/charts').exists())
    def test_chart_versions_source_hash_and_configuration_only(self):
        a=self.csv();o=handle(self.store,'chart.create',{'source_artifact_id':a['artifact_id']},'user_confirmed');config=copy.deepcopy(o['chart']['chart_config']);config['title']='Display version 2';config['y_axis']['max']=10
        newer=handle(self.store,'chart.save',{'chart_id':o['chart']['chart_id'],'config':config,'expected_version':1},'user_confirmed')
        self.assertEqual(newer['chart']['version'],2);self.assertEqual(open_chart(self.store,o['chart']['chart_id'],1)['chart']['chart_config']['title'],'研究结果');self.assertEqual(read_artifact(self.store,a['artifact_id'])['file_hash'],a['file_hash'])
        bad=copy.deepcopy(config);bad['backtest_period']='changed'
        with self.assertRaises(DatasetError):validate_config(bad,o['data'])
        with self.assertRaises(DatasetError):save_chart(self.store,o['chart']['chart_id'],config,1)
        self.store.path(a['file_path']).write_text('changed')
        with self.assertRaises(DatasetError):open_chart(self.store,o['chart']['chart_id'])
    def test_log_and_semantic_conversion_rejected(self):
        a=self.csv();o=create_chart(self.store,a['artifact_id']);config=copy.deepcopy(o['chart']['chart_config']);config['chart_type']='coefficient'
        with self.assertRaises(DatasetError):validate_config(config,o['data'])
        config['chart_type']='line';config['x_axis']['scale']='log'
        with self.assertRaises(DatasetError):validate_config(config,o['data'])
        config['x_axis']['scale']='linear';config['y_axis']['min']=10;config['y_axis']['max']=5
        with self.assertRaises(DatasetError):validate_config(config,o['data'])
    def test_archived_project_view_does_not_grant_writes(self):
        a=self.csv();entries=json.loads(self.registry.read_text('utf8'));entries[0]['status']='archived';self.registry.write_text(json.dumps(entries),encoding='utf8')
        viewer=ViewerStore();self.assertTrue(handle(viewer,'sheet.page',{'path':a['file_path']}))
        with self.assertRaises(DatasetError):ProjectDatasetStore()
        with self.assertRaises(DatasetError):viewer.transaction()
    def test_artifact_types_cross_project_and_stale_hash(self):
        a=self.csv();contract('ArtifactRef',a)
        with self.assertRaises(DatasetError):register_artifact(self.store,'table.csv','0'*64)
        with patch.dict(os.environ,{'NOMISMA_MODE':'economic','NOMISMA_PROJECT_ID':'project_economic','NOMISMA_WORKSPACE':str(self.other)}):
            with self.assertRaises(DatasetError):read_artifact(ProjectDatasetStore(),a['artifact_id'])
    def test_economic_twfe_bootstrap_shared_experiment_chart_trace(self):
        with patch.dict(os.environ,{'NOMISMA_MODE':'economic','NOMISMA_PROJECT_ID':'project_economic','NOMISMA_WORKSPACE':str(self.other)}):
            self.store=ProjectDatasetStore();rng=np.random.default_rng(31);rows=[]
            for i in range(8):
                for t in range(8):
                    x=math_sin(i+t*.7);rows.append({'entity':str(i),'period':t,'x':x,'y':2*x+3*i+.5*t+rng.normal(0,.1)})
            f=pd.DataFrame(rows);wb=Workbook();ws=wb.active;ws.title='Panel';ws.append(list(f.columns))
            for r in f.itertuples(index=False,name=None):ws.append(r)
            wb.save(self.other/'panel.xlsx');wb.close()
            raw_before=file_sha256(self.other/'panel.xlsx');self.assertTrue(inspect(self.store,{'path':'panel.xlsx'})['rows'])
            imported=import_dataset(self.store,source_path='panel.xlsx',expected_sha256=raw_before,sheet='Panel',formula_policy='reject');ref={'dataset_id':imported['dataset_id'],'sha256':imported['sha256']}
            within=lambda v:v-f.groupby('entity')[v.name].transform('mean')-f.groupby('period')[v.name].transform('mean')+v.mean()
            wx=within(f.x);wy=within(f.y);expected=float(np.dot(wx,wy)/np.dot(wx,wx))
            p={'y':'y','x':['x'],'entity':'entity','time':'period','missing_policy':'reject','alpha':.05,'bootstrap_draws':99,'seed':7}
            tasks=[self.task('quality','nomisma_dataset_quality',{'dataset':ref,'parameters':{},'save_report':True},outputs=['quality_report']),self.task('panel','nomisma_econ_panel',{'dataset':ref,'parameters':p},deps=['quality'],outputs=['analysis'])]
            s=self.execute(self.research(tasks),'quality');s=self.execute(s,'panel');self.assertEqual(s['status'],'computed',s)
            facts=s['tasks']['panel']['receipt']['data']['facts'];self.assertAlmostEqual(facts['coefficients'][0]['estimate'],expected,places=10);self.assertEqual(facts['bootstrap'][0]['seed'],7)
            e=capture_experiment(self.store,s['research_id']);contract('ExperimentRecord',e);self.assertEqual(len(e['task_references']),2);self.assertTrue(reproduction_check(self.store,e['experiment_id'])['conditions_match'])
            a=next(a for a in e['artifacts'] if a['file_path'].endswith('results.json'));o=create_chart(self.store,a['artifact_id']);self.assertEqual(o['allowed_types'],['coefficient'])
            changed=copy.deepcopy(o['chart']['chart_config']);changed['title']='TWFE actual coefficients';save_chart(self.store,o['chart']['chart_id'],changed,1)
            trace=handle(self.store,'trace',{'chart_id':o['chart']['chart_id']});self.assertEqual(trace['experiment']['experiment_id'],e['experiment_id']);self.assertTrue(trace['tasks'][0]['approval_requests'][0]['started_at']);self.assertTrue(trace['viewer_configuration_modified'])
            rp=replay_plan(self.store,e['experiment_id']);self.assertNotIn('request_id',rp['plan']['tasks'][0]['arguments'])
    def test_panel_identification_and_missing_assumptions_fail_honestly(self):
        with patch.dict(os.environ,{'NOMISMA_MODE':'economic','NOMISMA_PROJECT_ID':'project_economic','NOMISMA_WORKSPACE':str(self.other)}):
            self.store=ProjectDatasetStore();f=pd.DataFrame([{'entity':str(i),'period':t,'x':i,'y':i+t*.5} for i in range(8) for t in range(4)]);ref=self.publish(f)
            args={'dataset':ref,'parameters':{'y':'y','x':['x'],'entity':'entity','time':'period','missing_policy':'reject','alpha':.05,'bootstrap_draws':0,'seed':1},'request_id':str(uuid.uuid4())}
            with self.assertRaises(DatasetError) as e:asyncio.run(run_job(self.store,'nomisma_econ_panel',args,uuid.uuid4().hex))
            self.assertEqual(e.exception.code,'RANK_DEFICIENT')
    def test_finance_existing_3a_chain_uses_same_worker_and_artifact_service(self):
        dates,cal,bars,states,fees,actions,obs,membership=market();refs={n:self.publish(f) for n,f in {'membership':membership,'calendar':cal,'bars':bars,'market_state':states,'fee_schedule':fees}.items()}
        wb=Workbook();ws=wb.active;ws.title='Observations';ws.append(list(obs.columns))
        for row in obs.itertuples(index=False,name=None):ws.append(row)
        wb.save(self.root/'financial.xlsx');wb.close();before=file_sha256(self.root/'financial.xlsx');self.assertTrue(inspect(self.store,{'path':'financial.xlsx'})['rows'])
        imported=import_dataset(self.store,source_path='financial.xlsx',expected_sha256=before,sheet='Observations',formula_policy='reject');refs['observations']={'dataset_id':imported['dataset_id'],'sha256':imported['sha256']}
        labels=[];prices=bars.set_index(['session','security_id'])
        for i,d in enumerate(dates[:-2]):
            for a in bars.security_id.unique():labels.append({'session':d,'security_id':a,'entry_at':dates[i+1]+'T09:30:00+08:00','exit_at':dates[i+2]+'T15:00:00+08:00','label_available_at':dates[i+2]+'T15:05:00+08:00','forward_return':float(prices.loc[(dates[i+2],a),'close']/prices.loc[(dates[i+1],a),'open']-1)})
        refs['labels']=self.publish(pd.DataFrame(labels))
        cfg={'sessions':dates,'formation_times':{d:d+'T15:05:00+08:00' for d in dates},'factors':[{'name':'price','method':'mean','window':1,'skip':0,'inputs':['close'],'direction':1,'definition':'Synthetic causal factor'}],
          'processing':{'winsor':{'method':'none'},'standardize':True,'min_assets':5},'evaluation':{'groups':3,'hac_lags':1,'min_assets':5},'combiner':{'names':['price'],'method':'equal','fit_at':dates[2]+'T15:05:00+08:00','training_start':dates[0],'training_end':dates[2],'ridge_alpha':0.},
          'selection':{'top_n':2},'rebalance_sessions':[dates[3]],'portfolio':{'method':'equal','investment':.8,'max_weight':.5},'backtest':{'initial_cash':100000.,'slippage_bps':0.,'stale_price_policy':'reject','execution_mode':'declared_scenario'},'performance':{'periods_per_year':252,'risk_free_annual':0.},'no_actions_confirmed':True}
        args={'objective':'strategy_evaluation','research_question':'Synthetic shared factor-to-backtest integration','inputs':refs,'configuration':cfg}
        s=self.execute(self.research([self.task('strategy','nomisma_fin_scenario',args)]),'strategy');self.assertEqual(s['status'],'computed',s)
        receipt=s['tasks']['strategy']['receipt'];tables,r=read_result(self.store,receipt['data']['result']);self.assertIn('nav',tables);self.assertIn('ic',tables);self.assertTrue(tables['ic'].ic.notna().any());self.assertFalse(r['strict_backtest_ready']);self.assertEqual(file_sha256(self.root/'financial.xlsx'),before)
        e=capture_experiment(self.store,s['research_id']);self.assertEqual(e['domain'],'finance');self.assertTrue(e['dataset_version'])
        a=next(a for a in e['artifacts'] if a['file_path'].endswith('_nav.csv'));o=create_chart(self.store,a['artifact_id']);self.assertTrue(o['data']['rows']);self.assertEqual(o['source']['experiment_id'],e['experiment_id'])
        s=control(self.store,'deliver',{'research_id':s['research_id']});self.assertEqual(s['status'],'completed');self.assertGreater(s['delivery']['claim_count'],0)

def math_sin(x):return float(np.sin(x))
if __name__=='__main__':unittest.main()
