import numpy as np
import pandas as pd
from _bootstrap import AnalysisTest
from analysis_sample import prepare_sample
from analysis_statistics import describe,correlate


class StatisticsTests(AnalysisTest):
    def sample(self,values,roles=None):
        return prepare_sample(self.dataset(pd.DataFrame(values)),columns=list(values),roles=roles,
                              missing_policy='complete_case' if roles else 'available')

    def test_fixed_descriptive_reference_and_linear_quantiles(self):
        row=describe(self.sample({'x':[2,4,6]}),['x'])['columns'][0]
        for key,value in {'mean':4,'median':4,'variance':4,'std':2,'skewness':0,'count':3}.items():self.assertAlmostEqual(row[key],value)
        self.assertEqual(row['quantiles'],{'0.25':3.,'0.5':4.,'0.75':5.})
        self.assertIsNone(row['excess_kurtosis']);self.assertEqual(row['unavailable']['excess_kurtosis'],'INSUFFICIENT_SAMPLE')

    def test_fixed_bias_corrected_skew_and_fisher_excess_kurtosis(self):
        row=describe(self.sample({'x':[1,2,3,4]}),['x'])['columns'][0]
        self.assertAlmostEqual(row['skewness'],0);self.assertAlmostEqual(row['excess_kurtosis'],-1.2)

    def test_nullable_all_null_constant_and_singleton(self):
        s=self.sample({'all':pd.Series([pd.NA,pd.NA,pd.NA],dtype='Float64'),'constant':[5.,5.,5.],'one':[2.,np.nan,np.nan]})
        rows={r['name']:r for r in describe(s,list(s.frame))['columns']}
        self.assertEqual(rows['all']['count'],0);self.assertIsNone(rows['all']['mean'])
        self.assertEqual(rows['constant']['variance'],0);self.assertIsNone(rows['constant']['skewness'])
        self.assertIsNone(rows['one']['variance']);self.assertEqual(rows['one']['missing_count'],2)

    def test_perfect_positive_negative_pearson(self):
        s=self.sample({'x':[1,2,3,4],'positive':[2,4,6,8],'negative':[8,6,4,2]})
        r=correlate(s,list(s.frame));self.assertAlmostEqual(r['matrix'][0][1],1);self.assertAlmostEqual(r['matrix'][0][2],-1)
        self.assertTrue(all(p['n']==4 and p['p_value'] is None for p in r['pairs']));self.assertTrue(r['positive_semidefinite'])

    def test_monotonic_nonlinear_spearman_and_average_ties(self):
        r=correlate(self.sample({'x':[1,2,3,4],'square':[1,4,9,16]}),['x','square'],method='spearman')
        self.assertAlmostEqual(r['pairs'][0]['coefficient'],1)
        r=correlate(self.sample({'x':[1,2,2,4],'y':[1,3,2,4]}),['x','y'],method='spearman')
        # Centered ranks x=(-1.5,0,0,1.5), y=(-1.5,.5,-.5,1.5): sqrt(.9).
        self.assertAlmostEqual(r['pairs'][0]['coefficient'],np.sqrt(.9))

    def test_pairwise_counts_hashes_and_non_psd_matrix(self):
        s=self.sample({'a':[0.,1.,0.,1.,np.nan,np.nan],
                       'b':[0.,1.,np.nan,np.nan,0.,1.],
                       'c':[np.nan,np.nan,0.,1.,1.,0.]})
        r=correlate(s,['a','b','c'],missing_policy='pairwise')
        np.testing.assert_allclose(r['eigenvalues'],[-1,2,2],atol=1e-12)
        self.assertFalse(r['positive_semidefinite']);self.assertIn('PAIRWISE_MATRIX_NOT_PSD',r['warnings'])
        self.assertEqual([p['n'] for p in r['pairs']],[2,2,2]);self.assertEqual(len(set(p['positions_sha256'] for p in r['pairs'])),3)

    def test_different_pair_sample_sizes_and_listwise(self):
        s=self.sample({'a':[1.,2.,3.,4.],'b':[1.,2.,3.,np.nan],'c':[1.,2.,np.nan,np.nan]})
        r=correlate(s,['a','b','c'],missing_policy='pairwise');self.assertEqual([p['n'] for p in r['pairs']],[3,2,2])
        r=correlate(s,['a','b','c'],missing_policy='listwise');self.assertEqual([p['n'] for p in r['pairs']],[2,2,2])
        self.code('MISSING_VALUES',lambda:correlate(s,['a','b']))

    def test_small_n_and_constant_are_null_with_reason(self):
        r=correlate(self.sample({'a':[1.,np.nan],'b':[2.,3.]}),['a','b'],missing_policy='pairwise')
        self.assertIsNone(r['pairs'][0]['coefficient']);self.assertEqual(r['pairs'][0]['reason'],'INSUFFICIENT_SAMPLE')
        r=correlate(self.sample({'a':[1,1,1],'b':[2,3,4]}),['a','b'])
        self.assertEqual(r['pairs'][0]['reason'],'CONSTANT_COLUMN')

    def test_iid_inference_is_explicit_and_small_spearman_is_not_fabricated(self):
        s=self.sample({'a':[1,2,3,4,5],'b':[1,3,2,4,6]})
        r=correlate(s,['a','b'],inference='iid_asymptotic')['pairs'][0]
        self.assertTrue(0<=r['p_value']<=1);self.assertEqual(len(r['confidence_interval']),2)
        r=correlate(s,['a','b'],method='spearman',inference='iid_asymptotic')['pairs'][0]
        self.assertIsNone(r['p_value']);self.assertEqual(r['inference_reason'],'PERMUTATION_REQUIRED_NOT_IMPLEMENTED')
        s=self.sample({'a':[1,2,3],'b':[2,3,4],'date':['2025-01-01','2025-01-02','2025-01-03']},roles={'date':'date'})
        self.code('INFERENCE_ASSUMPTION_REQUIRED',lambda:correlate(s,['a','b'],inference='iid_asymptotic'))

    def test_invalid_quantiles_and_large_integer_identifier_rejected(self):
        s=self.sample({'x':[2,4,6]})
        for q in ([.5,.25],[float('nan')],[True]):self.code('INVALID_ARGUMENT',lambda:describe(s,['x'],quantiles=q))
        self.code('INVALID_ARGUMENT',lambda:describe(s,['x'],ddof=0))
        s=self.sample({'id':[2**60,2**60+1]});self.code('PRECISION_LOSS',lambda:describe(s,['id']))

    def test_unexpected_numerical_overflow_is_not_converted_to_null(self):
        s=self.sample({'x':[1e308,1e308,-1e308]})
        self.code('NUMERICAL_FAILURE',lambda:describe(s,['x']))
