"""New CSV/XLSX data artifacts, independent of the existing workbook editor."""
from __future__ import annotations

from datetime import date,datetime
import json
import math
from numbers import Integral,Real
from pathlib import Path
import re
import uuid

import pandas as pd
from openpyxl import Workbook,load_workbook
from openpyxl.styles import Font,PatternFill

from data_quality import missing_kind
from dataset_io import load_dataset,write_canonical_csv,read_canonical_csv
from dataset_store import DatasetError,ProjectDatasetStore,file_sha256,io_boundary,packages,utc_now


def _filename(name: str, format: str) -> str:
    if (not isinstance(name,str) or not name or len(name)>80 or name.rstrip(' .')!=name
            or any(char in name for char in '/\\:\x00<>|?*') or any(ord(char)<32 for char in name) or name in ('.','..')
            or Path(name).suffix.lower()!='.'+format
            or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])',name.split('.')[0],re.I)):
        raise DatasetError('INVALID_PATH','Export filename must be a safe basename with the matching extension')
    return name


def _sheet_names(names: list[str]) -> None:
    if (len(set(name.casefold() for name in names if isinstance(name,str)))!=len(names)
            or any(not isinstance(name,str) or not name or len(name)>31 or re.search(r'[\[\]:*?/\\]',name)
                   or name.startswith("'") or name.endswith("'") for name in names)):
        raise DatasetError('INVALID_SHEET','Sheet names must be legal, non-empty and case-insensitively unique')


def _excel_scalar(value):
    if missing_kind(value):return None
    if hasattr(value,'item') and not isinstance(value,(datetime,date,pd.Timestamp)):value=value.item()
    if isinstance(value,str):
        if not value:raise DatasetError('XLSX_EMPTY_STRING','Excel cannot round-trip empty strings separately from blank cells')
        if len(value)>32767:raise DatasetError('RESOURCE_LIMIT','Excel cell text exceeds 32767 characters')
        return value
    if isinstance(value,(datetime,pd.Timestamp)):
        if value.tzinfo is not None or value.microsecond%1000 or getattr(value,'nanosecond',0):
            raise DatasetError('XLSX_PRECISION_UNSUPPORTED','Timezone-aware or sub-millisecond timestamps require an explicit conversion')
        return value.to_pydatetime() if isinstance(value,pd.Timestamp) else value
    if isinstance(value,date):return value
    if isinstance(value,bool):return value
    if isinstance(value,Integral):return str(value) if len(str(abs(int(value))))>15 else int(value)
    if isinstance(value,Real):
        if not math.isfinite(value):raise DatasetError('XLSX_NON_FINITE','Excel does not support infinite numeric cells')
        return float(value)
    raise DatasetError('XLSX_TYPE_UNSUPPORTED','Excel export accepts only supported scalar cells')


def _verify_xlsx(file: Path, tables: dict[str,pd.DataFrame]) -> None:
    workbook=load_workbook(file,read_only=True,data_only=False,keep_links=False)
    try:
        if workbook.sheetnames!=list(tables):raise DatasetError('ROUNDTRIP_MISMATCH','Workbook sheet list changed')
        for name,frame in tables.items():
            rows=list(workbook[name].iter_rows())
            if len(rows)!=len(frame)+1 or not rows or [cell.value for cell in rows[0]]!=list(frame.columns):
                raise DatasetError('ROUNDTRIP_MISMATCH','Workbook row count or header changed')
            for actual,expected in zip(rows[1:],frame.itertuples(index=False,name=None)):
                if len(actual)!=len(expected):raise DatasetError('ROUNDTRIP_MISMATCH','Workbook column count changed')
                for cell,value in zip(actual,expected):
                    normalized=_excel_scalar(value);observed=cell.value
                    if cell.data_type=='f':raise DatasetError('ROUNDTRIP_MISMATCH','Export unexpectedly created a formula')
                    if isinstance(normalized,(date,datetime)):
                        left=pd.Timestamp(normalized);right=pd.Timestamp(observed) if observed is not None else None
                        equal=right is not None and left==right
                    elif isinstance(normalized,float):
                        equal=isinstance(observed,(int,float)) and not isinstance(observed,bool) and math.isclose(normalized,observed,rel_tol=1e-12,abs_tol=1e-12)
                    else:equal=type(observed)==type(normalized) and observed==normalized
                    if not equal:raise DatasetError('ROUNDTRIP_MISMATCH','Workbook value or scalar type changed')
    finally:workbook.close()


def _write_xlsx(file: Path,tables: dict[str,pd.DataFrame],profile: str) -> list[str]:
    _sheet_names(list(tables))
    limit_rows=2000 if profile=='desktop' else 100001
    limit_cells=20000 if profile=='desktop' else 2_000_000
    if any(len(frame)+1>limit_rows or len(frame.columns)>100 for frame in tables.values()) or sum((len(frame)+1)*len(frame.columns) for frame in tables.values())>limit_cells:
        raise DatasetError('RESOURCE_LIMIT','Workbook exceeds the selected desktop/data profile; no truncation occurred')
    warnings=[];workbook=Workbook();workbook.remove(workbook.active)
    try:
        for name,frame in tables.items():
            sheet=workbook.create_sheet(name);sheet.freeze_panes='A2'
            for column,title in enumerate(frame.columns,1):
                cell=sheet.cell(1,column,str(title));cell.data_type='s'
                cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='174F94')
            for row_number,row in enumerate(frame.itertuples(index=False,name=None),2):
                for column,value in enumerate(row,1):
                    normalized=_excel_scalar(value);cell=sheet.cell(row_number,column,normalized)
                    if isinstance(normalized,str):cell.data_type='s'
                    if isinstance(normalized,(date,datetime)):
                        cell.number_format='yyyy-mm-dd hh:mm:ss.000' if isinstance(normalized,datetime) and normalized.time()!=datetime.min.time() else 'yyyy-mm-dd'
                    if isinstance(value,Integral) and not isinstance(value,bool) and isinstance(normalized,str):
                        warnings.append('LONG_INTEGER_AS_TEXT: Excel precision would otherwise be lost')
        workbook.save(file)
    finally:workbook.close()
    if file.stat().st_size>10_000_000:raise DatasetError('RESOURCE_LIMIT','Exported workbook exceeds 10 MB')
    _verify_xlsx(file,tables)
    return sorted(set(warnings))


def _quality_report(store: ProjectDatasetStore,quality_report_id: str,dataset_id: str,sha256: str) -> tuple[dict,dict]:
    if not isinstance(quality_report_id,str) or not re.fullmatch(r'qr_[a-f0-9]{32}',quality_report_id):
        raise DatasetError('INVALID_ARGUMENT','Invalid quality report identity')
    path=f'datasets/metadata/quality/{quality_report_id}.json';file=store.path(path)
    if file.stat().st_size>1_000_000:raise DatasetError('RESOURCE_LIMIT','Quality report exceeds its input limit')
    try:
        report=json.loads(file.read_text(encoding='utf8'))
        identity=(report['dataset_id'],report['input_sha256'],report['project_context']['project_id'])
    except (ValueError,KeyError,TypeError) as error:
        raise DatasetError('QUALITY_REPORT_INVALID','Quality report metadata is malformed') from error
    if identity!=(dataset_id,sha256,store.context['project_id']):
        raise DatasetError('QUALITY_REPORT_MISMATCH','Quality report belongs to a different dataset/version/project')
    return report,{'quality_report_id':quality_report_id,'path':path,'sha256':file_sha256(file)}


@io_boundary
def export_dataset(store: ProjectDatasetStore,dataset_id: str,*,expected_sha256: str,
                   format: str,file_name: str | None=None,encoding: str='utf-8-sig',
                   date_format: str='iso8601',data_sheet_name: str='Data',
                   quality_report_id: str | None=None,include_dictionary: bool=True,
                   include_provenance: bool=True,compatibility_profile: str='desktop',
                   csv_formula_policy: str='reject') -> dict:
    if format not in ('csv','xlsx') or encoding not in ('utf-8','utf-8-sig') or date_format!='iso8601':
        raise DatasetError('INVALID_ARGUMENT','Export supports CSV/XLSX, explicit UTF-8 encoding and ISO dates')
    if compatibility_profile not in ('desktop','data') or csv_formula_policy not in ('reject','escape'):
        raise DatasetError('INVALID_ARGUMENT','Invalid export profile or CSV formula policy')
    if not isinstance(include_dictionary,bool) or not isinstance(include_provenance,bool):
        raise DatasetError('INVALID_ARGUMENT','Metadata-sheet flags must be boolean')
    frame,metadata=load_dataset(store,dataset_id,expected_sha256=expected_sha256)
    artifact_id='art_'+uuid.uuid4().hex
    name=_filename(file_name if file_name is not None else artifact_id+'.'+format,format)
    destination=f'datasets/exports/{name}'
    report=association=None
    if quality_report_id:report,association=_quality_report(store,quality_report_id,dataset_id,metadata['sha256'])
    warnings=[];sheets=[];serialization=None;escapes=[]
    with store.transaction() as transaction:
        staged=transaction.file('export.'+format)
        if format=='csv':
            if any(str(column).startswith(('=','+','-','@','\t','\r')) for column in frame.columns):
                raise DatasetError('CSV_FORMULA_RISK','Unsafe spreadsheet-style header requires explicit upstream renaming')
            presentation=frame.copy(deep=True)
            for column in frame:
                for index,value in enumerate(frame[column]):
                    if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')):
                        if csv_formula_policy=='reject':raise DatasetError('CSV_FORMULA_RISK','CSV contains formula-like text; use explicit escape policy')
                        presentation.loc[index,column]="'"+value;escapes.append({'row':index,'column':column})
            serialization=write_canonical_csv(presentation,staged,encoding=encoding)
            restored=read_canonical_csv(staged,serialization)
            for escaped in escapes:restored.loc[escaped['row'],escaped['column']]=restored.loc[escaped['row'],escaped['column']][1:]
            try:pd.testing.assert_frame_equal(restored,frame)
            except AssertionError as error:raise DatasetError('ROUNDTRIP_MISMATCH','CSV round trip changed values or dtype') from error
            if escapes:warnings.append('CSV_FORMULA_ESCAPED: apostrophe prefix and reversal positions are recorded')
        else:
            _sheet_names([data_sheet_name,*(['Quality'] if report is not None else []),
                          *(['Dictionary'] if include_dictionary else []),*(['Provenance'] if include_provenance else [])])
            tables={data_sheet_name:frame}
            if report is not None:
                tables['Quality']=pd.DataFrame([[key,json.dumps(value,ensure_ascii=False,allow_nan=False)] for key,value in report.items()],columns=['section','details'])
            if include_dictionary:
                tables['Dictionary']=pd.DataFrame([{'name':column['name'],'dtype':column['pandas_dtype'],
                    'semantic_type':column.get('semantic_type','unknown')} for column in metadata['column_schema']])
            if include_provenance:
                values={'dataset_id':dataset_id,'source_type':metadata['source_type'],'source_name':metadata['source_name'],
                        'source_function':metadata['source_function'],'fetched_at':metadata['fetched_at'],
                        'input_sha256':metadata['sha256'],'processing_history':metadata.get('processing_history',[])}
                tables['Provenance']=pd.DataFrame([[key,json.dumps(value,ensure_ascii=False,allow_nan=False)] for key,value in values.items()],columns=['field','value'])
            warnings.extend(_write_xlsx(staged,tables,compatibility_profile))
            sheets=[{'name':key,'row_count':len(value),'column_count':len(value.columns)} for key,value in tables.items()]
        if store.metadata(dataset_id)!=metadata:raise DatasetError('STALE_DATASET','Dataset metadata changed during export')
        receipt={'artifact_id':artifact_id,'dataset_id':dataset_id,'project_context':store.context,
            'source_file':metadata['current_file_path'],'source_sha256':metadata['sha256'],
            'output_file':destination,'output_sha256':file_sha256(staged),'format':format,'exported_at':utc_now(),
            'export_parameters':{'encoding':encoding,'date_format':date_format,'data_sheet_name':data_sheet_name,
                'include_dictionary':include_dictionary,'include_provenance':include_provenance,
                'compatibility_profile':compatibility_profile,'csv_formula_policy':csv_formula_policy},
            'sheets':sheets,'row_count':len(frame),'column_count':len(frame.columns),
            'quality_report':association,'processing_history':metadata.get('processing_history',[]),
            'source_metadata_path':f'datasets/metadata/{dataset_id}.json','column_schema':metadata['column_schema'],
            'serialization':serialization,'escaped_cells':escapes,'warnings':warnings,'roundtrip_verified':True,
            'formula_calculation':False,'software_versions':packages()}
        descriptor=transaction.file('artifact.json')
        with descriptor.open('x',encoding='utf8') as stream:json.dump(receipt,stream,ensure_ascii=False,allow_nan=False,indent=2)
        if descriptor.stat().st_size>1_000_000:raise DatasetError('RESOURCE_LIMIT','Export metadata exceeds 1 MB')
        metadata_path=f'datasets/metadata/artifacts/{artifact_id}.json'
        store.publish_artifacts(transaction,{'export.'+format:destination,'artifact.json':metadata_path})
        receipt['metadata_path']=metadata_path
        return receipt
