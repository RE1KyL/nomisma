import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch
import uuid

import pandas as pd
from support import ProjectTest
from dataset_store import DatasetError, ProjectDatasetStore, file_sha256


class StoreTests(ProjectTest):
    def commit(self, name='Test dataset'):
        frame = pd.DataFrame({'date':['2025-01-02'],'close':[12.5]})
        with self.store.transaction() as transaction:
            frame.to_csv(transaction.file('source.csv'), index=False, mode='x', encoding='utf-8-sig')
            return self.store.commit(transaction, frame, dataset_name=name, original_name='source.csv',
                                     current_name='source.csv', provenance=self.provenance(), parser_options={})

    def test_unique_metadata_real_hash_and_bounded_summary(self):
        first, second = self.commit(), self.commit()
        self.assertNotEqual(first['dataset_id'],second['dataset_id'])
        metadata = self.store.metadata(first['dataset_id'])
        for key in ['dataset_name','source_type','source_name','source_function','request_parameters',
                    'fetched_at','imported_at','data_start','data_end','row_count','column_count',
                    'column_schema','package_version','original_file_path','current_file_path','sha256','import_history']:
            self.assertIn(key, metadata)
        self.assertEqual(file_sha256(self.root/first['current_file_path']),metadata['sha256'])
        self.assertEqual(metadata['source_type'],'user_provided')
        self.assertIsNone(metadata['fetched_at'])
        self.assertLess(len(json.dumps(first).encode()),16384)
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])

    def test_paths_reject_absolute_traversal_ads_and_backslash(self):
        for relative in ['../outside.csv','C:/Windows/a.csv','//host/share/a.csv','a/../../x','a\\b.csv','x.csv:stream','a//b','./a','a\x00b']:
            with self.subTest(relative=repr(relative)),self.assertRaises(DatasetError):
                self.store.path(relative,allow_missing=True)

    def test_actual_junction_or_symlink_is_rejected(self):
        link=self.root/'redirect'
        if os.name=='nt':
            result=subprocess.run(['D:/node.exe','-e',"require('fs').symlinkSync(process.argv[1],process.argv[2],'junction')",str(self.other),str(link)],capture_output=True)
            self.assertEqual(result.returncode,0)
        else:
            link.symlink_to(self.other,target_is_directory=True)
        try:
            with self.assertRaises(DatasetError) as caught:self.store.path('redirect/new.csv',allow_missing=True)
            self.assertEqual(caught.exception.code,'LINK_NOT_ALLOWED')
            self.other.rmdir()  # A dangling junction must still be rejected by lstat.
            with self.assertRaises(DatasetError) as caught:self.store.path('redirect/new.csv',allow_missing=True)
            self.assertEqual(caught.exception.code,'LINK_NOT_ALLOWED')
        finally:link.rmdir() if os.name=='nt' else link.unlink()

    def test_existing_output_is_not_overwritten(self):
        fixed=uuid.UUID('00000000-0000-4000-8000-000000000001')
        destination=self.root/f'datasets/raw/ds_{fixed.hex}'
        destination.mkdir(parents=True);(destination/'keep.txt').write_text('USER',encoding='utf8')
        with patch('dataset_store.uuid.uuid4',return_value=fixed),self.assertRaises(DatasetError) as caught:self.commit()
        self.assertEqual(caught.exception.code,'FILE_EXISTS')
        self.assertEqual((destination/'keep.txt').read_text(),'USER')
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])

    def test_existing_metadata_and_atomic_destination_are_not_overwritten(self):
        fixed=uuid.UUID('00000000-0000-4000-8000-000000000002')
        directory=self.root/'datasets/metadata';directory.mkdir(parents=True)
        marker=directory/f'ds_{fixed.hex}.json';marker.write_text('USER ORIGINAL')
        with patch('dataset_store.uuid.uuid4',return_value=fixed),self.assertRaises(DatasetError):self.commit()
        self.assertEqual(marker.read_text(),'USER ORIGINAL')
        self.assertFalse((self.root/f'datasets/raw/ds_{fixed.hex}').exists())
        source=self.root/'new.txt';destination=self.root/'keep.txt'
        source.write_text('NEW');destination.write_text('EXISTING')
        with self.assertRaises(FileExistsError):self.store._publish_file(source,destination)
        self.assertEqual(destination.read_text(),'EXISTING');self.assertEqual(source.read_text(),'NEW')

    def test_failure_after_data_publication_rolls_back_only_own_files(self):
        publish=self.store._publish_file
        def failing(source,destination):
            if destination.suffix=='.json':raise PermissionError('test failure')
            publish(source,destination)
        with patch.object(self.store,'_publish_file',side_effect=failing),self.assertRaises(DatasetError):self.commit()
        self.assertEqual(list((self.root/'datasets/raw').iterdir()),[])
        self.assertEqual(list((self.root/'datasets/metadata').iterdir()),[])
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])

    def test_modified_file_and_cross_project_descriptor_are_rejected(self):
        result=self.commit();file=self.root/result['current_file_path'];file.write_bytes(b'changed')
        with self.assertRaises(DatasetError) as caught:self.store.metadata(result['dataset_id'])
        self.assertEqual(caught.exception.code,'STALE_DATASET')
        path=self.root/result['metadata_path'];metadata=json.loads(path.read_text(encoding='utf8'))
        metadata['project_id']='project_economic';path.write_text(json.dumps(metadata),encoding='utf8')
        with self.assertRaises(DatasetError) as caught:self.store.metadata(result['dataset_id'])
        self.assertEqual(caught.exception.code,'METADATA_INVALID')

    def test_archived_forged_or_missing_context_is_rejected(self):
        with patch.dict(os.environ,{'NOMISMA_MODE':'economic'}),self.assertRaises(DatasetError):ProjectDatasetStore()
        with patch.dict(os.environ,{'NOMISMA_WORKSPACE':str(self.other)}),self.assertRaises(DatasetError):ProjectDatasetStore()
        entries=json.loads(self.registry.read_text());entries[0]['status']='archived';self.registry.write_text(json.dumps(entries))
        with self.assertRaises(DatasetError):self.commit()

