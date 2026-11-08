import asyncio,importlib.util,json,os,sys,uuid
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('explore_boot',HERE.parent/'phase3a1/_bootstrap.py');boot=importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)
from dataset_store import ProjectDatasetStore,DatasetError,file_sha256
from explore_store import ingest,read,connect
from mcp_contract import visible,validate
from mcp_jobs import run_job
from research_control import control

class ExploreTests(boot.ProjectTest):
    def setUp(self):
        super().setUp();entries=json.loads(self.registry.read_text('utf8'));entries[0]['mode']='big_data_explore';self.registry.write_text(json.dumps(entries),encoding='utf8')
        p=patch.dict(os.environ,{'NOMISMA_MODE':'big_data_explore'});p.start();self.addCleanup(p.stop);self.store=ProjectDatasetStore()
    def source(self):
        p=self.root/'table.csv';p.write_text('id,value\n001,1\n002,2\n003,3\n',encoding='utf8');return {'source_path':'table.csv','expected_sha256':file_sha256(p)}
    def test_registry_and_scope(self):
        names=visible('big_data_explore');self.assertIn('nomisma_explore_import',names);self.assertNotIn('nomisma_akshare_fetch',names);self.assertNotIn('nomisma_fin_scenario',names)
        with self.assertRaises(DatasetError):validate('nomisma_explore_import',{'parameters':self.source(),'request_id':str(uuid.uuid4())},'finance')
    def test_worker_import_profile_and_idempotency(self):
        p=self.source();args={'parameters':p,'request_id':str(uuid.uuid4())}
        r=asyncio.run(run_job(self.store,'nomisma_explore_import',args,uuid.uuid4().hex));self.assertTrue(r['ok'],r)
        ref={k:r['data'][k] for k in ('dataset_id','sha256')};m,manifest=read(self.store,ref)
        with connect(self.store.path(manifest['files'][1]['path'])) as db:self.assertEqual(db.execute('SELECT id FROM data ORDER BY rowid LIMIT 1').fetchone()[0],'001')
        self.assertEqual(asyncio.run(run_job(self.store,'nomisma_explore_import',args,uuid.uuid4().hex)),r)
        out=asyncio.run(run_job(self.store,'nomisma_explore_profile',{'dataset':ref,'parameters':{'column':'value','method':'numeric'},'request_id':str(uuid.uuid4())},uuid.uuid4().hex))
        self.assertTrue(out['ok'],out);self.assertEqual(out['data']['facts']['mean'],2);self.assertEqual(out['data']['facts']['sample_variance'],1)
        self.assertTrue(all(file_sha256(self.store.path(f['path']))==f['sha256'] for f in out['artifacts']))
    def test_tamper_and_unknown_format(self):
        p=self.source();r=ingest(self.store,p,uuid.uuid4().hex,str(uuid.uuid4()));ref={k:r[k] for k in ('dataset_id','sha256')}
        m,manifest=read(self.store,ref);self.store.path(manifest['files'][1]['path']).write_bytes(b'tampered')
        with self.assertRaises(DatasetError):read(self.store,ref)
        bad=self.root/'fake.csv';bad.write_text('[{"value":1}]',encoding='utf8')
        with self.assertRaises(DatasetError):ingest(self.store,{'source_path':'fake.csv','expected_sha256':file_sha256(bad)},uuid.uuid4().hex,str(uuid.uuid4()))
    def test_parquet_and_jsonl(self):
        import pyarrow as pa,pyarrow.parquet as pq
        p=self.root/'table.parquet';pq.write_table(pa.table({'id':['001','002'],'x':[1,None]}),p)
        for path in ('table.parquet','table.jsonl'):
            if path.endswith('jsonl'):(self.root/path).write_text('{"id":"001","x":1}\n{"id":"002","x":null}\n',encoding='utf8')
            r=ingest(self.store,{'source_path':path,'expected_sha256':file_sha256(self.root/path)},uuid.uuid4().hex,str(uuid.uuid4()))
            self.assertEqual(r['row_count'],2)
    def test_processing_preview_and_mining(self):
        from explore_processing import process
        from explore_mining import mine
        from spreadsheet_inspector import inspect
        r=ingest(self.store,self.source(),uuid.uuid4().hex,str(uuid.uuid4()));ref={k:r[k] for k in ('dataset_id','sha256')}
        d=process(self.store,ref,{'method':'select','columns':['value']},uuid.uuid4().hex,str(uuid.uuid4()))
        self.assertEqual(d['row_count'],3)
        page=inspect(self.store,{'path':r['manifest_path'],'start_row':2,'row_count':2})
        self.assertEqual(page['rows'][0]['cells'][0]['value'],'001')
        out,files=mine(self.store,ref,{'method':'pca','columns':['value']},uuid.uuid4().hex,str(uuid.uuid4()))
        self.assertAlmostEqual(out['facts']['explained_variance_ratio'][0],1)
        self.assertEqual(len(files),2)
    def test_join_conversion_and_cardinality(self):
        from explore_processing import process
        def add(name,text):
            f=self.root/name;f.write_text(text,encoding='utf8');r=ingest(self.store,{'source_path':name,'expected_sha256':file_sha256(f)},uuid.uuid4().hex,str(uuid.uuid4()));return {k:r[k] for k in ('dataset_id','sha256')}
        left=add('left.csv','id,value\n001,1\n002,2\n003,3\n');right=add('right.csv','id,region\n001,A\n002,B\n')
        r=process(self.store,left,{'method':'join','column':'id','right_dataset':right},uuid.uuid4().hex,str(uuid.uuid4()))
        ref={k:r[k] for k in ('dataset_id','sha256')};_,manifest=read(self.store,ref)
        self.assertEqual(manifest['lineage']['parents'],[left,right]);self.assertEqual(r['row_count'],3)
        with connect(self.store.path(manifest['files'][1]['path'])) as db:self.assertEqual(db.execute('SELECT right_region FROM data ORDER BY rowid').fetchall(),[('A',),('B',),(None,)])
        duplicate=add('duplicates.csv','id,region\n001,A\n001,B\n')
        with self.assertRaises(DatasetError):process(self.store,left,{'method':'join','column':'id','right_dataset':duplicate},uuid.uuid4().hex,str(uuid.uuid4()))
        converted=process(self.store,left,{'method':'cast_numeric','column':'value'},uuid.uuid4().hex,str(uuid.uuid4()));self.assertEqual(converted['row_count'],3)
    def test_correlations_and_frequency_counts(self):
        from explore_analysis import profile
        p=self.root/'pair.jsonl';p.write_text('\n'.join(json.dumps({'x':i,'y':2*i,'group':str(i%2)}) for i in range(10)),encoding='utf8')
        r=ingest(self.store,{'source_path':p.name,'expected_sha256':file_sha256(p)},uuid.uuid4().hex,str(uuid.uuid4()));ref={k:r[k] for k in ('dataset_id','sha256')}
        out,_=profile(self.store,ref,{'method':'correlation','column':'x','other_column':'y'},uuid.uuid4().hex,str(uuid.uuid4()));self.assertAlmostEqual(out['facts']['correlation'],1)
        out,_=profile(self.store,ref,{'method':'frequency','column':'group'},uuid.uuid4().hex,str(uuid.uuid4()));self.assertEqual(out['facts']['returned_observations'],10);self.assertEqual(out['facts']['other_observations'],0)
    def test_process_contract_rejects_incomplete_parameters(self):
        args={'dataset':{'dataset_id':'ds_'+'a'*32,'sha256':'a'*64},'parameters':{'method':'join'},'request_id':str(uuid.uuid4())}
        with self.assertRaises(DatasetError):validate('nomisma_explore_process',args,'big_data_explore')
    def test_api_connector_snapshot_and_rejected_arbitrary_url(self):
        import io
        from explore_api import fetch,ENDPOINT
        body={'daily':{'time':['2025-01-01','2025-01-02'],'temperature_2m_mean':[1.,None]},'daily_units':{'temperature_2m_mean':'°C'}}
        class Client:
            def open(self,request,timeout):
                assert request.full_url.startswith(ENDPOINT+'?') and timeout==15
                return io.BytesIO(json.dumps(body).encode('utf8'))
        p={'latitude':31.2,'longitude':121.5,'start_date':'2025-01-01','end_date':'2025-01-02'}
        r=fetch(self.store,p,uuid.uuid4().hex,str(uuid.uuid4()),opener=Client());_,manifest=read(self.store,{k:r[k] for k in ('dataset_id','sha256')})
        self.assertEqual(manifest['lineage']['response_document'],body);self.assertEqual(r['row_count'],2)
        with self.assertRaises(DatasetError):validate('nomisma_explore_fetch',{'parameters':{**p,'url':'http://localhost/'},'request_id':str(uuid.uuid4())},'big_data_explore')
    def test_real_worker_join_inputs_and_profile_validation(self):
        r=ingest(self.store,self.source(),uuid.uuid4().hex,str(uuid.uuid4()));ref={k:r[k] for k in ('dataset_id','sha256')}
        args={'dataset':ref,'parameters':{'method':'join','column':'id','right_dataset':ref},'request_id':str(uuid.uuid4())}
        out=asyncio.run(run_job(self.store,'nomisma_explore_process',args,uuid.uuid4().hex));self.assertTrue(out['ok'],out);self.assertEqual(out['data']['row_count'],3)
    def test_complete_research_dag_denial_report_experiment_chart(self):
        from research_control import resolve
        from shared_store import capture_experiment,register_artifact,reproduction_check
        from chart_store import create_chart,save_chart
        task=lambda key,tool,args,deps,outputs:{'task_id':key,'task_name':key,'objective':'Explore real integration','tool':tool,'arguments':args,'dependencies':deps,'expected_outputs':outputs,'validation_criteria':['receipt','artifacts','warnings']}
        plan={'objective':'Explore full scan with unchanged IDs','mode':'big_data_explore','locked_constraints':{'sample':'all three synthetic rows; no cleaning'},'tasks':[
            task('probe','nomisma_explore_probe',{'parameters':{'source_path':self.source()['source_path']}},[],['receipt']),
            task('import_data','nomisma_explore_import',{'parameters':{'source_path':'table.csv','expected_sha256':{'$from':'probe','pointer':'/verified/source/sha256'}}},['probe'],['dataset']),
            task('profile','nomisma_explore_profile',{'dataset':{'dataset_id':{'$from':'import_data','pointer':'/verified/dataset/dataset_id'},'sha256':{'$from':'import_data','pointer':'/verified/dataset/sha256'}},'parameters':{'method':'numeric','column':'value'}},['import_data'],['receipt'])]}
        state=control(self.store,'create',{'plan':plan});rid=state['research_id'];state=control(self.store,'confirm',{'research_id':rid})
        t=state['tasks']['probe'];control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'denied_call','permission_id':'denied_permission'})
        control(self.store,'decision',{'permission_id':'denied_permission','allow':False});self.assertFalse((self.root/'datasets/metadata').exists())
        state=control(self.store,'retry',{'research_id':rid,'task_id':'probe'})
        for key in ('probe','import_data','profile'):
            t=state['tasks'][key];args=resolve(t['arguments'],state['tasks']);cid=uuid.uuid4().hex;pid=uuid.uuid4().hex
            control(self.store,'admit',{'tool':t['tool'],'arguments':args,'tool_call_id':cid,'permission_id':pid});control(self.store,'decision',{'permission_id':pid,'allow':True})
            receipt=asyncio.run(run_job(self.store,t['tool'],args,uuid.uuid4().hex));state=control(self.store,'result',{'tool_call_id':cid,'receipt':receipt})
        self.assertEqual(state['status'],'computed');delivered=control(self.store,'deliver',{'research_id':rid});self.assertEqual(delivered['status'],'completed')
        experiment=capture_experiment(self.store,rid);self.assertEqual(len(experiment['task_references']),3)
        art=next(a for a in state['tasks']['profile']['receipt']['artifacts'] if a['path'].endswith('.csv'))
        source=register_artifact(self.store,art['path'],art['sha256'],experiment_id=experiment['experiment_id'],task_id='profile');chart=create_chart(self.store,source['artifact_id'])
        config=chart['chart']['chart_config'].copy();config['title']='Actual Explore numeric profile';saved=save_chart(self.store,chart['chart']['chart_id'],config,1);self.assertEqual(saved['chart']['version'],2)
    def test_seeded_clustering_and_labelled_text_counts(self):
        from explore_mining import mine
        f=self.root/'clusters.jsonl';f.write_text('\n'.join(json.dumps({'x':i,'y':i*2}) for i in (0,1,2,20,21,22)),encoding='utf8')
        r=ingest(self.store,{'source_path':f.name,'expected_sha256':file_sha256(f)},uuid.uuid4().hex,str(uuid.uuid4()));ref={k:r[k] for k in ('dataset_id','sha256')};p={'method':'kmeans','columns':['x','y'],'clusters':2,'seed':7}
        a,_=mine(self.store,ref,p,uuid.uuid4().hex,str(uuid.uuid4()));b,_=mine(self.store,ref,p,uuid.uuid4().hex,str(uuid.uuid4()));self.assertEqual(a['facts'],b['facts']);self.assertTrue(a['facts']['converged']);self.assertEqual(a['facts']['clusters'],2)
        f=self.root/'text.txt';f.write_text('Hello hello 中国新闻\nhello news 中国新闻\n',encoding='utf8')
        r=ingest(self.store,{'source_path':f.name,'expected_sha256':file_sha256(f),'text_records':'lines'},uuid.uuid4().hex,str(uuid.uuid4()));out,files=mine(self.store,{k:r[k] for k in ('dataset_id','sha256')},{'method':'text_frequency','columns':['text']},uuid.uuid4().hex,str(uuid.uuid4()))
        self.assertEqual(out['facts']['documents'],2);self.assertIn('not dictionary',out['facts']['tokenization']);self.assertIn('hello,3',self.store.path(next(a['path'] for a in files if a['path'].endswith('.csv'))).read_text('utf8'))
    def test_closed_sqlite_and_values_xlsx_import(self):
        import sqlite3
        from openpyxl import Workbook
        f=self.root/'source.sqlite'
        with sqlite3.connect(f) as db:db.execute('CREATE TABLE survey(id TEXT,value INTEGER)');db.execute("INSERT INTO survey VALUES('001',3)")
        db.close()
        f=self.root/'source.xlsx';wb=Workbook();wb.active.append(['id','value']);wb.active.append(['001',3]);wb.save(f);wb.close()
        for name,extra in [('source.sqlite',{'table':'survey'}),('source.xlsx',{})]:
            r=ingest(self.store,{'source_path':name,'expected_sha256':file_sha256(self.root/name),**extra},uuid.uuid4().hex,str(uuid.uuid4()));_,m=read(self.store,{k:r[k] for k in ('dataset_id','sha256')})
            with connect(self.store.path(m['files'][1]['path'])) as db:self.assertEqual(db.execute('SELECT id,value FROM data').fetchone(),('001',3))
