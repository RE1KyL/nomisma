import numpy as np
import pandas as pd
from _bootstrap import AnalysisTest
from finance_factor import analyze_factor
from finance_portfolio import analyze_portfolio
from finance_common import publish_finance
from analysis_store import read_analysis


class MultiAssetTests(AnalysisTest):
    def factor_frame(self,periods=6,assets=6):
        rows=[]
        for day,date in enumerate(pd.date_range('2025-01-01',periods=periods)):
            for i in range(assets):
                rows.append({'date':date,'asset':str(i),'factor':float(i+1),'price':100*(1+(i+1)*.01)**day,'available':date})
        return pd.DataFrame(rows)

    def factor(self,frame,**kwargs):
        source=self.dataset(frame)
        parameters={'date_column':'date','asset_column':'asset','factor_column':'factor','price_column':'price',
            'available_at_column':'available','assets':list(frame.asset.unique()),'universe_status':'declared_ex_ante',
            'frequency':'daily','price_adjustment':'unknown','entry_lag':1,'horizon':1,'groups':3,**kwargs}
        return analyze_factor(self.store,source.metadata['dataset_id'],expected_sha256=source.metadata['sha256'],**parameters)

    def portfolio(self,frame,**kwargs):
        source=self.dataset(frame)
        parameters={'date_column':'date','asset_column':'asset','return_column':'r','return_kind':'simple',
            'assets':list(frame.asset.unique()),'universe_status':'declared_ex_ante','frequency':'daily',
            'weighting':'equal','metrics':['max_drawdown'],**kwargs}
        return analyze_portfolio(self.store,source.metadata['dataset_id'],expected_sha256=source.metadata['sha256'],**parameters)

    def test_cross_sectional_ic_rankic_group_returns_and_forward_dates(self):
        a=self.factor(self.factor_frame());r=a.tables['factor_evaluation']
        self.assertEqual(a.computation['valid_cross_sections'],4)
        np.testing.assert_allclose(r.ic.iloc[:4],1,atol=1e-12);np.testing.assert_allclose(r.rank_ic.iloc[:4],1)
        f=a.tables['formation'];first=f.iloc[0];self.assertEqual(first.entry_date.strftime('%Y-%m-%d'),'2025-01-02')
        self.assertEqual(first.exit_date.strftime('%Y-%m-%d'),'2025-01-03')
        groups=a.tables['group_evaluation'];np.testing.assert_allclose(groups.mean_forward_return.iloc[:3],[.015,.035,.055],atol=1e-12)
        self.assertTrue(r.p_value.isna().all())

    def test_future_factors_and_prices_do_not_change_past_group_assignment(self):
        original=self.factor_frame();changed=original.copy();changed.loc[changed.date>=pd.Timestamp('2025-01-04'),'factor']=-999.
        changed.loc[changed.date>=pd.Timestamp('2025-01-04'),'price']*=1.7
        a=self.factor(original).tables['formation'];b=self.factor(changed).tables['formation']
        past=a.date<pd.Timestamp('2025-01-04',tz='UTC')
        np.testing.assert_array_equal(a.loc[past,'group'],b.loc[past,'group'])

    def test_ties_average_rank_keep_equal_and_constant_factor_reason(self):
        f=self.factor_frame();f.loc[f.asset.isin(['0','1']),'factor']=1
        a=self.factor(f).tables['formation'];self.assertEqual(a.loc[(a.date==a.date.iloc[0])&a.asset.isin(['0','1']),'group'].nunique(),1)
        rejected=self.factor(f,ties='reject').tables['factor_evaluation'];self.assertTrue((rejected.reason=='FACTOR_TIES_REJECTED').all())
        f.factor=1.;a=self.factor(f).tables['factor_evaluation'];self.assertTrue((a.reason=='CONSTANT_FACTOR').all())

    def test_unavailable_factor_min_assets_lag_and_ex_ante_guard(self):
        self.code('SAMPLE_TOO_SMALL',lambda:self.factor(self.factor_frame(assets=4)))
        self.code('LOOKAHEAD_RISK',lambda:self.factor(self.factor_frame(),entry_lag=0))
        self.code('LOOKAHEAD_RISK',lambda:self.factor(self.factor_frame(),universe_status='unknown'))
        f=self.factor_frame();f.loc[f.asset=='0','available']=pd.Timestamp('2030-01-01')
        a=self.factor(f).tables['formation'];self.assertTrue((a.loc[a.asset=='0','eligibility_reason']=='NOT_AVAILABLE_AT_FORMATION').all())

    def test_missing_forward_outcome_never_changes_groups_or_reweights_group_mean(self):
        f=self.factor_frame();a=self.factor(f)
        f.loc[(f.asset=='0')&(f.date==pd.Timestamp('2025-01-03')),'price']=np.nan
        b=self.factor(f);np.testing.assert_array_equal(a.tables['formation']['group'],b.tables['formation']['group'])
        first=b.tables['group_evaluation'].iloc[0];self.assertEqual(first['reason'],'INCOMPLETE_FORWARD_GROUP');self.assertTrue(pd.isna(first.mean_forward_return))
        evaluation=b.tables['factor_evaluation'].iloc[0]
        self.assertEqual(evaluation.reason,'INCOMPLETE_FORWARD_CROSS_SECTION');self.assertTrue(pd.isna(evaluation.ic))

    def test_equal_weight_portfolio_compounds_portfolio_returns_not_mean_asset_risk(self):
        f=pd.DataFrame({'date':['2025-01-01','2025-01-01','2025-01-02','2025-01-02'],
            'asset':['a','b','a','b'],'r':[.1,-.1,-.1,.1]})
        a=self.portfolio(f,metrics=['volatility','max_drawdown'],periods_per_year=252)
        np.testing.assert_allclose(a.tables['portfolio'].simple_return,[0.,0.],atol=1e-15)
        self.assertEqual(a.computation['risk']['volatility']['annualized'],0)
        self.assertEqual(a.computation['risk']['max_drawdown']['loss'],0)
        self.assertAlmostEqual(a.computation['covariance_consistency']['weights_covariance_weights'],0)

    def test_constant_weights_fixed_reference_and_no_silent_normalization(self):
        f=pd.DataFrame({'date':['2025-01-01','2025-01-01','2025-01-02','2025-01-02'],
            'asset':['a','b','a','b'],'r':[.1,-.1,-.1,.1]})
        a=self.portfolio(f,weighting='constant',weights={'a':.75,'b':.25})
        np.testing.assert_allclose(a.tables['portfolio'].simple_return,[.05,-.05]);self.assertAlmostEqual(a.computation['risk']['wealth_end'],.9975)
        self.code('INVALID_WEIGHTS',lambda:self.portfolio(f,weighting='constant',weights={'a':.8,'b':.8}))
        self.code('INVALID_WEIGHTS',lambda:self.portfolio(f,weighting='constant',weights={'a':1.2,'b':-.2}))

    def test_actual_weight_lag_and_no_future_weight_use(self):
        f=pd.DataFrame({'date':np.repeat(pd.date_range('2025-01-01',periods=3),2),'asset':['a','b']*3,
            'r':[np.nan,np.nan,.1,0.,.2,-.1],'w':[.75,.25,.25,.75,.9,.1]})
        f['available']=f.date
        kwargs={'weighting':'lagged_column','weight_column':'w','weight_available_at_column':'available','first_row':'formation'}
        a=self.portfolio(f,**kwargs);np.testing.assert_allclose(a.tables['portfolio'].simple_return,[.075,-.025])
        f.loc[f.date==pd.Timestamp('2025-01-03'),'w']=[0.,1.]
        b=self.portfolio(f,**kwargs);np.testing.assert_allclose(a.tables['portfolio'].simple_return,b.tables['portfolio'].simple_return)
        self.assertEqual(a.computation['excluded_initial_formation_periods'],1)
        f.loc[0,'available']=pd.Timestamp('2030-01-01')
        self.code('LOOKAHEAD_RISK',lambda:self.portfolio(f,**kwargs))

    def test_missing_assets_returns_and_unknown_universe_are_rejected(self):
        f=pd.DataFrame({'date':['2025-01-01','2025-01-01','2025-01-02','2025-01-02'],
            'asset':['a','b','a','b'],'r':[.1,-.1,np.nan,.1]})
        self.code('MISSING_ASSET_RETURN',lambda:self.portfolio(f))
        self.code('ASSET_ALIGNMENT_REQUIRED',lambda:self.portfolio(f.iloc[:3]))
        self.code('LOOKAHEAD_RISK',lambda:self.portfolio(f,universe_status='unknown'))

    def test_multi_asset_outputs_publish_and_reopen(self):
        a=self.factor(self.factor_frame());receipt=publish_finance(self.store,a)
        result,manifest=read_analysis(self.store,receipt['data']['analysis_id'],namespace='finance')
        self.assertEqual(result['method'],'fin_factor');self.assertTrue(manifest['committed'])
        self.assertIn('table_formation.csv',manifest['files'])
