import asyncio
import csv
import importlib.util
import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('generalized_boot',HERE.parent/'phase3a1/_bootstrap.py')
boot=importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)
from dataset_store import ProjectDatasetStore,DatasetError,file_sha256
from mcp_contract import validate,visible
from mcp_jobs import run_job
from research_control import control, resolve
from shared_service import handle
from spreadsheet_inspector import inspect


def token():return str(uuid.uuid4())


class GeneralizedExploreTests(boot.ProjectTest):
    def setUp(self):
        super().setUp()
        entries=json.loads(self.registry.read_text('utf8'));entries[0]['mode']='big_data_explore'
        self.registry.write_text(json.dumps(entries),encoding='utf8')
        env=patch.dict(os.environ,{'NOMISMA_MODE':'big_data_explore'})
        env.start();self.addCleanup(env.stop);self.store=ProjectDatasetStore()

    def execute(self,name,p,ref=None):
        args={'parameters':p,'request_id':token(),**({'dataset':ref} if ref else {})}
        validate(name,args,'big_data_explore')
        return asyncio.run(run_job(self.store,name,args,uuid.uuid4().hex))

    def ingest(self,filename):
        receipt=self.execute('nomisma_explore_import',{'source_path':filename,'expected_sha256':file_sha256(self.root/filename)})
        return {key:receipt['data'][key] for key in ('dataset_id','sha256')}

    def table(self,receipt):
        path=next(a['path'] for a in receipt['artifacts'] if a['path'].endswith('.csv'))
        with self.store.path(path).open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))

    def test_social_media_workflow_through_owned_worker(self):
        path=self.root/'videos.csv'
        path.write_text('publish_date,video_title,category,views,likes,comments,favorites,follower_growth\n'
                        '2025-01-01,Alpha, guide ,100,10,3,2,5\n'
                        '2025-01-02,Beta,review,200,15,2,3,8\n'
                        '2025-01-03,Gamma,guide,0,1,0,0,\n'
                        '2025-01-03,Gamma,guide,0,1,0,0,\n',encoding='utf8')
        original=file_sha256(path)
        ref=self.ingest(path.name)
        quality=self.execute('nomisma_explore_quality',{'outlier_columns':['views']},ref)
        self.assertEqual(quality['data']['facts']['duplicate_rows'],1)
        self.assertEqual(quality['data']['facts']['missing_by_field']['follower_growth'],2)
        trimmed=self.execute('nomisma_explore_transform',{'method':'trim','column':'category'},ref)
        trimmed={k:trimmed['data'][k] for k in ('dataset_id','sha256')}
        sql='''WITH unique_videos AS (SELECT DISTINCT * FROM input)
          SELECT video_title, category, CAST(views AS BIGINT) AS views,
          (CAST(likes AS DOUBLE)+CAST(comments AS DOUBLE)+CAST(favorites AS DOUBLE))
            /NULLIF(CAST(views AS DOUBLE),0) AS engagement,
          TRY_CAST(follower_growth AS INTEGER) AS follower_growth
          FROM unique_videos ORDER BY views DESC'''
        outcome=self.execute('nomisma_explore_query',{'sql':sql,'max_rows':10},trimmed)
        data=self.table(outcome)
        self.assertEqual([row['video_title'] for row in data],['Beta','Alpha','Gamma'])
        self.assertEqual([row['category'] for row in data],['review','guide','guide'])
        self.assertEqual([row['engagement'] for row in data],['0.1','0.15',''])
        self.assertEqual([row['follower_growth'] for row in data],['8','5',''])
        grouped=self.execute('nomisma_explore_transform',{'method':'group_sum','column':'views','group_by':'category'},trimmed)
        groupref={k:grouped['data'][k] for k in ('dataset_id','sha256')}
        aggregate=self.execute('nomisma_explore_query',{'sql':'SELECT * FROM input ORDER BY category'},groupref)
        self.assertEqual([(r['category'],float(r['views'])) for r in self.table(aggregate)],[('guide',100.),('review',200.)])
        trend=self.execute('nomisma_explore_query',{'sql':'SELECT publish_date, SUM(TRY_CAST(follower_growth AS BIGINT)) AS followers FROM (SELECT DISTINCT * FROM input) GROUP BY publish_date ORDER BY publish_date'},trimmed)
        self.assertEqual([row['followers'] for row in self.table(trend)],['5','8',''])
        chart=handle(self.store,'chart.create_from_file',{'path':next(a['path'] for a in trend['artifacts'] if a['path'].endswith('.csv')),
            'sha256':next(a['sha256'] for a in trend['artifacts'] if a['path'].endswith('.csv'))},'user_confirmed')
        self.assertTrue(chart['data']['rows'])
        self.assertEqual(file_sha256(path),original)
        with self.assertRaises(DatasetError):
            self.execute('nomisma_explore_query',{'sql':'SELECT watch_time FROM input'},ref)
        with self.assertRaises(DatasetError) as denied:
            handle(self.store,'explore.query',{'dataset':ref,'parameters':{'sql':'SELECT * FROM input'}},'read_only')
        self.assertEqual(denied.exception.code,'APPROVAL_REQUIRED')

    def test_sql_blocks_external_reads_and_multiple_statements(self):
        path=self.root/'source.json';path.write_text('[{"n":1},{"n":2}]',encoding='utf8')
        ref=self.ingest(path.name)
        for sql in ('SELECT * FROM input; DROP TABLE input', "SELECT * FROM read_csv_auto('C:/unapproved.csv')",
                    "SELECT * FROM input, read_csv_auto('https://evil.invalid/file.csv')"):
            with self.assertRaises(DatasetError):
                self.execute('nomisma_explore_query',{'sql':sql},ref)
        self.assertFalse(any((self.root/'datasets/exports').glob('explore_*')))

    def test_duckdb_full_scan_over_multiple_import_batches(self):
        path=self.root/'large.csv'
        with path.open('w',encoding='utf8') as stream:
            stream.write('row,value\n')
            for i in range(50_123):stream.write(f'{i},{i%100}\n')
        ref=self.ingest(path.name)
        receipt=self.execute('nomisma_explore_query',{'sql':'SELECT count(*) AS n, max(CAST(value AS INTEGER)) AS high FROM input'},ref)
        self.assertEqual(self.table(receipt),[{'n':'50123','high':'99'}])

    def test_streamed_exports_preserve_types_and_disable_csv_formulas(self):
        import pyarrow.parquet as pq
        source=self.root/'input.jsonl'
        source.write_text('{"label":"=1+2","value":7}\n{"label":"plain","value":null}\n',encoding='utf8')
        ref=self.ingest(source.name)
        csv_receipt=self.execute('nomisma_explore_export',{'format':'csv'},ref)
        self.assertEqual(self.table(csv_receipt),[{'label':"'=1+2",'value':'7.0'},{'label':'plain','value':''}])
        parquet=self.execute('nomisma_explore_export',{'format':'parquet'},ref)
        frame=pq.read_table(self.store.path(parquet['data']['output_file'])).to_pylist()
        self.assertEqual(frame,[{'label':'=1+2','value':7.0},{'label':'plain','value':None}])

    def test_registry_scope(self):
        for mode in ('economic','finance'):
            self.assertNotIn('nomisma_explore_query',visible(mode))
        self.assertIn('nomisma_fin_scenario',visible('finance'))
        self.assertIn('nomisma_econ_panel',visible('economic'))

    def test_approved_plan_resolves_dependencies_and_delivers_reports(self):
        source=self.root/'videos.jsonl'
        source.write_text('{"video_title":"Alpha","views":100,"likes":10}\n{"video_title":"Beta","views":200,"likes":15}\n',encoding='utf8')
        def task(name,tool,args,deps=(),outputs=('receipt',)):
            return {'task_id':name,'task_name':name,'objective':'Check video growth inputs',
                    'tool':tool,'arguments':args,'dependencies':list(deps),
                    'expected_outputs':list(outputs),'validation_criteria':['receipt','artifacts','warnings']}
        verified_dataset={field:{'$from':'ingest','pointer':'/verified/dataset/'+field}
                          for field in ('dataset_id','sha256')}
        tasks=[
            task('probe','nomisma_explore_probe',{'parameters':{'source_path':source.name}}),
            task('ingest','nomisma_explore_import',{'parameters':{'source_path':source.name,
                 'expected_sha256':{'$from':'probe','pointer':'/verified/source/sha256'}}},('probe',),('dataset',)),
            task('profile','nomisma_explore_quality',{'dataset':verified_dataset,'parameters':{}},('ingest',)),
            task('rank','nomisma_explore_query',{'dataset':verified_dataset,
                 'parameters':{'sql':'SELECT video_title, views, CAST(likes AS DOUBLE)/NULLIF(views,0) AS likes_per_view FROM input ORDER BY views DESC'}},('ingest','profile')),
        ]
        plan={'objective':'Rank sample videos using likes per view. Watch time and completion rate are unavailable.',
              'mode':'big_data_explore','locked_constraints':{'metrics':'likes divided by views'},'tasks':tasks}
        state=control(self.store,'create',{'plan':plan})
        state=control(self.store,'confirm',{'research_id':state['research_id'],'version':state['version']})
        for key in ('probe','ingest','profile','rank'):
            entry=state['tasks'][key];args=resolve(entry['arguments'],state['tasks'])
            call='call_'+uuid.uuid4().hex;permission='perm_'+uuid.uuid4().hex
            control(self.store,'admit',{'tool':entry['tool'],'arguments':args,'tool_call_id':call,'permission_id':permission})
            control(self.store,'decision',{'permission_id':permission,'allow':True})
            receipt=asyncio.run(run_job(self.store,entry['tool'],args,uuid.uuid4().hex))
            state=control(self.store,'result',{'tool_call_id':call,'receipt':receipt})
        self.assertEqual(state['status'],'computed')
        state=control(self.store,'deliver',{'research_id':state['research_id']})
        self.assertEqual(state['status'],'completed')
        self.assertTrue(state['delivery']['files'])
        for entry in state['delivery']['files']:
            self.assertEqual(file_sha256(self.store.path(entry['path'])),entry['sha256'])
