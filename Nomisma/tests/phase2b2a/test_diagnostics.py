import numpy as np
import pandas as pd
from _bootstrap import EconomicTest
from econ_diagnostics import diagnose_ols,publish_diagnostics
from analysis_store import read_analysis


class DiagnosticTests(EconomicTest):
    def fixture(self,dates=True):
        x=np.arange(16,dtype=float);noise=np.array([1.,-.5,1.5,-1.3,.2,-.9,.7,-.2,1.1,-1.6,.4,.6,-.8,1.4,-.7,.3])
        frame=pd.DataFrame({'x':x,'y':.4+1.2*x+noise})
        if dates:frame['date']=pd.date_range('2025-01-01',periods=16,freq='D')
        return self.fit(frame,**({'date_column':'date'} if dates else {}))

    def independent_auxiliary(self,y,x):
        coefficients=np.linalg.lstsq(x,y,rcond=None)[0];errors=y-x@coefficients
        return 1-np.dot(errors,errors)/np.dot(y-y.mean(),y-y.mean())

    def test_design_vif_jb_and_dw_against_independent_formulas(self):
        a=self.fixture();r=diagnose_ols(self.store,a,tests=['design','vif','jarque_bera','durbin_watson'])['tests']
        e=a.fitted.resid;n=len(e);centered=e-e.mean();m2=np.mean(centered**2)
        skew=np.mean(centered**3)/m2**1.5;kurt=np.mean(centered**4)/m2**2
        jb=n/6*(skew**2+(kurt-3)**2/4)
        self.assertAlmostEqual(r['jarque_bera']['statistic'],jb,places=12)
        # chi-square df2 survival has elementary exp(-statistic/2).
        self.assertAlmostEqual(r['jarque_bera']['p_value'],np.exp(-jb/2),places=12)
        self.assertAlmostEqual(r['durbin_watson']['statistic'],np.dot(np.diff(e),np.diff(e))/np.dot(e,e))
        self.assertIsNone(r['durbin_watson']['p_value']);self.assertEqual(r['vif']['columns'][0]['term'],'x')
        self.assertAlmostEqual(r['vif']['columns'][0]['vif'],1);self.assertEqual(r['design']['rank'],2)

    def test_bp_and_white_lm_f_against_independent_auxiliary_regressions(self):
        a=self.fixture();r=diagnose_ols(self.store,a,tests=['breusch_pagan','white'])['tests']
        x=a.sample.frame.x.to_numpy();e2=a.fitted.resid**2;n=len(x)
        for name,aux in [('breusch_pagan',np.column_stack([np.ones(n),x])),('white',np.column_stack([np.ones(n),x,x*x]))]:
            r2=self.independent_auxiliary(e2,aux);df=aux.shape[1]-1
            self.assertAlmostEqual(r[name]['lm'],n*r2,places=11)
            self.assertAlmostEqual(r[name]['f'],(r2/df)/((1-r2)/(n-aux.shape[1])),places=11)
            self.assertEqual(r[name]['lm_df'],df)
        self.assertEqual(r['breusch_pagan']['variant'],'Koenker');self.assertEqual(r['white']['auxiliary_columns'],3)

    def test_bp_original_variant_is_explicit(self):
        a=self.fixture();r=diagnose_ols(self.store,a,tests=['breusch_pagan'],bp_robust=False)['tests']['breusch_pagan']
        z=a.fitted.resid**2/np.mean(a.fitted.resid**2);x=a.design['matrix']
        fitted=x@np.linalg.lstsq(x,z,rcond=None)[0]
        self.assertAlmostEqual(r['lm'],np.dot(fitted-fitted.mean(),fitted-fitted.mean())/2,places=11)
        self.assertFalse(r['robust']);self.assertIn('normality',r['variant'])

    def test_bg_actual_lagged_residual_auxiliary_and_explicit_df(self):
        a=self.fixture();r=diagnose_ols(self.store,a,tests=['breusch_godfrey'],bg_lags=2)['tests']['breusch_godfrey']
        e=a.fitted.resid;n=len(e);lags=np.zeros((n,2));lags[1:,0]=e[:-1];lags[2:,1]=e[:-2]
        aux=np.column_stack([a.design['matrix'],lags]);r2=self.independent_auxiliary(e,aux)
        self.assertAlmostEqual(r['lm'],n*r2,places=11)
        self.assertAlmostEqual(r['f'],(r2/2)/((1-r2)/(n-4)),places=11)
        self.assertEqual(r['f_df_den'],12);self.assertEqual(r['n'],16);self.assertEqual(r['ordering']['lag_unit'],'observation')

    def test_serial_diagnostics_reject_missing_unsorted_and_duplicate_dates(self):
        a=self.fixture(False)
        self.code('TIME_ORDER_REQUIRED',lambda:diagnose_ols(self.store,a,tests=['durbin_watson']))
        for dates in [['2025-01-02','2025-01-01','2025-01-03','2025-01-04'],
                      ['2025-01-01','2025-01-01','2025-01-03','2025-01-04']]:
            a=self.fit(pd.DataFrame({'y':[1.,2.,2.,4.],'x':[0.,1.,2.,3.],'date':dates}),date_column='date')
            self.code('TIME_ORDER_INVALID',lambda:diagnose_ols(self.store,a,tests=['durbin_watson']))

    def test_no_implicit_lags_and_no_arbitrary_test_dispatch(self):
        a=self.fixture()
        self.code('INVALID_ARGUMENT',lambda:diagnose_ols(self.store,a,tests=['breusch_godfrey']))
        self.code('INVALID_ARGUMENT',lambda:diagnose_ols(self.store,a,tests=['design'],bg_lags=1))
        self.code('INVALID_ARGUMENT',lambda:diagnose_ols(self.store,a,tests=['eval']))
        self.code('INVALID_ARGUMENT',lambda:diagnose_ols(self.store,a,tests=['white','white']))
        self.code('INVALID_ARGUMENT',lambda:diagnose_ols(self.store,a,tests=['breusch_godfrey'],bg_lags=True))

    def test_white_small_sample_and_auxiliary_rank_fail_explicitly(self):
        a=self.fit(pd.DataFrame({'y':[1.,2.,2.],'x':[0.,1.,2.]}))
        self.code('SAMPLE_TOO_SMALL',lambda:diagnose_ols(self.store,a,tests=['white']))
        a=self.fit(pd.DataFrame({'y':[1.,2.,2.,4.],'x':[0.,1.,2.,3.]}))
        self.code('SAMPLE_TOO_SMALL',lambda:diagnose_ols(self.store,a,tests=['jarque_bera']))
        frame=pd.DataFrame({'y':[1.,2.,2.,4.,3.,7.,4.,6.],'x':[0.,1.,0.,1.,0.,1.,0.,1.]})
        a=self.fit(frame)
        self.code('DIAGNOSTIC_RANK_DEFICIENT',lambda:diagnose_ols(self.store,a,tests=['white']))

    def test_white_expansion_dimension_cap(self):
        from econ_models import fit_ols
        rng=np.random.default_rng(42);frame=pd.DataFrame(rng.normal(size=(30,15)),columns=['x'+str(i) for i in range(15)])
        frame['y']=rng.normal(size=30);source=self.dataset(frame)
        a=fit_ols(self.store,source.metadata['dataset_id'],expected_sha256=source.metadata['sha256'],y='y',x_numeric=list(frame.columns[:-1]))
        self.code('RESOURCE_LIMIT',lambda:diagnose_ols(self.store,a,tests=['white']))

    def test_changed_fitted_results_rejected_and_diagnostics_publish_round_trip(self):
        a=self.fixture();receipt=publish_diagnostics(self.store,a,tests=['design','vif','jarque_bera'])
        result,manifest=read_analysis(self.store,receipt['data']['analysis_id'],namespace='economic')
        self.assertEqual(result['method'],'ols_diagnostics');self.assertEqual(result['results']['source_model_seal'],a.seal)
        self.assertTrue(manifest['committed']);self.assertIn('jarque_bera',result['results']['tests'])
        a.fitted.params[0]+=1
        self.code('STALE_ANALYSIS',lambda:diagnose_ols(self.store,a,tests=['design']))
