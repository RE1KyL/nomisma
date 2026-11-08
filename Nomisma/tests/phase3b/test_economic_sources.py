import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from _bootstrap import ProjectTest
from dataset_io import load_dataset
from dataset_store import DatasetError, ProjectDatasetStore, file_sha256
from economic_sources import acquire, discover, status
from mcp_contract import validate, visible
from mcp_jobs import run_job


class SourceFixture:
    def __init__(self, *documents):
        self.documents=iter(documents)
        self.requests=[]

    def open(self, request, timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps(next(self.documents)).encode())


class EconomicSourceTests(ProjectTest):
    def setUp(self):
        super().setUp()
        self.root=self.other
        env=patch.dict(os.environ,{'NOMISMA_PROJECT_ID':'project_economic','NOMISMA_MODE':'economic',
                                  'NOMISMA_WORKSPACE':str(self.root)})
        env.start();self.addCleanup(env.stop);self.store=ProjectDatasetStore()

    def read(self, receipt):
        return load_dataset(self.store,receipt['dataset_id'],expected_sha256=receipt['sha256'])

    def test_world_bank_registered_snapshot(self):
        code='NY.GDP.MKTP.CD'
        source=SourceFixture([{'pages':1,'lastupdated':'2025-01-01'},
            [{'date':str(y),'value':v,'indicator':{'id':code}} for y,v in ((2019,2),(2020,3),(2021,None),(2022,5))]],
            [{'pages':1},[{'id':code,'name':'GDP (current US$)','unit':'','sourceNote':'Current dollars'}]])
        result=acquire(self.store,{'source':'world_bank','indicator_id':code,'country_or_region':'CHN',
                                    'start_date':'2020-01-01','end_date':'2021-12-31'},opener=source)
        frame,meta=self.read(result)
        self.assertEqual(len(frame),2)
        self.assertEqual(frame.value.iloc[0],3)
        self.assertEqual(meta['economic_source']['unit'],'Current USD')
        self.assertIsNone(meta['economic_source']['release_date'])
        self.assertEqual(meta['economic_source']['missing_values'],1)
        self.assertEqual(parse_qs(urlsplit(source.requests[0].full_url).query)['date'],['2020:2021'])
        self.assertEqual(file_sha256(self.store.path(meta['original_file_path'])),meta['original_sha256'])

    def test_bls_public_series_without_key(self):
        code='CUUR0000SA0'
        source=SourceFixture({'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':code,
            'data':[{'year':'2024','period':'M01','value':'305.1'},{'year':'2024','period':'M13','value':'310'}]}]}})
        result=acquire(self.store,{'source':'bls','indicator_id':code,'start_date':'2024-01-01','end_date':'2024-12-31'},opener=source)
        self.assertEqual(self.read(result)[0].value.tolist(),[305.1])
        self.assertEqual(json.loads(source.requests[0].data),{'seriesid':[code],'startyear':'2024','endyear':'2024'})

    def test_keyed_sources_handle_missing_configuration_and_redact_echo(self):
        for source,code in [('fred','CPIAUCSL'),('bea','T10105:1')]:
            self.assertEqual(status(source,None)['status'],'credential_required')
            with self.assertRaises(DatasetError) as error:
                acquire(self.store,{'source':source,'indicator_id':code,'start_date':'2020-01-01','end_date':'2020-12-31'})
            self.assertEqual(error.exception.code,'SOURCE_CREDENTIAL_REQUIRED')
        canary='a'*32
        f=SourceFixture({'seriess':[{'id':'CPIAUCSL','title':'Fixture','units':'Index','seasonal_adjustment':'SA'}]},
                        {'realtime_start':'2025-01-01','count':1,'observations':[{'date':'2020-01-01','value':'255'}],'echo':canary})
        bea_canary='11111111-2222-3333-4444-555555555555'
        bea=SourceFixture({'BEAAPI':{'Request':{'UserID':bea_canary},'Results':{'Data':[
             {'LineNumber':'1','TimePeriod':'2020Q1','DataValue':'1,024.2','CL_UNIT':'USD','UNIT_MULT':'6'}]}}})
        for kind,code,opener,expected,key in [('fred','CPIAUCSL',f,255,canary),('bea','T10105:1',bea,1024.2,bea_canary)]:
            with patch('economic_sources._credential',return_value=key):
                result=acquire(self.store,{'source':kind,'indicator_id':code,'start_date':'2020-01-01','end_date':'2020-12-31'},opener=opener)
                frame,meta=self.read(result)
                self.assertEqual(frame.value.tolist(),[expected])
                self.assertNotIn(key,self.store.path(meta['original_file_path']).read_text('utf8'))
                self.assertNotIn(key,json.dumps(meta))

    def test_nbs_import_and_discovery_through_real_worker(self):
        self.assertTrue(discover('CPI',country='CHN'))
        source=self.root/'nbs-export.csv';source.write_text('month,value\n2020-01-01,105.4\n2020-02-01,105.2\n',encoding='utf8')
        args={'parameters':{'source_path':source.name,'expected_sha256':file_sha256(source),'indicator_id':'CPI',
             'date_column':'month','value_column':'value','frequency':'monthly','unit':'Previous year same month=100',
             'start_date':'2020-01-01','end_date':'2020-01-31'},'request_id':str(uuid.uuid4())}
        validate('nomisma_macro_import_nbs',args,'economic')
        receipt=asyncio.run(run_job(self.store,'nomisma_macro_import_nbs',args,uuid.uuid4().hex))
        frame,meta=self.read(receipt['data'])
        self.assertEqual(frame.value.tolist(),[105.4])
        self.assertEqual(meta['original_sha256'],file_sha256(source))
        self.assertEqual(meta['economic_source']['provenance'],'user_identified_official_export')
        for mode in ('finance','big_data_explore'):
            self.assertNotIn('nomisma_macro_fetch',visible(mode))

    @unittest.skipUnless(sys.platform=='win32','Windows CurrentUser DPAPI')
    def test_actual_dpapi_readiness_and_local_only_rejection(self):
        helper=Path(__file__).resolve().parents[2]/'launcher/credential_dpapi.py'
        payload=b'abc0123456789abc0123456789abc012'
        directory=self.state/'data-source-credentials'
        directory.mkdir()
        sealed=subprocess.run([sys.executable,'-I','-S','-B',str(helper),'encrypt'],input=payload,
                              capture_output=True,check=True,timeout=10).stdout
        credential=directory/'fred.secret'
        credential.write_bytes(sealed)
        self.assertNotIn(payload,sealed)
        self.assertEqual(status('fred',self.state)['status'],'ready_to_request')
        policy=self.state/'privacy.v1.json'
        policy.write_text('{"version":1,"mode":"local_only"}',encoding='utf8')
        with patch.dict(os.environ,{'NOMISMA_PRIVACY_SETTINGS':str(policy)}):
            with self.assertRaises(DatasetError) as denied:
                acquire(self.store,{'source':'fred','indicator_id':'CPIAUCSL','start_date':'2020-01-01','end_date':'2020-12-31'},
                        credential_root=self.state)
            self.assertEqual(denied.exception.code,'LOCAL_ONLY')
        credential.write_bytes(b'corrupt fixture')
        self.assertEqual(status('fred',self.state)['status'],'credential_required')
