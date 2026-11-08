import hashlib,json,os
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from _bootstrap import AnalysisTest
from analysis_sample import prepare_sample
from analysis_statistics import describe
from analysis_result import encode_result,normalize_result,make_analysis_result,build_summary
from analysis_store import save_analysis,read_analysis
from dataset_store import DatasetError,file_sha256,ProjectDatasetStore


class ResultStoreTests(AnalysisTest):
    def result(self):
        sample=prepare_sample(self.dataset(pd.DataFrame({'x':[2,4,6]})),columns=['x'])
        return make_analysis_result(sample,describe(sample,['x']))

    def test_strict_json_and_full_fidelity_not_preview_truncation(self):
        text='研究'*1000;value={'text':text,'large_id':2**60,'n':np.int64(6),'date':pd.Timestamp('2025-01-01')}
        r=json.loads(encode_result(value));self.assertEqual(r['text'],text)
        self.assertEqual(r['large_id'],{'type':'integer','value':str(2**60)});self.assertEqual(r['n'],6)
        for invalid in [float('nan'),float('inf'),pd.NA,pd.NaT,object(),{1:'bad'}]:
            with self.assertRaises(DatasetError):encode_result(invalid)

    def test_full_sample_identity_and_return_budgets(self):
        result=self.result();preview=[{'name':str(i),'text':'长'*600} for i in range(100)]
        receipt=build_summary(result,[],preview=preview)
        raw=encode_result(receipt)
        wire=encode_result({'content':[{'type':'text','text':raw.decode()}],'structuredContent':receipt,'isError':False})
        self.assertLessEqual(len(raw),8192);self.assertLessEqual(len(wire),20480)
        self.assertGreaterEqual(receipt['data']['preview_omitted'],80);self.assertGreater(receipt['data']['preview_cells_truncated'],0)
        self.assertEqual(receipt['data']['sample_summary']['actual_sample_size'],3)
        self.assertEqual(result['sample_positions'],[0,1,2]);self.assertEqual(len(result['execution_id']),32)
        self.assertEqual(result['software_versions']['scipy'],'1.16.3')

    def test_oversized_non_optional_receipt_is_rejected_not_silently_shortened(self):
        result=self.result();result['warnings']=['x'*30_000]
        self.code('RESOURCE_LIMIT',lambda:build_summary(result,[]))

    def test_metadata_last_round_trip_typed_csv_source_unchanged(self):
        result=self.result();metadata=self.store.metadata(result['dataset_id']);source=self.store.path(metadata['current_file_path'])
        before=source.read_bytes();events=[];publish=self.store._publish_file
        def observe(src,dst):events.append(dst.name);publish(src,dst)
        with patch.object(self.store,'_publish_file',side_effect=observe):
            receipt=save_analysis(self.store,result,tables={'statistics':pd.DataFrame({'n':pd.Series([3],dtype='Int64'),'mean':[4.]})})
        restored,manifest=read_analysis(self.store,result['analysis_id'])
        self.assertEqual(events[-1],'metadata.json');self.assertEqual(restored,normalize_result(result))
        self.assertTrue(manifest['committed']);self.assertTrue(receipt['data']['committed'])
        self.assertTrue(all(a['path'].startswith('analyses/shared/') and file_sha256(self.store.path(a['path']))==a['sha256'] for a in receipt['artifacts']))
        self.assertEqual(source.read_bytes(),before);self.assertEqual(list((self.root/'analyses/shared/.staging').iterdir()),[])

    def test_existing_analysis_never_overwritten(self):
        result=self.result();save_analysis(self.store,result)
        file=self.root/f"analyses/shared/{result['analysis_id']}/results.json";before=file.read_bytes()
        self.code('FILE_EXISTS',lambda:save_analysis(self.store,result));self.assertEqual(file.read_bytes(),before)

    def test_caller_cannot_supply_analysis_path_or_table_path(self):
        result=self.result()
        for name in ['../escape','an_'+('a'*32)+'/x',str(self.other)]:
            result['analysis_id']=name;self.code('INVALID_ARGUMENT',lambda:save_analysis(self.store,result))
        result=self.result();self.code('INVALID_PATH',lambda:save_analysis(self.store,result,tables={'../escape':pd.DataFrame({'x':[1]})}))

    def test_cross_project_result_and_read_rejected(self):
        result=self.result();save_analysis(self.store,result)
        result['project_context']['project_id']='project_economic'
        self.code('PROJECT_CONTEXT_INVALID',lambda:save_analysis(self.store,result))
        with patch.dict(os.environ,{'NOMISMA_PROJECT_ID':'project_economic','NOMISMA_MODE':'economic','NOMISMA_WORKSPACE':str(self.other)}):
            other=ProjectDatasetStore();target=other.directory('analyses/shared')/result['analysis_id']
            # Copy only our synthetic test result to test ownership, never real user data.
            import shutil
            shutil.copytree(self.root/'analyses/shared'/result['analysis_id'],target)
            self.code('PROJECT_CONTEXT_INVALID',lambda:read_analysis(other,result['analysis_id']))

    def test_precommit_cancellation_and_readback_failure_leave_no_success(self):
        result=self.result()
        def cancelled():raise DatasetError('PROMPT_CANCELLED','Synthetic precommit cancellation')
        self.code('PROMPT_CANCELLED',lambda:save_analysis(self.store,result,before_commit=cancelled))
        self.assertFalse((self.root/f"analyses/shared/{result['analysis_id']}").exists())
        with patch('analysis_store.read_canonical_csv',return_value=pd.DataFrame({'wrong':[1]})):
            self.code('ROUNDTRIP_MISMATCH',lambda:save_analysis(self.store,result,tables={'check':pd.DataFrame({'x':[4.]})}))
        self.assertFalse((self.root/f"analyses/shared/{result['analysis_id']}").exists())
        self.assertEqual(list((self.root/'analyses/shared/.staging').iterdir()),[])

    def test_failure_mid_publish_rolls_back_only_owned_files(self):
        result=self.result();sentinel=self.root/'user-important.txt';sentinel.write_text('KEEP',encoding='utf8')
        publish=self.store._publish_file;calls=[]
        def fail_second(src,dst):
            calls.append(dst.name)
            if len(calls)==2:raise PermissionError('test')
            publish(src,dst)
        with patch.object(self.store,'_publish_file',side_effect=fail_second):
            self.code('PERMISSION_DENIED',lambda:save_analysis(self.store,result))
        self.assertFalse((self.root/f"analyses/shared/{result['analysis_id']}").exists());self.assertEqual(sentinel.read_text(),'KEEP')

    def test_published_readback_failure_rolls_back_commit_marker(self):
        result=self.result()
        with patch('analysis_store.read_analysis',side_effect=DatasetError('ROUNDTRIP_MISMATCH','Injected readback failure')):
            self.code('ROUNDTRIP_MISMATCH',lambda:save_analysis(self.store,result))
        self.assertFalse((self.root/f"analyses/shared/{result['analysis_id']}").exists())

    def test_input_changed_before_commit_and_context_switch_rejected(self):
        result=self.result();metadata=self.store.metadata(result['dataset_id']);source=self.store.path(metadata['current_file_path'])
        def mutate_fixture():source.write_bytes(source.read_bytes()+b'\n')
        self.code('STALE_DATASET',lambda:save_analysis(self.store,result,before_commit=mutate_fixture))
        self.assertFalse((self.root/f"analyses/shared/{result['analysis_id']}").exists())
        result=self.result()
        def deactivate():
            entries=json.loads(self.registry.read_text());entries[0]['status']='archived';self.registry.write_text(json.dumps(entries))
        self.code('PROJECT_CONTEXT_INVALID',lambda:save_analysis(self.store,result,before_commit=deactivate))

    def test_tampered_artifact_or_expected_hash_rejected(self):
        result=self.result();save_analysis(self.store,result)
        self.code('STALE_ANALYSIS',lambda:read_analysis(self.store,result['analysis_id'],expected_sha256='0'*64))
        (self.root/f"analyses/shared/{result['analysis_id']}/analysis.md").write_text('tamper',encoding='utf8')
        self.code('STALE_ANALYSIS',lambda:read_analysis(self.store,result['analysis_id']))

    def test_uncommitted_folder_and_inconsistent_sample_rejected(self):
        result=self.result();result['sample_positions']=[0,0,1]
        self.code('ANALYSIS_INVALID',lambda:save_analysis(self.store,result))
        result=self.result();directory=self.root/'analyses/shared'/result['analysis_id'];directory.mkdir(parents=True)
        self.code('FILE_NOT_FOUND',lambda:read_analysis(self.store,result['analysis_id']))

    def test_wrong_sample_hash_and_malformed_manifest_are_stable_errors(self):
        result=self.result();result['sample_summary']['positions_sha256']='0'*64
        self.code('ANALYSIS_INVALID',lambda:save_analysis(self.store,result))
        result=self.result();save_analysis(self.store,result)
        file=self.root/f"analyses/shared/{result['analysis_id']}/metadata.json"
        file.write_text('{"committed":true,"committed":false}',encoding='utf8')
        self.code('ANALYSIS_INVALID',lambda:read_analysis(self.store,result['analysis_id']))

    def test_real_windows_junction_is_rejected(self):
        if os.name!='nt':self.skipTest('Windows reparse point check')
        import subprocess
        result=self.result();(self.root/'analyses').mkdir()
        link=self.root/'analyses/shared'
        # A single native PowerShell invocation creates only a test-owned junction.
        command='New-Item -ItemType Junction -Path $env:NMS_TEST_LINK -Target $env:NMS_TEST_TARGET -ErrorAction Stop | Out-Null'
        subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',command],
            env={**os.environ,'NMS_TEST_LINK':str(link),'NMS_TEST_TARGET':str(self.other)},check=True,capture_output=True)
        try:
            self.code('LINK_NOT_ALLOWED',lambda:save_analysis(self.store,result));self.assertEqual(list(self.other.iterdir()),[])
        finally:link.rmdir()
