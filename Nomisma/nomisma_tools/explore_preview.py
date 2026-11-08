"""Disk table adapter for the existing read-only Spreadsheet Inspector."""
import re
from dataset_store import DatasetError
from explore_store import read,connect,quote

def preview(store,p):
    match=re.fullmatch(r'datasets/raw/(ds_[a-f0-9]{32})/manifest.json',p['path'])
    if not match:raise DatasetError('INVALID_PATH','Not an Explore dataset manifest')
    meta=store.metadata(match[1]);m,manifest=read(store,{'dataset_id':match[1],'sha256':meta['sha256']})
    if p.get('version') and p['version']!=m['sha256']:raise DatasetError('STALE_ARTIFACT','Dataset version changed')
    start=p.get('start_row',1);count=p.get('row_count',200);col=p.get('start_column',1);width=p.get('column_count',50);names=manifest['columns']
    if any(type(v)!=int for v in (start,count,col,width)) or not 1<=start<=1000001 or not 1<=count<=500 or not 1<=col<=len(names) or not 1<=width<=100 or count*width>25000:raise DatasetError('RESOURCE_LIMIT','Invalid preview bounds')
    def letter(n):
        out=''
        while n:n,r=divmod(n-1,26);out=chr(65+r)+out
        return out
    def cell(v,r,c):return {'address':letter(c)+str(r),'row':r,'column':c,'value':v,'display':'' if v is None else str(v),'kind':'null' if v is None else 'number' if isinstance(v,(int,float)) else 'text','number_format':'General','formula':None,'cached_value':None,'flags':['missing'] if v is None else []}
    search=p.get('search','');conditions=[];args=[]
    if not isinstance(search,str) or len(search)>200:raise DatasetError('INVALID_ARGUMENT','Invalid search')
    if search:
        conditions.append('('+' OR '.join('instr(CAST('+quote(n)+' AS TEXT),?)>0' for n in names)+')');args.extend([search]*len(names))
    fil=p.get('filter');sort=p.get('sort');order='rowid'
    for option in (fil,sort):
        if option is not None and (not isinstance(option,dict) or type(option.get('column'))!=int or not 1<=option['column']<=len(names)):raise DatasetError('INVALID_ARGUMENT','Unknown view column')
    if fil:
        if set(fil)!={'column','contains'} or not isinstance(fil['contains'],str) or len(fil['contains'])>200:raise DatasetError('INVALID_ARGUMENT','Invalid filter')
        conditions.append('instr(CAST('+quote(names[fil['column']-1])+' AS TEXT),?)>0');args.append(fil['contains'])
    if sort:
        if set(sort)!={'column','direction'} or sort['direction'] not in ('ascending','descending'):raise DatasetError('INVALID_ARGUMENT','Invalid sort')
        order=quote(names[sort['column']-1])+(' ASC' if sort['direction']=='ascending' else ' DESC')+',rowid'
    where=' WHERE '+' AND '.join(conditions) if conditions else ''
    source=next(f['path'] for f in manifest['files'] if f['path'].endswith('/data.sqlite'))
    rows=[]
    with connect(store.path(source)) as db:
        total=db.execute('SELECT count(*) FROM data'+where,args).fetchone()[0]+(0 if conditions else 1)
        if not conditions and start==1:rows.append({'source_row':1,'cells':[cell(n,1,i+1) for i,n in enumerate(names[col-1:col-1+width],col-1)],'first_cell':cell(names[0],1,1)})
        offset=max(0,start-1-(0 if conditions else 1));limit=count-len(rows)
        for row in db.execute('SELECT rowid,* FROM data'+where+' ORDER BY '+order+' LIMIT ? OFFSET ?',[*args,limit,offset]):
            r=row[0]+1;rows.append({'source_row':r,'cells':[cell(row[i],r,i) for i in range(col,min(col+width,len(names)+1))],'first_cell':cell(row[1],r,1)})
    header=[cell(names[i],1,i+1) for i in range(col-1,min(len(names),col-1+width))]
    return {'path':p['path'],'version':m['sha256'],'sheets':[{'name':'Data','rows':m['row_count']+1,'columns':len(names)}],'sheet':'Data','start_row':start,'start_column':col,'total_view_rows':total,'rows':rows,'first_row':header,'first_column_header':cell(names[0],1,1),'quality_reports':[],'quality_scope':'Full disk dataset; row numbers identify canonical disk-table source rows. No precomputed quality overlay.','read_only':True,'warnings':['Sorting and filters affect only the view. Numeric types follow stored parser policy.']}
