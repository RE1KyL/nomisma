import math,os
from unittest.mock import patch
import numpy as np
import pandas as pd
from _bootstrap import AnalysisTest
from finance_returns import analyze_returns
from finance_risk import analyze_risk,risk_from_returns
from finance_common import publish_finance
from analysis_store import read_analysis
from dataset_store import ProjectDatasetStore,file_sha256


class ReturnsRiskTests(AnalysisTest):
    def returns(self,values,**kwargs):
        s=self.dataset(pd.DataFrame({'date':pd.date_range('2025-01-01',periods=len(values)),'price':values}))
        return analyze_returns(self.store,s.metadata['dataset_id'],expected_sha256=s.metadata['sha256'],date_column='date',
            price_column='price',frequency='daily',price_adjustment='unknown',**kwargs)

    def risk(self,values,metrics,**kwargs):
        s=self.dataset(pd.DataFrame({'date':pd.date_range('2025-01-01',periods=len(values)),'r':values}))
        return analyze_risk(self.store,s.metadata['dataset_id'],expected_sha256=s.metadata['sha256'],date_column='date',
            return_column='r',return_kind='simple',frequency='daily',metrics=metrics,**kwargs)

    def test_fixed_prices_simple_log_and_initial_not_zero_return(self):
        a=self.returns([100.,110.,99.]);table=a.tables['returns']
        self.assertTrue(pd.isna(table['return'].iloc[0]));self.assertEqual(table.reason.iloc[0],'FIRST_PRICE')
        np.testing.assert_allclose(table['return'].iloc[1:],[.1,-.1],atol=1e-15)
        self.assertAlmostEqual(a.computation['assets'][0]['cumulative_return'],-.01)
        b=self.returns([100.,110.,99.],return_kind='log')
        np.testing.assert_allclose(b.tables['returns']['return'].iloc[1:],np.log1p([.1,-.1]),atol=1e-15)
        self.assertAlmostEqual(b.computation['assets'][0]['wealth_end'],.99)

    def test_missing_prices_do_not_bridge_or_restart_global_wealth(self):
        self.code('MISSING_VALUES',lambda:self.returns([100.,np.nan,110.,121.]))
        a=self.returns([100.,np.nan,110.,121.],missing_policy='strict_adjacent');t=a.tables['returns']
        self.assertEqual(t.reason.iloc[1:3].tolist(),['MISSING_CURRENT_PRICE','MISSING_PREVIOUS_PRICE'])
        self.assertAlmostEqual(t.simple_return.iloc[3],.1);self.assertTrue(pd.isna(t.wealth.iloc[3]))
        self.assertFalse(a.computation['assets'][0]['full_chain_available'])
        self.code('INCOMPLETE_RETURN_CHAIN',lambda:risk_from_returns(self.store,a,metrics=['max_drawdown']))

    def test_bad_prices_order_and_duplicate_dates_rejected(self):
        for values in [[100.,0.,110.],[100.,-1.,110.]]:self.code('INVALID_PRICE',lambda:self.returns(values))
        for dates,code in [(['2025-01-02','2025-01-01'],'TIME_ORDER_INVALID'),(['2025-01-01','2025-01-01'],'DUPLICATE_TIME_KEY')]:
            s=self.dataset(pd.DataFrame({'date':dates,'price':[100.,110.]}))
            self.code(code,lambda:analyze_returns(self.store,s.metadata['dataset_id'],expected_sha256=s.metadata['sha256'],date_column='date',
                price_column='price',frequency='daily',price_adjustment='unknown'))

    def test_initial_loss_peak_valley_and_recovery(self):
        a=self.risk([-.1,.05,.1],['max_drawdown']);r=a.computation['assets'][0]['max_drawdown']
        self.assertAlmostEqual(r['loss'],.1);self.assertEqual(r['peak_observation'],-1)
        self.assertIsNone(r['peak_date']);self.assertEqual(r['trough_date'][:10],'2025-01-01')
        self.assertTrue(r['recovered']);self.assertEqual(r['recovery_date'][:10],'2025-01-03')
        a=self.risk([-.1,.05],['max_drawdown']);self.assertFalse(a.computation['assets'][0]['max_drawdown']['recovered'])

    def test_positive_returns_zero_downside_and_constant_sharpe(self):
        a=self.risk([.1,.1,.1],['volatility','max_drawdown','sharpe','sortino','calmar'],periods_per_year=12,
            risk_free={'basis':'annual_effective','value':0.},mar={'basis':'annual_effective','value':0.})
        r=a.computation['assets'][0]
        self.assertEqual(r['volatility']['period'],0);self.assertEqual(r['max_drawdown']['loss'],0)
        for metric in ['sharpe','sortino','calmar']:self.assertIsNone(r[metric]['value']);self.assertIn(metric,r['unavailable'])

    def test_volatility_sortino_and_negative_excess_reference(self):
        a=self.risk([.1,-.1],['volatility','sharpe','sortino'],periods_per_year=4,
            risk_free={'basis':'annual_effective','value':.04},mar={'basis':'annual_effective','value':0.})
        r=a.computation['assets'][0];rf=1.04**.25-1
        self.assertAlmostEqual(r['volatility']['period'],math.sqrt(.02));self.assertAlmostEqual(r['volatility']['annualized'],2*math.sqrt(.02))
        self.assertAlmostEqual(r['sharpe']['risk_free_per_period'],rf)
        self.assertAlmostEqual(r['sharpe']['value'],-2*rf/math.sqrt(.02));self.assertEqual(r['sortino']['denominator_observations'],2)
        self.assertAlmostEqual(r['sortino']['downside_period_deviation'],math.sqrt(.005))

    def test_calmar_uses_compound_cagr_and_observation_count(self):
        a=self.risk([.1,-.1],['calmar','max_drawdown'],periods_per_year=2);r=a.computation['assets'][0]
        self.assertAlmostEqual(r['calmar']['CAGR'],-.01);self.assertAlmostEqual(r['calmar']['value'],-.1)
        self.assertEqual(r['calmar']['years_from_period_count'],1)

    def test_leap_day_window_uses_declared_period_count_not_guessed_calendar_years(self):
        source=self.dataset(pd.DataFrame({'date':['2020-02-28','2020-02-29','2020-03-01'],'r':[.1,-.1,.01]}))
        a=analyze_risk(self.store,source.metadata['dataset_id'],expected_sha256=source.metadata['sha256'],date_column='date',
            return_column='r',return_kind='simple',frequency='daily',periods_per_year=252,metrics=['calmar'])
        calmar=a.computation['assets'][0]['calmar']
        self.assertAlmostEqual(calmar['years_from_period_count'],3/252)
        self.assertAlmostEqual(calmar['CAGR'],(1.1*.9*1.01)**84-1,places=12)

    def test_historical_var_es_fractional_tail_ties_and_negative_var(self):
        returns=np.arange(-.19,.01,.01)
        a=self.risk(returns,['var','es'],alpha_loss=.925);r=a.computation['assets'][0]
        # 20 points, worst 1.5 units: (0.19+0.5*0.18)/1.5. Linear q index=17.575.
        self.assertAlmostEqual(r['es']['tail_mass'],1.5);self.assertAlmostEqual(r['es']['loss'],(.19+.5*.18)/1.5)
        self.assertAlmostEqual(r['var']['loss'],.17575);self.assertGreaterEqual(r['es']['loss'],r['var']['loss'])
        a=self.risk([.01]*20,['var','es']);r=a.computation['assets'][0]
        self.assertAlmostEqual(r['var']['loss'],-.01);self.assertAlmostEqual(r['es']['loss'],-.01)

    def test_ruin_is_explicit_and_log_conversion_does_not_fake_ruin(self):
        a=self.risk([-.1,-1.,0.],['max_drawdown','calmar'],periods_per_year=12);r=a.computation['assets'][0]
        self.assertEqual(r['wealth_end'],0);self.assertEqual(r['max_drawdown']['loss'],1)
        self.assertIsNone(r['calmar']['value']);self.assertEqual(r['unavailable']['calmar'],'WEALTH_ZERO')
        self.code('INVALID_RETURN_AFTER_RUIN',lambda:self.risk([-1.,.1],['max_drawdown']))
        self.code('INVALID_RETURN',lambda:self.risk([-1.01,.1],['max_drawdown']))

    def test_annualization_and_rates_never_default_or_treat_percent_5_as_decimal(self):
        self.code('INVALID_FINANCIAL_CONVENTION',lambda:self.risk([.1,.2],['volatility']))
        self.code('INVALID_FINANCIAL_CONVENTION',lambda:self.risk([.1,.2],['sharpe'],periods_per_year=252))
        self.code('INVALID_FINANCIAL_CONVENTION',lambda:self.risk([.1,.2],['sharpe'],periods_per_year=252,risk_free={'basis':'annual_effective','value':5}))
        self.code('SAMPLE_TOO_SMALL',lambda:self.risk([.1,.2],['es']))

    def test_price_to_risk_reuses_source_without_fake_dataset_and_publishes(self):
        a=self.returns([100.,110.,99.],periods_per_year=2);r=risk_from_returns(self.store,a,metrics=['volatility','max_drawdown'])
        self.assertEqual(r.sample.metadata['dataset_id'],a.sample.metadata['dataset_id'])
        self.assertEqual(r.computation['assets'][0]['n'],2)
        before=file_sha256(self.store.path(a.sample.metadata['current_file_path']))
        receipt=publish_finance(self.store,r);result,_=read_analysis(self.store,receipt['data']['analysis_id'],namespace='finance')
        self.assertEqual(result['analysis_domain'],'finance');self.assertEqual(before,file_sha256(self.store.path(a.sample.metadata['current_file_path'])))
        with patch.dict(os.environ,{'NOMISMA_MODE':'economic','NOMISMA_PROJECT_ID':'project_economic','NOMISMA_WORKSPACE':str(self.other)}):
            other=ProjectDatasetStore();self.code('MODE_NOT_ALLOWED',lambda:publish_finance(other,r))
            self.code('MODE_NOT_ALLOWED',lambda:read_analysis(other,result['analysis_id'],namespace='finance'))
