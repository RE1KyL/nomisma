"""Read-only, bounded sheet pages. Never evaluate formulas, macros or write source."""
import csv,datetime as dt,itertools,math,re,zipfile
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from dataset_store import DatasetError,file_sha256
from mcp_jobs import read_json
from xlsx_safety import UnsafeWorkbookXML, validate_xml_parts

MAX_ROWS=100_000;MAX_COLS=1000
def cell(value,row,column,number_format='General',cached=None,is_formula=False):
    kind='empty' if value is None else 'boolean' if isinstance(value,bool) else 'date' if isinstance(value,(dt.date,dt.datetime,dt.time)) else 'number' if isinstance(value,(int,float)) else 'formula' if is_formula else 'text'
    flags=[]
    if kind=='empty':flags.append('missing')
    elif value=='':flags.append('empty_text')
    if kind=='number' and isinstance(value,float) and not math.isfinite(value):flags.append('invalid_number');value=str(value)
    if kind=='date':value=value.isoformat()
    if isinstance(value,int) and abs(value)>2**53:value=str(value);kind='integer_text'
    raw=value;display='' if raw is None else str(raw)
    if kind=='boolean':display='TRUE' if value else 'FALSE'
    if kind=='number':
        cleaned=number_format.split(';')[0]
        precision=re.search(r'\.([0#]+)',cleaned);digits=min(len(precision[1]),12) if precision else 0
        if '%' in cleaned:display=f'{value*100:,.{digits}f}%'
        elif number_format!='General' and re.search(r'[0#]',cleaned):display=f'{value:,.{digits}f}' if ',' in cleaned else f'{value:.{digits}f}'
        else:display=str(value)
    if kind=='formula':display=str(cached) if cached is not None else str(value)
    return {'address':get_column_letter(column)+str(row),'row':row,'column':column,'value':raw,'display':display,
       'kind':kind,'number_format':number_format,'formula':raw if kind=='formula' else None,'cached_value':cached,'flags':flags}

class SheetSource:
    def __init__(self,store,path):
        self.store=store;self.path=path;self.file=store.path(path);self.books=[];self.typed_frame=None
        if not self.file.is_file() or self.file.stat().st_size>20_000_000:raise DatasetError('RESOURCE_LIMIT','Inspector supports files up to 20 MB')
        self.sha=file_sha256(self.file);self.xlsx=self.file.suffix.lower()=='.xlsx'
        if self.xlsx:
            with zipfile.ZipFile(self.file) as z:
                if len(z.infolist())>2000 or sum(i.file_size for i in z.infolist())>80_000_000:raise DatasetError('RESOURCE_LIMIT','Workbook decompression budget exceeded')
                try:
                    validate_xml_parts(z)
                except UnsafeWorkbookXML as error:
                    raise DatasetError('UNSUPPORTED_WORKBOOK', str(error)) from error
            self.wb=load_workbook(self.file,read_only=True,data_only=False,keep_links=False)
            self.cached=load_workbook(self.file,read_only=True,data_only=True,keep_links=False);self.books=[self.wb,self.cached]
            if len(self.wb.worksheets)>100:raise DatasetError('RESOURCE_LIMIT','At most 100 sheets are supported')
            self.sheets=[{'name':s.title,'rows':s.max_row or 0,'columns':s.max_column or 0} for s in self.wb.worksheets]
        elif self.file.suffix.lower() in ('.csv','.tsv'):
            match=re.fullmatch(r'datasets/(raw|processed)/(ds_[a-f0-9]{32})/[^/]+',path)
            if match:
                meta=store.metadata(match[2]);options=meta.get('parser_options',{})
                if meta['current_file_path']==path and options.get('canonical_csv_version')==1:
                    from dataset_io import read_canonical_csv
                    self.typed_frame=read_canonical_csv(self.file,options)
            if re.fullmatch(r'analyses/(shared|economic|finance)/an_[a-f0-9]{32}/table_[a-z0-9_]+\.csv',path):
                from analysis_store import read_analysis
                from dataset_io import read_canonical_csv
                parts=path.split('/');result,manifest=read_analysis(store,parts[2],namespace=parts[1]);self.typed_frame=read_canonical_csv(self.file,manifest['files'][parts[3]]['serialization'])
            quant=re.fullmatch(r'datasets/exports/(qr_[a-f0-9]{32})_[a-z0-9_]+\.csv',path)
            if quant:
                from dataset_io import read_canonical_csv
                manifest=read_json(store.path('datasets/metadata/artifacts/'+quant[1]+'_result.json'),8_000_000)
                if manifest.get('project_id')!=store.context['project_id'] or manifest.get('mode')!=store.context['mode']:raise DatasetError('PROJECT_CONTEXT_INVALID','Research table owner mismatch')
                for d in manifest['tables'].values():
                    if d['path']==path:
                        if d['sha256']!=self.sha:raise DatasetError('STALE_ARTIFACT','Research table differs from its manifest')
                        self.typed_frame=read_canonical_csv(self.file,d['serialization']);break
            with self.file.open('r',encoding='utf-8-sig',newline='') as f:
                reader=csv.reader(f,delimiter='\t' if self.file.suffix.lower()=='.tsv' else ',');n=0;width=0
                for row in reader:
                    n+=1;width=max(width,len(row))
                    if n>MAX_ROWS+1 or width>MAX_COLS:raise DatasetError('RESOURCE_LIMIT','Sheet exceeds 100000 data rows / 1000 columns')
            self.sheets=[{'name':'CSV','rows':n,'columns':width}]
        else:raise DatasetError('ARTIFACT_UNSUPPORTED','Inspector accepts XLSX/UTF-8 CSV/TSV')
    def close(self):
        for b in self.books:b.close()
    def rows(self,sheet,start=1,end=None):
        descriptor=next((s for s in self.sheets if s['name']==sheet),None)
        if descriptor is None:raise DatasetError('INVALID_SHEET','Unknown worksheet')
        if descriptor['rows']>MAX_ROWS+1 or descriptor['columns']>MAX_COLS:raise DatasetError('RESOURCE_LIMIT','Worksheet dimensions exceed inspection budget')
        end=min(end or descriptor['rows'],descriptor['rows'])
        if self.xlsx:
            source=self.wb[sheet].iter_rows(min_row=start,max_row=end)
            caches=self.cached[sheet].iter_rows(min_row=start,max_row=end)
            for r,(values,cached) in enumerate(zip(source,caches),start):
                yield r,[cell(c.value,r,j+1,c.number_format,cached[j].value if c.data_type=='f' else None,c.data_type=='f') for j,c in enumerate(values)]
        else:
            if self.typed_frame is not None:
                import pandas as pd
                frame=self.typed_frame
                for r in range(start,end+1):
                    if r==1:values=list(frame.columns)
                    else:
                        values=[]
                        for v in frame.iloc[r-2]:
                            if pd.isna(v):v=None
                            elif hasattr(v,'item'):v=v.item()
                            values.append(v)
                    yield r,[cell(v,r,j+1) for j,v in enumerate(values)]
                return
            with self.file.open('r',encoding='utf-8-sig',newline='') as f:
                reader=csv.reader(f,delimiter='\t' if self.file.suffix.lower()=='.tsv' else ',')
                for r,values in enumerate(itertools.islice(reader,start-1,end),start):yield r,[cell(values[j] if j<len(values) and values[j]!='' else None,r,j+1) for j in range(descriptor['columns'])]
    def unchanged(self):
        self.store.check_context()
        if file_sha256(self.store.path(self.path))!=self.sha:raise DatasetError('STALE_ARTIFACT','File changed while inspecting; reopen it')

def quality_overlay(store,path,sha):
    """Only exact saved report samples are marked; never imply samples are exhaustive."""
    directory=store.path('datasets/metadata/quality',allow_missing=True);marks={};reports=[]
    if not directory.exists():return marks,reports
    for p in sorted(directory.glob('qr_*.json'))[-100:]:
        q=read_json(store.path(p.relative_to(store.root).as_posix()))
        if q.get('input_file')!=path or q.get('input_sha256')!=sha:continue
        meta=store.metadata(q['dataset_id']);names=[c['name'] for c in meta['column_schema']];reports.append({'path':p.relative_to(store.root).as_posix(),'sha256':file_sha256(p)})
        def mark(position,name,kind):
            if name in names:marks.setdefault((position+2,names.index(name)+1),set()).add(kind)
        for name,v in q.get('missing',{}).get('columns',{}).items():
            for s in v.get('samples',[]):mark(s['row_position'],name,'missing')
        for s in q.get('duplicates',{}).values():
            if isinstance(s,dict):
                for pos in s.get('sample_row_positions',[]):
                    for name in names:mark(pos,name,'duplicate')
        for name,v in q.get('outliers',{}).items():
            for method in ('iqr','z_score'):
                for s in v.get(method,{}).get('samples',[]):mark(s['row_position'],name,'outlier_candidate')
        temporal=q.get('time_series') or {}
        if temporal.get('date_column'):
            for s in temporal.get('parse_failure_samples',[]):mark(s['row_position'],temporal['date_column'],'date_error')
    return marks,reports

def inspect(store,payload):
    allowed={'path','version','sheet','start_row','row_count','start_column','column_count','search','filter','sort','expected_types'}
    if not isinstance(payload,dict) or set(payload)-allowed:raise DatasetError('INVALID_ARGUMENT','Unknown inspector option')
    if payload['path'].endswith('/manifest.json'):
        from explore_preview import preview
        return preview(store,payload)
    source=SheetSource(store,payload['path'])
    try:
        if payload.get('version') and payload['version']!=source.sha:raise DatasetError('STALE_ARTIFACT','Inspector source version changed')
        sheet=payload.get('sheet') or source.sheets[0]['name'];start=payload.get('start_row',1);count=payload.get('row_count',200);col=payload.get('start_column',1);cols=payload.get('column_count',50)
        if any(type(v)!=int for v in (start,count,col,cols)) or not 1<=start<=MAX_ROWS+1 or not 1<=count<=500 or not 1<=col<=MAX_COLS or not 1<=cols<=100 or count*cols>25000:raise DatasetError('RESOURCE_LIMIT','Invalid sheet page dimensions')
        search=payload.get('search','');fil=payload.get('filter');sort=payload.get('sort');expected=payload.get('expected_types',{})
        if not isinstance(search,str) or len(search)>200 or not isinstance(expected,dict) or len(expected)>20:raise DatasetError('INVALID_ARGUMENT','Invalid inspection query')
        for obj,keys in [(fil,{'column','contains'}),(sort,{'column','direction'})]:
            if obj is not None and (not isinstance(obj,dict) or set(obj)!=keys or type(obj['column'])!=int or not 1<=obj['column']<=MAX_COLS):raise DatasetError('INVALID_ARGUMENT','Invalid view-only sort/filter')
        if fil and (not isinstance(fil['contains'],str) or len(fil['contains'])>200):raise DatasetError('INVALID_ARGUMENT','Filter text too long')
        if sort and sort['direction'] not in ('ascending','descending'):raise DatasetError('INVALID_ARGUMENT','Invalid sort direction')
        for key,kind in expected.items():
            if not re.fullmatch(r'[A-Z]{1,3}',key) or kind not in ('number','date','text'):raise DatasetError('INVALID_ARGUMENT','Invalid declared inspection type')
        marks,reports=quality_overlay(store,payload['path'],source.sha)
        descriptor=next((s for s in source.sheets if s['name']==sheet),None)
        if descriptor is None:raise DatasetError('INVALID_SHEET','Unknown worksheet')
        filtered=bool(search or fil or sort);rows=[];total=descriptor['rows'];first=next(source.rows(sheet,1,1),(0,[]))[1]
        if filtered:
            candidates=[]
            for row,values in source.rows(sheet):
                if search and not any(search.casefold() in c['display'].casefold() for c in values):continue
                if fil and (fil['column']>len(values) or fil['contains'].casefold() not in values[fil['column']-1]['display'].casefold()):continue
                # Keep only keys+source positions for the full view; load cells only for a page.
                key=values[sort['column']-1]['value'] if sort and sort['column']<=len(values) else ''
                candidates.append((row,str(key) if key is not None else ''))
            if sort:
                def sorting(v):
                    try:return (0,float(v[1]))
                    except ValueError:return (1,v[1])
                candidates.sort(key=sorting,reverse=sort['direction']=='descending')
            total=len(candidates);selected=[r for r,k in candidates[start-1:start-1+count]];lookup={}
            needed=set(selected)
            if needed:
                for r,values in source.rows(sheet,min(needed),max(needed)):
                    if r in needed:lookup[r]=values
            rows=[{'source_row':r,'cells':lookup[r][col-1:col-1+cols],'first_cell':lookup[r][0] if lookup[r] else None} for r in selected]
        else:rows=[{'source_row':r,'cells':values[col-1:col-1+cols],'first_cell':values[0] if values else None} for r,values in source.rows(sheet,start,start+count-1)]
        for row in rows:
            for c in row['cells']+([row['first_cell']] if row['first_cell'] is not None and col>1 else []):
                c['flags']=sorted(set(c['flags'])|marks.get((c['row'],c['column']),set()))
                declared=expected.get(get_column_letter(c['column']))
                if declared and c['value'] is not None:
                    try:
                        if declared=='number' and not math.isfinite(float(c['value'])):raise ValueError()
                        if declared=='date':dt.date.fromisoformat(str(c['value'])[:10])
                    except (ValueError,TypeError):c['flags'].append('date_error' if declared=='date' else 'type_error')
        source.unchanged()
        return {'schema_version':'sheet-page-1','path':payload['path'],'version':source.sha,'sheets':source.sheets,'sheet':sheet,
          'start_row':start,'start_column':col,'total_view_rows':total,'rows':rows,'first_row':first[col-1:col-1+cols],'first_column_header':first[0] if first else None,
          'quality_reports':reports,'quality_scope':'Missing/nonfinite values in visible cells; exact saved quality report samples; declared view-only type checks. Saved samples are not exhaustive.',
          'read_only':True,'warnings':['Formula expressions and cached values are separate; no recalculation or macro execution.','Sorting and filtering affect only this inspection view; source coordinates are preserved.']}
    finally:source.close()
