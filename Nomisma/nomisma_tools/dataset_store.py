"""Internal dataset persistence. Deliberately not imported or registered by MCP."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from functools import wraps
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import stat
from typing import Any, Callable, Iterator, ParamSpec, TypeVar
import uuid

import pandas as pd

from project_context import project_context
from workbook_io import project_file

MAX_ROWS = 100_000
MAX_COLUMNS = 100
MAX_CELLS = 2_000_000
MAX_SUMMARY_BYTES = 16_384
DATASET_ID = re.compile(r"ds_[a-f0-9]{32}\Z")
P = ParamSpec('P')
T = TypeVar('T')


class DatasetError(Exception):
    """Stable public code; messages do not interpolate credentials or raw errors."""
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def io_boundary(function: Callable[P, T]) -> Callable[P, T]:
    """Keep expected filesystem failures identifiable at the internal public API."""
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return function(*args, **kwargs)
        except DatasetError:
            raise
        except FileNotFoundError as error:
            raise DatasetError('FILE_NOT_FOUND', 'A required project file is missing') from error
        except PermissionError as error:
            raise DatasetError('PERMISSION_DENIED', 'Project file access was denied') from error
        except OSError as error:
            raise DatasetError('FILE_IO_ERROR', 'A project file operation failed') from error
    return wrapped


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def packages() -> dict[str, str]:
    import platform
    return {'python': platform.python_version(), **{
        name: importlib.metadata.version(name)
        for name in ('pandas', 'numpy', 'openpyxl', 'akshare')
    }}


def _plain(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, int) and abs(value) > 2**53:
        return str(value)
    if isinstance(value, str):
        return value[:128]
    return value


def frame_schema(frame: pd.DataFrame, inference: dict[str, dict] | None = None) -> list[dict]:
    return [{
        'column_id': f'c{index + 1:04}', 'name': str(name),
        'pandas_dtype': str(frame[name].dtype),
        'missing_count': int(frame[name].isna().sum()),
        **(inference or {}).get(str(name), {'semantic_type': 'unknown', 'confidence': 0.0}),
    } for index, name in enumerate(frame.columns)]


def check_shape(frame: pd.DataFrame) -> None:
    if frame.empty:
        raise DatasetError('EMPTY_DATA', 'No data rows were returned')
    if len(frame) > MAX_ROWS or len(frame.columns) > MAX_COLUMNS or frame.size > MAX_CELLS:
        raise DatasetError('RESOURCE_LIMIT', 'Dataset exceeds the row, column or cell limit')


def _reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


class DatasetTransaction:
    def __init__(self, store: ProjectDatasetStore, directory: Path, dataset_id: str) -> None:
        self.store, self.directory, self.dataset_id = store, directory, dataset_id
        self.names: set[str] = set()
        self.published: list[tuple[Path, os.stat_result]] = []
        self.final_directory: Path | None = None
        self.committed = False

    def file(self, name: str) -> Path:
        extensions='csv|tsv|xlsx|json'
        if self.store.context['mode']=='big_data_explore':extensions+='|parquet|jsonl|txt|sqlite|db'
        if not re.fullmatch(r'[a-z][a-z0-9_-]*\.('+extensions+r')', name):
            raise DatasetError('INVALID_PATH', 'Internal staging filename is invalid')
        self.names.add(name)
        return self.directory / name

    def cleanup(self) -> None:
        # Only files created by this exact transaction; never recursive-delete a project.
        if not self.committed:
            for file, identity in reversed(self.published):
                self.store.path(file.relative_to(self.store.root).as_posix())
                info = file.stat()
                if (info.st_dev, info.st_ino) != (identity.st_dev, identity.st_ino):
                    raise DatasetError('CLEANUP_CONFLICT', 'A published path changed identity')
                file.unlink()
            if self.final_directory is not None:
                self.final_directory.rmdir()  # Refuses to remove unexpected contents.
        for name in self.names:
            file = self.directory / name
            if file.exists():
                self.store.path(file.relative_to(self.store.root).as_posix())
                file.unlink()
        self.directory.rmdir()


class ProjectDatasetStore:
    """Project authority comes only from the existing registered environment."""
    def __init__(self) -> None:
        try:
            state = Path(os.environ.get('NOMISMA_STATE_ROOT', ''))
            if not state.is_absolute():
                raise ValueError('Missing independent state')
            for suffix in ('', 'desktop-dev', 'desktop-dev/openacpui-data', 'desktop-dev/openacpui-data/projects.json'):
                if _reparse(state / suffix):
                    raise ValueError('Redirected registry')
            binding = project_context()
            if binding is None:
                raise ValueError('Missing context')
            self.context, self.root = binding
            self.root = self.root.absolute()
            if _reparse(self.root):
                raise ValueError('Redirected root')
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise DatasetError('PROJECT_CONTEXT_INVALID', 'A registered active project context is required') from error

    def check_context(self) -> None:
        try:
            current = project_context()
            if current is None or current[0] != self.context or current[1] != self.root:
                raise ValueError('Changed context')
        except (ValueError, OSError, KeyError) as error:
            raise DatasetError('PROJECT_CONTEXT_INVALID', 'Project context changed or is no longer active') from error

    def path(self, relative: str, *, allow_missing: bool = False) -> Path:
        if (not isinstance(relative, str) or not relative or '\x00' in relative
                or '\\' in relative or ':' in relative or PureWindowsPath(relative).is_absolute()
                or Path(relative).is_absolute() or any(part in ('', '.', '..') for part in relative.split('/'))):
            raise DatasetError('INVALID_PATH', 'Only normalized project-relative paths are accepted')
        target = self.root / relative
        cursor = self.root
        try:
            for part in ('', *relative.split('/')):
                cursor = cursor / part
                try:
                    if _reparse(cursor):
                        raise DatasetError('LINK_NOT_ALLOWED', 'Links and Windows reparse points are not allowed')
                except FileNotFoundError as error:
                    if not allow_missing:
                        raise DatasetError('FILE_NOT_FOUND', 'Project file was not found') from error
            if not target.resolve().is_relative_to(self.root.resolve()):
                raise DatasetError('PATH_OUTSIDE_PROJECT', 'Path is outside the registered project')
            if not allow_missing:
                # Reuse the editor's existing source-path checks without its editing handler.
                project_file(self.root, relative)
        except PermissionError as error:
            raise DatasetError('PERMISSION_DENIED', 'Project path is not readable') from error
        return target

    def directory(self, relative: str) -> Path:
        self.check_context()
        self.path(relative, allow_missing=True)
        cursor = self.root
        for part in relative.split('/'):
            cursor /= part
            try:
                cursor.mkdir()
            except FileExistsError:
                pass
            if _reparse(cursor) or not cursor.is_dir():
                raise DatasetError('LINK_NOT_ALLOWED', 'Output ancestor is not a regular directory')
        return cursor

    @contextmanager
    def transaction(self) -> Iterator[DatasetTransaction]:
        self.check_context()
        stage = self.directory('datasets/.staging')
        dataset_id = 'ds_' + uuid.uuid4().hex
        directory = stage / dataset_id
        directory.mkdir()  # Exclusive; never reuses a previous transaction.
        transaction = DatasetTransaction(self, directory, dataset_id)
        try:
            yield transaction
        finally:
            transaction.cleanup()

    @staticmethod
    def _publish_file(source: Path, destination: Path) -> None:
        # Windows rename refuses an existing destination atomically. On POSIX,
        # use exclusive hard-link creation instead of its overwriting rename.
        # Both publish complete, fsynced bytes; no exists()+os.replace race.
        if os.name == 'nt':
            os.rename(source, destination)
        else:
            os.link(source, destination)

    def commit(self, transaction: DatasetTransaction, frame: pd.DataFrame, *,
               dataset_name: str, original_name: str, current_name: str,
               provenance: dict, parser_options: dict,
               inference: dict[str, dict] | None = None, extra: dict | None = None,
               storage_area: str = 'raw', parent_metadata: dict | None = None,
               additional_files: list[str] | None = None) -> dict:
        self.check_context()
        if (transaction.store is not self or transaction.committed
                or not DATASET_ID.fullmatch(transaction.dataset_id)
                or self.path(f'datasets/.staging/{transaction.dataset_id}') != transaction.directory):
            raise DatasetError('STAGING_INVALID', 'Transaction does not belong to this project store')
        if storage_area not in ('raw','processed'):
            raise DatasetError('INVALID_ARGUMENT','Unknown dataset storage area')
        if storage_area=='raw':
            check_shape(frame)
        elif (len(frame.columns)==0 or len(frame)>MAX_ROWS or len(frame.columns)>MAX_COLUMNS or frame.size>MAX_CELLS):
            raise DatasetError('RESOURCE_LIMIT','Processed dataset exceeds its shape limit')
        if storage_area=='processed':
            if not parent_metadata:
                raise DatasetError('INVALID_ARGUMENT','Processed data requires its parent dataset')
            actual_parent=self.metadata(parent_metadata['dataset_id'])
            if actual_parent!=parent_metadata:
                raise DatasetError('STALE_DATASET','Parent metadata changed during processing')
        if not isinstance(dataset_name, str) or not dataset_name.strip() or len(dataset_name) > 120:
            raise DatasetError('INVALID_ARGUMENT', 'Dataset name must contain 1 to 120 characters')
        if provenance.get('source_type') not in ('akshare', 'user_provided', 'unknown','official_economic'):
            raise DatasetError('INVALID_ARGUMENT', 'Unsupported source type')
        source_keys = {'source_type', 'source_name', 'source_function', 'request_parameters',
                       'fetched_at', 'imported_at', 'import_history'}
        if set(provenance) != source_keys:
            raise DatasetError('INVALID_ARGUMENT', 'Provenance must contain exactly the required source fields')
        if set(extra or {}) & {'dataset_id', 'dataset_name', 'project_id', 'mode', 'session_id',
                             'sha256', 'original_sha256', 'original_file_path', 'current_file_path',
                             'row_count', 'column_count', 'column_schema', *source_keys}:
            raise DatasetError('INVALID_ARGUMENT', 'Supplemental metadata cannot replace dataset identity')
        file_names=set([current_name,*(additional_files or []),*([original_name] if storage_area=='raw' else [])])
        for name in file_names:
            if name not in transaction.names or not transaction.file(name).is_file():
                raise DatasetError('STAGING_INVALID', 'Dataset staging file is missing')
        dataset_id = transaction.dataset_id
        original = transaction.file(original_name) if storage_area=='raw' else self.path(parent_metadata['original_file_path'])
        current = transaction.file(current_name)
        schema = frame_schema(frame, inference)
        date_columns = [column['name'] for column in schema if column.get('semantic_type') in ('date', 'datetime')]
        start = end = None
        if len(date_columns) == 1:
            dates = pd.to_datetime(frame[date_columns[0]], errors='coerce').dropna()
            if len(dates):
                start, end = dates.min().date().isoformat(), dates.max().date().isoformat()
        prefix = f'datasets/{storage_area}/{dataset_id}'
        metadata = {
            'schema_version': 1, 'dataset_id': dataset_id, 'dataset_name': dataset_name.strip(),
            **self.context, 'created_at': utc_now(), **provenance,
            'data_start': start, 'data_end': end,
            'date_column': date_columns[0] if len(date_columns)==1 else None,
            'date_range_status': 'identified' if len(date_columns)==1 else 'ambiguous' if date_columns else 'not_available',
            'row_count': len(frame), 'column_count': len(frame.columns), 'column_schema': schema,
            'package_version': packages(), 'file_type': current.suffix.lower().lstrip('.'),
            'sheet_name': parser_options.get('sheet'), 'parser_options': parser_options,
            'original_file_path': f'{prefix}/{original_name}' if storage_area=='raw' else parent_metadata['original_file_path'],
            'current_file_path': f'{prefix}/{current_name}',
            'original_sha256': file_sha256(original), 'sha256': file_sha256(current),
        }
        if storage_area=='processed':
            metadata.update(storage_area='processed',parent_dataset_id=parent_metadata['dataset_id'],
                            root_dataset_id=parent_metadata.get('root_dataset_id',parent_metadata['dataset_id']),
                            parent_sha256=parent_metadata['sha256'])
        if set(extra or {}) & set(metadata):
            raise DatasetError('INVALID_ARGUMENT', 'Supplemental metadata cannot replace canonical fields')
        metadata.update(extra or {})
        metadata['import_history'] = [*metadata['import_history'], {
            'operation':'publish_snapshot','at':utc_now(),
            'original_file_path':metadata['original_file_path'],'original_sha256':metadata['original_sha256'],
            'current_file_path':metadata['current_file_path'],'sha256':metadata['sha256'],
            'row_count':metadata['row_count'],'column_count':metadata['column_count'],
        }]
        meta_file = transaction.file('metadata.json')
        if len(json.dumps(metadata,ensure_ascii=False,allow_nan=False).encode('utf8'))>1_000_000:
            raise DatasetError('RESOURCE_LIMIT','Dataset metadata exceeds 1 MB')
        with meta_file.open('x', encoding='utf8') as stream:
            json.dump(metadata, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        raw_parent = self.directory(f'datasets/{storage_area}')
        metadata_parent = self.directory('datasets/metadata')
        final = raw_parent / dataset_id
        try:
            final.mkdir()  # Exclusive ownership; no existing folder or file is overwritten.
            transaction.final_directory = final
            for name in sorted(file_names):
                source = transaction.file(name)
                with source.open('r+b') as stream:
                    os.fsync(stream.fileno())
                destination = final / name
                self._publish_file(source, destination)
                transaction.published.append((destination, destination.stat()))
            self.check_context()
            meta_destination = metadata_parent / f'{dataset_id}.json'
            self._publish_file(meta_file, meta_destination)  # Commit marker is last.
            transaction.published.append((meta_destination, meta_destination.stat()))
            transaction.committed = True
        except FileExistsError as error:
            raise DatasetError('FILE_EXISTS', 'Dataset destination already exists; nothing was overwritten') from error
        except PermissionError as error:
            raise DatasetError('PERMISSION_DENIED', 'Cannot publish dataset files') from error
        except OSError as error:
            raise DatasetError('WRITE_FAILED', 'Atomic dataset publication failed') from error
        return self.summary(metadata, frame)

    def metadata(self, dataset_id: str) -> dict:
        self.check_context()
        if not isinstance(dataset_id, str) or not DATASET_ID.fullmatch(dataset_id):
            raise DatasetError('INVALID_ARGUMENT', 'Invalid Dataset ID')
        file = self.path(f'datasets/metadata/{dataset_id}.json')
        if file.stat().st_size > 1_000_000:
            raise DatasetError('RESOURCE_LIMIT', 'Dataset metadata exceeds its limit')
        try:
            metadata = json.loads(file.read_text(encoding='utf8'))
            if metadata['dataset_id'] != dataset_id or metadata['project_id'] != self.context['project_id'] or metadata['mode'] != self.context['mode']:
                raise ValueError('Ownership mismatch')
            area=metadata.get('storage_area','raw')
            if area not in ('raw','processed'):
                raise ValueError('Unknown dataset storage')
            root_id=metadata.get('root_dataset_id',dataset_id)
            if not DATASET_ID.fullmatch(root_id):
                raise ValueError('Invalid root dataset identity')
            for field, digest in [('original_file_path', 'original_sha256'), ('current_file_path', 'sha256')]:
                relative = metadata[field]
                prefix=f'datasets/raw/{root_id}/' if field=='original_file_path' else f'datasets/{area}/{dataset_id}/'
                if not relative.startswith(prefix):
                    raise ValueError('Dataset path mismatch')
                if file_sha256(self.path(relative)) != metadata[digest]:
                    raise DatasetError('STALE_DATASET', 'Dataset bytes no longer match their metadata')
            return metadata
        except (ValueError, KeyError, TypeError) as error:
            raise DatasetError('METADATA_INVALID', 'Dataset metadata is invalid') from error

    def publish_artifacts(self, transaction: DatasetTransaction, files: dict[str,str]) -> None:
        """Publish complete artifacts, with the last mapping entry as commit marker."""
        self.check_context()
        if (transaction.store is not self or transaction.committed
                or not DATASET_ID.fullmatch(transaction.dataset_id)
                or self.path(f'datasets/.staging/{transaction.dataset_id}')!=transaction.directory):
            raise DatasetError('STAGING_INVALID','Artifact transaction is not owned by this store')
        if not files or len(set(files.values()))!=len(files):
            raise DatasetError('INVALID_ARGUMENT','Artifact destinations must be non-empty and unique')
        destinations=[]
        for name,relative in files.items():
            if not relative.startswith(('datasets/exports/','datasets/metadata/quality/','datasets/metadata/artifacts/')):
                raise DatasetError('INVALID_PATH','Artifact destination is outside the allowed namespaces')
            self.path(relative,allow_missing=True)
            if name not in transaction.names or not transaction.file(name).is_file():
                raise DatasetError('STAGING_INVALID','Artifact staging file is missing')
            destinations.append((transaction.file(name),relative))
        try:
            for source,relative in destinations:
                self.check_context()
                self.directory(str(Path(relative).parent).replace('\\','/'))
                destination=self.path(relative,allow_missing=True)
                with source.open('r+b') as stream:os.fsync(stream.fileno())
                self._publish_file(source,destination)
                transaction.published.append((destination,destination.stat()))
            transaction.committed=True
        except FileExistsError as error:
            raise DatasetError('FILE_EXISTS','Artifact destination exists; overwrite is not supported') from error
        except PermissionError as error:
            raise DatasetError('PERMISSION_DENIED','Artifact publication was denied') from error

    def summary(self, metadata: dict, frame: pd.DataFrame) -> dict:
        result = {key: metadata[key] for key in (
            'dataset_id', 'dataset_name', 'row_count', 'column_count', 'file_type',
            'source_type', 'source_name', 'source_function', 'sheet_name',
            'original_file_path', 'current_file_path', 'sha256', 'original_sha256',
        )}
        result.update(project_context=self.context,
                      metadata_path=f"datasets/metadata/{metadata['dataset_id']}.json",
                      fields=metadata['column_schema'],
                      sample=[{str(k): _plain(v) for k, v in row.items()}
                              for row in frame.iloc[:5, :12].to_dict(orient='records')],
                      fields_omitted=0, sample_rows_omitted=max(0, len(frame) - 5))
        while len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf8')) > MAX_SUMMARY_BYTES:
            if result['sample']:
                result['sample'].pop()
                result['sample_rows_omitted'] += 1
            elif result['fields']:
                result['fields'] = result['fields'][:-1]
                result['fields_omitted'] += 1
            else:
                raise DatasetError('RESOURCE_LIMIT', 'Dataset summary cannot fit its budget')
        return result
