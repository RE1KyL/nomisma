"""Immutable chart display versions, referencing one verified source artifact."""
import copy,math,re,uuid
import numpy as np
import pandas as pd
from dataset_store import DatasetError,utc_now,file_sha256
from shared_store import read_artifact,publish_json
from shared_contract import identifier,object_schema
from mcp_jobs import read_json
from jsonschema import Draft202012Validator

def schema():
    text={'type':'string','maxLength':200};bounded={'type':['number','null'],'minimum':-1e15,'maximum':1e15}
    axis=object_schema({'title':text,'unit':text,'min':bounded,'max':bounded,'scale':{'enum':['linear','log']},'format':{'enum':['number','percent','currency','date']},'precision':{'type':'integer','minimum':0,'maximum':10},'ticks':{'type':'integer','minimum':2,'maximum':12}},['title','unit','min','max','scale','format','precision','ticks'])
    series=object_schema({'column':{'type':'string','minLength':1,'maxLength':128},'label':text,'visible':{'type':'boolean'},'color':{'type':'string','pattern':'^#[a-fA-F0-9]{6}$'},'width':{'type':'number','minimum':.5,'maximum':8},'marker':{'type':'number','minimum':0,'maximum':12}},['column','label','visible','color','width','marker'])
    return object_schema({'chart_type':{'enum':['line','bar','scatter','histogram','box','heatmap','coefficient']},'title':text,'subtitle':text,
        'x_column':{'type':'string','minLength':1,'maxLength':128},'x_axis':axis,'y_axis':axis,'legend':{'enum':['top','bottom','right','none']},
        'grid':{'type':'boolean'},'width':{'type':'integer','minimum':480,'maximum':2400},'height':{'type':'integer','minimum':320,'maximum':1600},
        'font_size':{'type':'integer','minimum':10,'maximum':24},'bins':{'type':'integer','minimum':2,'maximum':100},
        'series':{'type':'array','items':series,'minItems':1,'maxItems':12}},['chart_type','title','subtitle','x_column','x_axis','y_axis','legend','grid','width','height','font_size','bins','series'])

def source_data(store,artifact):
    path=artifact['file_path'];file=store.path(path);semantic='table';note=[]
    if path.lower().endswith('.csv'):
        # Reuse typed serializations for registered data and analysis tables when available.
        from dataset_io import load_dataset,read_canonical_csv
        frame=None
        p=store.path('datasets/metadata',allow_missing=True)
        if p.exists():
            for m in p.glob('ds_*.json'):
                meta=store.metadata(m.stem)
                if meta['current_file_path']==path:frame,_=load_dataset(store,m.stem,expected_sha256=artifact['file_hash']);break
        if frame is None and re.fullmatch(r'analyses/(shared|economic|finance)/an_[a-f0-9]{32}/table_[a-z0-9_]+\.csv',path):
            from analysis_store import read_analysis
            parts=path.split('/');result,manifest=read_analysis(store,parts[2],namespace=parts[1]);info=manifest['files'][parts[3]]
            frame=read_canonical_csv(file,info['serialization'])
            if 'coefficients' in parts[3]:semantic='coefficient'
        if frame is None and re.fullmatch(r'datasets/exports/qr_[a-f0-9]{32}_[a-z0-9_]+\.csv',path) and store.context['mode']=='finance':
            from quant_experiments import read_result
            run_id=path.split('/')[-1][:35];marker='datasets/metadata/artifacts/'+run_id+'_result.json'
            tables,record=read_result(store,{'run_id':run_id,'path':marker,'sha256':file_sha256(store.path(marker))})
            frame=next((tables[name] for name,d in record['tables'].items() if d['path']==path),None)
        if frame is None:frame=pd.read_csv(file)
    elif path.endswith('/results.json'):
        from analysis_store import read_analysis
        parts=path.split('/')
        if len(parts)!=4 or parts[0]!='analyses':raise DatasetError('CHART_SOURCE_INVALID','Only verified model result JSON is chartable')
        result,manifest=read_analysis(store,parts[2],namespace=parts[1]);r=result['results']
        if 'coefficients' in r:
            frame=pd.DataFrame([{'term':c['term'],'estimate':c['estimate'],'ci_low':c['confidence_interval'][0],'ci_high':c['confidence_interval'][1]} for c in r['coefficients']]);semantic='coefficient'
        elif 'pairs' in r:
            labels=r['columns'];matrix=pd.DataFrame(r['matrix'],index=labels,columns=labels)
            matrix.insert(0,'variable',matrix.index);frame=matrix.reset_index(drop=True);semantic='matrix'
        else:raise DatasetError('CHART_SOURCE_INVALID','Choose a saved numeric analysis table for this result')
        note+=result.get('warnings',[])
    else:raise DatasetError('CHART_SOURCE_INVALID','Create interactive charts from CSV or supported machine result JSON, not image pixels')
    if not 1<=len(frame)<=5000 or len(frame.columns)>100 or frame.size>200_000:raise DatasetError('RESOURCE_LIMIT','Chart source exceeds 5000 rows / 200000 cells; create an explicit upstream aggregate')
    if any(not isinstance(c,str) or len(c)>128 for c in frame.columns) or not frame.columns.is_unique:raise DatasetError('CHART_SOURCE_INVALID','Unique bounded column names required')
    def plain(v):
        if pd.isna(v):return None
        if isinstance(v,(pd.Timestamp,)):return v.isoformat()
        if hasattr(v,'item'):v=v.item()
        if isinstance(v,float) and not math.isfinite(v):raise DatasetError('NON_FINITE_INPUT','Chart source contains nonfinite values')
        if isinstance(v,int) and abs(v)>2**53:raise DatasetError('PRECISION_LOSS','Chart numeric values exceed exact JavaScript precision')
        return v if isinstance(v,(str,int,float,bool)) else str(v)
    data={'columns':list(frame.columns),'rows':[{str(k):plain(v) for k,v in row.items()} for row in frame.to_dict('records')],
        'numeric_columns':[c for c in frame if pd.api.types.is_numeric_dtype(frame[c]) and not pd.api.types.is_bool_dtype(frame[c])],
        'semantic':semantic,'warnings':note+['Display settings do not change research parameters. Missing points remain gaps; no implicit imputation.']}
    if semantic=='table' and any(c in frame for c in ('date','session','time')):
        field=next(c for c in ('date','session','time') if c in frame)
        parsed=pd.to_datetime(frame[field],errors='coerce',utc=True)
        if parsed.isna().any() or not parsed.is_monotonic_increasing or parsed.duplicated().any():
            raise DatasetError('CHART_STRUCTURE_INVALID','Time-series charts require unique ordered dates; create an explicit filter/pivot/aggregate task for long multi-asset data')
        data['semantic']='time_series'
    if file_sha256(file)!=artifact['file_hash']:raise DatasetError('STALE_ARTIFACT','Chart source changed while loading')
    return data

def default_config(data):
    kind='coefficient' if data['semantic']=='coefficient' else 'heatmap' if data['semantic']=='matrix' else 'line' if data['semantic']=='time_series' else 'scatter'
    x='term' if kind=='coefficient' else next((c for c in ('date','session','time') if c in data['columns']),data['columns'][0])
    numeric=[c for c in data['numeric_columns'] if c!=x and c not in ('ci_low','ci_high')][:12 if data['semantic']=='matrix' else 4]
    preferred=[c for c in ('nav','ic','rank_ic','wealth') if c in data['numeric_columns']]
    if preferred:numeric=preferred
    if data['semantic']=='table' and x not in data['numeric_columns']:kind='bar'
    if not numeric:raise DatasetError('CHART_SOURCE_INVALID','No numeric series exists')
    colors=['#2563eb','#0f766e','#b45309','#7c3aed']
    axis=lambda title:{'title':title,'unit':'source units','min':None,'max':None,'scale':'linear','format':'number','precision':3,'ticks':5}
    return {'chart_type':kind,'title':'研究结果','subtitle':'','x_column':x,'x_axis':axis(x),'y_axis':axis('数值'),
        'legend':'bottom','grid':True,'width':1000,'height':560,'font_size':13,'bins':20,
        'series':[{'column':c,'label':c,'visible':True,'color':colors[i%4],'width':2,'marker':3} for i,c in enumerate(numeric)]}
def validate_config(config,data):
    if not Draft202012Validator(schema()).is_valid(config):raise DatasetError('CHART_CONFIG_INVALID','Unknown or invalid display option; research parameters are not chart settings')
    allowed={'coefficient':['coefficient'],'matrix':['heatmap'],'time_series':['line','bar','scatter'],'table':['line','bar','scatter','histogram','box']}[data['semantic']]
    if config['chart_type'] not in allowed:raise DatasetError('CHART_SEMANTICS_INVALID','Chart type would change the meaning of this source')
    if config['x_column'] not in data['columns'] or any(s['column'] not in data['numeric_columns'] for s in config['series']):raise DatasetError('CHART_CONFIG_INVALID','Selected columns are absent or nonnumeric')
    if len({s['column'] for s in config['series']})!=len(config['series']):raise DatasetError('CHART_CONFIG_INVALID','Series must be unique')
    if data['semantic']=='coefficient' and (config['x_column']!='term' or any(s['column']!='estimate' for s in config['series'])):raise DatasetError('CHART_SEMANTICS_INVALID','Coefficient intervals must retain term/estimate definitions')
    for which in ('x_axis','y_axis'):
        a=config[which]
        if a['min'] is not None and a['max'] is not None and a['min']>=a['max']:raise DatasetError('CHART_RANGE_INVALID','Axis lower bound must be below upper bound')
        values=[row.get(config['x_column']) for row in data['rows']] if which=='x_axis' else [row[s['column']] for row in data['rows'] for s in config['series'] if s['visible']]
        if a['scale']=='log':
            if config['chart_type'] not in ('line','scatter') or any(type(v) not in (int,float) or v<=0 for v in values if v is not None) or any(a[k] is not None and a[k]<=0 for k in ('min','max')):
                raise DatasetError('CHART_LOG_INVALID','Log axis requires positive numeric line/scatter values')
    return config
def versions(store,cid):
    identifier('chart_id',cid);p=store.path('.nomisma/shared/charts/'+cid,allow_missing=True)
    if not p.exists():raise DatasetError('CHART_NOT_FOUND','Chart is not registered')
    files=sorted(p.glob('v_*.json'))
    if not 1<=len(files)<=100:raise DatasetError('CHART_VERSION_INVALID','Chart version budget exceeded')
    rows=[read_json(store.path(f.relative_to(store.root).as_posix()),2_000_000) for f in files]
    if any(r.get('project_id')!=store.context['project_id'] or r.get('domain')!=store.context['mode'] or r['version']!=i+1 for i,r in enumerate(rows)):raise DatasetError('CHART_OWNER_INVALID','Chart versions or project ownership do not match')
    return rows
def open_chart(store,cid,version=None):
    rows=versions(store,cid);version=version or len(rows)
    if type(version)!=int or not 1<=version<=len(rows):raise DatasetError('CHART_VERSION_INVALID','Unknown chart version')
    record=rows[version-1];artifact=read_artifact(store,record['source_artifact_id']);data=source_data(store,artifact);validate_config(record['chart_config'],data)
    return {'chart':record,'source':artifact,'data':data,'versions':[{'version':r['version'],'created_at':r['created_at']} for r in rows],
       'default_config':rows[0]['chart_config'],'allowed_types':{'coefficient':['coefficient'],'matrix':['heatmap'],'time_series':['line','bar','scatter'],'table':['line','bar','scatter','histogram','box']}[data['semantic']]}
def create_chart(store,aid,config=None):
    a=read_artifact(store,aid);data=source_data(store,a);config=validate_config(config or default_config(data),data);cid='ch_'+uuid.uuid4().hex
    record={'schema_version':'chart-1','chart_id':cid,'project_id':store.context['project_id'],'domain':store.context['mode'],'experiment_id':a['experiment_id'],
      'source_task_id':a['task_id'],'source_artifact_id':aid,'source_hash':a['file_hash'],'chart_type':config['chart_type'],'chart_config':config,'created_at':utc_now(),'version':1}
    publish_json(store,'.nomisma/shared/charts/'+cid+'/v_0001.json',record)
    # Existing file explorer can reopen the chart; this contains only a reference.
    publish_json(store,'datasets/exports/'+cid+'.chart.json',{'chart_id':cid,'project_id':store.context['project_id']})
    return open_chart(store,cid)
def save_chart(store,cid,config,expected_version):
    opened=open_chart(store,cid);latest=len(opened['versions'])
    if latest!=expected_version:raise DatasetError('STATE_CONFLICT','Chart changed; reopen before saving')
    validate_config(config,opened['data']);r=copy.deepcopy(opened['chart']);r.update(chart_config=config,chart_type=config['chart_type'],version=latest+1,created_at=utc_now())
    if latest>=100:raise DatasetError('RESOURCE_LIMIT','At most 100 chart display versions')
    publish_json(store,'.nomisma/shared/charts/'+cid+f'/v_{latest+1:04}.json',r);return open_chart(store,cid)
