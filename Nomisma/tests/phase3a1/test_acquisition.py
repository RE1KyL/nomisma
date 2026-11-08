import json,sys,unittest
from unittest.mock import patch
from _bootstrap import TOOLS,HERE
from dataset_store import DatasetError
from ashare_acquisition import validate_request,acquire_candidate

class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        root=HERE/'temp';root.mkdir(exist_ok=True)
        p=patch('ashare_acquisition.tempfile.tempdir',str(root));p.start();self.addCleanup(p.stop)
    def request(self):return {'kind':'eastmoney_bars','code':'000001','start':'2025-01-02','end':'2025-01-15'}
    def test_valid_request(self):self.assertEqual(validate_request(self.request()),self.request())
    def test_scope_and_extra_parameters_rejected(self):
        for patcher in [{'kind':'eval'},{'code':'123456'},{'adjust':'qfq'},{'start':'2025-01-01','end':'2026-12-31'},{'code':'sh000001'}]:
            r={**self.request(),**patcher}
            with self.subTest(patcher=patcher),self.assertRaises(DatasetError):validate_request(r)
    def test_snapshots_cannot_imply_historical_date(self):
        with self.assertRaises(DatasetError):validate_request({**self.request(),'kind':'sina_balance'})
    def test_cancel_before_launch(self):
        with patch('ashare_acquisition.subprocess.Popen') as p,self.assertRaises(DatasetError) as caught:
            acquire_candidate(self.request(),cancelled=lambda:True)
        p.assert_not_called();self.assertEqual(caught.exception.code,'CANCELLED')
    def test_timeout_kills_only_owned_child(self):
        class Child:
            killed=False
            def poll(self):return None
            def kill(self):self.killed=True
            def wait(self):return 0
        child=Child()
        with patch('ashare_acquisition.subprocess.Popen',return_value=child),self.assertRaises(DatasetError) as caught:
            acquire_candidate(self.request(),timeout=.001)
        self.assertTrue(child.killed);self.assertEqual(caught.exception.code,'SOURCE_TIMEOUT')
    def test_cancel_during_owned_child(self):
        class Child:
            killed=False
            def poll(self):return None
            def kill(self):self.killed=True
            def wait(self):return 0
        counter=[];child=Child()
        def cancelled():counter.append(1);return len(counter)>1
        with patch('ashare_acquisition.subprocess.Popen',return_value=child),self.assertRaises(DatasetError) as caught:
            acquire_candidate(self.request(),cancelled=cancelled)
        self.assertTrue(child.killed);self.assertEqual(caught.exception.code,'CANCELLED')
    def test_no_production_registration(self):
        from mcp_contract import SPECS,visible,validate
        manifest=json.loads((HERE.parents[1]/'launcher/research_tool_manifest.json').read_text('utf8'))
        self.assertEqual(set(SPECS),set().union(*manifest.values()))
        for mode in ('economic','finance','big_data_explore'):
            self.assertEqual(sorted(visible(mode)),manifest[mode])
        with self.assertRaises(DatasetError):validate('nomisma_fin_data_intake',{},'finance')
    def test_raw_function_dispatch_not_accepted(self):
        with self.assertRaises(DatasetError):validate_request({'function':'stock_zh_a_hist','code':'000001','start':None,'end':None})
    def test_real_child_deadline_cleanup(self):
        root=HERE/'temp';before=set(root.iterdir())
        with self.assertRaises(DatasetError) as caught:acquire_candidate(self.request(),timeout=.01,temporary_root=root)
        self.assertEqual(caught.exception.code,'SOURCE_TIMEOUT');self.assertEqual(set(root.iterdir()),before)
    def test_worker_fixed_call_and_serialized_receipt(self):
        import tempfile,pandas as pd
        import akshare as ak
        from ashare_acquisition import _child
        from pathlib import Path
        with tempfile.TemporaryDirectory(dir=HERE/'temp') as d:
            p=Path(d)/'payload.csv';frame=pd.DataFrame({'date':['2025-01-02'],'x':[1]})
            with patch.object(ak,'stock_zh_a_hist',return_value=frame) as called:
                receipt=_child(self.request(),p)
            called.assert_called_once_with(symbol='000001',period='daily',start_date='20250102',end_date='20250115',adjust='')
            self.assertEqual(receipt['sha256'],__import__('hashlib').sha256(p.read_bytes()).hexdigest())
            self.assertEqual(receipt['status'],'candidate_not_production_verified')

if __name__=='__main__':unittest.main()
