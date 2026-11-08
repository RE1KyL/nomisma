import datetime
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
import zipfile
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

ROOT=Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1] / "nomisma_tools"))
import workbook_io as service

class WorkbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=ROOT/'runs'/uuid.uuid4().hex
        cls.root.mkdir(parents=True,exist_ok=False)
        (cls.root/'datasets/raw').mkdir(parents=True)
        (cls.root/'datasets/processed').mkdir(parents=True)
        w=Workbook();s=w.active;s.title='Prices';w.create_sheet('Notes')['A1']='第二张表'
        s.append(['名称','数值','日期','货币','比例','公式'])
        s.append(['沪深300',123.5,datetime.datetime(2026,11,1),100.25,0.15,'=B2*2'])
        s['C2'].number_format='yyyy-mm-dd';s['D2'].number_format='¥#,##0.00';s['E2'].number_format='0.00%'
        s['A2'].font=Font(bold=True,color='174F94');s['A2'].fill=PatternFill('solid',fgColor='EAF3FF')
        w.save(cls.root/'datasets/raw/sample.xlsx');w.close()
        shutil.copyfile(cls.root/'datasets/raw/sample.xlsx',cls.root/'datasets/processed/sample.xlsx')

    def test_01_read_two_sheets_and_types(self):
        doc=service.handle({'root':str(self.root),'path':'datasets/processed/sample.xlsx','action':'open'})
        self.assertEqual([s['name'] for s in doc['sheets']],['Prices','Notes'])
        self.assertEqual(doc['sheets'][0]['rows'][1][2]['kind'],'date')
        self.assertEqual(doc['sheets'][0]['rows'][1][5]['kind'],'formula')
        with (self.root/'workbook-open-evidence.json').open('x',encoding='utf8') as f:json.dump(doc,f,ensure_ascii=False,indent=2)

    def test_02_save_real_workbook_preserves_other_cells(self):
        file=self.root/'datasets/processed/sample.xlsx'
        original=service.version(self.root/'datasets/raw/sample.xlsx')
        doc=service.handle({'root':str(self.root),'path':'datasets/processed/sample.xlsx','action':'open'})
        result=service.handle({'root':str(self.root),'path':'datasets/processed/sample.xlsx','action':'save','version':doc['version'],
            'changes':[{'sheet':'Prices','cell':'B2','value':504.7,'kind':'number'}]})
        w=load_workbook(file,data_only=False)
        self.assertEqual(w['Prices']['B2'].value,504.7)
        self.assertEqual(w['Notes']['A1'].value,'第二张表')
        self.assertEqual(w['Prices']['F2'].value,'=B2*2')
        self.assertEqual(w['Prices']['E2'].number_format,'0.00%')
        self.assertEqual(w['Prices']['D2'].number_format,'¥#,##0.00')
        self.assertTrue(w['Prices']['A2'].font.bold)
        self.assertEqual(w['Prices']['C2'].value,datetime.datetime(2026,11,1))
        w.close()
        self.assertEqual(service.version(self.root/'datasets/raw/sample.xlsx'),original)
        with (self.root/'workbook-save-evidence.json').open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)

    def test_03_conflict_and_raw_protection(self):
        for relative,version in [('datasets/raw/sample.xlsx','any'),('datasets/processed/sample.xlsx','stale')]:
            with self.assertRaises(ValueError):service.handle({'root':str(self.root),'path':relative,'action':'save','version':version,'changes':[]})

    def test_04_path_traversal(self):
        for relative in ['../outside.xlsx','C:/Users/test.xlsx','datasets/../sample.xlsx','datasets\\raw\\sample.xlsx']:
            with self.assertRaises(ValueError):service.project_file(self.root,relative)

    def test_05_unsupported_feature_is_rejected(self):
        target=self.root/'unsupported.xlsx'
        shutil.copyfile(self.root/'datasets/raw/sample.xlsx',target)
        with zipfile.ZipFile(target,'a') as z:z.writestr('xl/drawings/drawing1.xml','<drawing/>')
        with self.assertRaises(ValueError):service.validate(target)

    def test_06_invalid_value_does_not_change_file(self):
        target=self.root/'datasets/processed/sample.xlsx'
        before=service.version(target)
        for value in ['a'*32768,float('nan')]:
            with self.assertRaises(ValueError):service.handle({'root':str(self.root),'path':'datasets/processed/sample.xlsx','action':'save','version':before,
                'changes':[{'sheet':'Prices','cell':'A2','value':value}]})
            self.assertEqual(service.version(target),before)

    def test_07_protected_and_oversized_workbooks(self):
        for name,configure in [('protected.xlsx',lambda s:setattr(s.protection,'sheet',True)),
                               ('large.xlsx',lambda s:s.cell(2001,1,'beyond prototype limit'))]:
            w=Workbook();configure(w.active);w.save(self.root/name);w.close()
            with self.assertRaises(ValueError):service.handle({'root':str(self.root),'path':name,'action':'open'})

if __name__=='__main__': unittest.main(verbosity=2)
