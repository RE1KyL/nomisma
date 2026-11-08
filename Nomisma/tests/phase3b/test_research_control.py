import asyncio,copy,json,os,unittest,uuid
from unittest.mock import patch
from _bootstrap import ProjectTest
from dataset_store import DatasetError,ProjectDatasetStore,file_sha256
from dataset_io import import_dataset
from research_control import control,resolve,ResearchStore,plan_validate,verify_receipt
from research_verification import verify_claim,numeric_claims
from mcp_jobs import run_job

def task(key,tool,args,deps=None,outputs=None):
    return {'task_id':key,'task_name':key,'objective':'Controlled test computation','tool':tool,'arguments':args,
       'dependencies':deps or [],'expected_outputs':outputs or ['receipt'],'validation_criteria':['receipt','artifacts','warnings']}

class ResearchTests(ProjectTest):
    def dataset(self):
        p=self.root/'input.csv';p.write_text('x,y\n0,1\n1,2\n2,2\n3,4\n',encoding='utf8')
        r=import_dataset(self.store,source_path='input.csv',expected_sha256=file_sha256(p),encoding='utf-8',delimiter='comma')
        return {'dataset_id':r['dataset_id'],'sha256':r['sha256']}
    def plan(self,tasks=None):
        ref=self.dataset()
        if tasks is None:tasks=[task('inspect','nomisma_dataset_inspect',{'dataset':ref,'parameters':{'sample_rows':2}},outputs=['dataset'])]
        return {'objective':'Synthetic research goal','mode':self.store.context['mode'],'locked_constraints':{'scope':'association_only'},'tasks':tasks}
    def create(self,plan=None):return control(self.store,'create',{'plan':plan or self.plan()})
    def confirm(self,state):return control(self.store,'confirm',{'research_id':state['research_id'],'version':state['version']})
    def perform(self,state,key):
        t=state['tasks'][key];args=resolve(t['arguments'],state['tasks']);call='call_'+uuid.uuid4().hex;permission='p_'+uuid.uuid4().hex
        state=control(self.store,'admit',{'tool':t['tool'],'arguments':args,'tool_call_id':call,'permission_id':permission})
        state=control(self.store,'decision',{'permission_id':permission,'allow':True})
        result=asyncio.run(run_job(self.store,t['tool'],args,uuid.uuid4().hex))
        return control(self.store,'result',{'tool_call_id':call,'receipt':result})
    def test_plan_confirmation_is_not_tool_approval(self):
        s=self.create();t=s['tasks']['inspect'];self.assertEqual(s['status'],'proposed')
        with self.assertRaises(DatasetError):control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'c','permission_id':'p'})
        s=self.confirm(s);self.assertEqual(s['tasks']['inspect']['status'],'ready');self.assertEqual(s['calls'],0)
    def test_exact_arguments_required(self):
        s=self.confirm(self.create());t=s['tasks']['inspect'];wrong=copy.deepcopy(t['arguments']);wrong['parameters']['sample_rows']=5
        with self.assertRaises(DatasetError):control(self.store,'admit',{'tool':t['tool'],'arguments':wrong,'tool_call_id':'c','permission_id':'p'})
    def test_independent_readonly_tasks_can_wait_separately_but_writes_conflict(self):
        ref=self.dataset()
        tasks=[task('inspect','nomisma_dataset_inspect',{'dataset':ref,'parameters':{'sample_rows':2}}),
            task('catalog','nomisma_akshare_catalog',{}),task('quality','nomisma_dataset_quality',{'dataset':ref,'parameters':{},'save_report':True})]
        s=self.confirm(self.create(self.plan(tasks)))
        for key in ('inspect','catalog'):
            t=s['tasks'][key];s=control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':key,'permission_id':key})
        self.assertEqual(sum(t['status']=='waiting_approval' for t in s['tasks'].values()),2)
        t=s['tasks']['quality']
        with self.assertRaises(DatasetError):control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'q','permission_id':'q'})
    def test_cycle_and_fake_tool_are_rejected(self):
        p=self.plan();p['tasks'][0]['dependencies']=['inspect']
        with self.assertRaises(DatasetError):self.create(p)
        p=self.plan();p['tasks'][0]['tool']='nomisma_arbitrary_python'
        with self.assertRaises(DatasetError):self.create(p)
    def test_missing_dataset_and_changed_hash(self):
        p=self.plan();p['tasks'][0]['arguments']['dataset']['sha256']='0'*64
        with self.assertRaises(DatasetError):self.create(p)
    def test_deny_is_not_execution_or_research_cancellation(self):
        s=self.confirm(self.create());t=s['tasks']['inspect']
        control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'c','permission_id':'p'})
        s=control(self.store,'decision',{'permission_id':'p','allow':False})
        self.assertEqual(s['tasks']['inspect']['reason'],'USER_DENIED');self.assertEqual(s['status'],'active')
        self.assertFalse((self.root/'.nomisma/requests').exists())
    def test_retry_after_deny_needs_fresh_approval(self):
        s=self.confirm(self.create());t=s['tasks']['inspect'];control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'c','permission_id':'p'})
        s=control(self.store,'decision',{'permission_id':'p','allow':False});s=control(self.store,'retry',{'research_id':s['research_id'],'task_id':'inspect'})
        self.assertEqual(s['tasks']['inspect']['status'],'ready')
        with self.assertRaises(DatasetError):control(self.store,'decision',{'permission_id':'p','allow':True})
    def test_actual_worker_receipt_and_delivery(self):
        s=self.perform(self.confirm(self.create()),'inspect');self.assertEqual(s['status'],'computed')
        s=control(self.store,'deliver',{'research_id':s['research_id']});self.assertEqual(s['status'],'completed')
        for f in s['delivery']['files']:self.assertEqual(file_sha256(self.store.path(f['path'])),f['sha256'])
        again=control(self.store,'deliver',{'research_id':s['research_id']})
        self.assertEqual(s['delivery'],again['delivery'])
    def test_idle_cancel_finishes_without_waiting_for_a_prompt(self):
        s=self.confirm(self.create());s=control(self.store,'cancel',{'research_id':s['research_id']})
        self.assertEqual(s['status'],'cancelled');self.assertEqual(s['tasks']['inspect']['status'],'cancelled')
    def test_modified_journal_is_rejected(self):
        s=self.create();self.confirm(s)
        p=self.store.path('.nomisma/research/'+s['research_id']+'/events/000000.json')
        raw=p.read_bytes();p.write_bytes(raw.replace(b'Synthetic research goal',b'Modified research goal'))
        with self.assertRaises(DatasetError):control(self.store,'list',{})
    def test_output_tamper_blocks_delivery(self):
        s=self.perform(self.confirm(self.create()),'inspect');t=s['tasks']['inspect'];ref=t['verified']['dataset']
        meta=self.store.metadata(ref['dataset_id']);self.store.path(meta['current_file_path']).write_bytes(b'changed')
        with self.assertRaises(DatasetError):control(self.store,'deliver',{'research_id':s['research_id']})
    def test_waiting_approval_resume_invalidates_old_permission(self):
        s=self.confirm(self.create());t=s['tasks']['inspect'];control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'c','permission_id':'p'})
        s=control(self.store,'interrupted',{});s=control(self.store,'resume',{'research_id':s['research_id']})
        self.assertEqual(s['tasks']['inspect']['status'],'ready')
        with self.assertRaises(DatasetError):control(self.store,'decision',{'permission_id':'p','allow':True})
    def test_aborted_acp_notification_before_interruption_expires_approval(self):
        s=self.confirm(self.create());t=s['tasks']['inspect'];control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'c','permission_id':'p'})
        s=control(self.store,'result',{'tool_call_id':'c','receipt':None})
        self.assertEqual(s['tasks']['inspect']['reason'],'APPROVAL_EXPIRED')
        s=control(self.store,'interrupted',{});s=control(self.store,'resume',{'research_id':s['research_id']})
        self.assertEqual(s['tasks']['inspect']['status'],'ready')
    def test_terminal_cancel_result_retains_cancelled_task_status(self):
        from mcp_contract import error_result
        s=self.confirm(self.create());t=s['tasks']['inspect'];control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'c','permission_id':'p'})
        control(self.store,'decision',{'permission_id':'p','allow':True});control(self.store,'cancel',{'research_id':s['research_id']})
        s=control(self.store,'result',{'tool_call_id':'c','receipt':error_result('TASK_CANCELLED',uuid.uuid4().hex,'tool_error')})
        self.assertEqual(s['tasks']['inspect']['status'],'cancelled')
        s=control(self.store,'interrupted',{});self.assertEqual(s['status'],'cancelled')
    def test_unknown_running_outcome_is_not_reexecuted(self):
        s=self.confirm(self.create());t=s['tasks']['inspect'];control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'c','permission_id':'p'})
        control(self.store,'decision',{'permission_id':'p','allow':True});s=control(self.store,'interrupted',{})
        self.assertEqual(s['tasks']['inspect']['reason'],'OUTCOME_UNKNOWN')
        with self.assertRaises(DatasetError):control(self.store,'retry',{'research_id':s['research_id'],'task_id':'inspect'})
    def test_cas_and_duplicate_owner(self):
        s=self.create();old=s['version'];self.confirm(s)
        with self.assertRaises(DatasetError):control(self.store,'pause',{'research_id':s['research_id'],'version':old})
        with self.assertRaises(DatasetError):self.create(self.plan())
    def test_cross_session_cannot_control_another_research(self):
        s=self.create()
        with patch.dict(os.environ,{'NOMISMA_SESSION_ID':'other_session'}):
            other=ProjectDatasetStore()
            with self.assertRaises(DatasetError):control(other,'confirm',{'research_id':s['research_id']})
            self.assertEqual(control(other,'admit',{}),{'managed':False})
    def test_revision_returns_to_direction_confirmation(self):
        s=self.confirm(self.create());p=self.plan();p['locked_constraints']['scope']='new_sample_definition'
        s=control(self.store,'revise',{'research_id':s['research_id'],'plan':p});self.assertEqual(s['status'],'proposed');self.assertEqual(s['plan_version'],2)
    def test_pause_rejects_tool_admission(self):
        s=self.confirm(self.create());s=control(self.store,'pause',{'research_id':s['research_id']});t=s['tasks']['inspect']
        with self.assertRaises(DatasetError):control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'c','permission_id':'p'})
    def test_failed_dependency_does_not_block_independent_ready_task(self):
        ref=self.dataset();args={'dataset':ref,'parameters':{'sample_rows':2}}
        p=self.plan([task('first','nomisma_dataset_inspect',args),task('dependent','nomisma_dataset_inspect',args,['first']),task('independent','nomisma_akshare_catalog',{})])
        s=self.confirm(self.create(p));t=s['tasks']['first'];control(self.store,'admit',{'tool':t['tool'],'arguments':t['arguments'],'tool_call_id':'c','permission_id':'p'})
        s=control(self.store,'decision',{'permission_id':'p','allow':False});self.assertEqual(s['tasks']['dependent']['reason'],'DEPENDENCY_FAILED');self.assertEqual(s['tasks']['independent']['status'],'ready')
    def test_numeric_claim_does_not_accept_wrong_scale_or_display(self):
        doc={'coefficient':.9,'p_value':None};c=numeric_claims(doc)[0];self.assertTrue(verify_claim(doc,c))
        self.assertFalse(verify_claim(doc,{**c,'value':90.}));self.assertFalse(verify_claim(doc,{**c,'display':'0.9000'}))
    def test_economic_ols_diagnostics_reference_and_full_delivery(self):
        with patch.dict(os.environ,{'NOMISMA_MODE':'economic','NOMISMA_PROJECT_ID':'project_economic','NOMISMA_WORKSPACE':str(self.other)}):
            self.store=ProjectDatasetStore();self.root=self.other;ref=self.dataset()
            a=task('ols','nomisma_econ_ols',{'dataset':ref,'parameters':{'y':'y','x_numeric':['x'],'intercept':True,'missing_policy':'reject','covariance':'classical'}},outputs=['analysis'])
            b=task('diagnose','nomisma_econ_diagnostics',{'analysis':{'analysis_id':{'$from':'ols','pointer':'/verified/analysis/analysis_id'},'sha256':{'$from':'ols','pointer':'/verified/analysis/sha256'}},'parameters':{'tests':['design']}},['ols'],['analysis'])
            s=self.confirm(self.create(self.plan([a,b])));s=self.perform(s,'ols');s=self.perform(s,'diagnose');self.assertEqual(s['status'],'computed')
            self.assertAlmostEqual(s['tasks']['ols']['receipt']['data']['facts']['coefficients'][1]['estimate'],.9)
            s=control(self.store,'deliver',{'research_id':s['research_id']});self.assertGreater(s['delivery']['claim_count'],0)
    def test_finance_returns_risk_dependency_and_exact_report_numbers(self):
        p=self.root/'prices.csv';p.write_text('date,p\n2025-01-01,100\n2025-01-02,110\n2025-01-03,99\n',encoding='utf8')
        r=import_dataset(self.store,source_path='prices.csv',expected_sha256=file_sha256(p),encoding='utf-8',delimiter='comma')
        ref={'dataset_id':r['dataset_id'],'sha256':r['sha256']}
        a=task('returns','nomisma_fin_returns',{'dataset':ref,'parameters':{'date_column':'date','price_column':'p','frequency':'daily','price_adjustment':'unknown','return_kind':'simple','missing_policy':'reject'}},outputs=['analysis'])
        b=task('risk','nomisma_fin_risk',{'analysis':{'analysis_id':{'$from':'returns','pointer':'/verified/analysis/analysis_id'},'sha256':{'$from':'returns','pointer':'/verified/analysis/sha256'}},'parameters':{'metrics':['max_drawdown']}},['returns'],['analysis'])
        s=self.confirm(self.create(self.plan([a,b])));s=self.perform(s,'returns');s=self.perform(s,'risk')
        self.assertAlmostEqual(s['tasks']['risk']['receipt']['data']['facts']['assets'][0]['max_drawdown']['loss'],.1)
        s=control(self.store,'deliver',{'research_id':s['research_id']})
        manifest=json.loads(self.store.path(s['delivery']['manifest_path']).read_text('utf8'))
        claims=[c for c in manifest['claims'] if c['task_id']=='risk' and c['pointer']=='/results/assets/0/max_drawdown/loss']
        self.assertEqual(len(claims),1);self.assertAlmostEqual(claims[0]['value'],.1)
        self.assertTrue(manifest['warnings']) # Unknown price adjustment remains visible.
    def test_committed_result_recovery_does_not_rerun_worker(self):
        ref=self.dataset();a=task('quality','nomisma_dataset_quality',{'dataset':ref,'parameters':{},'save_report':True})
        s=self.confirm(self.create(self.plan([a])));t=s['tasks']['quality'];args=t['arguments']
        control(self.store,'admit',{'tool':t['tool'],'arguments':args,'tool_call_id':'c','permission_id':'p'});control(self.store,'decision',{'permission_id':'p','allow':True})
        r=asyncio.run(run_job(self.store,t['tool'],args,uuid.uuid4().hex));s=control(self.store,'interrupted',{})
        self.assertEqual(s['tasks']['quality']['status'],'succeeded');self.assertEqual(s['tasks']['quality']['receipt']['data']['execution_id'],r['data']['execution_id'])
    def test_saved_quality_reference_drives_reviewed_cleaning_without_overwrite(self):
        ref=self.dataset();a=task('quality','nomisma_dataset_quality',{'dataset':ref,'parameters':{},'save_report':True},outputs=['quality_report'])
        b=task('clean','nomisma_dataset_clean',{'dataset':ref,'parameters':{'steps':[{'operation':'sort_values','by':['x'],'ascending':True,'na_position':'last'}]},
            'quality_report':{'quality_report_id':{'$from':'quality','pointer':'/verified/quality_report/quality_report_id'},'sha256':{'$from':'quality','pointer':'/verified/quality_report/sha256'}},
            'cleaning_plan':{'reason':'Explicitly reviewed sort','expected_impact':'Reorder rows only'}},['quality'],['dataset'])
        s=self.confirm(self.create(self.plan([a,b])));s=self.perform(s,'quality');s=self.perform(s,'clean')
        self.assertEqual(s['status'],'computed');self.assertNotEqual(s['tasks']['clean']['verified']['dataset']['dataset_id'],ref['dataset_id'])
        self.assertEqual(self.store.metadata(ref['dataset_id'])['sha256'],ref['sha256'])

if __name__=='__main__':unittest.main()
