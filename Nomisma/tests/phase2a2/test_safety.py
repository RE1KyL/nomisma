import json
import os
import subprocess
from unittest.mock import patch

import pandas as pd
from _bootstrap import ProjectTest
from dataset_io import import_dataset,write_canonical_csv
from dataset_store import DatasetError,file_sha256
from data_quality import evaluate_quality,check_dataset_quality
from data_cleaning import clean_dataset
from data_export import export_dataset


class SafetyTests(ProjectTest):
    def source(self,frame=None):
        if frame is None:frame=pd.DataFrame({'x':pd.Series([1.,2.,3.],dtype='Float64')})
        with self.store.transaction() as transaction:
            options=write_canonical_csv(frame,transaction.file('source.csv'))
            return self.store.commit(transaction,frame,dataset_name='Boundary fixture',original_name='source.csv',
                current_name='source.csv',provenance=self.provenance(),parser_options=options)

    def test_extreme_finite_statistics_do_not_claim_reliable_z_scores(self):
        report=evaluate_quality(pd.DataFrame({'x':[1e308]*8}))
        self.assertEqual(report['outliers']['x']['z_score']['status'],'numerical_overflow')
        json.dumps(report,allow_nan=False)

    def test_non_finite_fill_statistic_is_rejected_without_processed_output(self):
        source=self.source(pd.DataFrame({'x':pd.Series([1e308,1e308,None],dtype='Float64')}))
        with self.assertRaises(DatasetError) as caught:
            clean_dataset(self.store,source['dataset_id'],expected_sha256=source['sha256'],
                steps=[{'operation':'fill_missing','columns':['x'],'method':'mean'}])
        self.assertEqual(caught.exception.code,'NUMERIC_OVERFLOW')
        self.assertFalse((self.root/'datasets/processed').exists())
        self.assertEqual(file_sha256(self.root/source['current_file_path']),source['sha256'])
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])

    def test_xlsx_infinity_is_explicitly_rejected_but_csv_preserves_it(self):
        source=self.source(pd.DataFrame({'x':pd.Series([1.,float('inf'),None],dtype='Float64')}))
        with self.assertRaises(DatasetError) as caught:
            export_dataset(self.store,source['dataset_id'],expected_sha256=source['sha256'],format='xlsx')
        self.assertEqual(caught.exception.code,'XLSX_NON_FINITE')
        receipt=export_dataset(self.store,source['dataset_id'],expected_sha256=source['sha256'],format='csv')
        self.assertTrue(receipt['roundtrip_verified'])

    def test_xlsx_nanosecond_timestamp_cannot_be_silently_rounded(self):
        frame=pd.DataFrame({'date':pd.to_datetime(['2025-01-02T00:00:00.000000001'])})
        source=self.source(frame)
        with self.assertRaises(DatasetError) as caught:
            export_dataset(self.store,source['dataset_id'],expected_sha256=source['sha256'],format='xlsx')
        self.assertEqual(caught.exception.code,'XLSX_PRECISION_UNSUPPORTED')
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])

    def test_failure_publishing_receipt_rolls_back_only_own_export(self):
        source=self.source();publish=self.store._publish_file
        keep=self.store.directory('datasets/exports')/'user.csv';keep.write_bytes(b'USER')
        def failing(staged,destination):
            if destination.suffix=='.json':raise PermissionError('Injected permission failure')
            publish(staged,destination)
        with patch.object(self.store,'_publish_file',side_effect=failing),self.assertRaises(DatasetError) as caught:
            export_dataset(self.store,source['dataset_id'],expected_sha256=source['sha256'],format='csv',file_name='failed.csv')
        self.assertEqual(caught.exception.code,'PERMISSION_DENIED')
        self.assertEqual(keep.read_bytes(),b'USER');self.assertFalse((keep.parent/'failed.csv').exists())
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])
        self.assertEqual(file_sha256(self.root/source['current_file_path']),source['sha256'])

    def test_actual_export_junction_and_dangling_junction_are_rejected(self):
        source=self.source();link=self.root/'datasets/exports'
        if os.name=='nt':
            result=subprocess.run(['D:/node.exe','-e',"require('fs').symlinkSync(process.argv[1],process.argv[2],'junction')",str(self.other),str(link)],capture_output=True)
            self.assertEqual(result.returncode,0)
        else:link.symlink_to(self.other,target_is_directory=True)
        try:
            for dangling in (False,True):
                if dangling:self.other.rmdir()
                with self.subTest(dangling=dangling),self.assertRaises(DatasetError) as caught:
                    export_dataset(self.store,source['dataset_id'],expected_sha256=source['sha256'],format='csv')
                self.assertEqual(caught.exception.code,'LINK_NOT_ALLOWED')
            self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])
        finally:link.rmdir() if os.name=='nt' else link.unlink()

    def test_quality_metadata_change_is_rejected_before_publication(self):
        source=self.source();metadata=self.store.metadata
        def changed(dataset_id):
            value=metadata(dataset_id);changed.calls+=1
            if changed.calls>1:value['dataset_name']='changed concurrently'
            return value
        changed.calls=0
        with patch.object(self.store,'metadata',side_effect=changed),self.assertRaises(DatasetError) as caught:
            check_dataset_quality(self.store,source['dataset_id'],expected_sha256=source['sha256'])
        self.assertEqual(caught.exception.code,'STALE_DATASET')
        self.assertFalse((self.root/'datasets/metadata/quality').exists())

    def test_malformed_quality_report_has_a_stable_error(self):
        source=self.source();report=check_dataset_quality(self.store,source['dataset_id'],expected_sha256=source['sha256'])
        (self.root/report['report_path']).write_text('{}',encoding='utf8')
        with self.assertRaises(DatasetError) as caught:
            export_dataset(self.store,source['dataset_id'],expected_sha256=source['sha256'],format='xlsx',quality_report_id=report['quality_report_id'])
        self.assertEqual(caught.exception.code,'QUALITY_REPORT_INVALID')

    def test_ambiguous_date_filter_is_rejected(self):
        (self.root/'date.csv').write_text('date,x\n2025-01-02,1\n',encoding='utf8')
        source=import_dataset(self.store,'date.csv')
        with self.assertRaises(DatasetError) as caught:
            clean_dataset(self.store,source['dataset_id'],expected_sha256=source['sha256'],
                steps=[{'operation':'filter_rows','column':'date','operator':'greater_than','value':'01/02/2025'}])
        self.assertEqual(caught.exception.code,'TYPE_MISMATCH')
