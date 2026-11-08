"""Finite relational operations; SQL is constructed exclusively from verified fields."""
import uuid,math
from dataset_store import DatasetError,file_sha256
from explore_store import read,connect,quote,ingest,MAX_ROWS

def process(store,ref,p,execution_id,request_id):
    import pandas as pd
    import pyarrow as pa,pyarrow.parquet as pq
    m,manifest=read(store,ref);names=manifest['columns'];method=p['method'];parameters=[]
    def field(name):
        if name not in names:raise DatasetError('INVALID_COLUMN','Unknown processing field')
        return quote(name)
    if method=='select':
        selected=p['columns'];sql='SELECT '+','.join(field(n) for n in selected)+' FROM data'
    elif method=='filter':
        op={'eq':'=','gt':'>','lt':'<'}[p['operator']];sql='SELECT * FROM data WHERE '+field(p['column'])+op+'?';parameters=[p['value']]
    elif method=='group_count':sql='SELECT '+field(p['column'])+',count(*) AS count FROM data GROUP BY '+field(p['column'])
    elif method=='deduplicate':sql='SELECT DISTINCT * FROM data'
    elif method=='join':
        right=p.get('right_dataset');rm,rmanifest=read(store,right)
        key=p['column'];right_key=p.get('right_key',key)
        if key not in names or right_key not in rmanifest['columns']:raise DatasetError('INVALID_COLUMN','Join key does not exist')
        right_source=next(f['path'] for f in rmanifest['files'] if f['path'].endswith('/data.sqlite'))
        select=['l.'+quote(n)+' AS '+quote(n) for n in names]
        aliases=names.copy()
        for n in rmanifest['columns']:
            alias='right_'+n
            if alias in aliases:raise DatasetError('SCHEMA_INVALID','Join output names collide')
            aliases.append(alias);select.append('r.'+quote(n)+' AS '+quote(alias))
        if len(aliases)>100:raise DatasetError('RESOURCE_LIMIT','Join exceeds column budget')
        sql='SELECT '+','.join(select)+' FROM data l '+('LEFT JOIN' if p.get('join_type','left')=='left' else 'INNER JOIN')+' right_db.data r ON l.'+quote(key)+'=r.'+quote(right_key)
    elif method in ('fill_missing','string_trim','cast_numeric'):
        column=p['column'];field(column);selected=[]
        for n in names:
            q=field(n)
            expr=('COALESCE('+q+',?)' if method=='fill_missing' else 'trim('+q+')' if method=='string_trim' else 'strict_number('+q+')') if n==column else q
            selected.append(expr+' AS '+q)
        if method=='fill_missing':parameters=[p['value']]
        sql='SELECT '+','.join(selected)+' FROM data'
    else:raise DatasetError('METHOD_UNAVAILABLE','Processing method unavailable')
    source=next(f['path'] for f in manifest['files'] if f['path'].endswith('/data.sqlite'))
    with store.transaction() as t:
        file=t.file('derived.parquet');writer=None;count=0
        try:
            with connect(store.path(source)) as db:
                if method=='filter' and p['operator'] in ('gt','lt'):
                    q=field(p['column'])
                    if db.execute('SELECT 1 FROM data WHERE '+q+" IS NOT NULL AND typeof("+q+") NOT IN ('integer','real') LIMIT 1").fetchone():raise DatasetError('TYPE_MISMATCH','Ordered filters require explicit numeric conversion first')
                if method=='join':
                    db.execute('ATTACH DATABASE ? AS right_db',(store.path(right_source).as_uri()+'?mode=ro',))
                    duplicate=db.execute('SELECT 1 FROM right_db.data WHERE '+quote(right_key)+' IS NOT NULL GROUP BY '+quote(right_key)+' HAVING count(*)>1 LIMIT 1').fetchone()
                    if duplicate:raise DatasetError('JOIN_CARDINALITY','Right join key must be unique; no implicit many-to-many expansion')
                def strict_number(v):
                    if v is None:return None
                    x=float(v)
                    if not math.isfinite(x) or abs(x)>2**53:raise ValueError('Unsafe numeric conversion')
                    return x
                db.create_function('strict_number',1,strict_number)
                for frame in pd.read_sql_query(sql,db,params=parameters,chunksize=5000):
                    count+=len(frame)
                    if count>MAX_ROWS:raise DatasetError('RESOURCE_LIMIT','Derived output exceeds budget')
                    table=pa.Table.from_pandas(frame,preserve_index=False)
                    if writer is None:writer=pq.ParquetWriter(file,table.schema)
                    writer.write_table(table)
        finally:
            if writer:writer.close()
        if not count:raise DatasetError('EMPTY_DATA','Processing selected no records')
        parents=[ref]+([right] if method=='join' else [])
        result=ingest(store,{'source_path':file.relative_to(store.root).as_posix(),'expected_sha256':file_sha256(file),'dataset_name':'Derived '+method},execution_id,request_id,lineage={'parents':parents,'parameters':p})
    # Parent versions and actual processing rules are also part of the reviewed
    # request ledger and resulting execution artifact, not overwritten metadata.
    return {**result,'parents':parents,'processing_parameters':p}
