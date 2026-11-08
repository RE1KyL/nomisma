import numpy as np
import pandas as pd
from _bootstrap import AnalysisTest
from analysis_sample import prepare_sample,build_design_matrix,load_analysis_input


class SampleTests(AnalysisTest):
    def test_single_union_mask_for_all_roles_and_exclusion_counts(self):
        source=self.dataset(pd.DataFrame({'y':[1.,np.nan,3.,4.,5.,6.],'x':[0.,1.,np.nan,3.,4.,5.],
            'date':['2025-01-01','2025-01-02','2025-01-03',None,'2025-01-05','2025-01-06'],
            'cluster':['a','a','b','b',None,'c']}))
        before=source.frame.copy(deep=True)
        sample=prepare_sample(source,columns=['y','x'],roles={'y':'y','x':['x'],'date':'date','cluster':'cluster'},
            missing_policy='complete_case',filters=[{'column':'y','operator':'greater_than','value':0}])
        self.assertEqual(sample.positions,(0,5));self.assertEqual(sample.frame.x.tolist(),[0.,5.])
        self.assertEqual(sample.frame.cluster.tolist(),['a','c']);self.assertEqual(sample.frame.date.dt.day.tolist(),[1,6])
        self.assertEqual(sample.summary['excluded_by_filters'],1);self.assertEqual(sample.summary['excluded_by_missing'],3)
        self.assertEqual(sample.summary['missing_by_column'],{'y':0,'x':1,'date':1,'cluster':1})
        self.assertEqual(sample.summary['actual_sample_size'],2);pd.testing.assert_frame_equal(source.frame,before)

    def test_missing_requires_policy_and_available_disallows_roles(self):
        source=self.dataset(pd.DataFrame({'x':[1.,np.nan]}))
        self.code('MISSING_VALUES',lambda:prepare_sample(source,columns=['x']))
        self.code('INVALID_ARGUMENT',lambda:prepare_sample(source,columns=['x'],roles={'x':['x']},missing_policy='available'))
        self.assertEqual(len(prepare_sample(source,columns=['x'],missing_policy='available').frame),2)

    def test_filter_is_finite_and_sequential(self):
        source=self.dataset(pd.DataFrame({'x':[1,2,3,4]}))
        s=prepare_sample(source,columns=['x'],filters=[{'column':'x','operator':'greater_than','value':1},
            {'column':'x','operator':'less_equal','value':3}])
        self.assertEqual(s.positions,(1,2));self.assertEqual([x['excluded'] for x in s.summary['filters']],[1,1])
        self.code('INVALID_OPERATION',lambda:prepare_sample(source,columns=['x'],filters=[{'column':'x','operator':'eval','value':'1+1'}]))

    def test_infinity_bad_date_and_invalid_roles_rejected(self):
        source=self.dataset(pd.DataFrame({'x':[1.,float('inf')],'date':['2025-01-01','not a date']}))
        self.code('NON_FINITE_INPUT',lambda:prepare_sample(source,columns=['x']))
        self.code('INVALID_DATE',lambda:prepare_sample(source,columns=['date'],roles={'date':'date'}))
        self.code('INVALID_ARGUMENT',lambda:prepare_sample(source,columns=['date'],roles=[]))

    def test_position_identity_does_not_use_duplicate_pandas_labels(self):
        source=self.dataset(pd.DataFrame({'x':[1,2,3]}));source.frame.index=[8,8,8]
        s=prepare_sample(source,columns=['x'],filters=[{'column':'x','operator':'not_in','value':[2]}])
        self.assertEqual(s.positions,(0,2));self.assertEqual(s.frame.x.tolist(),[1,3])

    def test_loader_requires_registered_id_and_exact_hash(self):
        source=self.dataset(pd.DataFrame({'x':[2,4,6]}))
        self.code('STALE_DATASET',lambda:load_analysis_input(self.store,source.metadata['dataset_id'],'0'*64))
        self.code('INVALID_ARGUMENT',lambda:load_analysis_input(self.store,'../input.csv',source.metadata['sha256']))

    def design_sample(self,**extra):
        source=self.dataset(pd.DataFrame({'y':[1,2,2,4,5,7],'x':[0,1,2,3,4,5],
            'group':['a','a','b','b','c','c'],**extra}))
        return prepare_sample(source,columns=list(source.frame),roles={'y':'y','x':['x']})

    def test_design_numeric_categorical_reference_and_hash(self):
        s=self.design_sample();d=build_design_matrix(s,x_numeric=['x'],categorical=['group'],reference_levels={'group':'a'})
        self.assertEqual(d['columns'],['__intercept__','x','group[b]','group[c]']);self.assertEqual(d['rank'],4)
        np.testing.assert_array_equal(d['matrix'][:,2],[0,0,1,1,0,0]);self.assertEqual(d['df_resid'],2)
        self.assertEqual(d['matrix_sha256'],build_design_matrix(s,x_numeric=['x'],categorical=['group'],reference_levels={'group':'a'})['matrix_sha256'])

    def test_invalid_reference_and_dummy_trap_not_silently_dropped(self):
        s=self.design_sample(duplicate=[0,1,2,3,4,5])
        self.code('INVALID_REFERENCE',lambda:build_design_matrix(s,x_numeric=['x'],categorical=['group'],reference_levels={'group':'absent'}))
        self.code('DESIGN_RANK_DEFICIENT',lambda:build_design_matrix(s,x_numeric=['x','duplicate']))
        self.code('INVALID_ARGUMENT',lambda:build_design_matrix(s,x_numeric=['x'],categorical=['group']))

    def test_constant_y_small_n_reserved_label_and_non_numeric_rejected(self):
        s=prepare_sample(self.dataset(pd.DataFrame({'y':[1,1,1],'x':[0,1,2]})),columns=['y','x'],roles={'y':'y'})
        self.code('CONSTANT_DEPENDENT',lambda:build_design_matrix(s,x_numeric=['x']))
        s=prepare_sample(self.dataset(pd.DataFrame({'y':[1,2],'x':[0,1]})),columns=['y','x'],roles={'y':'y'})
        self.code('SAMPLE_TOO_SMALL',lambda:build_design_matrix(s,x_numeric=['x']))
        s=self.design_sample(__intercept__=[1,2,3,4,5,6])
        self.code('INVALID_COLUMN',lambda:build_design_matrix(s,x_numeric=['__intercept__']))
        self.code('TYPE_MISMATCH',lambda:build_design_matrix(s,x_numeric=['group']))

    def test_reordered_columns_preserve_sample_and_ill_conditioning_is_reported(self):
        source=self.dataset(pd.DataFrame({'y':[1.,3.,2.,5.],'x':[0.,1.,2.,3.],'tiny':[0.,1e-11,-1e-11,2e-11]}))
        s=prepare_sample(source,columns=['y','x','tiny'],roles={'y':'y'})
        t=prepare_sample(source,columns=['tiny','x','y'],roles={'y':'y'})
        self.assertEqual(s.positions,t.positions);self.assertEqual(s.summary['positions_sha256'],t.summary['positions_sha256'])
        d=build_design_matrix(s,x_numeric=['x','tiny']);self.assertIn('ILL_CONDITIONED_DESIGN',d['warnings'])
