"""Bounded TWFE via full-rank LSDV, entity clustered inference and wild bootstrap.

Separate from accepted classical OLS. No DID/causal identification is inferred.
"""
import math
import numpy as np
import pandas as pd
from scipy.stats import t as student_t
from statsmodels.regression.linear_model import OLS
from analysis_sample import load_analysis_input,prepare_sample
from analysis_result import make_analysis_result
from analysis_store import save_analysis
from dataset_store import DatasetError

def compute_panel(store,dataset,parameters,execution_id,request_id):
    if store.context['mode']!='economic':raise DatasetError('MODE_NOT_ALLOWED','TWFE requires an Economic project')
    p=parameters;y=p['y'];x=p['x'];entity=p['entity'];time=p['time'];columns=[y,*x,entity,time]
    if len(columns)!=len(set(columns)):raise DatasetError('INVALID_COLUMN','Panel roles must use distinct columns')
    source=load_analysis_input(store,dataset['dataset_id'],dataset['sha256'])
    sample=prepare_sample(source,columns=columns,roles={'y':y,'x':x},missing_policy=p['missing_policy'],filters=p.get('filters',[]))
    f=sample.frame
    if len(f)>2000 or f.duplicated([entity,time]).any():raise DatasetError('PANEL_STRUCTURE_INVALID','At most 2000 unique entity/time observations are supported')
    for c in [y,*x]:
        if not pd.api.types.is_numeric_dtype(f[c]) or pd.api.types.is_bool_dtype(f[c]):raise DatasetError('TYPE_MISMATCH','Panel y and x must be numeric')
    groups,labels=pd.factorize(f[entity],sort=True);g=len(labels);periods=f[time].nunique()
    if not 6<=g<=100 or not 2<=periods<=100:raise DatasetError('PANEL_STRUCTURE_INVALID','Entity-cluster inference requires 6–100 entities and 2–100 periods')
    if (f.groupby(entity).size()<2).any():raise DatasetError('PANEL_STRUCTURE_INVALID','Singleton entities require explicit upstream handling')
    dummies=pd.get_dummies(f[[entity,time]].astype(str),drop_first=True,dtype=float)
    design=np.column_stack([np.ones(len(f)),f[x].to_numpy(dtype=float),dummies.to_numpy(dtype=float)])
    dependent=f[y].to_numpy(dtype=float)
    if not np.isfinite(design).all() or not np.isfinite(dependent).all():raise DatasetError('NON_FINITE_INPUT','Finite panel values required')
    rank=np.linalg.matrix_rank(design)
    if rank!=design.shape[1] or len(f)-rank<max(5,len(x)+1):raise DatasetError('RANK_DEFICIENT','TWFE absorbs a regressor, panel is disconnected, or residual degrees of freedom are insufficient')
    if np.linalg.cond(design)>1e10:raise DatasetError('ILL_CONDITIONED','Panel design is numerically ill-conditioned')
    fitted=OLS(dependent,design).fit();robust=fitted.get_robustcov_results(cov_type='cluster',groups=groups,use_correction=True,df_correction=True,use_t=True)
    if np.linalg.norm(fitted.resid)<1e-10:raise DatasetError('PERFECT_FIT','Residual variance too small for panel inference')
    alpha=p['alpha'];draws=p['bootstrap_draws'];seed=p['seed'];rng=np.random.default_rng(seed);pinv=np.linalg.pinv(design)
    coefficients=[];bootstrap=[]
    weights=rng.choice(np.array([-1.,1.]),size=(draws,g)) if draws else None
    generated=fitted.fittedvalues[:,None]+fitted.resid[:,None]*weights[:,groups].T if draws else None
    estimates=pinv@generated if draws else None
    for i,name in enumerate(x,1):
        b=float(fitted.params[i]);se=float(robust.bse[i])
        if not math.isfinite(se) or se<=0:raise DatasetError('NUMERICAL_FAILURE','Clustered standard error unavailable')
        stat=b/se;crit=float(student_t.ppf(1-alpha/2,g-1))
        coefficients.append({'term':name,'estimate':b,'standard_error':se,'statistic':stat,'p_value':float(2*student_t.sf(abs(stat),g-1)),
            'confidence_interval':[b-crit*se,b+crit*se],'null_value':0.,'alternative':'two-sided'})
        if draws:
            delta=estimates[i]-b;lo,hi=np.quantile(delta,[alpha/2,1-alpha/2])
            restricted=np.delete(design,i,axis=1);fit0=OLS(dependent,restricted).fit()
            null_draws=pinv@(fit0.fittedvalues[:,None]+fit0.resid[:,None]*weights[:,groups].T)
            bootstrap.append({'term':name,'method':'entity_wild_cluster_rademacher_basic_interval_and_null_imposed_coefficient_test',
              'draws':draws,'seed':seed,'confidence_interval':[float(b-hi),float(b-lo)],
              'p_value':float((1+np.sum(np.abs(null_draws[i])>=abs(b)))/(draws+1)),
              'statistic':'unstudentized coefficient under restricted null','finite_sample_guarantee':False})
    warnings=['TWFE is an associational specification; causal identification and parallel trends are not established.',
       'Entity clustered inference assumes independence across entities; common shocks can require another covariance design.']
    if g<30:warnings.append('Few clusters: asymptotic and bootstrap inference may be unreliable.')
    computation={'method':'twfe','analysis_domain':'economic','algorithm_version':'nomisma-twfe-lsdv-1',
       'coefficients':coefficients,'bootstrap':bootstrap,'n':len(f),'entities':g,'periods':int(periods),'design_rank':int(rank),
       'df_resid':int(len(f)-rank),'covariance':{'kind':'entity_cluster','df':g-1,'small_sample_correction':True},
       'fixed_effects':{'entity':entity,'time':time,'reference_entity':str(labels[0]),'design_columns':['intercept',*x,*list(dummies.columns)],'estimates':fitted.params.tolist()},
       'warnings':warnings}
    result=make_analysis_result(sample,computation,execution_id=execution_id,request_id=request_id,parameters=p)
    table=pd.DataFrame([{**{k:v for k,v in c.items() if k!='confidence_interval'},'ci_low':c['confidence_interval'][0],'ci_high':c['confidence_interval'][1]} for c in coefficients])
    residuals=pd.DataFrame({'source_position':list(sample.positions),'fitted':fitted.fittedvalues,'residual':fitted.resid})
    receipt=save_analysis(store,result,tables={'coefficients':table,'residuals':residuals})
    receipt['data']['facts']={k:v for k,v in computation.items() if k!='fixed_effects'}
    from mcp_operations import small
    return small(receipt)
