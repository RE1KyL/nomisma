import asyncio,json,os,uuid,subprocess,sys,time
from unittest.mock import patch
import anyio
import pandas as pd
from _bootstrap import AnalysisTest,HERE
from dataset_store import DatasetError,file_sha256
from mcp_jobs import run_job,read_json,write_json,recover,SnapshotStore,changed_files,validate_candidate
from mcp_contract import error_result,validate


class SafetyTests(AnalysisTest):
    def test_failed_request_replays_same_public_receipt(self):
        import server
        from types import SimpleNamespace
        args=self.request();args['parameters']['columns']=['nonexistent']
        params=SimpleNamespace(name='nomisma_analysis_describe',arguments=args)
        a=asyncio.run(server.call_tool(None,params));b=asyncio.run(server.call_tool(None,params))
        self.assertTrue(a.is_error);self.assertEqual(a.structured_content,b.structured_content)
    def test_windows_job_object_closes_nested_owned_process(self):
        if os.name!='nt':self.skipTest('Windows Job Object')
        from mcp_process import Worker
        original=subprocess.Popen;pidfile=self.base/'owned-child.json'
        def spawn_fixed(*args,**kwargs):
            return original([sys.executable,str(HERE/'tree_fixture.py'),str(pidfile)],**kwargs)
        with patch('mcp_process.subprocess.Popen',spawn_fixed):worker=Worker()
        try:
            worker.start('.nomisma/jobs/job_'+'a'*32)
            for _ in range(100):
                if pidfile.exists():break
                time.sleep(.02)
            self.assertTrue(pidfile.exists());pid=json.loads(pidfile.read_text('utf8'))['pid']
            import ctypes
            from ctypes import wintypes as w
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
            kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.CloseHandle.argtypes=[w.HANDLE]
            handle=kernel.OpenProcess(0x100000,False,pid);self.assertTrue(handle)
            try:
                worker.close();self.assertEqual(kernel.WaitForSingleObject(handle,5000),0)
            finally:kernel.CloseHandle(handle)
        finally:worker.close()

    def request(self):
        source=self.dataset(pd.DataFrame({'x':[2.,4.,6.]}))
        return {'dataset':{'dataset_id':source.metadata['dataset_id'],'sha256':source.metadata['sha256']},
                'parameters':{'columns':['x'],'missing_policy':'reject'},'request_id':str(uuid.uuid4())}

    def test_cancel_after_prepare_before_commit(self):
        args=self.request();events=[]
        async def test():
            with anyio.CancelScope() as scope:
                def event(name,**fields):
                    events.append(name)
                    if name=='worker_prepared':scope.cancel()
                await run_job(self.store,'nomisma_analysis_describe',args,uuid.uuid4().hex,event)
            self.assertTrue(scope.cancelled_caught)
        asyncio.run(test())
        self.assertIn('worker_prepared',events);self.assertIn('operation_cancelled',events)
        self.assertFalse(list(self.root.glob('analyses/*/an_*/metadata.json')))

    def test_cancel_after_linearized_commit_keeps_receipt_and_replay(self):
        args=self.request();results=[]
        async def test():
            with anyio.CancelScope() as scope:
                def event(name,**fields):
                    if name=='operation_committed':scope.cancel()
                results.append(await run_job(self.store,'nomisma_analysis_describe',args,uuid.uuid4().hex,event))
        asyncio.run(test());self.assertTrue(results[0]['ok'])
        replay=asyncio.run(run_job(self.store,'nomisma_analysis_describe',args,uuid.uuid4().hex))
        self.assertEqual(replay,results[0]);self.assertEqual(len(list(self.root.glob('analyses/*/an_*/metadata.json'))),1)

    def test_input_changed_after_prepare_is_rejected(self):
        args=self.request();path=self.store.path(self.store.metadata(args['dataset']['dataset_id'])['current_file_path'])
        def event(name,**fields):
            if name=='worker_prepared':path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaises(DatasetError) as caught:asyncio.run(run_job(self.store,'nomisma_analysis_describe',args,uuid.uuid4().hex,event))
        self.assertEqual(caught.exception.code,'STALE_DATASET');self.assertFalse(list(self.root.glob('analyses/*/an_*/metadata.json')))

    def test_parent_readback_failure_rolls_back_no_success_marker(self):
        args=self.request()
        import mcp_jobs
        original=mcp_jobs.validate_candidate
        def verify(store,*rest):
            if not isinstance(store,SnapshotStore):raise DatasetError('ROUNDTRIP_MISMATCH','Injected parent readback failure')
            return original(store,*rest)
        with patch('mcp_jobs.validate_candidate',verify):
            with self.assertRaises(DatasetError):asyncio.run(run_job(self.store,'nomisma_analysis_describe',args,uuid.uuid4().hex))
        self.assertFalse(list(self.root.glob('analyses/*/an_*/metadata.json')))
        self.assertFalse((self.root/'.nomisma/requests'/args['request_id']/'committed.json').exists())

    def test_recovery_does_not_delete_uncertain_file_even_with_matching_hash(self):
        directory=self.store.directory('.nomisma/requests/'+str(uuid.uuid4()))
        write_json(directory/'request.json',{'fingerprint':'test','execution_id':'a'*32})
        target=self.store.directory('datasets/exports')/'untouched.csv';target.write_text('user bytes',encoding='utf8')
        write_json(directory/'prepared.json',{'files':{'datasets/exports/untouched.csv':file_sha256(target)}})
        with self.assertRaises(DatasetError) as caught:recover(self.store,directory,'test')
        self.assertEqual(caught.exception.code,'RECOVERY_CONFLICT');self.assertEqual(target.read_text('utf8'),'user bytes')

    def test_recovery_removes_only_identity_recorded_uncommitted_files(self):
        directory=self.store.directory('.nomisma/requests/'+str(uuid.uuid4()));(directory/'owned').mkdir()
        write_json(directory/'request.json',{'fingerprint':'test','execution_id':'a'*32})
        target=self.store.directory('datasets/exports')/'own.csv';target.write_text('owned bytes',encoding='utf8');info=target.stat()
        digest=file_sha256(target);relative='datasets/exports/own.csv'
        write_json(directory/'prepared.json',{'files':{relative:digest}})
        write_json(directory/'owned/0.json',{'path':relative,'sha256':digest,'device':str(info.st_dev),'inode':str(info.st_ino)})
        result=recover(self.store,directory,'test');self.assertEqual(result['error']['code'],'REQUEST_INTERRUPTED');self.assertFalse(target.exists())

    def test_missing_or_stale_cleaning_quality_report_rejected(self):
        args=self.request();args['parameters']={'steps':[{'operation':'drop_duplicates'}]}
        with self.assertRaises(DatasetError):validate('nomisma_dataset_clean',args,'finance')
        args.update(quality_report={'quality_report_id':'qr_'+'a'*32,'sha256':'b'*64},cleaning_plan={'reason':'test','expected_impact':'unknown'})
        validate('nomisma_dataset_clean',args,'finance')
        with self.assertRaises(DatasetError):asyncio.run(run_job(self.store,'nomisma_dataset_clean',args,uuid.uuid4().hex))

    def test_candidate_link_and_extra_output_rejected(self):
        root=self.store.directory('.nomisma/test-candidate');view=SnapshotStore(self.store,root)
        (root/'extra.txt').write_text('not an artifact',encoding='utf8')
        files=changed_files(view,{})
        result={'ok':True,'summary':'probe','data':{'execution_id':'a'*32},'artifacts':[],'warnings':[],'error':None}
        with self.assertRaises(DatasetError):validate_candidate(view,'nomisma_dataset_inspect',{},result,files)

    def test_source_version_and_unverified_interface_are_closed(self):
        from akshare_registry import verified_source
        for source in ['cn_equity_eastmoney_history','stock_zh_a_hist','__import__']:
            with self.assertRaises(DatasetError):verified_source(source)
        from mcp_contract import validate
        for interface in ['stock_zh_index_daily','cn_equity_eastmoney_history']:
            with self.assertRaises(DatasetError):validate('nomisma_akshare_fetch',{'parameters':{'interface_name':interface,'symbol':'sh000300'},'request_id':str(uuid.uuid4())},'finance')
