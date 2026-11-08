import copy,json,unittest,os
from unittest.mock import patch
import numpy as np
import pandas as pd
from _bootstrap import ProjectTest,market
from dataset_store import DatasetError,ProjectDatasetStore,file_sha256,utc_now
from dataset_io import write_canonical_csv
from quant_validation import walk_forward_splits,block_bootstrap,compare_runs
from quant_research import research_plan,run_research
from quant_experiments import read_result,reproduce_plan,begin_attempt,finish_attempt,list_runs

class ValidationTests(unittest.TestCase):
    def samples(self):
        dates=market(days=20)[0];rows=[]
        for t,d in enumerate(dates[:-2]):rows.append({'sample_id':str(t),'session':d,'formation_at':d+'T15:05:00+08:00',
            'label_start':dates[t+1]+'T09:30:00+08:00','label_end':dates[t+2]+'T15:00:00+08:00','label_available_at':dates[t+2]+'T15:05:00+08:00'})
        return dates,pd.DataFrame(rows)
    def test_walk_forward_purges_immature_labels(self):
        dates,s=self.samples();folds=walk_forward_splits(s,sessions=dates,train_size=6,test_size=3,step=3,embargo=1,expanding=False)
        for f in folds:
            self.assertFalse(set(f['train_ids'])&set(f['test_ids']))
            for sid in f['train_ids']:self.assertLess(pd.Timestamp(s.set_index('sample_id').loc[sid].label_available_at),pd.Timestamp(f['fit_before']))
    def test_overlapping_test_folds_rejected(self):
        dates,s=self.samples()
        with self.assertRaises(DatasetError):walk_forward_splits(s,sessions=dates,train_size=6,test_size=3,step=1,embargo=0,expanding=False)
    def test_block_bootstrap_seed_is_reproducible(self):
        a=block_bootstrap([.1,-.2,.3,.4,.2],block_length=2,draws=100,seed=7,confidence=.95)
        b=block_bootstrap([.1,-.2,.3,.4,.2],block_length=2,draws=100,seed=7,confidence=.95);self.assertEqual(a,b)
    def test_comparison_does_not_pick_holdout_winner(self):
        r=compare_runs([{'run_id':'r','parameters_hash':'p','data_hash':'d','status':'failed','metric':None,'failure_reason':'missing'}],selection_role='final_holdout')
        self.assertIsNone(r['best_run_id']);self.assertEqual(r['attempt_count'],1)

class PipelineTests(ProjectTest):
    def publish(self,frame):
        # Existing generic v1 dataset publisher, not a new Agent source adapter.
        with self.store.transaction() as t:
            p=t.file('source.csv');serial=write_canonical_csv(frame,p)
            prov={'source_type':'user_provided','source_name':'synthetic_fixture','source_function':None,
                'request_parameters':{},'fetched_at':None,'imported_at':utc_now(),'import_history':[]}
            r=self.store.commit(t,frame,dataset_name='synthetic',original_name='source.csv',current_name='source.csv',provenance=prov,parser_options=serial)
        return {'dataset_id':r['dataset_id'],'sha256':r['sha256']}
    def fixture(self,objective='strategy_evaluation'):
        dates,cal,bars,states,fees,actions,obs,membership=market()
        inputs={n:self.publish(f) for n,f in {'observations':obs,'membership':membership,'calendar':cal,'bars':bars,'market_state':states,'fee_schedule':fees}.items()}
        cfg={'sessions':dates,'formation_times':{d:d+'T15:05:00+08:00' for d in dates},
            'factors':[{'name':'price','method':'mean','window':1,'skip':0,'inputs':['close'],'direction':1,'definition':'Synthetic causal price factor'}],
            'processing':{'winsor':{'method':'none'},'standardize':True,'min_assets':5},
            'combiner':{'names':['price'],'method':'equal','fit_at':dates[2]+'T15:05:00+08:00','training_start':dates[0],'training_end':dates[2],'ridge_alpha':0.},
            'selection':{'top_n':2},'rebalance_sessions':[dates[3]],'portfolio':{'method':'equal','investment':.8,'max_weight':.5},
            'backtest':{'initial_cash':100000.,'slippage_bps':0.,'stale_price_policy':'reject','execution_mode':'declared_scenario'},
            'performance':{'periods_per_year':252,'risk_free_annual':0.},'no_actions_confirmed':True}
        intent={'objective':objective,'research_question':'Synthetic complete chain verification','requested_methods':[]}
        plan=research_plan(intent,available_roles=inputs,locked_constraints=cfg)
        return plan,inputs,cfg
    def test_full_factor_to_backtest_pipeline_and_reproduction(self):
        plan,inputs,cfg=self.fixture();r=run_research(self.store,plan=plan,inputs=inputs,configuration=cfg,locked_constraints=cfg)
        self.assertTrue(r['ok'],r);tables,m=read_result(self.store,r['result']);self.assertIn('nav',tables);self.assertFalse(m['strict_backtest_ready'])
        reproduction=reproduce_plan(self.store,r['result']);self.assertTrue(reproduction['requires_new_execution_authorization'])
        rr=run_research(self.store,plan=reproduction['plan'],inputs=reproduction['inputs'],configuration=cfg,locked_constraints=cfg,
            experiment_id=reproduction['experiment_id'],parent_run=reproduction['parent_run'])
        self.assertTrue(rr['ok'],rr);other,_=read_result(self.store,rr['result']);pd.testing.assert_frame_equal(tables['nav'],other['nav'])
        self.assertNotEqual(r['result']['run_id'],rr['result']['run_id'])
    def test_factor_only_question_does_not_add_trading(self):
        plan,inputs,cfg=self.fixture('factor_research');r=run_research(self.store,plan=plan,inputs=inputs,configuration=cfg,locked_constraints=cfg)
        self.assertTrue(r['ok'],r);tables,_=read_result(self.store,r['result']);self.assertNotIn('nav',tables)
    def test_strategy_change_requires_new_plan(self):
        plan,inputs,cfg=self.fixture();changed=copy.deepcopy(cfg);changed['selection']['top_n']=3
        with self.assertRaises(DatasetError):run_research(self.store,plan=plan,inputs=inputs,configuration=changed,locked_constraints=changed)
    def test_cancelled_attempt_is_preserved(self):
        plan,inputs,cfg=self.fixture();r=run_research(self.store,plan=plan,inputs=inputs,configuration=cfg,locked_constraints=cfg,cancelled=lambda:True)
        self.assertFalse(r['ok']);_,m=read_result(self.store,r['result']);self.assertEqual(m['status'],'cancelled')
    def test_failed_computation_is_preserved(self):
        plan,inputs,cfg=self.fixture();cfg['backtest']['execution_mode']='strict'
        plan=research_plan(plan['intent'],available_roles=inputs,locked_constraints=cfg)
        r=run_research(self.store,plan=plan,inputs=inputs,configuration=cfg,locked_constraints=cfg)
        self.assertFalse(r['ok']);self.assertEqual(r['error_code'],'DATA_NOT_CERTIFIED');_,m=read_result(self.store,r['result']);self.assertEqual(m['status'],'failed')
    def test_tampered_output_is_rejected(self):
        plan,inputs,cfg=self.fixture('factor_research');r=run_research(self.store,plan=plan,inputs=inputs,configuration=cfg,locked_constraints=cfg)
        _,m=read_result(self.store,r['result']);path=next(iter(m['tables'].values()))['path'];self.store.path(path).write_bytes(b'changed')
        with self.assertRaises(DatasetError):read_result(self.store,r['result'])
    def test_cross_mode_is_not_allowed(self):
        plan,inputs,cfg=self.fixture()
        with patch.dict(os.environ,{'NOMISMA_MODE':'economic','NOMISMA_PROJECT_ID':'project_economic','NOMISMA_WORKSPACE':str(self.other)}):
            with self.assertRaises(DatasetError):run_research(ProjectDatasetStore(),plan=plan,inputs=inputs,configuration=cfg,locked_constraints=cfg)
    def test_registry_and_knowledge_exist_without_enabling_production_tools(self):
        from _bootstrap import TOOLS
        from mcp_contract import SPECS
        doc=json.loads((TOOLS.parent/'knowledge/finance_capabilities.json').read_text('utf8'))
        self.assertFalse(doc['production_enabled']);self.assertEqual(len(doc['entries']),4)
        self.assertTrue(all(entry['tool_id'] not in SPECS for entry in doc['entries']))
        for e in doc['entries']:self.assertTrue((TOOLS.parent/'knowledge'/e['knowledge']).is_file())
    def test_walk_forward_fits_and_scores_every_fold(self):
        from quant_factors import FactorSpec,form_factors,process_cross_sections
        dates,_,_,_,_,_,o,m=market(days=20)
        f=form_factors(o,m,sessions=dates,specs=[FactorSpec('price','mean',1,0,('close',),1,'Synthetic price')],formation_times={d:d+'T15:05:00+08:00' for d in dates})
        p=process_cross_sections(f,winsor={'method':'none'},standardize=True,min_assets=5)
        samples=[];labels=[]
        for t,d in enumerate(dates[:-2]):
            for j,a in enumerate(('a','b','c','d','e','f')):
                start=dates[t+1]+'T09:30:00+08:00';end=dates[t+2]+'T15:00:00+08:00';known=dates[t+2]+'T15:05:00+08:00'
                samples.append({'sample_id':d+'_'+a,'session':d,'security_id':a,'formation_at':d+'T15:05:00+08:00','label_start':start,'label_end':end,'label_available_at':known})
                labels.append({'session':d,'security_id':a,'entry_at':start,'exit_at':end,'label_available_at':known,'forward_return':j*.01})
        inputs={n:self.publish(frame) for n,frame in {'processed':p,'samples':pd.DataFrame(samples),'labels':pd.DataFrame(labels)}.items()}
        config={'walk_forward':{'sessions':dates,'split_options':{'train_size':6,'test_size':3,'step':3,'embargo':1,'expanding':False},
           'combiner_options':{'names':['price'],'method':'ridge','ridge_alpha':.1}}}
        intent={'objective':'out_of_sample','research_question':'Frozen model chronological validation','requested_methods':[]}
        plan=research_plan(intent,available_roles=inputs,locked_constraints=config)
        r=run_research(self.store,plan=plan,inputs=inputs,configuration=config,locked_constraints=config)
        self.assertTrue(r['ok'],r);tables,result=read_result(self.store,r['result']);self.assertGreater(tables['oos_scores'].fold.nunique(),1)
        self.assertTrue(tables['oos_scores'].reason.isna().all());self.assertEqual(len(result['summary']['models']),len(result['summary']['folds']))
    def test_history_preserves_failed_cancelled_and_started_runs(self):
        plan,inputs,cfg=self.fixture();attempt=begin_attempt(self.store,plan=plan,inputs=inputs)
        r=run_research(self.store,plan=plan,inputs=inputs,configuration=cfg,locked_constraints=cfg,cancelled=lambda:True)
        states={row['status'] for row in list_runs(self.store)}
        self.assertEqual(states,{'started_or_interrupted','cancelled'})

if __name__=='__main__':unittest.main()
