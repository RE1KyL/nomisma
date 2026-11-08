"""Version-two project datasets: immutable manifest + disk table, no arbitrary SQL API."""
import csv,json,math,os,re,shutil,sqlite3,uuid
from contextlib import contextmanager,closing
from pathlib import Path
from dataset_store import DatasetError,file_sha256,utc_now
from xlsx_safety import UnsafeWorkbookXML, validate_xml_parts

MAX_SOURCE=128_000_000
MAX_DISK=256_000_000
MAX_ROWS=1_000_000

def fail(code,message):raise DatasetError(code,message)
def quote(name):return '"'+name.replace('"','""')+'"'
@contextmanager
def connect(file):
    db=sqlite3.connect(file.as_uri()+'?mode=ro',uri=True)
    try:yield db
    finally:db.close()
def read(store,ref):
    m=store.metadata(ref['dataset_id'])
    if m.get('schema_version')!=2:fail('DATASET_KIND','Explore requires a version-two dataset')
    if m['sha256']!=ref['sha256']:fail('STALE_DATASET','Dataset version changed')
    manifest=json.loads(store.path(m['current_file_path']).read_text('utf8'))
    if manifest.get('dataset_id')!=m['dataset_id'] or any(manifest.get('project_context',{}).get(k)!=store.context[k] for k in ('project_id','mode')):fail('METADATA_INVALID','Manifest ownership differs')
    for f in manifest['files']:
        if not f['path'].startswith('datasets/raw/'+m['dataset_id']+'/') or file_sha256(store.path(f['path']))!=f['sha256']:fail('STALE_DATASET','Dataset object changed')
    return m,manifest

def batches(file,p):
    """Yield bounded dataframes; parsing policy is explicit and recorded."""
    import pandas as pd
    ext=file.suffix.lower()
    with file.open('rb') as stream:head=stream.read(32)
    if ext=='.parquet':
        if head[:4]!=b'PAR1':fail('FORMAT_MISMATCH','Not a Parquet file')
        import pyarrow.parquet as pq
        for batch in pq.ParquetFile(file).iter_batches(batch_size=5000):yield batch.to_pandas()
    elif ext in ('.csv','.tsv'):
        if b'\x00' in head or head.startswith((b'PK',b'PAR1',b'{',b'[')):fail('FORMAT_MISMATCH','CSV content differs from declared format')
        yield from pd.read_csv(file,encoding='utf-8-sig',sep='\t' if ext=='.tsv' else ',',dtype='string',keep_default_na=False,skip_blank_lines=False,chunksize=5000)
    elif ext=='.jsonl':
        rows=[]
        with file.open(encoding='utf8') as stream:
            for line in stream:
                if len(line.encode('utf8'))>1_000_000:fail('RESOURCE_LIMIT','JSON record exceeds budget')
                value=json.loads(line)
                if not isinstance(value,dict) or any(isinstance(v,(dict,list)) for v in value.values()):fail('NESTED_STRUCTURE','Explicit upstream flattening required')
                rows.append(value)
                if len(rows)==5000:yield pd.DataFrame(rows);rows=[]
        if rows:yield pd.DataFrame(rows)
    elif ext=='.json':
        if file.stat().st_size>8_000_000:fail('RESOURCE_LIMIT','Large JSON arrays need explicit JSONL conversion')
        rows=json.loads(file.read_text('utf8'))
        if not isinstance(rows,list) or not all(isinstance(r,dict) and all(not isinstance(v,(dict,list)) for v in r.values()) for r in rows):fail('NESTED_STRUCTURE','Expected a flat record array')
        for i in range(0,len(rows),5000):yield pd.DataFrame(rows[i:i+5000])
    elif ext=='.txt':
        if p.get('text_records')!='lines':fail('DOCUMENT_BOUNDARY','Declare text_records=lines')
        with file.open(encoding='utf8') as stream:
            rows=[]
            for i,line in enumerate(stream):
                if len(line)>250000:fail('RESOURCE_LIMIT','Document exceeds budget')
                rows.append({'document_id':str(i+1),'text':line.rstrip('\r\n')})
                if len(rows)==5000:yield pd.DataFrame(rows);rows=[]
            if rows:yield pd.DataFrame(rows)
    elif ext=='.xlsx':
        from openpyxl import load_workbook
        import zipfile
        with zipfile.ZipFile(file) as z:
            if sum(x.file_size for x in z.infolist())>80_000_000 or len(z.infolist())>2000:fail('RESOURCE_LIMIT','Workbook expansion exceeds budget')
            try:
                validate_xml_parts(z)
            except UnsafeWorkbookXML as error:
                raise DatasetError('UNSUPPORTED_WORKBOOK', str(error)) from error
        wb=load_workbook(file,read_only=True,data_only=False,keep_links=False)
        try:
            s=wb[p['sheet']] if p.get('sheet') else wb.active;it=s.iter_rows(values_only=True);cols=list(next(it));rows=[]
            for row in it:
                if any(isinstance(v,str) and v.startswith('=') for v in row):fail('FORMULA_POLICY','Formula-bearing import requires an explicit upstream values snapshot')
                rows.append(row)
                if len(rows)==5000:yield pd.DataFrame(rows,columns=cols);rows=[]
            if rows:yield pd.DataFrame(rows,columns=cols)
        finally:wb.close()
    elif ext in ('.sqlite','.db'):
        if not head.startswith(b'SQLite format 3\x00'):fail('FORMAT_MISMATCH','Not a SQLite database')
        table=p.get('table')
        with connect(file) as db:
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if table not in names:fail('TABLE_REQUIRED','Choose an existing source table')
            yield from pd.read_sql_query('SELECT * FROM '+quote(table),db,chunksize=5000)
    else:fail('UNSUPPORTED_FILE_TYPE','Unsupported Explore source')

def ingest(store,p,execution_id,request_id,probe=False,lineage=None):
    import pandas as pd
    source=store.path(p['source_path'])
    if source.suffix.lower() in ('.sqlite','.db') and source.with_name(source.name+'-wal').exists():fail('WAL_SNAPSHOT_REQUIRED','Export a consistent closed SQLite snapshot; live WAL files are not imported')
    if source.stat().st_size>MAX_SOURCE:fail('RESOURCE_LIMIT','Source exceeds 128 MB profile')
    before=file_sha256(source)
    if not probe and 'expected_sha256' not in p:fail('STALE_SOURCE','Import requires a bound source version')
    if before!=p.get('expected_sha256',before):fail('STALE_SOURCE','Source changed')
    if probe:
        first=next(batches(source,p),None)
        if first is None:fail('EMPTY_DATA','No source records')
        return {'source_sha256':before,'bytes':source.stat().st_size,'fields':list(first.columns),'sampled_rows':len(first),'exact_row_count':False,'format':source.suffix.lower(),'sample_decoded_bytes':int(first.memory_usage(deep=True).sum()),'planned_strategy':'bounded_batches_to_disk_sqlite','resource_profile':{'max_source_bytes':MAX_SOURCE,'max_disk_table_bytes':MAX_DISK,'max_rows':MAX_ROWS,'batch_rows':5000,'batch_decoded_bytes':32_000_000,'worker_memory_bytes':1024**3},'limitations':['Initial batch only; field types and scale are not a full-data quality assessment.']}
    with store.transaction() as t:
        copied=t.file('source'+source.suffix.lower());shutil.copyfile(source,copied)
        if file_sha256(copied)!=before:fail('STALE_SOURCE','Source changed during snapshot')
        data=t.file('data.sqlite');rows=0;cols=None;types=None
        with closing(sqlite3.connect(data)) as db:
            db.execute('PRAGMA cache_size=-8192')
            for frame in batches(copied,p):
                if int(frame.memory_usage(deep=True).sum())>32_000_000:fail('RESOURCE_LIMIT','Decoded batch exceeds 32 MB profile')
                names=list(frame.columns)
                if not names or len(names)>100 or len(set(names))!=len(names) or any(not isinstance(n,str) or not n or len(n)>120 for n in names):fail('SCHEMA_INVALID','Unique bounded column names required')
                if cols is None:cols=names;types=[str(v) for v in frame.dtypes]
                if names!=cols:fail('SCHEMA_DRIFT','Source fields changed between batches')
                for i,c in enumerate(frame):
                    if re.search(r'int|float',types[i],re.I) and frame[c].map(lambda v:isinstance(v,str)).any():fail('SCHEMA_DRIFT','Numeric source field changed to text')
                # Preserve heterogeneous scalar JSON fields as text, not a
                # SQLite numeric-affinity column silently coercing identifiers.
                for c in frame:
                    if str(frame[c].dtype)=='object':
                        kinds={type(v) for v in frame[c] if v is not None and not pd.isna(v)}
                        if bytes in kinds:fail('UNSUPPORTED_FIELD','Binary fields require explicit decoding')
                        if str in kinds and len(kinds)>1:fail('SCHEMA_DRIFT','Mixed string and numeric values require explicit normalization')
                rows+=len(frame)
                if rows>MAX_ROWS:fail('RESOURCE_LIMIT','Dataset exceeds one million rows')
                # SQLite is a table storage adapter. Non-native scalar types are
                # preserved as text with original parser dtype in the manifest.
                for c in frame:
                    frame[c]=frame[c].map(lambda v:None if pd.isna(v) else v if isinstance(v,(str,int,float,bytes)) else str(v))
                frame.to_sql('data',db,if_exists='append',index=False)
                if data.stat().st_size>MAX_DISK:fail('DISK_BUDGET','Disk table exceeds budget')
        if not rows:fail('EMPTY_DATA','No source records')
        prefix='datasets/raw/'+t.dataset_id+'/'
        files=[{'path':prefix+f.name,'sha256':file_sha256(f),'bytes':f.stat().st_size} for f in (copied,data)]
        manifest={'schema_version':2,'storage_kind':'sqlite','project_context':store.context,'dataset_id':t.dataset_id,'files':files,'row_count':rows,'columns':cols,'parser_dtypes':types,'source_parameters':p,'execution_id':execution_id,'request_id':request_id,'lineage':lineage}
        marker=t.file('manifest.json');marker.write_text(json.dumps(manifest,ensure_ascii=False,allow_nan=False),encoding='utf8')
        meta={'schema_version':2,**store.context,'dataset_id':t.dataset_id,'dataset_name':p.get('dataset_name','Explore dataset'),'created_at':utc_now(),'row_count':rows,'column_count':len(cols),'column_schema':[{'name':n,'pandas_dtype':d} for n,d in zip(cols,types)],'storage_area':'raw','original_file_path':files[0]['path'],'original_sha256':before,'current_file_path':prefix+'manifest.json','sha256':file_sha256(marker),'file_type':'sqlite_manifest','source_type':'user_provided','source_name':source.name,'source_function':None,'parser_options':p,'sheet_name':p.get('sheet'),'processing_history':[] if lineage is None else [lineage]}
        metadata=t.file('metadata.json');metadata.write_text(json.dumps(meta,ensure_ascii=False),encoding='utf8')
        final=store.directory('datasets/raw')/t.dataset_id;final.mkdir();t.final_directory=final
        for f in (copied,data,marker):
            dest=final/f.name;store._publish_file(f,dest);t.published.append((dest,dest.stat()))
        dest=store.directory('datasets/metadata')/(t.dataset_id+'.json');store._publish_file(metadata,dest);t.published.append((dest,dest.stat()));t.committed=True
        return {'dataset_id':t.dataset_id,'sha256':meta['sha256'],'row_count':rows,'column_count':len(cols),'manifest_path':meta['current_file_path']}

def input_files(store,ref):
    m,manifest=read(store,ref)
    return {m['current_file_path'],'datasets/metadata/'+m['dataset_id']+'.json',*[f['path'] for f in manifest['files']]}
