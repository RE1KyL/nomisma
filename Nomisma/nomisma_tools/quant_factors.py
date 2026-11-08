"""3A-2: causal factor formation, cross-sectional processing and evaluation.

Internal Python API. Availability and membership are data, never inferred from
whether a future label exists. No automatic financial TTM or corporate actions.
"""
from dataclasses import dataclass,asdict
import hashlib,math
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from ashare_data import day,timestamp,number,columns,fail
from analysis_result import encode_result
from dataset_store import check_shape

VERSION='quant-factors-1'
KEY=['session','security_id']

@dataclass(frozen=True)
class FactorSpec:
    name:str
    method:str
    window:int
    skip:int
    inputs:tuple[str,...]
    direction:int
    definition:str

def validate_spec(spec):
    if not isinstance(spec,FactorSpec) or spec.method not in ('momentum','volatility','mean','ratio'):
        fail('INVALID_FACTOR','Unsupported finite factor definition')
    if (not isinstance(spec.name,str) or not spec.name.isidentifier() or len(spec.name)>40
        or type(spec.window)!=int or not 1<=spec.window<=252 or type(spec.skip)!=int or not 0<=spec.skip<=60
        or spec.direction not in (-1,1) or type(spec.direction)!=int
        or not isinstance(spec.definition,str) or not 1<=len(spec.definition)<=1000):
        fail('INVALID_FACTOR','Factor must have a bounded explicit definition')
    expected=2 if spec.method=='ratio' else 1
    if (not isinstance(spec.inputs,tuple) or len(spec.inputs)!=expected
        or any(not isinstance(v,str) or not v for v in spec.inputs)
        or len(set(spec.inputs))!=len(spec.inputs)):
        fail('INVALID_FACTOR','Factor inputs must be distinct explicit columns')
    if spec.method=='ratio' and (spec.window!=1 or spec.skip!=0):fail('INVALID_FACTOR','Ratios require explicitly prepared point-in-time inputs')
    if spec.method=='volatility' and spec.window<2:fail('INVALID_FACTOR','Sample volatility requires at least two returns')
    return hashlib.sha256(encode_result(asdict(spec))).hexdigest()

def validate_sessions(sessions):
    if not isinstance(sessions,list) or not sessions or len(sessions)>20000 or sessions!=sorted(set(sessions)):
        fail('INVALID_CALENDAR','Sessions must be a unique ordered calendar')
    for s in sessions:day(s)
    return {s:i for i,s in enumerate(sessions)}

def form_factors(observations,membership,*,sessions,specs,formation_times,cancelled=None):
    """observations row's available_at covers ALL its inputs; membership is PIT.

    Both tables use canonical session/security_id keys. For financial ratios the
    caller must provide as-of-resolved values and real availability; this API
    cannot manufacture them from report-period timestamps.
    """
    calendar=validate_sessions(sessions)
    if not isinstance(specs,list) or not 1<=len(specs)<=20:fail('RESOURCE_LIMIT','Select one to twenty factors')
    hashes={spec.name:validate_spec(spec) for spec in specs}
    if len(hashes)!=len(specs):fail('INVALID_FACTOR','Factor names must be unique')
    required=list(dict.fromkeys([*KEY,'available_at',*[c for spec in specs for c in spec.inputs]]))
    # Numeric factor inputs may be missing: retain a rejection ledger, never forward fill.
    columns(observations,[*KEY,'available_at'],KEY)
    if not set(required)<=set(observations):fail('SCHEMA_MISMATCH','Factor input column is missing')
    columns(membership,[*KEY,'eligible','known_at'],KEY)
    if not membership.eligible.map(lambda x:type(x) in (bool,np.bool_)).all():fail('INVALID_UNIVERSE','Membership eligibility must be explicit booleans')
    if not isinstance(formation_times,dict) or set(formation_times)!=set(membership.session):
        fail('INVALID_TIMESTAMP','Every formation session requires a trusted decision time')
    for s,t in formation_times.items():
        day(s)
        if s not in calendar or timestamp(t).tz_convert('Asia/Shanghai').date().isoformat()!=s:
            fail('INVALID_TIMESTAMP','Formation timestamp must belong to its calendar session')
    obs={a:g.set_index('session') for a,g in observations.groupby('security_id',sort=False)}
    if not set(observations.session)<=set(sessions):fail('INVALID_CALENDAR','Input contains sessions outside the declared calendar')
    rows=[]
    for m in membership.sort_values(KEY).to_dict('records'):
        if cancelled and cancelled():fail('CANCELLED','Factor formation cancelled')
        t=calendar[m['session']];decision=timestamp(formation_times[m['session']]);g=obs.get(m['security_id'])
        reason='UNIVERSE_NOT_KNOWN' if timestamp(m['known_at'])>decision else 'UNIVERSE_INELIGIBLE' if not m['eligible'] else None
        for spec in specs:
            why=reason;value=None;available=None
            end=t-spec.skip;start=end-spec.window if spec.method in ('momentum','volatility') else end-spec.window+1
            if why is None and start<0:why='INSUFFICIENT_HISTORY'
            needed=sessions[max(start,0):end+1] if end>=0 else []
            if why is None and (g is None or not set(needed)<=set(g.index)):why='HISTORY_GAP'
            if why is None:
                window=g.loc[needed]
                known=[timestamp(v) for v in window.available_at]
                available=max(known)
                if available>decision:why='INPUT_NOT_AVAILABLE'
                elif window[list(spec.inputs)].isna().any().any():why='INPUT_MISSING'
                else:
                    a=np.array([[number(v) for v in window[c]] for c in spec.inputs])
                    if spec.method in ('momentum','volatility') and (a<=0).any():why='INVALID_PRICE'
                    elif spec.method=='momentum':value=float(a[0,-1]/a[0,0]-1)
                    elif spec.method=='volatility':value=float(np.std(a[0,1:]/a[0,:-1]-1,ddof=1))
                    elif spec.method=='mean':value=float(a[0].mean())
                    elif a[1,-1]<=0:why='NONPOSITIVE_RATIO_DENOMINATOR'
                    else:value=float(a[0,-1]/a[1,-1])
            if value is not None:value=number(value)*spec.direction
            rows.append({**{k:m[k] for k in KEY},'factor':spec.name,'factor_hash':hashes[spec.name],
                'formation_at':decision.isoformat(),'available_at':None if available is None else available.isoformat(),
                'value':value,'reason':why,'direction':spec.direction})
    result=pd.DataFrame(rows);check_shape(result);return result

def process_cross_sections(factors,*,winsor,standardize,min_assets,controls=None,neutralize=()):
    """Controls: industry/log_size with individual *_available_at timestamps.
    OLS residualization is within formation cross-section, with explicit base industry.
    """
    columns(factors,[*KEY,'factor','formation_at'],[*KEY,'factor'])
    if not {'value','reason'}<=set(factors):fail('SCHEMA_MISMATCH','Factor values and rejection reasons required')
    if (not isinstance(winsor,dict) or winsor.get('method') not in ('none','mad','quantile')
        or type(standardize)!=bool or type(min_assets)!=int or min_assets<3
        or not set(neutralize)<={'industry','log_size'}):fail('INVALID_ARGUMENT','Explicit cross-sectional processing required')
    if set(winsor)-{'method','scale','lower','upper'}:fail('INVALID_ARGUMENT','Unknown processing options')
    if neutralize:
        if controls is None:fail('CONTROLS_MISSING','Neutralization requires point-in-time control values')
        columns(controls,[*KEY,*neutralize,*[n+'_available_at' for n in neutralize]],KEY)
        c=controls.set_index(KEY)
    out=[]
    for (session,factor),group in factors.groupby(['session','factor'],sort=True):
        g=group.copy();g['raw_value']=g['value'];g['winsor_value']=None;g['processed_value']=None
        eligible=g.reason.isna()&g.value.notna()
        if neutralize:
            for i,r in g.loc[eligible].iterrows():
                key=(r.session,r.security_id)
                if key not in c.index:g.at[i,'reason']='CONTROL_MISSING';continue
                row=c.loc[key]
                if any(timestamp(row[n+'_available_at'])>timestamp(r.formation_at) for n in neutralize):g.at[i,'reason']='CONTROL_NOT_AVAILABLE'
            eligible=g.reason.isna()&g.value.notna()
        idx=g.index[eligible];x=g.loc[idx,'value'].map(number).to_numpy(float)
        if len(idx)<min_assets:g.loc[idx,'reason']='INSUFFICIENT_CROSS_SECTION';out.append(g);continue
        if winsor['method']=='mad':
            scale=number(winsor.get('scale'),positive=True);med=np.median(x);mad=np.median(np.abs(x-med))
            if mad>0:x=np.clip(x,med-scale*mad,med+scale*mad)
        elif winsor['method']=='quantile':
            lo=number(winsor.get('lower'));hi=number(winsor.get('upper'))
            if not 0<=lo<.5<hi<=1:fail('INVALID_ARGUMENT','Quantile cutoffs are invalid')
            x=np.clip(x,*np.quantile(x,[lo,hi]))
        g.loc[idx,'winsor_value']=x
        if neutralize:
            cc=c.loc[[(session,a) for a in g.loc[idx,'security_id']]];design=[np.ones(len(x))];base=None
            if 'log_size' in neutralize:design.append(cc.log_size.map(number).to_numpy(float))
            if 'industry' in neutralize:
                if not cc.industry.map(lambda v:isinstance(v,str) and bool(v)).all():fail('INVALID_CONTROL','Industry values must be known text')
                levels=sorted(cc.industry.unique());base=levels[0]
                design.extend((cc.industry==v).to_numpy(float) for v in levels[1:])
            matrix=np.column_stack(design)
            if len(x)<=matrix.shape[1] or np.linalg.matrix_rank(matrix)<matrix.shape[1]:
                g.loc[idx,'reason']='NEUTRALIZATION_RANK_OR_DF';out.append(g);continue
            x=x-matrix@np.linalg.lstsq(matrix,x,rcond=None)[0];g['neutralization_base']=base
        if standardize:
            sd=float(np.std(x,ddof=0))
            if sd<=1e-14:g.loc[idx,'reason']='CONSTANT_FACTOR';out.append(g);continue
            x=(x-float(np.mean(x)))/sd
        g.loc[idx,'processed_value']=x;out.append(g)
    result=pd.concat(out,ignore_index=True);check_shape(result);return result

def hac_mean(values,*,lags):
    a=np.asarray(values,dtype=float);n=len(a)
    if type(lags)!=int or not 0<=lags<=1000:fail('INVALID_ARGUMENT','HAC lag bound is invalid')
    if n<2 or lags>=n:return {'mean':float(a.mean()) if n else None,'se':None,'t':None,'n':n,'reason':'INSUFFICIENT_TIME_POINTS'}
    x=a-a.mean();variance=float(x@x/n)
    for k in range(1,lags+1):variance+=2*(1-k/(lags+1))*float(x[k:]@x[:-k]/n)
    se=math.sqrt(max(0,variance)/n)
    return {'mean':float(a.mean()),'se':se,'t':float(a.mean()/se) if se>0 else None,'n':n,
            'lags':lags,'method':'Bartlett_HAC_mean','p_value':None,'reason':None if se>0 else 'ZERO_VARIANCE'}

def evaluate_factors(processed,labels,*,groups,hac_lags,min_assets):
    """labels include entry_at/exit_at/label_available_at and never affect formation."""
    columns(processed,[*KEY,'factor','formation_at'],[*KEY,'factor'])
    columns(labels,[*KEY,'entry_at','exit_at','label_available_at'],KEY)
    if 'forward_return' not in labels or type(groups)!=int or not 2<=groups<=10 or min_assets<groups:
        fail('INVALID_ARGUMENT','Explicit group/sample requirements and forward returns are needed')
    lab=labels.set_index(KEY);ics=[];assignments=[]
    for (session,factor),g in processed.groupby(['session','factor'],sort=True):
        known=g[g.reason.isna()&g.processed_value.notna()].copy()
        if len(known)>=min_assets and known.processed_value.nunique()>1:
            ranks=known.processed_value.rank(method='average');known['group']=np.minimum(groups,1+np.floor((ranks-1)*groups/len(known))).astype(int)
        else:known['group']=None
        pairs=[]
        for r in known.to_dict('records'):
            key=(session,r['security_id']);ret=None;why=None
            if key not in lab.index:why='LABEL_MISSING'
            else:
                l=lab.loc[key];entry=timestamp(l.entry_at);exit=timestamp(l.exit_at)
                if entry<=timestamp(r['formation_at']) or exit<=entry or timestamp(l.label_available_at)<exit:
                    fail('LOOKAHEAD_RISK','Forward label has an invalid time alignment')
                if pd.isna(l.forward_return):why='LABEL_MISSING'
                else:ret=number(l.forward_return)
            assignments.append({**{k:r[k] for k in KEY},'factor':factor,'group':r['group'],'factor_value':r['processed_value'],
                                'forward_return':ret,'reason':why})
            if ret is not None:pairs.append((r['processed_value'],ret))
        valid=len(known)>=min_assets and len(pairs)==len(known) and known.processed_value.nunique()>1
        a=np.array(pairs,float)
        valid=valid and np.ptp(a[:,1])>0
        ics.append({'session':session,'factor':factor,'formed_assets':len(known),'evaluated_assets':len(pairs),
            'ic':float(np.corrcoef(a[:,0],a[:,1])[0,1]) if valid else None,
            'rank_ic':float(spearmanr(a[:,0],a[:,1]).statistic) if valid else None,
            'reason':None if valid else 'INCOMPLETE_OR_CONSTANT_CROSS_SECTION'})
    table=pd.DataFrame(ics);assignment=pd.DataFrame(assignments)
    summary={factor:hac_mean(g.rank_ic.dropna().tolist(),lags=hac_lags) for factor,g in table.groupby('factor')}
    group_returns=[];turnover=[]
    if len(assignment):
        for (session,factor,grp),g in assignment.dropna(subset=['group']).groupby(['session','factor','group'],sort=True):
            complete=g.reason.isna().all() and g.forward_return.notna().all()
            group_returns.append({'session':session,'factor':factor,'group':int(grp),'formed_count':len(g),
                'mean_forward_return':float(g.forward_return.mean()) if complete else None,
                'reason':None if complete else 'INCOMPLETE_FORWARD_GROUP'})
        for factor,fg in assignment.groupby('factor'):
            previous={}
            for session,g in fg.groupby('session',sort=True):
                for grp,gg in g.dropna(subset=['group']).groupby('group'):
                    members=set(gg.security_id);old=previous.get(grp)
                    turnover.append({'session':session,'factor':factor,'group':int(grp),
                        'membership_turnover':None if old is None else 1-len(members&old)/max(len(members),len(old)),
                        'reason':'FIRST_FORMATION' if old is None else None});previous[grp]=members
    return {'ic':table,'groups':assignment,'group_returns':pd.DataFrame(group_returns),
        'group_turnover':pd.DataFrame(turnover),'summary':summary,
        'limitations':['Descriptive forward groups are not executable trading returns','HAC asymptotic t is not an IID p-value']}
