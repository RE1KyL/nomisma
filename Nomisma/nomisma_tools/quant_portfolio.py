"""3A-3: training-only multi-factor combination, selection and constrained weights."""
import hashlib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from ashare_data import columns,timestamp,number,fail
from analysis_result import encode_result
from quant_factors import KEY

VERSION='quant-portfolio-1'

def factor_matrix(processed,names):
    if not isinstance(names,list) or not 1<=len(names)<=20 or len(set(names))!=len(names):fail('INVALID_ARGUMENT','Explicit distinct factor names are required')
    columns(processed,[*KEY,'factor','formation_at'],[*KEY,'factor'])
    if not {'processed_value','reason'}<=set(processed):fail('SCHEMA_MISMATCH','Processed factor fields missing')
    selected=processed[processed.factor.isin(names)]
    values=selected.pivot(index=KEY,columns='factor',values='processed_value').reindex(columns=names)
    reasons=selected.pivot(index=KEY,columns='factor',values='reason').reindex(columns=names)
    valid=values.notna().all(axis=1)&reasons.isna().all(axis=1)
    times=selected.groupby(KEY).formation_at.agg(lambda s:s.iloc[0] if len(set(s))==1 else None)
    if times.isna().any():fail('INVALID_TIMESTAMP','Factors in one formation row disagree on time')
    return values,valid,times

def fit_combiner(processed,labels,*,names,method,fit_at,training_start,training_end,ridge_alpha):
    """All fit labels must be available by fit_at. No test-window fitting or imputation."""
    matrix,valid,times=factor_matrix(processed,names);when=timestamp(fit_at)
    if training_start>training_end:fail('INVALID_INTERVAL','Training range is reversed')
    if method not in ('equal','ridge'):fail('INVALID_ARGUMENT','Supported combiners are equal and ridge')
    alpha=number(ridge_alpha,nonnegative=True)
    idx=matrix.index.get_level_values('session');mask=valid&(idx>=training_start)&(idx<=training_end)
    x=matrix.loc[mask].copy();t=times.loc[mask]
    if len(x)==0:fail('SAMPLE_TOO_SMALL','No complete training cross-sections')
    if any(timestamp(v)>when for v in t):fail('LOOKAHEAD_RISK','Training formation time is in the future')
    excluded=0;label_keys=[]
    if method=='equal':
        coeff=np.ones(len(names))/len(names);mean=np.zeros(len(names));sd=np.ones(len(names));intercept=0.
    else:
        if labels is None:fail('LABELS_REQUIRED','Ridge fitting requires realized training labels')
        columns(labels,[*KEY,'forward_return','entry_at','exit_at','label_available_at'],KEY)
        lab=labels.set_index(KEY);kept=[];y=[]
        for key in x.index:
            if key not in lab.index:excluded+=1;continue
            l=lab.loc[key]
            if timestamp(l.entry_at)<=timestamp(times.loc[key]) or timestamp(l.exit_at)<=timestamp(l.entry_at) or timestamp(l.label_available_at)<timestamp(l.exit_at):
                fail('LOOKAHEAD_RISK','Training label interval is invalid')
            if timestamp(l.label_available_at)>when:excluded+=1;continue
            kept.append(key);y.append(number(l.forward_return));label_keys.append({'session':key[0],'security_id':key[1],'label_available_at':l.label_available_at})
        x=x.loc[kept]
        if len(x)<len(names)+2:fail('SAMPLE_TOO_SMALL','Insufficient mature training labels')
        a=x.to_numpy(float);mean=a.mean(axis=0);sd=a.std(axis=0,ddof=0)
        if (sd<=1e-14).any():fail('CONSTANT_FACTOR','Training design has a constant factor')
        z=(a-mean)/sd;y=np.asarray(y,float);intercept=float(y.mean())
        gram=z.T@z+alpha*np.eye(len(names))
        if np.linalg.matrix_rank(gram)<len(names):fail('RANK_DEFICIENT','Ridge penalty does not resolve rank deficiency')
        coeff=np.linalg.solve(gram,z.T@(y-intercept))
    model={'schema_version':VERSION,'method':method,'factor_names':names,'fit_at':when.isoformat(),
       'training_start':training_start,'training_end':training_end,'training_rows':len(x),
       'excluded_immature_or_missing_labels':excluded,'ridge_alpha':alpha,'means':mean.tolist(),
       'scales':sd.tolist(),'coefficients':coeff.tolist(),'intercept':intercept,'label_keys':label_keys,
       'training_matrix_sha256':hashlib.sha256(encode_result(x.reset_index().to_dict('records'))).hexdigest()}
    model['model_hash']=hashlib.sha256(encode_result(model)).hexdigest();return model

def score_combiner(processed,model):
    check={k:v for k,v in model.items() if k!='model_hash'}
    if model.get('schema_version')!=VERSION or hashlib.sha256(encode_result(check)).hexdigest()!=model.get('model_hash'):
        fail('STALE_MODEL','Combiner model changed')
    matrix,valid,times=factor_matrix(processed,model['factor_names']);rows=[]
    for key,r in matrix.iterrows():
        when=timestamp(times.loc[key]);reason=None
        if when<timestamp(model['fit_at']) or key[0]<=model['training_end']:reason='NOT_OUT_OF_TRAINING_WINDOW'
        elif not valid.loc[key]:reason='INCOMPLETE_FACTOR_VECTOR'
        score=None
        if reason is None:
            score=number(float(((r.to_numpy(float)-np.asarray(model['means']))/np.asarray(model['scales']))@np.asarray(model['coefficients'])+model['intercept']))
        rows.append({'session':key[0],'security_id':key[1],'formation_at':when.isoformat(),'score':score,
                     'model_hash':model['model_hash'],'reason':reason})
    return pd.DataFrame(rows)

def select_assets(scores,*,top_n):
    columns(scores,[*KEY,'formation_at'],KEY)
    if type(top_n)!=int or not 1<=top_n<=100:fail('INVALID_ARGUMENT','Selection size is bounded and explicit')
    out=[]
    for session,g in scores.groupby('session',sort=True):
        known=g[g.reason.isna()&g.score.notna()].sort_values(['score','security_id'],ascending=[False,True])
        selected=set(known.head(top_n).security_id) if len(known)>=top_n else set()
        for r in g.to_dict('records'):
            out.append({**r,'selected':r['security_id'] in selected,'selection_reason':r['reason'] or
                ('INSUFFICIENT_CANDIDATES' if len(known)<top_n else None if r['security_id'] in selected else 'BELOW_RANK_CUTOFF')})
    return pd.DataFrame(out)

def estimate_covariance(returns,*,assets,decision_at,lookback,min_observations,diagonal_shrinkage):
    columns(returns,[*KEY,'return','available_at'],KEY)
    if not isinstance(assets,list) or not 1<=len(assets)<=100 or len(set(assets))!=len(assets):fail('INVALID_ARGUMENT','Explicit bounded assets are required')
    if type(lookback)!=int or not 2<=lookback<=504 or type(min_observations)!=int or not 2<=min_observations<=lookback:
        fail('INVALID_ARGUMENT','Covariance sample requirements are invalid')
    shrink=number(diagonal_shrinkage)
    if not 0<=shrink<=1:fail('INVALID_ARGUMENT','Shrinkage must be in [0,1]')
    when=timestamp(decision_at);g=returns[returns.security_id.isin(assets)].copy()
    g=g[g.available_at.map(timestamp)<=when]
    # Do not take an older complete window by dropping a missing recent session.
    wide=g.pivot(index='session',columns='security_id',values='return').reindex(columns=assets).sort_index().tail(lookback)
    if len(wide)<min_observations or wide.isna().any().any():fail('INCOMPLETE_COVARIANCE','Training return panel is incomplete')
    a=wide.map(number).to_numpy(float);cov=np.atleast_2d(np.cov(a,rowvar=False,ddof=1))
    cov=(1-shrink)*cov+shrink*np.diag(np.diag(cov))
    return {'assets':assets,'covariance':cov.tolist(),'mean_returns':a.mean(axis=0).tolist(),
            'observations':len(a),'last_session':str(wide.index[-1]),'decision_at':when.isoformat(),'shrinkage':shrink}

def construct_weights(*,assets,method,investment,max_weight,covariance=None,expected_returns=None,
                      risk_aversion=None,industries=None,industry_bounds=None,previous=None,max_turnover=None):
    if not isinstance(assets,list) or not 1<=len(assets)<=100 or len(set(assets))!=len(assets):fail('INVALID_ARGUMENT','Explicit asset order is required')
    investment=number(investment);cap=number(max_weight)
    if not 0<investment<=1 or not 0<cap<=1 or len(assets)*cap<investment-1e-12:fail('INFEASIBLE_CONSTRAINTS','Weight cap cannot meet investment')
    if method not in ('equal','min_variance','mean_variance'):fail('INVALID_ARGUMENT','Unsupported portfolio objective')
    n=len(assets);bounds=[(0.,cap)]*n;constraints=[{'type':'eq','fun':lambda w:float(w.sum()-investment)}]
    if industry_bounds:
        if not isinstance(industries,dict) or set(assets)-set(industries):fail('CONTROLS_MISSING','Industry exposure requires every selected asset')
        for industry,pair in industry_bounds.items():
            lo,hi=map(number,pair)
            if not 0<=lo<=hi<=1:fail('INVALID_ARGUMENT','Industry bounds invalid')
            mask=np.array([industries[a]==industry for a in assets],float)
            constraints.extend([{'type':'ineq','fun':lambda w,m=mask,l=lo:float(w@m-l)},
                                {'type':'ineq','fun':lambda w,m=mask,h=hi:float(h-w@m)}])
    prev=np.zeros(n)
    if previous is not None:
        if set(previous)-set(assets):fail('INVALID_ARGUMENT','Previous holdings outside selected assets need explicit liquidation accounting')
        prev=np.array([number(previous.get(a,0),nonnegative=True) for a in assets])
    if max_turnover is not None:
        limit=number(max_turnover,nonnegative=True)
        if previous is None:fail('INVALID_ARGUMENT','Turnover constraint needs prior portfolio')
        constraints.append({'type':'ineq','fun':lambda w:float(limit-np.abs(w-prev).sum())})
    w0=np.full(n,investment/n)
    if method=='equal':w=w0;message='Explicit equal weights';success=True
    else:
        cov=np.asarray(covariance,float)
        if cov.shape!=(n,n) or not np.isfinite(cov).all() or not np.allclose(cov,cov.T,atol=1e-12,rtol=1e-10) or np.linalg.eigvalsh(cov).min()<-1e-10:
            fail('INVALID_COVARIANCE','Covariance must be finite symmetric PSD in the declared asset order')
        if method=='mean_variance':
            mu=np.asarray(expected_returns,float);aversion=number(risk_aversion,positive=True)
            if mu.shape!=(n,) or not np.isfinite(mu).all():fail('INVALID_ARGUMENT','Expected returns must match the covariance asset order')
            objective=lambda w:float(aversion*w@cov@w-w@mu)
        else:objective=lambda w:float(w@cov@w)
        result=minimize(objective,w0,method='SLSQP',bounds=bounds,constraints=constraints,options={'maxiter':500,'ftol':1e-12})
        w=result.x;message=str(result.message);success=bool(result.success)
    residuals=[abs(float(w.sum()-investment)),max(0.,float(-w.min())),max(0.,float(w.max()-cap))]
    residuals.extend(max(0.,-c['fun'](w)) for c in constraints if c['type']=='ineq')
    if not success or not np.isfinite(w).all() or max(residuals)>1e-8:
        fail('OPTIMIZATION_FAILED','Constraints were not satisfied; they were not relaxed')
    return {'weights':dict(zip(assets,map(float,w))),'cash_weight':float(1-investment),'method':method,
            'solver_message':message,'max_constraint_violation':max(residuals),
            'one_way_absolute_weight_change':float(np.abs(w-prev).sum()) if previous is not None else None}
