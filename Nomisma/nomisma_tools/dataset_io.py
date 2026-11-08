"""CSV/XLSX ingestion for authorized Python callers, independent of MCP/editor UI."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
import io
import math
import os
from pathlib import Path
import re
from typing import Any
import zipfile
import uuid

import pandas as pd
from openpyxl import load_workbook
from xlsx_safety import UnsafeWorkbookXML, validate_xml_parts

from dataset_store import (DatasetError, ProjectDatasetStore, MAX_ROWS, MAX_COLUMNS,
                           MAX_CELLS, check_shape, file_sha256, utc_now, io_boundary)

MAX_CSV_BYTES = 20_000_000
MAX_XLSX_BYTES = 10_000_000
DELIMITERS = {'comma': ',', 'tab': '\t', 'semicolon': ';', 'pipe': '|'}
ENCODINGS = {'utf-8', 'utf-8-sig', 'gbk', 'gb18030'}


@dataclass
class LoadedTable:
    frame: pd.DataFrame
    parser_options: dict[str, Any]
    inference: dict[str, dict]


def infer_types(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Convert only wholly parseable columns; ambiguous/invalid values stay intact."""
    result = frame.copy()
    inference: dict[str, dict] = {}
    for name in frame.columns:
        series = frame[name]
        values = series.dropna()
        info: dict[str, Any] = {'semantic_type': 'unknown', 'confidence': 0.0}
        if not len(values):
            info['reason'] = 'All values are missing'
        elif values.map(lambda value: isinstance(value, bool)).all():
            result[name] = series.astype('boolean')
            info = {'semantic_type': 'boolean', 'confidence': 1.0}
        elif values.map(lambda value: isinstance(value, (date, datetime, pd.Timestamp))).all():
            result[name] = pd.to_datetime(series)
            info = {'semantic_type': 'datetime', 'confidence': 1.0, 'reason': 'Typed spreadsheet date values'}
        else:
            texts = values.map(str)
            identifier = bool(re.search(r'(^|_)(id|code|symbol)(_|$)|代码|编号|证件|邮编', str(name), re.I))
            identifier |= bool(texts.str.fullmatch(r'0\d+').any())
            identifier |= bool(texts.str.fullmatch(r'[+-]?\d{16,}').any())
            date_name = bool(re.search(r'date|time|日期|时间', str(name), re.I))
            date_format = None
            if date_name and texts.str.fullmatch(r'\d{4}-\d{2}-\d{2}').all():
                date_format = '%Y-%m-%d'
            elif date_name and texts.str.fullmatch(r'\d{4}/\d{2}/\d{2}').all():
                date_format = '%Y/%m/%d'
            elif date_name and texts.str.fullmatch(r'\d{8}').all():
                date_format = '%Y%m%d'
            if identifier:
                result[name] = series.astype('string')
                info = {'semantic_type': 'identifier', 'confidence': 0.95,
                        'reason': 'Identifier name, leading zero or long integer; text preserved'}
            elif date_format:
                parsed = pd.to_datetime(series, format=date_format, errors='coerce')
                failed = int((series.notna() & parsed.isna()).sum())
                if not failed:
                    result[name] = parsed
                    info = {'semantic_type': 'date', 'confidence': 1.0, 'format': date_format}
                else:
                    info = {'semantic_type': 'unknown', 'confidence': 0.0,
                            'reason': 'Date parsing failed; original values retained', 'parse_failure_count': failed}
            else:
                parsed = pd.to_numeric(series, errors='coerce')
                failed = int((series.notna() & parsed.isna()).sum())
                if failed == 0:
                    # Nullable integer/float keeps actual missing values explicit.
                    finite = parsed.dropna()
                    is_integer = bool(len(finite) and finite.map(lambda value: float(value).is_integer()).all())
                    try:
                        result[name] = parsed.astype('Int64' if is_integer else 'Float64')
                        info = {'semantic_type': 'number', 'confidence': 1.0}
                    except (ValueError, TypeError, OverflowError):
                        info = {'semantic_type': 'unknown', 'confidence': 0.0,
                                'reason': 'Numeric range or representation cannot be preserved'}
                else:
                    info = {'semantic_type': 'text', 'confidence': 1.0,
                            'numeric_parse_failure_count': failed, 'reason': 'Original text retained'}
        inference[str(name)] = info
    return result, inference


def _header(values: list, names: list[str] | None) -> list[str]:
    columns = names if names is not None else [str(value) if value is not None else '' for value in values]
    if (not columns or len(columns) > MAX_COLUMNS or len(columns) != len(values)
            or any(not isinstance(name, str) or not name.strip() or len(name) > 128 for name in columns)
            or len(set(columns)) != len(columns)):
        raise DatasetError('INVALID_HEADER', 'Headers must be non-empty, unique and match the column count')
    return columns


def _validate_header_row(header_row: int, column_names: list[str] | None) -> None:
    if isinstance(header_row, bool) or not isinstance(header_row, int) or not 0 <= header_row <= 100:
        raise DatasetError('INVALID_ARGUMENT', 'header_row must be an integer from 0 to 100')
    if header_row == 0 and not column_names:
        raise DatasetError('INVALID_HEADER', 'Files without a header require explicit column_names')
    if column_names is not None and (not isinstance(column_names,list) or any(not isinstance(name,str) for name in column_names)):
        raise DatasetError('INVALID_HEADER', 'column_names must be a list of strings')


def _csv_table(file: Path, *, encoding: str, delimiter: str, header_row: int,
               column_names: list[str] | None, empty_fields_are_null: bool) -> LoadedTable:
    if encoding != 'auto' and encoding not in ENCODINGS:
        raise DatasetError('INVALID_ENCODING', 'Only UTF-8, UTF-8 BOM, GBK and GB18030 are supported')
    raw = file.read_bytes()
    if not raw or len(raw) > MAX_CSV_BYTES:
        raise DatasetError('EMPTY_DATA' if not raw else 'RESOURCE_LIMIT', 'CSV is empty or exceeds 20 MB')
    candidates = [encoding] if encoding != 'auto' else (
        ['utf-8-sig'] if raw.startswith(b'\xef\xbb\xbf') else ['utf-8', 'gb18030'])
    text = None
    for candidate in candidates:
        try:
            text = raw.decode(candidate, errors='strict')
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise DatasetError('ENCODING_ERROR', 'CSV cannot be strictly decoded with the selected encodings')
    if '\x00' in text:
        raise DatasetError('ENCODING_ERROR', 'CSV contains NUL bytes; encoding or file format is unsupported')
    if not text.strip('\ufeff\r\n\t '):
        raise DatasetError('EMPTY_DATA', 'CSV contains no data')
    if delimiter == 'auto':
        if file.suffix.lower() == '.tsv':
            delimiter = 'tab'
        else:
            try:
                detected = csv.Sniffer().sniff(text[:65536], delimiters=',\t;|').delimiter
                delimiter = next(name for name, char in DELIMITERS.items() if char == detected)
            except csv.Error as error:
                # A genuine one-column CSV has no separator. Irregular multi-column
                # input must not be silently reinterpreted as one column.
                if any(char in text for char in ',\t;|'):
                    raise DatasetError('DELIMITER_UNCERTAIN', 'Specify a delimiter for ambiguous CSV') from error
                delimiter = 'comma'
    if delimiter not in DELIMITERS:
        raise DatasetError('INVALID_ARGUMENT', 'Unsupported CSV delimiter')
    records: list[list[Any]] = []
    cells = 0
    try:
        for record in csv.reader(io.StringIO(text, newline=''), delimiter=DELIMITERS[delimiter], strict=True):
            if len(record) > MAX_COLUMNS or any(len(value) > 32767 for value in record):
                raise DatasetError('RESOURCE_LIMIT', 'CSV column or cell size limit exceeded')
            records.append(record)
            cells += len(record)
            if len(records) > MAX_ROWS + header_row or cells > MAX_CELLS + header_row*MAX_COLUMNS:
                raise DatasetError('RESOURCE_LIMIT', 'CSV row limit exceeded')
    except csv.Error as error:
        raise DatasetError('CSV_PARSE_ERROR', 'CSV quoting or field size is invalid') from error
    if len(records) <= header_row:
        raise DatasetError('EMPTY_DATA', 'CSV contains no data after the header')
    head = records[header_row - 1] if header_row else records[0]
    columns = _header(head, column_names)
    rows = records[header_row:]
    if any(len(row) != len(columns) for row in rows):
        raise DatasetError('IRREGULAR_FIELDS', 'CSV rows have inconsistent field counts; no rows were skipped')
    if len(rows) * len(columns) > MAX_CELLS:
        raise DatasetError('RESOURCE_LIMIT', 'CSV exceeds its cell limit')
    frame = pd.DataFrame(rows, columns=columns)
    if empty_fields_are_null:
        frame = frame.replace('', pd.NA)
    frame, inference = infer_types(frame)
    check_shape(frame)
    return LoadedTable(frame, {'encoding':encoding, 'encoding_detection':'strict_candidate' if len(candidates)>1 else 'explicit_or_bom',
                               'delimiter':delimiter,'header_row':header_row,'header_unit':'logical_csv_record',
                               'empty_fields_are_null':empty_fields_are_null, 'sheet':None}, inference)


def _xlsx_table(file: Path, *, sheet: str | None, header_row: int,
                column_names: list[str] | None, formula_policy: str) -> LoadedTable:
    if file.stat().st_size > MAX_XLSX_BYTES:
        raise DatasetError('RESOURCE_LIMIT', 'XLSX exceeds 10 MB')
    if formula_policy not in ('reject', 'formula_text'):
        raise DatasetError('FORMULA_POLICY_UNSUPPORTED', 'Only reject or formula_text is supported; formulas are not calculated')
    try:
        with zipfile.ZipFile(file) as archive:
            if len(archive.infolist()) > 1000 or sum(item.file_size for item in archive.infolist()) > 50_000_000:
                raise DatasetError('RESOURCE_LIMIT', 'Workbook decompression limit exceeded')
            for item in archive.infolist():
                if any(word in item.filename.lower() for word in ('vba', 'externallink', 'activex', 'embedding')):
                    raise DatasetError('UNSUPPORTED_WORKBOOK', 'Active content or external workbook links are unsupported')
            validate_xml_parts(archive)
        workbook = load_workbook(file, read_only=True, data_only=False, keep_links=False)
    except UnsafeWorkbookXML as error:
        raise DatasetError('UNSUPPORTED_WORKBOOK', str(error)) from error
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as error:
        raise DatasetError('UNSUPPORTED_WORKBOOK', 'XLSX cannot be safely opened') from error
    try:
        if len(workbook.worksheets) > 16:
            raise DatasetError('RESOURCE_LIMIT', 'Workbook has more than 16 worksheets')
        selected = sheet or next((item.title for item in workbook if item.sheet_state == 'visible'), None)
        if selected not in workbook.sheetnames:
            raise DatasetError('SHEET_NOT_FOUND', 'Requested or default visible worksheet does not exist')
        worksheet = workbook[selected]
        worksheet.reset_dimensions()  # Do not trust declared worksheet dimensions.
        records: list[list[Any]] = []
        cells = 0
        for row in worksheet.iter_rows():
            values = []
            for cell in row:
                if cell.data_type == 'f' and formula_policy == 'reject':
                    raise DatasetError('FORMULA_POLICY_REQUIRED', 'Workbook has formulas; choose formula_text explicitly')
                values.append(cell.value)
            while values and values[-1] is None:
                values.pop()
            if len(values) > MAX_COLUMNS or any(isinstance(value,str) and len(value)>32767 for value in values):
                raise DatasetError('RESOURCE_LIMIT', 'Worksheet column or cell size limit exceeded')
            records.append(values)
            cells += len(values)
            if len(records) > MAX_ROWS + header_row or cells > MAX_CELLS + header_row*MAX_COLUMNS:
                raise DatasetError('RESOURCE_LIMIT', 'Worksheet row limit exceeded')
        if len(records) <= header_row:
            raise DatasetError('EMPTY_SHEET', 'Selected worksheet contains no data')
        columns = _header(records[header_row - 1] if header_row else records[0], column_names)
        rows = records[header_row:]
        if any(len(row)>len(columns) for row in rows):
            raise DatasetError('IRREGULAR_FIELDS', 'Worksheet has values beyond the selected header')
        if len(rows)*len(columns)>MAX_CELLS:
            raise DatasetError('RESOURCE_LIMIT', 'Worksheet exceeds its cell limit')
        frame = pd.DataFrame([row+[None]*(len(columns)-len(row)) for row in rows], columns=columns)
        frame, inference = infer_types(frame)
        check_shape(frame)
        return LoadedTable(frame, {'sheet':selected,'available_sheets':workbook.sheetnames,'header_row':header_row,
                                   'formula_policy':formula_policy,'formula_calculation':False}, inference)
    finally:
        workbook.close()


def _read_table(file: Path, *, encoding: str = 'auto', delimiter: str = 'auto',
               sheet: str | None = None, header_row: int = 1,
               column_names: list[str] | None = None, empty_fields_are_null: bool = True,
               formula_policy: str = 'reject') -> LoadedTable:
    """Internal DataFrame boundary; public ingestion returns only bounded summaries."""
    _validate_header_row(header_row, column_names)
    if not isinstance(encoding,str) or not isinstance(delimiter,str) or not isinstance(empty_fields_are_null,bool):
        raise DatasetError('INVALID_ARGUMENT', 'Invalid CSV parsing option types')
    if sheet is not None and (not isinstance(sheet,str) or not sheet or len(sheet)>31):
        raise DatasetError('INVALID_ARGUMENT', 'sheet must be a worksheet name')
    if file.suffix.lower() in ('.csv', '.tsv'):
        if sheet is not None:
            raise DatasetError('INVALID_ARGUMENT', 'CSV has no worksheets')
        return _csv_table(file, encoding=encoding, delimiter=delimiter, header_row=header_row,
                          column_names=column_names,empty_fields_are_null=empty_fields_are_null)
    if file.suffix.lower() == '.xlsx':
        return _xlsx_table(file, sheet=sheet, header_row=header_row,column_names=column_names,formula_policy=formula_policy)
    raise DatasetError('UNSUPPORTED_FILE_TYPE', 'Only CSV, TSV and XLSX can be imported')


@io_boundary
def import_dataset(store: ProjectDatasetStore, source_path: str, *, dataset_name: str | None = None,
                   encoding: str = 'auto', delimiter: str = 'auto', sheet: str | None = None,
                   header_row: int = 1, column_names: list[str] | None = None,
                   expected_sha256: str | None = None, source_type: str = 'user_provided',
                   provenance_note: str = '', empty_fields_are_null: bool = True,
                   formula_policy: str = 'reject') -> dict:
    """Snapshot a registered-project source without modifying or exposing its DataFrame."""
    store.check_context()
    source = store.path(source_path)
    if not source.is_file():
        raise DatasetError('WRONG_PATH_TYPE', 'Import source must be a regular file')
    if source.suffix.lower() not in ('.csv', '.tsv', '.xlsx'):
        raise DatasetError('UNSUPPORTED_FILE_TYPE', 'Only CSV, TSV and XLSX can be imported')
    if source_type not in ('user_provided', 'unknown'):
        raise DatasetError('INVALID_ARGUMENT', 'User import cannot claim an AKShare source')
    if not isinstance(provenance_note,str) or len(provenance_note)>512:
        raise DatasetError('INVALID_ARGUMENT', 'Source note exceeds its limit')
    if expected_sha256 is not None and (not isinstance(expected_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',expected_sha256)):
        raise DatasetError('INVALID_ARGUMENT', 'expected_sha256 must be a SHA-256 hex digest')
    limit = MAX_XLSX_BYTES if source.suffix.lower()=='.xlsx' else MAX_CSV_BYTES
    if source.stat().st_size > limit:
        raise DatasetError('RESOURCE_LIMIT', 'Import source exceeds its size limit')
    before = file_sha256(source)
    if expected_sha256 is not None and expected_sha256 != before:
        raise DatasetError('STALE_SOURCE', 'Source file hash does not match the expected version')
    with store.transaction() as transaction:
        snapshot_name = 'source' + source.suffix.lower()
        snapshot = transaction.file(snapshot_name)
        with source.open('rb') as reader, snapshot.open('xb') as writer:
            size = 0
            for block in iter(lambda:reader.read(1024*1024),b''):
                size += len(block)
                if size > limit:
                    raise DatasetError('RESOURCE_LIMIT', 'Source grew beyond its size limit')
                writer.write(block)
            writer.flush(); os.fsync(writer.fileno())
        if file_sha256(snapshot)!=before or file_sha256(store.path(source_path))!=before:
            raise DatasetError('STALE_SOURCE', 'Source changed during import; no dataset was committed')
        # Preserve TSV default selection even though the immutable snapshot has a CSV suffix.
        effective_delimiter='tab' if source.suffix.lower()=='.tsv' and delimiter=='auto' else delimiter
        table = _read_table(snapshot,encoding=encoding,delimiter=effective_delimiter,sheet=sheet,header_row=header_row,
                           column_names=column_names,empty_fields_are_null=empty_fields_are_null,formula_policy=formula_policy)
        when = utc_now()
        provenance = {'source_type':source_type,'source_name':'unknown','source_function':None,
                      'request_parameters':{},'fetched_at':None,'imported_at':when,
                      'import_history':[{'operation':'import','at':when,'source_path':source_path,
                                         'source_sha256':before,'row_count':len(table.frame),
                                         'parser_options':table.parser_options}]}
        return store.commit(transaction, table.frame, dataset_name=source.stem if dataset_name is None else dataset_name,
                            original_name=snapshot_name,current_name=snapshot_name,provenance=provenance,
                            parser_options=table.parser_options,inference=table.inference,
                            extra={'source_note':{'kind':'user_declared','text':provenance_note}})


def write_canonical_csv(frame: pd.DataFrame, file: Path, *, encoding: str='utf-8-sig') -> dict:
    """Typed CSV sidecar preserves empty strings, nulls and mixed scalar types."""
    if encoding not in ('utf-8','utf-8-sig'):
        raise DatasetError('INVALID_ENCODING','Canonical CSV requires UTF-8 or UTF-8 BOM')
    if len(frame)>MAX_ROWS or len(frame.columns)>MAX_COLUMNS or frame.size>MAX_CELLS:
        raise DatasetError('RESOURCE_LIMIT','CSV serialization exceeds the dataset limits')
    token='__NOMISMA_NULL_'+uuid.uuid4().hex+'__'
    if any(isinstance(value,str) and value==token for column in frame for value in frame[column]):
        raise DatasetError('SERIALIZATION_CONFLICT','Null token collides with a data value')
    schema=[]
    for name in frame:
        dtype=str(frame[name].dtype)
        if not re.fullmatch(r'(object|str|string|bool|boolean|[Uu]?[Ii]nt(8|16|32|64)|[Ff]loat(32|64)|datetime64\[(s|ms|us|ns)\])',dtype):
            raise DatasetError('UNSUPPORTED_DTYPE','Canonical CSV does not support this dtype')
        schema.append({'name':str(name),'dtype':dtype,'object_cells':{}})
    with file.open('x',encoding=encoding,newline='') as stream:
        writer=csv.writer(stream,lineterminator='\n');writer.writerow(frame.columns)
        for row_number,row in enumerate(frame.itertuples(index=False,name=None)):
            encoded=[]
            for column,value in enumerate(row):
                kind='str'
                if value is None:kind='none';text=token
                elif value is pd.NA:kind='pandas_na';text=token
                elif value is pd.NaT:kind='nat';text=token
                elif isinstance(value,(float,)) and math.isnan(value):kind='nan';text=token
                elif isinstance(value,(datetime,date,pd.Timestamp)):
                    kind='datetime' if isinstance(value,(datetime,pd.Timestamp)) else 'date';text=value.isoformat()
                elif isinstance(value,(bool,)):kind='bool';text='true' if value else 'false'
                elif isinstance(value,(int,)):kind='int';text=str(int(value))
                elif isinstance(value,(float,)):kind='float';text=repr(float(value))
                elif isinstance(value,str):text=value
                elif hasattr(value,'item'):
                    scalar=value.item();kind='bool' if isinstance(scalar,bool) else 'int' if isinstance(scalar,int) else 'float'
                    text=repr(scalar)
                else:raise DatasetError('UNSUPPORTED_VALUE','CSV can serialize only scalar data')
                if schema[column]['dtype']=='object' and kind!='str':
                    schema[column]['object_cells'][str(row_number)]=kind
                encoded.append(text)
            writer.writerow(encoded)
        stream.flush();os.fsync(stream.fileno())
    if file.stat().st_size>MAX_CSV_BYTES:
        raise DatasetError('RESOURCE_LIMIT','Serialized CSV exceeds 20 MB')
    return {'canonical_csv_version':1,'encoding':encoding,'null_token':token,'columns':schema}


def read_canonical_csv(file: Path, options: dict) -> pd.DataFrame:
    if file.stat().st_size>MAX_CSV_BYTES:
        raise DatasetError('RESOURCE_LIMIT','Canonical CSV exceeds 20 MB')
    with file.open(encoding=options['encoding'],newline='') as stream:rows=list(csv.reader(stream,strict=True))
    columns=options['columns'];names=[column['name'] for column in columns]
    if not rows or rows[0]!=names or any(len(row)!=len(names) for row in rows[1:]):
        raise DatasetError('ROUNDTRIP_MISMATCH','Canonical CSV columns or row widths changed')
    values={name:[] for name in names}
    for index,row in enumerate(rows[1:]):
        for column,text in zip(columns,row):
            dtype=column['dtype'];kind=column.get('object_cells',{}).get(str(index),'str')
            if text==options['null_token']:
                value=pd.NA if kind=='pandas_na' else pd.NaT if kind=='nat' else float('nan') if kind=='nan' else None
            elif dtype.startswith(('Int','UInt','int','uint')) or kind=='int':value=int(text)
            elif dtype.startswith(('Float','float')) or kind=='float':value=float(text)
            elif dtype in ('bool','boolean') or kind=='bool':
                if text.lower() not in ('true','false'):raise DatasetError('ROUNDTRIP_MISMATCH','Invalid boolean value')
                value=text.lower()=='true'
            elif kind=='date':value=date.fromisoformat(text)
            elif kind=='datetime':value=datetime.fromisoformat(text)
            else:value=text
            values[column['name']].append(value)
    result=pd.DataFrame()
    for column in columns:
        dtype=column['dtype'];items=values[column['name']]
        if not re.fullmatch(r'(object|str|string|bool|boolean|[Uu]?[Ii]nt(8|16|32|64)|[Ff]loat(32|64)|datetime64\[(s|ms|us|ns)\])',dtype):
            raise DatasetError('UNSUPPORTED_DTYPE','Canonical dtype is not allowed')
        result[column['name']]=pd.Series(pd.to_datetime(items) if dtype.startswith('datetime64') else items,dtype=dtype)
    if len(result)>MAX_ROWS or result.size>MAX_CELLS:
        raise DatasetError('RESOURCE_LIMIT','Canonical CSV exceeds its shape limits')
    return result


@io_boundary
def load_dataset(store: ProjectDatasetStore, dataset_id: str, *, expected_sha256: str | None=None) -> tuple[pd.DataFrame,dict]:
    """Internal DataFrame loader shared by quality, cleaning and export."""
    metadata=store.metadata(dataset_id)
    if expected_sha256 is not None and expected_sha256!=metadata['sha256']:
        raise DatasetError('STALE_DATASET','Expected dataset version does not match')
    if metadata.get('schema_version')==2:raise DatasetError('DATASET_KIND','Disk-backed datasets require Explore streaming tools')
    file=store.path(metadata['current_file_path']);options=metadata['parser_options']
    if options.get('canonical_csv_version')==1:
        frame=read_canonical_csv(file,options)
    else:
        allowed={key:value for key,value in options.items() if key in {
            'encoding','delimiter','sheet','header_row','column_names','empty_fields_are_null','formula_policy'}}
        frame=_read_table(file,**allowed).frame
    if (file_sha256(file)!=metadata['sha256'] or len(frame)!=metadata['row_count']
            or list(frame.columns)!=[column['name'] for column in metadata['column_schema']]):
        raise DatasetError('STALE_DATASET','Loaded data no longer matches its recorded hash and structure')
    return frame,metadata
