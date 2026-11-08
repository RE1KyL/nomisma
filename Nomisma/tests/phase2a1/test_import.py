from datetime import datetime
import json
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from support import ProjectTest
from dataset_store import DatasetError, file_sha256
from dataset_io import import_dataset


class ImportTests(ProjectTest):
    def csv(self, text, file_encoding='utf8', name='input.csv', **options):
        file=self.root/name;file.write_bytes(text.encode(file_encoding));before=file.read_bytes()
        result=import_dataset(self.store,name,**options)
        self.assertEqual(file.read_bytes(),before)
        self.assertEqual((self.root/result['original_file_path']).read_bytes(),before)
        self.assertEqual(file_sha256(file),result['sha256'])
        return result

    def workbook(self, rows=None):
        file=self.root/'input.xlsx';wb=Workbook();wb.active.title='First'
        for row in rows or [['日期','收盘','代码'],[datetime(2025,1,2),123.5,'000001']]:wb.active.append(row)
        second=wb.create_sheet('Second');second.append(['name','value']);second.append(['另一表',42])
        wb.save(file);wb.close();return file

    def test_utf8_types_dates_identifiers_and_literal_na(self):
        result=self.csv('日期,收盘,股票代码,备注\n2025-01-02,12.5,000001,NA\n2025-01-03,13,000002,正常\n')
        fields={f['name']:f for f in result['fields']}
        self.assertEqual(fields['日期']['semantic_type'],'date')
        self.assertEqual(fields['收盘']['semantic_type'],'number')
        self.assertEqual(fields['股票代码']['semantic_type'],'identifier')
        self.assertEqual(result['sample'][0]['股票代码'],'000001')
        self.assertEqual(result['sample'][0]['备注'],'NA')
        meta=self.store.metadata(result['dataset_id'])
        self.assertEqual(meta['data_start'],'2025-01-02');self.assertEqual(meta['data_end'],'2025-01-03')

    def test_utf8_bom(self):
        result=self.csv('名称,数值\n中文,1\n',file_encoding='utf-8-sig')
        self.assertEqual(result['fields'][0]['name'],'名称')
        self.assertEqual(self.store.metadata(result['dataset_id'])['parser_options']['encoding'],'utf-8-sig')

    def test_gbk_explicit_and_auto_compatible_decoding(self):
        first=self.csv('名称,数值\n中文,1\n',file_encoding='gbk',name='gbk.csv',encoding='gbk')
        self.assertEqual(first['sample'][0]['名称'],'中文')
        file=self.root/'auto.csv';file.write_bytes('名称,数值\n中文,1\n'.encode('gbk'))
        auto=import_dataset(self.store,'auto.csv')
        self.assertEqual(auto['sample'][0]['名称'],'中文')
        self.assertEqual(self.store.metadata(auto['dataset_id'])['parser_options']['encoding'],'gb18030')

    def test_common_delimiters_and_quoted_newline(self):
        for i,char in enumerate([',','\t',';','|']):
            with self.subTest(delimiter=repr(char)):
                result=self.csv(f'名称{char}值\n"中文\n多行"{char}2\n',name=f'delimiter{i}.csv')
                self.assertEqual(result['row_count'],1);self.assertEqual(result['column_count'],2)
                self.assertEqual(result['sample'][0]['名称'],'中文\n多行')

    def test_tsv_snapshot_keeps_extension_and_delimiter(self):
        result=self.csv('name\tvalue\nentry\t3\n',name='input.tsv')
        self.assertTrue(result['current_file_path'].endswith('.tsv'))
        self.assertEqual(result['file_type'],'tsv')

    def test_empty_file_and_bad_encoding_and_irregular_fields(self):
        for name,content,options,code in [
            ('empty.csv',b'',{},'EMPTY_DATA'),
            ('bad.csv',b'\x81',{},'ENCODING_ERROR'),
            ('null.csv',b'\x00abc',{},'ENCODING_ERROR'),
            ('width.csv',b'a,b\n1,2,3\n',{'delimiter':'comma'},'IRREGULAR_FIELDS'),
            ('quote.csv',b'a,b\n"never closed,2',{'delimiter':'comma'},'CSV_PARSE_ERROR'),
        ]:
            with self.subTest(name=name):
                (self.root/name).write_bytes(content)
                with self.assertRaises(DatasetError) as caught:import_dataset(self.store,name,**options)
                self.assertEqual(caught.exception.code,code)
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])
        self.assertFalse((self.root/'datasets/metadata').exists())

    def test_header_selection_no_header_and_duplicate_headers(self):
        result=self.csv('Report\n日期,值\n2025-01-02,3\n',delimiter='comma',header_row=2)
        self.assertEqual(result['row_count'],1)
        result=self.csv('甲,2\n乙,3\n',name='no_header.csv',header_row=0,column_names=['名称','值'])
        self.assertEqual(result['row_count'],2)
        (self.root/'duplicate.csv').write_text('same,same\n1,2\n',encoding='utf8')
        with self.assertRaises(DatasetError) as caught:import_dataset(self.store,'duplicate.csv')
        self.assertEqual(caught.exception.code,'INVALID_HEADER')

    def test_invalid_values_retained_not_silently_coerced(self):
        result=self.csv('date,number\n2025-02-30,1\n2025-01-02,bad\n')
        self.assertEqual(result['sample'][0]['date'],'2025-02-30')
        self.assertEqual(result['sample'][1]['number'],'bad')
        self.assertEqual(result['fields'][0]['parse_failure_count'],1)

    def test_multiple_date_columns_do_not_force_a_time_series_range(self):
        result=self.csv('start_date,end_date\n2025-01-02,2025-02-01\n')
        metadata=self.store.metadata(result['dataset_id'])
        self.assertEqual(metadata['date_range_status'],'ambiguous')
        self.assertIsNone(metadata['data_start']);self.assertIsNone(metadata['data_end'])

    def test_missing_empty_fields_policy_is_explicit(self):
        result=self.csv('name,value\nA,\nB,2\n')
        self.assertIsNone(result['sample'][0]['value'])
        self.assertEqual(self.store.metadata(result['dataset_id'])['parser_options']['empty_fields_are_null'],True)
        result=self.csv('name,value\nA,\nB,2\n',name='keep.csv',empty_fields_are_null=False)
        self.assertEqual(result['sample'][0]['value'],'')

    def test_excel_default_sheet_dates_numbers_and_source_preserved(self):
        file=self.workbook();before=file.read_bytes()
        result=import_dataset(self.store,'input.xlsx')
        self.assertEqual(result['sheet_name'],'First');self.assertEqual(result['row_count'],1)
        self.assertEqual(result['sample'][0]['代码'],'000001')
        self.assertEqual(result['fields'][0]['semantic_type'],'datetime')
        self.assertEqual(result['fields'][1]['semantic_type'],'number')
        self.assertEqual(file.read_bytes(),before)
        self.assertEqual((self.root/result['current_file_path']).read_bytes(),before)
        wb=load_workbook(file);self.assertEqual(wb.sheetnames,['First','Second']);wb.close()

    def test_single_sheet_and_selected_sheet_and_missing_sheet(self):
        self.workbook();result=import_dataset(self.store,'input.xlsx',sheet='Second')
        self.assertEqual(result['sample'][0]['name'],'另一表')
        with self.assertRaises(DatasetError) as caught:import_dataset(self.store,'input.xlsx',sheet='Missing')
        self.assertEqual(caught.exception.code,'SHEET_NOT_FOUND')
        wb=Workbook();wb.active.append(['value']);wb.active.append([5]);wb.save(self.root/'single.xlsx');wb.close()
        result=import_dataset(self.store,'single.xlsx');self.assertEqual(result['row_count'],1)

    def test_excel_header_row_and_empty_sheet(self):
        self.workbook([['Title'],['Date','Amount'],[datetime(2025,1,2),5]])
        result=import_dataset(self.store,'input.xlsx',header_row=2)
        self.assertEqual(result['fields'][0]['name'],'Date')
        wb=Workbook();wb.save(self.root/'empty.xlsx');wb.close()
        with self.assertRaises(DatasetError) as caught:import_dataset(self.store,'empty.xlsx')
        self.assertEqual(caught.exception.code,'EMPTY_SHEET')

    def test_formula_is_not_calculated_or_edited(self):
        self.workbook([['formula'],['=1+2']]);file=self.root/'input.xlsx';before=file.read_bytes()
        with self.assertRaises(DatasetError) as caught:import_dataset(self.store,'input.xlsx')
        self.assertEqual(caught.exception.code,'FORMULA_POLICY_REQUIRED')
        result=import_dataset(self.store,'input.xlsx',formula_policy='formula_text')
        self.assertEqual(result['sample'][0]['formula'],'=1+2');self.assertEqual(file.read_bytes(),before)

    def test_unsupported_file_stale_hash_source_forgery_and_size_limit(self):
        (self.root/'old.xls').write_bytes(b'old')
        with self.assertRaises(DatasetError) as caught:import_dataset(self.store,'old.xls')
        self.assertEqual(caught.exception.code,'UNSUPPORTED_FILE_TYPE')
        (self.root/'input.csv').write_text('a\n1\n')
        with self.assertRaises(DatasetError):import_dataset(self.store,'input.csv',expected_sha256='0'*64)
        with self.assertRaises(DatasetError):import_dataset(self.store,'input.csv',source_type='akshare')
        with patch('dataset_io.MAX_CSV_BYTES',1),self.assertRaises(DatasetError) as caught:import_dataset(self.store,'input.csv')
        self.assertEqual(caught.exception.code,'RESOURCE_LIMIT')

    def test_permission_error_and_source_change_are_identifiable(self):
        (self.root/'input.csv').write_text('a\n1\n')
        with patch('dataset_io.file_sha256',side_effect=PermissionError('denied')),self.assertRaises(DatasetError) as caught:
            import_dataset(self.store,'input.csv')
        self.assertEqual(caught.exception.code,'PERMISSION_DENIED')
        actual=file_sha256(self.root/'input.csv')
        with patch('dataset_io.file_sha256',side_effect=[actual,'changed']),self.assertRaises(DatasetError) as caught:
            import_dataset(self.store,'input.csv')
        self.assertEqual(caught.exception.code,'STALE_SOURCE')
        self.assertEqual(list((self.root/'datasets/.staging').iterdir()),[])

    def test_unknown_source_is_retained_and_long_integers_remain_text(self):
        result=self.csv('value\n12345678901234567890\n',source_type='unknown')
        self.assertEqual(result['source_type'],'unknown')
        self.assertEqual(result['sample'][0]['value'],'12345678901234567890')

    def test_chart_workbook_can_be_read_without_altering_editor_or_original(self):
        from openpyxl.chart import LineChart, Reference
        self.workbook([['value'],[1],[2]])
        file=self.root/'input.xlsx';wb=load_workbook(file)
        chart=LineChart();chart.add_data(Reference(wb.active,min_col=1,min_row=1,max_row=3),titles_from_data=True)
        wb.active.add_chart(chart,'D1');wb.save(file);wb.close();before=file.read_bytes()
        result=import_dataset(self.store,'input.xlsx')
        self.assertEqual(result['row_count'],2);self.assertEqual(file.read_bytes(),before)

    def test_zip_and_cell_limits_fail_without_committed_metadata(self):
        self.workbook()
        with patch('dataset_io.MAX_XLSX_BYTES',1),self.assertRaises(DatasetError) as caught:
            import_dataset(self.store,'input.xlsx')
        self.assertEqual(caught.exception.code,'RESOURCE_LIMIT')

    def test_invalid_option_types_and_empty_name_are_rejected(self):
        (self.root/'input.csv').write_text('a,b\n1,2\n')
        for options in [{'header_row':True},{'column_names':'ab'},{'encoding':[]},{'empty_fields_are_null':'yes'},
                        {'expected_sha256':'invalid'},{'dataset_name':''},{'sheet':123}]:
            with self.subTest(options=options),self.assertRaises(DatasetError):
                import_dataset(self.store,'input.csv',**options)
        (self.root/'cells.csv').write_text('a,b\n1,2\n3,4\n')
        with patch('dataset_io.MAX_CELLS',2),self.assertRaises(DatasetError) as caught:
            import_dataset(self.store,'cells.csv')
        self.assertEqual(caught.exception.code,'RESOURCE_LIMIT')

