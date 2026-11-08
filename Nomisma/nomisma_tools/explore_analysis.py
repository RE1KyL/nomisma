"""Bounded disk summaries with explicit numeric conversion and real result artifacts."""
import csv,json,math,uuid
from dataset_store import DatasetError,file_sha256
from explore_store import read,connect,quote

def profile(store,ref,p,execution_id,request_id):
    m,manifest=read(store,ref);column=p['column']
    if column not in manifest['columns']:raise DatasetError('INVALID_COLUMN','Unknown field')
    data=next(f['path'] for f in manifest['files'] if f['path'].endswith('/data.sqlite'))
    q=quote(column);kind=p['method'];rows=[];facts={}
    with connect(store.path(data)) as db:
        n=db.execute('SELECT count(*) FROM data').fetchone()[0]
        if kind=='correlation':
            other=p['other_column']
            if other not in manifest['columns'] or other==column:raise DatasetError('INVALID_COLUMN','Choose a distinct paired numeric field')
            count=0;mx=my=sx=sy=cov=0.
            for a,b in db.execute('SELECT '+q+','+quote(other)+' FROM data'):
                if a is None or b is None:raise DatasetError('MISSING_VALUES','Correlation requires an explicit upstream missing-value decision')
                try:a,b=float(a),float(b)
                except (ValueError,TypeError):raise DatasetError('TYPE_MISMATCH','Correlation needs numeric fields')
                if not math.isfinite(a) or not math.isfinite(b) or max(abs(a),abs(b))>2**53:raise DatasetError('NON_FINITE_INPUT','Unsafe numeric correlation input')
                count+=1;dx=a-mx;dy=b-my;mx+=dx/count;my+=dy/count;sx+=dx*(a-mx);sy+=dy*(b-my);cov+=dx*(b-my)
            if count<3 or min(sx,sy)<=0:raise DatasetError('INSUFFICIENT_DATA','Correlation needs three paired nonconstant observations')
            correlation=max(-1.,min(1.,cov/math.sqrt(sx*sy)))
            facts={'method':'pearson','count':count,'correlation':correlation,'inference':'none','exactness':'full_scan_float64','columns':[column,other]};columns=['pair','correlation'];rows=[(column+' / '+other,correlation)]
        elif kind=='frequency':
            rows=db.execute('SELECT '+q+',count(*) FROM data GROUP BY '+q+' ORDER BY count(*) DESC,'+q+' LIMIT 101').fetchall()
            facts={'method':kind,'total_rows':n,'groups_returned':min(100,len(rows)),'truncated':len(rows)>100,'null_is_distinct_from_empty_text':True}
            rows=rows[:100];facts['returned_observations']=sum(v[1] for v in rows);facts['other_observations']=n-facts['returned_observations'];columns=[column,'count']
        else:
            # Welford avoids cancellation in single-pass variance; conversion
            # is requested explicitly, failures are not silently excluded.
            count=missing=0;mean=m2=0.;lo=hi=None
            for (v,) in db.execute('SELECT '+q+' FROM data'):
                if v is None:missing+=1;continue
                try:x=float(v)
                except (ValueError,TypeError):raise DatasetError('TYPE_MISMATCH','Numeric profile requires fully parseable nonmissing values')
                if not math.isfinite(x):raise DatasetError('NON_FINITE_INPUT','Nonfinite numeric field')
                if abs(x)>2**53:raise DatasetError('PRECISION_LOSS','Numeric conversion exceeds exact float64 integer range')
                count+=1;delta=x-mean;mean+=delta/count;m2+=delta*(x-mean);lo=x if lo is None else min(lo,x);hi=x if hi is None else max(hi,x)
            facts={'method':'numeric','total_rows':n,'count':count,'missing':missing,'mean':mean if count else None,'sample_variance':m2/(count-1) if count>1 else None,'min':lo,'max':hi,'numeric_conversion':'explicit float64','exactness':'full_scan_float64'}
            if any(isinstance(v,float) and not math.isfinite(v) for v in facts.values()):raise DatasetError('NUMERICAL_FAILURE','Statistic overflow')
            columns=['statistic','value'];rows=[(k,v) for k,v in facts.items() if isinstance(v,(int,float))]
    stem='explore_'+uuid.uuid4().hex;prefix='datasets/exports/'+stem
    result={'execution_id':execution_id,'request_id':request_id,'dataset':ref,'parameters':p,'facts':facts,'warnings':['Exploratory association only; not causal identification.'],'source_manifest_sha256':ref['sha256']}
    with store.transaction() as t:
        f=t.file('result.json');f.write_text(json.dumps(result,allow_nan=False),encoding='utf8')
        c=t.file('table.csv')
        with c.open('w',encoding='utf8',newline='') as out:
            w=csv.writer(out);w.writerow(columns);w.writerows(rows)
        store.publish_artifacts(t,{'table.csv':prefix+'.csv','result.json':prefix+'.json'})
    return {'facts':facts,'dataset_id':ref['dataset_id'],'input_sha256':ref['sha256'],'result_path':prefix+'.json'},[{'path':prefix+ext,'sha256':file_sha256(store.path(prefix+ext))} for ext in ('.csv','.json')]
