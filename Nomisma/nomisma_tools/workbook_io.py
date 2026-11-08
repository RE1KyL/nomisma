"""Fixed workbook file service, invoked only by trusted desktop IPC (not an Agent tool)."""
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import zipfile
from xml.etree import ElementTree
from openpyxl import load_workbook
from xlsx_safety import validate_xml_parts


def project_file(root, relative, write=False):
    root = Path(root).absolute()
    rel = Path(relative)
    if not relative or rel.is_absolute() or '..' in rel.parts or ':' in relative or '\\' in relative:
        raise ValueError('Invalid project-relative path')
    target = root / rel
    for part in [root, *[root.joinpath(*rel.parts[:i]) for i in range(1, len(rel.parts)+1)]]:
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Links and junctions are not allowed')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Outside project')
    if write and tuple(p.lower() for p in rel.parts[:2]) == ('datasets', 'raw'):
        raise ValueError('Raw datasets are read-only')
    return target


def version(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def validate(file):
    if file.suffix.lower() != '.xlsx' or file.stat().st_size > 10_000_000:
        raise ValueError('Only xlsx up to 10 MB is supported')
    with zipfile.ZipFile(file) as archive:
        if len(archive.infolist()) > 1000 or sum(x.file_size for x in archive.infolist()) > 50_000_000:
            raise ValueError('Workbook exceeds decompression limits')
        forbidden = ('vba', 'pivot', 'drawing', 'chart', 'externalLink', 'slicer', 'embedding', 'activeX', 'customXml', 'tables/', 'comments', 'threadedComment', 'person', 'connections', 'queryTable')
        if any(any(word.lower() in item.filename.lower() for word in forbidden) for item in archive.infolist()):
            raise ValueError('Workbook contains unsupported features; editing refused to prevent data loss')
        validate_xml_parts(archive)
        # openpyxl may silently drop extension lists and rich-text runs. Refuse
        # these workbooks instead of promising a lossless round trip.
        for item in archive.infolist():
            if item.filename.endswith('.xml'):
                tree = ElementTree.fromstring(archive.read(item))
                if any(node.tag.rsplit('}', 1)[-1] in {'extLst', 'r'} for node in tree.iter()):
                    raise ValueError('Workbook has unsupported extensions or rich text; editing refused')


def handle(request):
    file = project_file(request['root'], request['path'], request['action'] == 'save')
    validate(file)
    before = version(file)
    wb = load_workbook(file, data_only=False)
    try:
        if any(s.max_row > 2000 or s.max_column > 100 for s in wb) or sum(s.max_row*s.max_column for s in wb)>20000:
            raise ValueError('Prototype limit: 2000 rows, 100 columns per sheet, 20000 cells per workbook')
        if any(s.protection.sheet for s in wb) or wb.security.lockStructure:
            raise ValueError('Protected workbooks are not editable in this prototype')
        if request['action'] == 'save':
            if request.get('version') != before:
                raise ValueError('File changed on disk; reopen before saving')
            changes = request.get('changes', [])
            if not isinstance(changes, list) or len(changes) > 10000:
                raise ValueError('Invalid cell patch')
            for change in changes:
                if change['sheet'] not in wb.sheetnames or not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,3}', change['cell']):
                    raise ValueError('Invalid cell address')
                cell = wb[change['sheet']][change['cell']]
                if cell.row > 2000 or cell.column > 100:
                    raise ValueError('Cell out of range')
                value = change['value']
                if not isinstance(value, (str, int, float, bool, type(None))):
                    raise ValueError('Invalid cell value')
                if isinstance(value, str) and len(value) > 32767:
                    raise ValueError('Cell text exceeds the Excel limit')
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError('Cell numbers must be finite')
                if change.get('kind') == 'date' and value:
                    value = dt.datetime.fromisoformat(value)
                cell.value = value
            fd, temp = tempfile.mkstemp(prefix='.nomisma-', suffix='.xlsx', dir=file.parent)
            os.close(fd)
            try:
                wb.save(temp)
                if version(file) != before:
                    raise ValueError('File changed during save')
                os.replace(temp, file)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        elif request['action'] != 'open':
            raise ValueError('Unsupported operation')
        sheets = []
        for ws in wb:
            rows = []
            for row in ws.iter_rows():
                values = []
                for cell in row:
                    value = cell.value
                    kind = 'date' if isinstance(value, (dt.date, dt.datetime)) else 'formula' if cell.data_type == 'f' else 'number' if isinstance(value, (int, float)) and not isinstance(value, bool) else 'boolean' if isinstance(value, bool) else 'text'
                    values.append({'cell': cell.coordinate, 'value': value.isoformat() if kind == 'date' else value,
                                   'kind': kind, 'format': cell.number_format})
                rows.append(values)
            sheets.append({'name': ws.title, 'rows': rows})
        return {'version': version(file), 'sheets': sheets,
                'warnings': ['Formula expressions are preserved; formulas are not calculated. Cached formula results may be cleared on save.']}
    finally:
        wb.close()


if __name__ == '__main__':
    try:
        request = json.loads(sys.stdin.read(2_000_000))
        print(json.dumps({'ok': True, 'data': handle(request)}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        raise SystemExit(1)
