import unittest,copy
import numpy as np
import pandas as pd
from _bootstrap import market
from dataset_store import DatasetError
from quant_factors import *
from quant_portfolio import *

class FactorTests(unittest.TestCase):
    def formed(self,obs=None,days=8):
        dates,cal,bars,states,fees,actions,o,m=market(days)
        return form_factors(o if obs is None else obs,m,sessions=dates,specs=[FactorSpec('price','mean',1,0,('close',),1,'Synthetic raw price level')],formation_times={d:d+'T15:05:00+08:00' for d in dates})
    def processed(self):return process_cross_sections(self.formed(),winsor={'method':'none'},standardize=True,min_assets=5)
    def test_causal_factor_append_future_invariance(self):
        a=self.formed(days=8);b=self.formed(days=10);pd.testing.assert_frame_equal(a,b[b.session.isin(a.session)].reset_index(drop=True))
    def test_future_availability_rejected(self):
        o=market()[6];o.loc[0,'available_at']='2026-01-01T00:00:00Z';out=self.formed(o)
        self.assertEqual(out.iloc[0].reason,'INPUT_NOT_AVAILABLE')
    def test_missing_lookback_not_filled(self):
        dates,*_=market();o=market()[6].iloc[1:];out=self.formed(o);self.assertEqual(out.iloc[0].reason,'HISTORY_GAP')
    def test_standardization_is_cross_sectional(self):
        out=self.processed()
        for _,g in out.groupby('session'):self.assertAlmostEqual(g.processed_value.astype(float).mean(),0,places=12);self.assertAlmostEqual(g.processed_value.astype(float).std(ddof=0),1,places=12)
    def test_neutralization_and_unknown_controls(self):
        f=self.formed();controls=f[['session','security_id']].copy();controls['industry']=['i' if x<'d' else 'j' for x in controls.security_id]
        controls['industry_available_at']=controls.session+'T09:00:00+08:00'
        out=process_cross_sections(f,winsor={'method':'none'},standardize=False,min_assets=5,controls=controls,neutralize=('industry',))
        joined=out.merge(controls,on=['session','security_id'])
        for _,g in joined.groupby(['session','industry']):self.assertAlmostEqual(g.processed_value.astype(float).mean(),0,places=10)
    def test_constant_cross_section_has_reason(self):
        f=self.formed();f['value']=1.;out=process_cross_sections(f,winsor={'method':'none'},standardize=True,min_assets=5)
        self.assertTrue((out.reason=='CONSTANT_FACTOR').all())
    def test_momentum_uses_complete_calendar(self):
        dates,_,_,_,_,_,o,m=market()
        f=form_factors(o,m,sessions=dates,specs=[FactorSpec('mom','momentum',2,1,('close',),1,'2 returns skipping latest session')],formation_times={d:d+'T15:05:00+08:00' for d in dates})
        r=f[(f.session==dates[3])&(f.security_id=='a')].iloc[0];self.assertAlmostEqual(r.value,10.2/10-1)
    def test_nonpositive_ratio_denominator(self):
        dates,_,_,_,_,_,o,m=market();o['den']=0.
        f=form_factors(o,m,sessions=dates,specs=[FactorSpec('ratio','ratio',1,0,('close','den'),1,'Explicit ratio')],formation_times={d:d+'T15:05:00+08:00' for d in dates})
        self.assertTrue((f.reason=='NONPOSITIVE_RATIO_DENOMINATOR').all())
    def test_hac_zero_variance_explicit(self):self.assertIsNone(hac_mean([1,1,1],lags=1)['t'])
    def test_group_assignment_not_changed_by_missing_label(self):
        p=self.processed();labels=p[['session','security_id']].copy();labels['entry_at']=labels.session+'T16:00:00+08:00';labels['exit_at']=labels.session+'T17:00:00+08:00';labels['label_available_at']=labels.exit_at;labels['forward_return']=np.arange(len(labels))*.001
        a=evaluate_factors(p,labels,groups=2,hac_lags=1,min_assets=5);labels.loc[0,'forward_return']=None
        b=evaluate_factors(p,labels,groups=2,hac_lags=1,min_assets=5);self.assertEqual(a['groups'].group.tolist(),b['groups'].group.tolist());self.assertTrue(pd.isna(b['ic'].ic.iloc[0]))

class PortfolioTests(unittest.TestCase):
    formed=FactorTests.formed
    processed=FactorTests.processed
    def model(self):
        p=self.processed();dates=sorted(p.session.unique())
        return p,fit_combiner(p,None,names=['price'],method='equal',fit_at=dates[2]+'T15:05:00+08:00',training_start=dates[0],training_end=dates[2],ridge_alpha=0.)
    def test_scores_only_after_training(self):
        p,m=self.model();s=score_combiner(p,m);self.assertTrue(s[s.session<=m['training_end']].score.isna().all())
    def test_model_tamper_rejected(self):
        p,m=self.model();m['coefficients']=[999]
        with self.assertRaises(DatasetError):score_combiner(p,m)
    def test_selection_is_deterministic_and_does_not_shrink_requested_count(self):
        p,m=self.model();s=score_combiner(p,m);out=select_assets(s,top_n=2)
        for d,g in out.groupby('session'):
            if d>m['training_end']:self.assertEqual(g.selected.sum(),2)
        out=select_assets(s,top_n=10);self.assertFalse(out.selected.any())
    def test_equal_weights_and_residual_cash(self):
        r=construct_weights(assets=['a','b'],method='equal',investment=.8,max_weight=.5)
        self.assertEqual(r['weights'],{'a':.4,'b':.4});self.assertAlmostEqual(r['cash_weight'],.2)
    def test_known_min_variance_solution(self):
        r=construct_weights(assets=['a','b'],method='min_variance',investment=1.,max_weight=1.,covariance=[[1,0],[0,4]])
        self.assertAlmostEqual(r['weights']['a'],.8,places=5)
    def test_infeasible_constraint_is_not_relaxed(self):
        with self.assertRaises(DatasetError):construct_weights(assets=['a'],method='equal',investment=1.,max_weight=.2)
    def test_industry_bounds_checked_for_equal_policy(self):
        with self.assertRaises(DatasetError):construct_weights(assets=['a','b'],method='equal',investment=1.,max_weight=1.,industries={'a':'i','b':'i'},industry_bounds={'i':(0,.5)})
    def test_invalid_covariance_rejected(self):
        with self.assertRaises(DatasetError):construct_weights(assets=['a','b'],method='min_variance',investment=1.,max_weight=1.,covariance=[[1,2],[2,1]])
    def test_covariance_does_not_use_future_available_return(self):
        r=pd.DataFrame([{'session':f'2025-01-0{i}','security_id':a,'return':i*.01,'available_at':f'2025-01-0{i}T16:00:00+08:00'} for i in range(2,5) for a in ['a','b']])
        c=estimate_covariance(r,assets=['a','b'],decision_at='2025-01-04T15:00:00+08:00',lookback=3,min_observations=2,diagonal_shrinkage=.2)
        self.assertEqual(c['last_session'],'2025-01-03')

if __name__=='__main__':unittest.main()
