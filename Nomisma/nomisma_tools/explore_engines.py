"""Bounded local SQL and columnar operations on immutable project datasets."""
import csv
import json
import math
import tempfile
import uuid
from pathlib import Path

from dataset_store import DatasetError, file_sha256
from explore_store import MAX_ROWS, connect, ingest, quote, read


def _disk_table(store, ref):
    _, manifest = read(store, ref)
    filename = next(item['path'] for item in manifest['files'] if item['path'].endswith('/data.sqlite'))
    return store.path(filename), manifest


def _batches(db):
    import pyarrow as pa
    types={'TEXT':pa.string(),'INTEGER':pa.int64(),'REAL':pa.float64()}
    fields=db.execute('PRAGMA table_info(data)').fetchall()
    try:schema=pa.schema([(row[1],types[row[2].upper()]) for row in fields])
    except KeyError:raise DatasetError('TYPE_MISMATCH','The stored SQLite fields need explicit scalar types') from None
    cursor=db.execute('SELECT * FROM data')
    while rows:=cursor.fetchmany(5000):
        try:
            yield pa.RecordBatch.from_arrays([
                pa.array([row[i] for row in rows],type=field.type) for i,field in enumerate(schema)
            ],schema=schema)
        except (pa.ArrowException, ValueError):
            raise DatasetError('TYPE_MISMATCH','Stored values do not match their declared types') from None


def _receipt(store,ref,p,execution_id,request_id,facts,columns,rows):
    prefix='datasets/exports/explore_'+uuid.uuid4().hex
    result={'execution_id':execution_id,'request_id':request_id,'dataset':ref,'parameters':p,
            'facts':facts,'source_manifest_sha256':ref['sha256']}
    with store.transaction() as tx:
        result_file=tx.file('result.json')
        result_file.write_text(json.dumps(result,allow_nan=False,ensure_ascii=False),encoding='utf8')
        table=tx.file('table.csv')
        with table.open('x',encoding='utf-8-sig',newline='') as out:
            writer=csv.writer(out);writer.writerow([_safe_cell(column) for column in columns])
            # Keep arbitrary labels inert when the result is opened in Excel.
            for index,row in enumerate(rows):
                writer.writerow([_safe_cell(value) for value in row])
                if index%32==0 and out.tell()>16_000_000:
                    raise DatasetError('RESOURCE_LIMIT','Query report exceeds 16 MB; select fewer or smaller fields')
            if out.tell()>16_000_000:
                raise DatasetError('RESOURCE_LIMIT','Query report exceeds 16 MB; select fewer or smaller fields')
        store.publish_artifacts(tx,{'table.csv':prefix+'.csv','result.json':prefix+'.json'})
    return ({'facts':facts,'dataset_id':ref['dataset_id'],'input_sha256':ref['sha256'],'result_path':prefix+'.json'},
            [{'path':prefix+ext,'sha256':file_sha256(store.path(prefix+ext))} for ext in ('.csv','.json')])


def _safe_cell(value):
    return "'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value


def _load_duckdb(store, ref, scratch):
    try:import duckdb
    except ImportError:raise DatasetError('ENGINE_UNAVAILABLE','Install the declared DuckDB runtime') from None
    file,manifest=_disk_table(store,ref)
    db=duckdb.connect(str(Path(scratch)/'query.duckdb'),config={
        'enable_external_access':'false','autoinstall_known_extensions':'false',
        'autoload_known_extensions':'false','allow_unsigned_extensions':'false','lock_configuration':'true',
        'threads':'1','memory_limit':'192MB','temp_directory':str(Path(scratch)/'spill'),
        'max_temp_directory_size':'256MB',
    })
    try:
        with connect(file) as source:
            for index,batch in enumerate(_batches(source)):
                db.register('_batch',batch)
                try:
                    db.execute('CREATE TABLE input AS SELECT * FROM _batch' if index==0 else 'INSERT INTO input SELECT * FROM _batch')
                finally:db.unregister('_batch')
        return db,manifest
    except BaseException:
        db.close()
        raise


def query(store,ref,p,execution_id,request_id):
    with store.transaction() as tx, tempfile.TemporaryDirectory(dir=tx.directory,prefix='query-') as scratch:
        db,_=_load_duckdb(store,ref,scratch)
        try:
            import duckdb
            statements=db.extract_statements(p['sql'])
            if len(statements)!=1 or statements[0].type!=duckdb.StatementType.SELECT:
                raise DatasetError('QUERY_DENIED','Use one SELECT against the registered input table')
            if db.get_table_names(p['sql'])!={'input'}:
                raise DatasetError('QUERY_DENIED','Only the registered input table is available')
            sql=statements[0].query.strip().rstrip(';')
            result=db.execute(f'SELECT * FROM ({sql}\n) AS result LIMIT ?', [p.get('max_rows',1000)+1])
            columns=[row[0] for row in result.description]
            if len(columns)>60 or len(set(columns))!=len(columns):
                raise DatasetError('RESOURCE_LIMIT','Query must return at most 60 uniquely named columns')
            rows=result.fetchall()
            if len(rows)>p.get('max_rows',1000):
                raise DatasetError('RESOURCE_LIMIT','Result exceeds requested rows. Aggregate or use a reviewed LIMIT')
            if any(not isinstance(cell,(str,int,float,bool,type(None))) and not hasattr(cell,'isoformat')
                   for row in rows for cell in row):
                raise DatasetError('TYPE_MISMATCH','Project SQL export requires scalar values')
            if any(isinstance(cell,str) and len(cell)>4096 or isinstance(cell,float) and not math.isfinite(cell)
                   for row in rows for cell in row):
                raise DatasetError('RESOURCE_LIMIT','Result contains an oversized or nonfinite scalar')
        except duckdb.Error:
            raise DatasetError('QUERY_FAILED','DuckDB rejected the query, input types or resource limits') from None
        finally:db.close()
    return _receipt(store,ref,p,execution_id,request_id,{'engine':'duckdb','row_count':len(rows),'column_count':len(columns)},columns,rows)


def quality(store,ref,p,execution_id,request_id):
    with store.transaction() as tx, tempfile.TemporaryDirectory(dir=tx.directory,prefix='quality-') as scratch:
        db,manifest=_load_duckdb(store,ref,scratch)
        try:
            names=manifest['columns']
            numeric=set(p.get('outlier_columns',[]))
            if not numeric<=set(names):raise DatasetError('INVALID_COLUMN','Outlier field is missing')
            blank=p.get('empty_strings_are_missing',True)
            counts=[];total=manifest['row_count']
            duplicate=total-db.execute('SELECT count(*) FROM (SELECT DISTINCT * FROM input)').fetchone()[0]
            for name,kind in zip(names,manifest['parser_dtypes']):
                q=quote(name)
                missing=f'{q} IS NULL'+(f" OR trim(CAST({q} AS VARCHAR)) = ''" if blank else '')
                n=db.execute(f'SELECT count(*) FROM input WHERE {missing}').fetchone()[0]
                outliers=None
                if name in numeric:
                    cast=f'TRY_CAST({q} AS DOUBLE)'
                    invalid=db.execute(f'SELECT count(*) FROM input WHERE NOT ({missing}) AND ({cast} IS NULL OR NOT isfinite({cast}))').fetchone()[0]
                    if invalid:raise DatasetError('TYPE_MISMATCH','Outlier fields require finite numeric values')
                    if total-n>=4:
                        low,high=db.execute(f'SELECT quantile_cont({cast}, 0.25),quantile_cont({cast}, 0.75) FROM input WHERE NOT ({missing})').fetchone()
                        spread=1.5*(high-low)
                        outliers=db.execute(f'SELECT count(*) FROM input WHERE NOT ({missing}) AND ({cast} < ? OR {cast} > ?)',[low-spread,high+spread]).fetchone()[0]
                counts.append((name,kind,n,outliers))
        finally:db.close()
    facts={'engine':'duckdb','row_count':total,'column_count':len(names),'duplicate_rows':duplicate,
           'missing_cells':sum(row[2] for row in counts),'missing_by_field':{row[0]:row[2] for row in counts[:15]},
           'remaining_fields_in_artifact':max(0,len(counts)-15),
           'outliers_by_field':{row[0]:row[3] for row in counts if row[0] in numeric},
           'outlier_rule':'Tukey 1.5 IQR, minimum four nonmissing values','scan':'full'}
    return _receipt(store,ref,p,execution_id,request_id,facts,['field','imported_type','missing','iqr_outliers'],counts)


def transform(store,ref,p,execution_id,request_id):
    try:import polars as pl
    except (ImportError,RuntimeError):
        raise DatasetError('ENGINE_UNAVAILABLE','Polars is missing or incompatible with the current CPU/runtime') from None
    import pyarrow.parquet as pq
    source,manifest=_disk_table(store,ref)
    column=p['column'];group=p.get('group_by')
    if column not in manifest['columns'] or group and group not in manifest['columns']:
        raise DatasetError('INVALID_COLUMN','Choose existing fields')
    method=p['method'];count=0;writer=None
    try:
        with store.transaction() as tx:
            staged=tx.file('columnar.parquet')
            with connect(source) as db:
                for batch in _batches(db):
                    frame=pl.from_arrow(batch)
                    if method=='trim':
                        frame=frame.with_columns(pl.col(column).cast(pl.String).str.strip_chars())
                    elif method=='date_iso':
                        frame=frame.with_columns(pl.col(column).cast(pl.String).str.strptime(pl.Date,format=p['date_format'],strict=True))
                    elif method=='group_sum':
                        value=pl.col(column).cast(pl.Float64,strict=True)
                        if frame.select((value.is_not_null() & ~value.is_finite()).any()).item():
                            raise DatasetError('NON_FINITE_INPUT','Aggregation needs finite numeric values')
                        frame=frame.select(group,value.alias(column))
                    else:raise DatasetError('METHOD_UNAVAILABLE','Unknown Polars operation')
                    arrow=frame.to_arrow()
                    if writer is None:writer=pq.ParquetWriter(staged,arrow.schema)
                    writer.write_table(arrow)
                    count+=len(frame)
            if writer:writer.close();writer=None
            if method=='group_sum':
                output=tx.file('groups.parquet')
                (pl.scan_parquet(staged).group_by(group).agg(
                    pl.when(pl.col(column).count()>0).then(pl.col(column).sum()).otherwise(None).alias(column)
                ).sort(group).sink_parquet(output,engine='streaming'))
            else:output=staged
            rows=pq.ParquetFile(output).metadata.num_rows
            if rows>MAX_ROWS:raise DatasetError('RESOURCE_LIMIT','Derived output exceeds the dataset profile')
            return {**ingest(store,{'source_path':output.relative_to(store.root).as_posix(),
                         'expected_sha256':file_sha256(output),'dataset_name':'Polars '+method},execution_id,request_id,
                         lineage={'parents':[ref],'parameters':p,'engine':'polars','input_rows':count,'output_rows':rows}),
                    'input_rows':count,'output_rows':rows,'engine':'polars'}
    except pl.exceptions.PolarsError:
        raise DatasetError('TYPE_MISMATCH','Polars could not apply the selected conversion or aggregation') from None
    finally:
        if writer:writer.close()


def export(store,ref,p,execution_id,request_id):
    import pyarrow.parquet as pq
    source,manifest=_disk_table(store,ref)
    fmt=p['format'];count=0;writer=None
    prefix='datasets/exports/explore_'+uuid.uuid4().hex
    with store.transaction() as tx:
        staged=tx.file('data.'+fmt)
        with connect(source) as db:
            if fmt=='csv':
                with staged.open('x',encoding='utf-8-sig',newline='') as stream:
                    out=csv.writer(stream);out.writerow([_safe_cell(name) for name in manifest['columns']])
                    for batch in _batches(db):
                        for row in zip(*(column.to_pylist() for column in batch.columns)):
                            out.writerow([_safe_cell(cell) for cell in row])
                        count+=len(batch)
                        if stream.tell()>128_000_000:raise DatasetError('RESOURCE_LIMIT','CSV export exceeds 128 MB')
            else:
                try:
                    for batch in _batches(db):
                        if writer is None:writer=pq.ParquetWriter(staged,batch.schema)
                        writer.write_batch(batch)
                        count+=len(batch)
                        if staged.stat().st_size>128_000_000:raise DatasetError('RESOURCE_LIMIT','Parquet export exceeds 128 MB')
                finally:
                    if writer:writer.close()
        if count!=manifest['row_count'] or staged.stat().st_size>128_000_000:
            raise DatasetError('RESULT_VALIDATION_FAILED','Export does not match dataset size or budget')
        facts={'row_count':count,'column_count':len(manifest['columns']),'format':fmt,
               'csv_formula_protection':fmt=='csv','csv_null_policy':'blank' if fmt=='csv' else None}
        descriptor={'execution_id':execution_id,'request_id':request_id,'dataset':ref,'parameters':p,
                    'facts':facts,'source_manifest_sha256':ref['sha256'],'output_sha256':file_sha256(staged)}
        tx.file('result.json').write_text(json.dumps(descriptor,allow_nan=False),encoding='utf8')
        dest=prefix+'.'+fmt
        store.publish_artifacts(tx,{'data.'+fmt:dest,'result.json':prefix+'.json'})
    return ({'facts':facts,'dataset_id':ref['dataset_id'],'input_sha256':ref['sha256'],
             'result_path':prefix+'.json','output_file':dest},
            [{'path':path,'sha256':file_sha256(store.path(path))} for path in (dest,prefix+'.json')])
