import math,os
from unittest.mock import patch
import numpy as np
import pandas as pd
from _bootstrap import EconomicTest
from econ_models import fit_ols,coefficient_test,joint_test,publish_ols,residual_table
from analysis_store import read_analysis,save_analysis
from analysis_result import make_analysis_result
from dataset_store import ProjectDatasetStore,file_sha256


class OLSTests(EconomicTest):
    def fixture(self):return self.fit(pd.DataFrame({'y':[1.,2.,2.,4.],'x':[0.,1.,2.,3.]}))

    def test_fixed_hand_computed_ols_covariance_t_p_and_ci(self):
        analysis=self.fixture();r=analysis.computation
        self.assertEqual(r['n'],4);self.assertEqual(r['df_resid'],2);self.assertAlmostEqual(r['sse'],.7)
        np.testing.assert_allclose([x['estimate'] for x in r['coefficients']],[.9,.9],atol=1e-12)
        np.testing.assert_allclose(r['covariance_matrix'],[[.245,-.105],[-.105,.07]],atol=1e-12)
        self.assertAlmostEqual(r['r_squared'],81/95);self.assertAlmostEqual(r['overall_f']['statistic'],81/7)
        for row,var in zip(r['coefficients'],[.245,.07]):
            t=.9/math.sqrt(var);self.assertAlmostEqual(row['statistic'],t)
            # Closed form two-sided Student t p for df=2, independent of SciPy.
            self.assertAlmostEqual(row['p_value'],1-abs(t)/math.sqrt(t*t+2),places=12)
            critical=4.302652729911275
            np.testing.assert_allclose(row['confidence_interval'],[.9-critical*math.sqrt(var),.9+critical*math.sqrt(var)],atol=1e-11)
        self.assertEqual(r['covariance']['distribution'],'Student-t')

    def test_named_t_one_sided_and_named_joint_f(self):
        a=self.fixture();two=coefficient_test(self.store,a,term='x');greater=coefficient_test(self.store,a,term='x',alternative='greater')
        less=coefficient_test(self.store,a,term='x',alternative='less')
        self.assertAlmostEqual(greater['p_value'],two['p_value']/2);self.assertAlmostEqual(less['p_value'],1-greater['p_value'])
        zero=coefficient_test(self.store,a,term='x',null_value=.9);self.assertAlmostEqual(zero['statistic'],0,places=12)
        f=joint_test(self.store,a,restrictions=[{'weights':{'x':1.},'value':0.}])
        self.assertAlmostEqual(f['statistic'],81/7);self.assertAlmostEqual(f['p_value'],two['p_value'])
        self.assertEqual((f['df_num'],f['df_den']),(1,2))

    def test_invalid_hypotheses_do_not_execute_expressions(self):
        a=self.fixture()
        self.code('INVALID_ARGUMENT',lambda:coefficient_test(self.store,a,term='eval(x)'))
        for restrictions in [[],[{'weights':{'not_a_term':1},'value':0}],
            [{'weights':{'x':float('inf')},'value':0}]]:
            self.code('INVALID_ARGUMENT',lambda:joint_test(self.store,a,restrictions=restrictions))
        self.code('INVALID_RESTRICTION',lambda:joint_test(self.store,a,restrictions=[{'weights':{'x':1},'value':0},{'weights':{'x':2},'value':0}]))

    def test_missing_mask_date_and_reference_group(self):
        source=self.dataset(pd.DataFrame({'y':[1.,2.,np.nan,4.,5.,8.,7.,9.],'x':[0.,1.,2.,3.,4.,5.,6.,7.],
            'group':['a','a','b','b','a','b','a','b'],
            'date':['2025-01-01','2025-01-02','2025-01-03',None,'2025-01-05','2025-01-06','2025-01-07','2025-01-08']}))
        a=fit_ols(self.store,source.metadata['dataset_id'],expected_sha256=source.metadata['sha256'],y='y',x_numeric=['x'],
            categorical=['group'],reference_levels={'group':'a'},missing_policy='complete_case',date_column='date')
        self.assertEqual(a.sample.positions,(0,1,4,5,6,7));self.assertEqual(a.design['columns'],['__intercept__','x','group[b]'])
        self.assertEqual(residual_table(self.store,a).source_row.tolist(),[0,1,4,5,6,7])
        self.assertEqual(a.sample.summary['excluded_by_missing'],2)

    def test_constant_y_perfect_fit_collinearity_small_n_and_infinite_rejected(self):
        for frame,code in [(pd.DataFrame({'y':[1.,1.,1.],'x':[0.,1.,2.]}),'CONSTANT_DEPENDENT'),
                           (pd.DataFrame({'y':[1.,3.,5.,7.],'x':[0.,1.,2.,3.]}),'PERFECT_FIT'),
                           (pd.DataFrame({'y':[1.,2.,3.],'x':[1.,1.,1.]}),'DESIGN_RANK_DEFICIENT'),
                           (pd.DataFrame({'y':[1.,2.],'x':[0.,1.]}),'SAMPLE_TOO_SMALL'),
                           (pd.DataFrame({'y':[1.,2.,3.],'x':[0.,1.,float('inf')]}),'NON_FINITE_INPUT')]:
            with self.subTest(code=code):self.code(code,lambda:self.fit(frame))

    def test_no_intercept_and_intercept_only_have_explicit_r_squared(self):
        a=self.fit(pd.DataFrame({'y':[1.,2.,2.,4.],'x':[1.,2.,3.,4.]}),intercept=False)
        self.assertEqual(a.computation['r_squared_definition'],'uncentered');self.assertEqual(a.computation['rank'],1)
        source=self.dataset(pd.DataFrame({'y':[1.,2.,2.,4.]}))
        a=fit_ols(self.store,source.metadata['dataset_id'],expected_sha256=source.metadata['sha256'],y='y',x_numeric=[])
        self.assertEqual(a.computation['overall_f']['reason'],'NO_SLOPE_TERMS');self.assertAlmostEqual(a.computation['coefficients'][0]['estimate'],2.25)

    def test_unsupported_covariance_and_implicit_constant_rejected(self):
        frame=pd.DataFrame({'y':[1.,2.,2.,4.],'x':[0.,1.,2.,3.]})
        self.code('INVALID_ARGUMENT',lambda:self.fit(frame,covariance='HC3'))
        self.code('INVALID_ARGUMENT',lambda:self.fit(frame,alpha=float('nan')))
        self.code('IMPLICIT_CONSTANT',lambda:self.fit(pd.DataFrame({'y':[1.,2.,2.,4.],'x':[1.,1.,1.,1.]}),intercept=False))

    def test_edited_in_memory_result_and_changed_dataset_rejected(self):
        a=self.fixture();a.sample.frame.loc[0,'x']=42
        self.code('STALE_ANALYSIS',lambda:coefficient_test(self.store,a,term='x'))
        a=self.fixture();source=self.store.path(a.sample.metadata['current_file_path']);source.write_bytes(source.read_bytes()+b'\n')
        self.code('STALE_DATASET',lambda:publish_ols(self.store,a))

    def test_finance_mode_cannot_fit_read_or_publish_economic_results(self):
        a=self.fixture();receipt=publish_ols(self.store,a)
        with patch.dict(os.environ,{'NOMISMA_MODE':'finance','NOMISMA_PROJECT_ID':'project_finance','NOMISMA_WORKSPACE':str(self.finance_root)}):
            store=ProjectDatasetStore()
            self.code('MODE_NOT_ALLOWED',lambda:fit_ols(store,a.sample.metadata['dataset_id'],expected_sha256=a.sample.metadata['sha256'],y='y',x_numeric=['x']))
            self.code('MODE_NOT_ALLOWED',lambda:read_analysis(store,receipt['data']['analysis_id'],namespace='economic'))

    def test_published_economic_result_round_trip_and_source_unchanged(self):
        a=self.fixture();source=self.store.path(a.sample.metadata['current_file_path']);before=file_sha256(source)
        receipt=publish_ols(self.store,a);r,m=read_analysis(self.store,receipt['data']['analysis_id'],namespace='economic')
        self.assertEqual(r['software_versions']['statsmodels'],'0.15.0');self.assertEqual(r['analysis_domain'],'economic')
        self.assertTrue(all(x['path'].startswith('analyses/economic/') for x in receipt['artifacts']))
        self.assertEqual(r['results'],a.computation);self.assertTrue(m['committed']);self.assertEqual(before,file_sha256(source))
        self.code('FILE_NOT_FOUND',lambda:read_analysis(self.store,r['analysis_id']))
        self.code('INVALID_PATH',lambda:read_analysis(self.store,r['analysis_id'],namespace='../raw'))
        self.code('FILE_EXISTS',lambda:save_analysis(self.store,r))

    def test_economic_model_cannot_be_published_in_shared_namespace(self):
        a=self.fixture();r=make_analysis_result(a.sample,a.computation);r['analysis_domain']='shared'
        self.code('MODE_NOT_ALLOWED',lambda:save_analysis(self.store,r))

    def test_disguised_nested_model_identity_is_rejected(self):
        a=self.fixture();r=make_analysis_result(a.sample,a.computation)
        r['analysis_domain']='shared';r['method']='describe'
        self.code('ANALYSIS_INVALID',lambda:save_analysis(self.store,r))

    def test_extreme_finite_outcomes_fail_without_fake_statistics(self):
        self.code('NUMERICAL_FAILURE',lambda:self.fit(pd.DataFrame({'y':[1e200,-1e200,2e200,3e200],'x':[0.,1.,2.,3.]})))

    def test_model_state_and_declared_design_tampering_is_rejected(self):
        a=self.fixture();a.fitted.df_resid=100
        self.code('STALE_ANALYSIS',lambda:coefficient_test(self.store,a,term='x'))
        a=self.fixture();a.design['rank']=1
        self.code('STALE_ANALYSIS',lambda:publish_ols(self.store,a))

    def test_two_slope_ols_is_invariant_to_predictor_column_order(self):
        from econ_models import fit_ols
        frame=pd.DataFrame({'y':[1.,2.,4.,3.,5.,8.,6.,9.],'x':[0.,1.,2.,3.,4.,5.,6.,7.],
                            'z':[0.,1.,0.,2.,0.,1.,2.,0.]})
        source=self.dataset(frame);common={'expected_sha256':source.metadata['sha256'],'y':'y'}
        a=fit_ols(self.store,source.metadata['dataset_id'],x_numeric=['x','z'],**common)
        b=fit_ols(self.store,source.metadata['dataset_id'],x_numeric=['z','x'],**common)
        left={r['term']:r['estimate'] for r in a.computation['coefficients']};right={r['term']:r['estimate'] for r in b.computation['coefficients']}
        for name in left:self.assertAlmostEqual(left[name],right[name],places=12)
        np.testing.assert_allclose(a.fitted.resid,b.fitted.resid,atol=1e-12)

    def test_categorical_full_dummy_trap_and_invalid_field_types(self):
        from econ_models import fit_ols
        frame=pd.DataFrame({'y':[1.,2.,2.,4.,3.,5.],'x':[1.,1.,0.,0.,0.,0.],
            'b':[0.,0.,1.,1.,0.,0.],'c':[0.,0.,0.,0.,1.,1.]})
        source=self.dataset(frame);common={'expected_sha256':source.metadata['sha256'],'y':'y'}
        self.code('DESIGN_RANK_DEFICIENT',lambda:fit_ols(self.store,source.metadata['dataset_id'],x_numeric=['x','b','c'],**common))
        self.code('INVALID_ARGUMENT',lambda:fit_ols(self.store,source.metadata['dataset_id'],x_numeric=[{}],**common))

    def test_categorical_expansion_has_a_cell_budget_before_matrix_allocation(self):
        frame=pd.DataFrame({'y':[1.,2.,4.,3.,6.,4.,8.,7.,9.,8.],'x':np.arange(10,dtype=float),
                            'group':['a','b','c','d','e']*2})
        with patch('analysis_sample.MAX_CELLS',35):
            self.code('RESOURCE_LIMIT',lambda:self.fit(frame,categorical=['group'],reference_levels={'group':'a'}))
