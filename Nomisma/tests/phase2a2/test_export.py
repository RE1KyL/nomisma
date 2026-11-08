from unittest.mock import patch
import json
from datetime import datetime
import pandas as pd
from openpyxl import load_workbook
from _bootstrap import ProjectTest
from dataset_io import import_dataset,read_canonical_csv
from dataset_store import DatasetError,file_sha256
from data_quality import check_dataset_quality
from data_cleaning import clean_dataset
from data_export import export_dataset


class ExportTests(ProjectTest):
    def source(self,text='date,name,value\n2025-01-02,中文,12.5\n2025-01-03,测试,10\n'):
        (self.root/'input.csv').write_text(text,encoding='utf8')
        self.source_result=import_dataset(self.store,'input.csv');self.raw_before=(self.root/self.source_result['current_file_path']).read_bytes()
        return self.source_result

    def export(self,result,**options):
        receipt=export_dataset(self.store,result['dataset_id'],expected_sha256=result['sha256'],**options)
        self.assertEqual(receipt['output_sha256'],file_sha256(self.root/receipt['output_file']))
        self.assertTrue(receipt['roundtrip_verified'])
        self.assertEqual((self.root/self.source_result['current_file_path']).read_bytes(),self.raw_before)
        saved=json.loads((self.root/receipt['metadata_path']).read_text(encoding='utf8'))
        self.assertEqual(saved['source_sha256'],result['sha256'])
        self.assertEqual(saved['output_sha256'],receipt['output_sha256'])
        return receipt

    def test_csv_encodings_chinese_dates_fields_and_independent_read(self):
        source=self.source()
        for encoding in ['utf-8','utf-8-sig']:
            with self.subTest(encoding=encoding):
                receipt=self.export(source,format='csv',encoding=encoding)
                file=self.root/receipt['output_file'];self.assertEqual(file.read_bytes().startswith(b'\xef\xbb\xbf'),encoding=='utf-8-sig')
                plain=pd.read_csv(file,encoding=encoding)
                self.assertEqual(plain['name'].tolist(),['中文','测试'])
                self.assertEqual(list(plain.columns),['date','name','value'])
                self.assertEqual(plain['date'].str[:10].tolist(),['2025-01-02','2025-01-03'])
                typed=read_canonical_csv(file,receipt['serialization'])
                self.assertEqual(typed['date'].dt.day.tolist(),[2,3])

    def test_xlsx_multisheet_quality_dictionary_provenance_and_types(self):
        source=self.source();quality=check_dataset_quality(self.store,source['dataset_id'],expected_sha256=source['sha256'],date_column='date')
        receipt=self.export(source,format='xlsx',quality_report_id=quality['quality_report_id'])
        workbook=load_workbook(self.root/receipt['output_file'],data_only=False)
        self.assertEqual(workbook.sheetnames,['Data','Quality','Dictionary','Provenance'])
        self.assertEqual(workbook['Data']['B2'].value,'中文')
        self.assertEqual(workbook['Data']['C2'].value,12.5)
        self.assertIsInstance(workbook['Data']['A2'].value,datetime)
        self.assertTrue(workbook['Data']['A1'].font.bold)
        self.assertEqual(workbook['Data']['A2'].number_format,'yyyy-mm-dd')
        self.assertEqual(receipt['quality_report']['sha256'],quality['sha256'])
        workbook.close()

    def test_data_only_xlsx_and_sheet_collision_are_explicit(self):
        source=self.source()
        receipt=self.export(source,format='xlsx',include_dictionary=False,include_provenance=False,data_sheet_name='研究数据')
        workbook=load_workbook(self.root/receipt['output_file']);self.assertEqual(workbook.sheetnames,['研究数据']);workbook.close()
        for name in ['Dictionary','dictionary','bad:name','a'*32]:
            with self.subTest(name=name),self.assertRaises(DatasetError):
                self.export(source,format='xlsx',data_sheet_name=name)

    def test_existing_filename_is_never_overwritten(self):
        source=self.source();first=self.export(source,format='csv',file_name='keep.csv')
        file=self.root/first['output_file'];before=file.read_bytes();count=len(list((self.root/'datasets/metadata/artifacts').glob('*.json')))
        with self.assertRaises(DatasetError) as caught:self.export(source,format='csv',file_name='keep.csv')
        self.assertEqual(caught.exception.code,'FILE_EXISTS');self.assertEqual(file.read_bytes(),before)
        self.assertEqual(len(list((self.root/'datasets/metadata/artifacts').glob('*.json'))),count)
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])

    def test_absolute_traversal_reserved_and_invalid_names_are_rejected(self):
        source=self.source()
        for name in ['../out.csv','C:/out.csv','sub/out.csv','CON.csv','bad?.csv','out.csv:stream','bad.xlsx']:
            with self.subTest(name=name),self.assertRaises(DatasetError):self.export(source,format='csv',file_name=name)

    def test_export_failure_cleans_staging_without_formal_file(self):
        source=self.source()
        with patch('data_export._verify_xlsx',side_effect=DatasetError('ROUNDTRIP_MISMATCH','test')):
            with self.assertRaises(DatasetError):self.export(source,format='xlsx',file_name='failed.xlsx')
        self.assertFalse((self.root/'datasets/exports/failed.xlsx').exists())
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])

    def test_csv_formula_like_text_requires_explicit_escape_and_xlsx_is_literal(self):
        source=self.source('name\n=1+2\n')
        with self.assertRaises(DatasetError) as caught:self.export(source,format='csv')
        self.assertEqual(caught.exception.code,'CSV_FORMULA_RISK')
        csv=self.export(source,format='csv',csv_formula_policy='escape')
        self.assertIn("'=1+2",(self.root/csv['output_file']).read_text(encoding='utf-8-sig'))
        self.assertEqual(len(csv['escaped_cells']),1)
        xlsx=self.export(source,format='xlsx')
        workbook=load_workbook(self.root/xlsx['output_file'],data_only=False)
        self.assertEqual(workbook['Data']['A2'].value,'=1+2');self.assertEqual(workbook['Data']['A2'].data_type,'s');workbook.close()

    def test_empty_strings_and_infinities_not_silently_corrupted_by_excel(self):
        source=self.source('name,x\nA,1\n,2\n')
        # Explicit empty-field policy preserves empty strings for this fixture.
        explicit=import_dataset(self.store,'input.csv',empty_fields_are_null=False)
        with self.assertRaises(DatasetError) as caught:self.export(explicit,format='xlsx')
        self.assertEqual(caught.exception.code,'XLSX_EMPTY_STRING')

    def test_quality_report_for_old_version_not_attached_to_cleaned_data(self):
        source=self.source('x\n1\n1\n2\n');quality=check_dataset_quality(self.store,source['dataset_id'],expected_sha256=source['sha256'])
        cleaned=clean_dataset(self.store,source['dataset_id'],expected_sha256=source['sha256'],steps=[{'operation':'drop_duplicates'}])
        with self.assertRaises(DatasetError) as caught:self.export(cleaned,format='xlsx',quality_report_id=quality['quality_report_id'])
        self.assertEqual(caught.exception.code,'QUALITY_REPORT_MISMATCH')

    def test_desktop_size_limit_no_truncation(self):
        source=self.source('x\n'+'\n'.join(str(i) for i in range(2000))+'\n')
        with self.assertRaises(DatasetError) as caught:self.export(source,format='xlsx')
        self.assertEqual(caught.exception.code,'RESOURCE_LIMIT')
        receipt=self.export(source,format='xlsx',compatibility_profile='data')
        self.assertEqual(receipt['row_count'],2000)

