import json,os,unittest
from unittest.mock import patch
import pandas as pd
from _bootstrap import ProjectTest,master,calendar
from dataset_store import DatasetError,ProjectDatasetStore,file_sha256
from dataset_io import load_dataset,import_dataset
from ashare_data import assess_gate
from ashare_intake import save_intake,IntakeProgress
from research_data_store import publish_table,publish_bundle,read_bundle,resolve_table

class StoreTests(ProjectTest):
    def part(self,frame=None):
        f=master() if frame is None else frame
        return {'frame':f,'raw_bytes':f.to_csv(index=False).encode('utf8'),'source_note':'Synthetic fixture, not certified history'}
    def save(self):return save_intake(self.store,{'security_master':self.part(),'calendar':self.part(calendar())},gate=assess_gate())
    def test_bundle_roundtrip_and_original_payload(self):
        result=self.save();ref=result['data']['bundle'];frames,m,gate=read_bundle(self.store,ref)
        self.assertTrue(result['ok']);self.assertEqual(set(frames),{'security_master','calendar'})
        self.assertFalse(gate['strict_backtest_ready'])
        for role,s in m['tables'].items():
            _,meta=resolve_table(self.store,s['dataset'])
            self.assertEqual(meta['source_validation_status'],'candidate_not_production_verified')
            self.assertTrue(self.store.path(meta['original_file_path']).read_bytes())
    def test_wide_raw_financial_source_preserved(self):
        raw=pd.DataFrame({str(i):[i] for i in range(150)}).to_csv(index=False).encode()
        frame=pd.DataFrame({'security_id':['s'],'value':[1.]})
        ref=publish_table(self.store,frame,raw_bytes=raw,role='financial_snapshot',source_note='Wide provider snapshot')
        f,meta=resolve_table(self.store,ref);self.assertEqual(f.shape,(1,2))
        self.assertEqual(self.store.path(meta['original_file_path']).read_bytes(),raw)
    def test_data_tamper_detected(self):
        r=self.save();ref=r['data']['tables']['calendar'];_,m=resolve_table(self.store,ref)
        self.store.path(m['current_file_path']).write_text('changed',encoding='utf8')
        with self.assertRaises(DatasetError):read_bundle(self.store,r['data']['bundle'])
    def test_raw_tamper_detected(self):
        r=self.save();ref=r['data']['tables']['calendar'];_,m=resolve_table(self.store,ref)
        self.store.path(m['original_file_path']).write_bytes(b'changed')
        with self.assertRaises(DatasetError):read_bundle(self.store,r['data']['bundle'])
    def test_metadata_tamper_detected(self):
        r=self.save();ref=r['data']['tables']['calendar'];p=self.store.path('datasets/metadata/'+ref['dataset_id']+'.json')
        m=json.loads(p.read_text('utf8'));m['source_note']='tampered';p.write_text(json.dumps(m),encoding='utf8')
        with self.assertRaises(DatasetError):read_bundle(self.store,r['data']['bundle'])
    def test_bundle_tamper_detected(self):
        r=self.save();ref=r['data']['bundle'];self.store.path(ref['manifest_path']).write_text('{}',encoding='utf8')
        with self.assertRaises(DatasetError):read_bundle(self.store,ref)
    def test_gate_tamper_detected(self):
        r=self.save();ref=r['data']['bundle'];_,m,_=read_bundle(self.store,ref)
        gate=next(p for p in m['files'] if p.endswith('_gate.json'));self.store.path(gate).write_text('{}',encoding='utf8')
        with self.assertRaises(DatasetError):read_bundle(self.store,ref)
    def test_missing_partition_detected(self):
        r=self.save();_,meta=resolve_table(self.store,r['data']['tables']['calendar']);self.store.path(meta['current_file_path']).unlink()
        with self.assertRaises(DatasetError):read_bundle(self.store,r['data']['bundle'])
    def test_evidence_hash_checked_and_bound(self):
        p=self.root/'evidence.txt';p.write_text('original',encoding='utf8');ref={'path':'evidence.txt','sha256':file_sha256(p)}
        r=save_intake(self.store,{'security_master':self.part()},gate=assess_gate(),evidence=[ref]);p.write_text('changed',encoding='utf8')
        with self.assertRaises(DatasetError):read_bundle(self.store,r['data']['bundle'])
    def test_cross_project_and_economic_write_denied(self):
        r=self.save();ref=r['data']['bundle']
        with patch.dict(os.environ,{'NOMISMA_MODE':'economic','NOMISMA_PROJECT_ID':'project_economic','NOMISMA_WORKSPACE':str(self.other)}):
            other=ProjectDatasetStore()
            with self.assertRaises(DatasetError):save_intake(other,{'security_master':self.part()},gate=assess_gate())
            with self.assertRaises(DatasetError):read_bundle(other,ref)
        self.assertFalse((self.other/'datasets').exists())
    def test_archive_context_rechecked(self):
        registry=json.loads(self.registry.read_text('utf8'));registry[0]['status']='archived';self.registry.write_text(json.dumps(registry),encoding='utf8')
        with self.assertRaises(DatasetError):self.save()
        self.assertFalse((self.root/'datasets').exists())
    def test_cancellation_before_write(self):
        with self.assertRaises(DatasetError) as caught:save_intake(self.store,{'security_master':self.part()},gate=assess_gate(),cancelled=lambda:True)
        self.assertEqual(caught.exception.code,'CANCELLED');self.assertFalse((self.root/'datasets').exists())
    def test_cancellation_retains_committed_upstream_ref_without_bundle(self):
        progress=IntakeProgress()
        with self.assertRaises(DatasetError):save_intake(self.store,{'security_master':self.part(),'calendar':self.part(calendar())},gate=assess_gate(),progress=progress,cancelled=lambda:bool(progress.tables))
        self.assertEqual(set(progress.tables),{'security_master'});self.assertIsNone(progress.bundle)
        resolve_table(self.store,progress.tables['security_master'])
        self.assertFalse((self.root/'datasets/metadata/artifacts').exists())
    def test_no_overwrite_and_bundle_commit_marker_last(self):
        from dataset_store import ProjectDatasetStore
        r=self.save();first=self.store.path(r['data']['bundle']['manifest_path']);digest=file_sha256(first)
        second=self.save();self.assertNotEqual(second['data']['bundle'],r['data']['bundle']);self.assertEqual(file_sha256(first),digest)
        calls=[];original=ProjectDatasetStore._publish_file
        def capture(source,dest):calls.append(dest.name);return original(source,dest)
        with patch.object(ProjectDatasetStore,'_publish_file',side_effect=capture):self.save()
        self.assertTrue(calls[-1].startswith('rb_'));self.assertFalse(calls[-1].endswith(('_gate.json','_environment.json')))
    def test_partial_bundle_publication_rolls_back_owned_files(self):
        ref=publish_table(self.store,role='security_master',**self.part());before=set(self.root.rglob('*'))
        from dataset_store import ProjectDatasetStore
        original=ProjectDatasetStore._publish_file;calls=[]
        def broken(source,dest):
            calls.append(dest)
            if len(calls)==2:raise OSError('synthetic write failure')
            return original(source,dest)
        with patch.object(ProjectDatasetStore,'_publish_file',side_effect=broken),self.assertRaises((DatasetError,OSError)):
            publish_bundle(self.store,{'security_master':ref},gate=assess_gate())
        self.assertFalse(any(p.is_file() for p in self.root.glob('datasets/metadata/artifacts/*')))
        resolve_table(self.store,ref)
    def test_legacy_dataset_import_works(self):
        p=self.root/'legacy.csv';p.write_text('code,x\n000001,2\n',encoding='utf8')
        r=import_dataset(self.store,source_path='legacy.csv',expected_sha256=file_sha256(p),encoding='utf-8',delimiter='comma')
        f,_=resolve_table(self.store,{'dataset_id':r['dataset_id'],'sha256':r['sha256']});self.assertEqual(f.code.iloc[0],'000001')
    def test_role_hash_and_path_rejection(self):
        r=self.save();ref=r['data']['tables']['calendar']
        with self.assertRaises(DatasetError):publish_bundle(self.store,{'security_master':ref},gate=assess_gate())
        with self.assertRaises(DatasetError):resolve_table(self.store,{**ref,'sha256':'0'*64})
        with self.assertRaises(DatasetError):publish_bundle(self.store,{'calendar':ref},gate=assess_gate(),evidence=[{'path':'../outside','sha256':'0'*64}])
    def test_gate_cannot_claim_strict_readiness(self):
        ref=publish_table(self.store,role='security_master',**self.part());g=assess_gate();g['strict_backtest_ready']=True
        with self.assertRaises(DatasetError):publish_bundle(self.store,{'security_master':ref},gate=g)
    def test_invalid_later_part_does_not_publish_earlier_part(self):
        parts={'security_master':self.part(),'arbitrary_role':self.part()}
        with self.assertRaises(DatasetError):save_intake(self.store,parts,gate=assess_gate())
        self.assertFalse((self.root/'datasets').exists())
    def test_provider_claim_requires_bound_receipt(self):
        p=self.part();p['source_function']='stock_zh_a_hist'
        with self.assertRaises(DatasetError):publish_table(self.store,role='security_master',**p)
    def test_reviewed_fact_requires_original_artifact(self):
        from _bootstrap import facts
        with self.assertRaises(DatasetError):save_intake(self.store,{'financial_facts':self.part(facts())},gate=assess_gate())
        self.assertFalse((self.root/'datasets').exists())
    def test_budget_and_unrecognized_source_rejected(self):
        p=self.part();p['source_function']='arbitrary'
        with self.assertRaises(DatasetError):publish_table(self.store,role='security_master',**p)
        p=self.part();p['frame']=pd.DataFrame({str(i):[1] for i in range(101)})
        with self.assertRaises(DatasetError):publish_table(self.store,role='security_master',**p)
    def test_json_manifest_shape_rejected_with_stable_code(self):
        import hashlib
        r=self.save();ref=r['data']['bundle'];p=self.store.path(ref['manifest_path']);p.write_bytes(b'[]')
        ref['sha256']=file_sha256(p)
        with self.assertRaises(DatasetError) as caught:read_bundle(self.store,ref)
        self.assertEqual(caught.exception.code,'BUNDLE_INVALID')

if __name__=='__main__':unittest.main()
