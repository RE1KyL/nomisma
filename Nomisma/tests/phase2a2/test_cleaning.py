import json
from unittest.mock import patch
import pandas as pd
from _bootstrap import ProjectTest
from data_cleaning import clean_dataset
from dataset_io import import_dataset,load_dataset
from dataset_store import DatasetError,file_sha256


class CleaningTests(ProjectTest):
    def source(self,text):
        (self.root/'input.csv').write_text(text,encoding='utf8')
        result=import_dataset(self.store,'input.csv')
        self.before=(self.root/result['current_file_path']).read_bytes()
        return result

    def run_steps(self,result,steps):
        before=(self.root/result['current_file_path']).read_bytes()
        cleaned=clean_dataset(self.store,result['dataset_id'],expected_sha256=result['sha256'],steps=steps)
        frame,metadata=load_dataset(self.store,cleaned['dataset_id'])
        self.assertEqual((self.root/result['current_file_path']).read_bytes(),before)
        self.assertTrue(cleaned['current_file_path'].startswith('datasets/processed/'))
        return frame,metadata,cleaned

    def test_drop_duplicates_keeps_explicit_key_and_no_raw_changes(self):
        source=self.source('key,x\na,1\na,2\nb,3\n')
        frame,meta,result=self.run_steps(source,[{'operation':'drop_duplicates','subset':['key'],'keep':'last'}])
        self.assertEqual(frame.x.tolist(),[2,3]);self.assertEqual(result['processing_history'][0]['affected_rows'],1)
        self.assertEqual(meta['root_dataset_id'],source['dataset_id'])

    def test_drop_missing_any_all_and_explicit_empty_policy(self):
        source=self.source('x,y\n1,\n,2\n,\n3,4\n')
        frame,_,_=self.run_steps(source,[{'operation':'drop_missing','subset':['x','y'],'how':'all'}])
        self.assertEqual(len(frame),3)
        frame,_,_=self.run_steps(source,[{'operation':'drop_missing','subset':['x','y'],'how':'any'}])
        self.assertEqual(len(frame),1)

    def test_fill_methods_and_fractional_integer_widening(self):
        source=self.source('x,y\n1,5\n2,\n,7\n')
        for method,expected in [('mean',1.5),('median',1.5)]:
            with self.subTest(method=method):
                frame,_,result=self.run_steps(source,[{'operation':'fill_missing','columns':['x'],'method':method}])
                self.assertEqual(frame.x.iloc[2],expected)
                self.assertIn('x',result['processing_history'][0]['details']['dtype_changes'])
        frame,_,_=self.run_steps(source,[{'operation':'fill_missing','columns':['y'],'method':'constant','value':0}])
        self.assertEqual(frame.y.iloc[1],0)

    def test_winsorization_matches_hand_quantiles(self):
        source=self.source('x\n0\n10\n20\n30\n')
        frame,_,result=self.run_steps(source,[{'operation':'winsorize','columns':['x'],'lower_quantile':.25,'upper_quantile':.75}])
        self.assertEqual(frame.x.tolist(),[7.5,10,20,22.5])
        self.assertEqual(result['processing_history'][0]['affected_rows'],2)
        self.assertEqual(result['processing_history'][0]['details']['bounds']['x']['lower'],7.5)

    def test_sort_and_filter_with_null_exclusion(self):
        source=self.source('date,x\n2025-01-03,3\n2025-01-01,\n2025-01-02,2\n')
        frame,_,_=self.run_steps(source,[{'operation':'sort_values','by':['date'],'ascending':True},
            {'operation':'filter_rows','column':'x','operator':'greater_than','value':1}])
        self.assertEqual(frame.x.tolist(),[2,3])
        self.assertEqual(frame.date.dt.strftime('%Y-%m-%d').tolist(),['2025-01-02','2025-01-03'])

    def test_processed_metadata_reuses_date_annotation_and_recalculates_range(self):
        source=self.source('date,x\n2025-01-01,1\n2025-01-02,2\n2025-01-03,3\n')
        frame,metadata,_=self.run_steps(source,[{'operation':'filter_rows','column':'x','operator':'greater_than','value':1}])
        self.assertEqual(metadata['date_column'],'date')
        self.assertEqual(metadata['date_range_status'],'identified')
        self.assertEqual(metadata['data_start'],'2025-01-02')
        self.assertEqual(metadata['data_end'],'2025-01-03')
        self.assertEqual(metadata['column_schema'][0]['semantic_type'],'date')

    def test_filter_membership_null_and_dates(self):
        source=self.source('date,x\n2025-01-01,1\n2025-01-02,\n2025-01-03,3\n')
        for step,count in [({'operation':'filter_rows','column':'x','operator':'is_null'},1),
                           ({'operation':'filter_rows','column':'x','operator':'in','value':[1,3]},2),
                           ({'operation':'filter_rows','column':'date','operator':'greater_equal','value':'2025-01-02'},2)]:
            with self.subTest(step=step):self.assertEqual(len(self.run_steps(source,[step])[0]),count)

    def test_invalid_names_parameters_expressions_and_types_leave_no_processed_data(self):
        source=self.source('x\n1\n2\n')
        for steps in [[],[{'operation':'eval','code':'1+1'}],
            [{'operation':'drop_missing','subset':['not_here']}],
            [{'operation':'fill_missing','columns':['x'],'method':'automatic'}],
            [{'operation':'fill_missing','columns':['x'],'method':'constant','value':'bad'}],
            [{'operation':'winsorize','columns':['x'],'lower_quantile':.9,'upper_quantile':.1}],
            [{'operation':'filter_rows','column':'x','operator':'__import__','value':'os'}],
            [{'operation':'sort_values','by':['x'],'ascending':'yes'}]]:
            with self.subTest(steps=steps),self.assertRaises(DatasetError):
                clean_dataset(self.store,source['dataset_id'],expected_sha256=source['sha256'],steps=steps)
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])
        self.assertFalse((self.root/'datasets/processed').exists())

    def test_multistep_history_files_hashes_and_parent_linkage(self):
        source=self.source('x\n3\n1\n1\n2\n')
        frame,meta,result=self.run_steps(source,[{'operation':'drop_duplicates','keep':'first'},
                                               {'operation':'sort_values','by':['x'],'ascending':True}])
        history=result['processing_history'];self.assertEqual(frame.x.tolist(),[1,2,3])
        self.assertEqual(history[0]['rows_before'],4);self.assertEqual(history[0]['rows_after'],3)
        self.assertEqual(history[1]['input_sha256'],history[0]['output_sha256'])
        for step in history:
            self.assertEqual(file_sha256(self.root/step['output_file']),step['output_sha256'])
            self.assertEqual(file_sha256(self.root/step['input_file']),step['input_sha256'])
            for key in ['operation_id','started_at','completed_at','parameters','warnings','software_versions','affected_rows']:
                self.assertIn(key,step)
        child,child_meta,_=self.run_steps(result,[{'operation':'filter_rows','column':'x','operator':'greater_than','value':1}])
        self.assertEqual(child_meta['root_dataset_id'],source['dataset_id'])
        self.assertEqual(len(child_meta['processing_history']),3)
        self.assertEqual((self.root/source['current_file_path']).read_bytes(),self.before)

    def test_later_step_failure_rolls_back_all_intermediates(self):
        source=self.source('x\n1\n2\n')
        with self.assertRaises(DatasetError):
            clean_dataset(self.store,source['dataset_id'],expected_sha256=source['sha256'],steps=[
                {'operation':'sort_values','by':['x'],'ascending':False},{'operation':'drop_missing','subset':['missing']}])
        self.assertFalse((self.root/'datasets/processed').exists())
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])

    def test_zero_row_result_is_a_valid_explicit_processed_dataset(self):
        source=self.source('x\n1\n2\n')
        frame,meta,_=self.run_steps(source,[{'operation':'filter_rows','column':'x','operator':'greater_than','value':10}])
        self.assertEqual(len(frame),0);self.assertEqual(meta['row_count'],0)

    def test_stale_input_version_is_rejected(self):
        source=self.source('x\n1\n2\n')
        with self.assertRaises(DatasetError):
            clean_dataset(self.store,source['dataset_id'],expected_sha256='0'*64,steps=[{'operation':'drop_duplicates'}])

