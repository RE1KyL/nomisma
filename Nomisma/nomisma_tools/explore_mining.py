"""Small, explicit full-sample mining methods over verified disk data."""
import collections,csv,json,math,re,uuid
import numpy as np
from dataset_store import DatasetError,file_sha256
from explore_store import read,connect,quote

def mine(store,ref,p,execution_id,request_id):
    m,manifest=read(store,ref)
    if m['row_count']>5000:raise DatasetError('RESOURCE_LIMIT','Mining profile permits 5000 full-sample rows; explicit upstream aggregation or sampling required')
    columns=p.get('columns',[]);method=p['method']
    if not columns or any(c not in manifest['columns'] for c in columns):raise DatasetError('INVALID_COLUMN','Choose existing mining fields')
    source=next(f['path'] for f in manifest['files'] if f['path'].endswith('/data.sqlite'))
    with connect(store.path(source)) as db:records=db.execute('SELECT '+','.join(quote(c) for c in columns)+' FROM data ORDER BY rowid').fetchall()
    if method=='text_frequency':
        if len(columns)!=1:raise DatasetError('INVALID_ARGUMENT','Text frequency uses one field')
        counter=collections.Counter();documents=0
        for (text,) in records:
            if not isinstance(text,str):raise DatasetError('TYPE_MISMATCH','Every text record must be a string')
            documents+=1
            # Deliberately labelled character bigrams for Chinese, not a
            # claim that a dictionary-based Chinese tokenizer is installed.
            counter.update(re.findall(r'[a-z0-9]+',text.lower()))
            for span in re.findall(r'[\u4e00-\u9fff]+',text):counter.update(span[i:i+2] for i in range(len(span)-1))
            if len(counter)>100000:raise DatasetError('RESOURCE_LIMIT','Vocabulary exceeds budget')
        rows=counter.most_common(100);header=['term','count'];facts={'method':method,'documents':documents,'vocabulary':len(counter),'returned_terms':len(rows),'tokenization':'Latin lowercase words and Chinese character bigrams; not dictionary segmentation'}
    else:
        try:x=np.array(records,dtype=float)
        except (ValueError,TypeError):raise DatasetError('TYPE_MISMATCH','Mining requires explicit finite numeric fields')
        if len(x)<3 or not np.isfinite(x).all():raise DatasetError('MISSING_VALUES','At least three complete finite records required')
        std=x.std(axis=0,ddof=1)
        if (std==0).any():raise DatasetError('CONSTANT_COLUMN','Remove constant features explicitly')
        x=(x-x.mean(axis=0))/std
        if method=='pca':
            u,s,v=np.linalg.svd(x,full_matrices=False);scores=u[:,:2]*s[:2];header=['source_row']+['component_'+str(i+1) for i in range(scores.shape[1])];rows=[[i+2,*r.tolist()] for i,r in enumerate(scores)]
            facts={'method':'pca','n':len(x),'standardization':'sample standard deviation','explained_variance_ratio':((s*s)/(s*s).sum()).tolist(),'loadings':v.tolist(),'features':columns}
        elif method=='kmeans':
            k=p.get('clusters',2);seed=p.get('seed',0)
            if k>=len(x):raise DatasetError('INSUFFICIENT_DATA','Clusters must be fewer than observations')
            rng=np.random.default_rng(seed);centers=x[rng.choice(len(x),k,replace=False)];previous=None
            for iteration in range(100):
                distance=((x[:,None,:]-centers[None,:,:])**2).sum(axis=2);labels=distance.argmin(axis=1)
                if len(set(labels.tolist()))!=k:raise DatasetError('EMPTY_CLUSTER','Initialization produced empty clusters; revise seed or k explicitly')
                if previous is not None and np.array_equal(labels,previous):break
                previous=labels.copy();centers=np.array([x[labels==j].mean(axis=0) for j in range(k)])
            inertia=float(((x-centers[labels])**2).sum());header=['source_row','cluster'];rows=[[i+2,int(v)] for i,v in enumerate(labels)]
            facts={'method':'kmeans','n':len(x),'clusters':k,'seed':seed,'iterations':iteration+1,'converged':iteration<99,'inertia':inertia,'standardization':'sample standard deviation','centers_standardized':centers.tolist(),'features':columns}
        else:raise DatasetError('METHOD_UNAVAILABLE','Unknown mining method')
    prefix='datasets/exports/explore_'+uuid.uuid4().hex
    document={'execution_id':execution_id,'request_id':request_id,'dataset':ref,'parameters':p,'facts':facts,'warnings':['Full declared sample only. Exploratory patterns do not establish causal or population conclusions.']}
    with store.transaction() as t:
        f=t.file('result.json');f.write_text(json.dumps(document,allow_nan=False),encoding='utf8');c=t.file('table.csv')
        with c.open('w',encoding='utf8',newline='') as out:w=csv.writer(out);w.writerow(header);w.writerows(rows)
        store.publish_artifacts(t,{'table.csv':prefix+'.csv','result.json':prefix+'.json'})
    return {'dataset_id':ref['dataset_id'],'input_sha256':ref['sha256'],'facts':facts,'result_path':prefix+'.json'},[{'path':prefix+ext,'sha256':file_sha256(store.path(prefix+ext))} for ext in ('.csv','.json')]
