"""3A-5: chronological splits, dependent bootstrap and explicit performance.

No selection of the best test outcome. All candidate attempts belong in an
experiment ledger; a validation set used to choose parameters is not a holdout.
"""
import math
import numpy as np
import pandas as pd
from ashare_data import columns,timestamp,number,fail
from quant_factors import validate_sessions
from finance_risk import _measure

VERSION='quant-validation-1'

def walk_forward_splits(samples,*,sessions,train_size,test_size,step,embargo,expanding):
    """samples contain actual label start/end/availability, not guessed horizons."""
    ix=validate_sessions(sessions)
    columns(samples,['sample_id','session','formation_at','label_start','label_end','label_available_at'],('sample_id',))
    if (any(type(v)!=int or v<1 for v in (train_size,test_size,step)) or type(embargo)!=int or embargo<0
        or type(expanding)!=bool or step<test_size):fail('INVALID_SPLIT','Use nonoverlapping test windows and explicit embargo')
    if not set(samples.session)<=set(sessions):fail('INVALID_CALENDAR','Sample session is outside the split calendar')
    for r in samples.to_dict('records'):
        if timestamp(r['label_start'])<=timestamp(r['formation_at']) or timestamp(r['label_end'])<=timestamp(r['label_start']) or timestamp(r['label_available_at'])<timestamp(r['label_end']):
            fail('LOOKAHEAD_RISK','Sample label interval is inconsistent')
    folds=[]
    for start in range(train_size+embargo,len(sessions)-test_size+1,step):
        test_sessions=sessions[start:start+test_size];end_train=start-embargo
        train_sessions=sessions[0 if expanding else end_train-train_size:end_train]
        test=samples[samples.session.isin(test_sessions)]
        if test.empty:continue
        # Fit must finish strictly before the first test formation.
        cutoff=min(timestamp(t) for t in test.formation_at)
        train=samples[samples.session.isin(train_sessions)];kept=[];purged=[]
        first_label=min(timestamp(v) for v in test.label_start);last_label=max(timestamp(v) for v in test.label_end)
        for r in train.to_dict('records'):
            overlap=timestamp(r['label_start'])<=last_label and timestamp(r['label_end'])>=first_label
            if timestamp(r['label_available_at'])>=cutoff or overlap:
                purged.append({'sample_id':r['sample_id'],'reason':'IMMATURE_OR_OVERLAPPING_LABEL'})
            else:kept.append(r['sample_id'])
        if not kept:fail('SAMPLE_TOO_SMALL','Purge removed every training observation')
        folds.append({'fold':len(folds),'train_ids':kept,'test_ids':test.sample_id.tolist(),
            'fit_before':cutoff.isoformat(),'train_start':train_sessions[0],'train_end':train_sessions[-1],
            'test_start':test_sessions[0],'test_end':test_sessions[-1],'purged':purged,'embargo_sessions':embargo})
    if not folds:fail('SAMPLE_TOO_SMALL','No complete walk-forward fold')
    return folds

def block_bootstrap(values,*,block_length,draws,seed,confidence):
    a=np.asarray(values,float);n=len(a)
    if a.ndim!=1 or not np.isfinite(a).all() or n<3:fail('INVALID_SAMPLE','Bootstrap requires a finite vector')
    if (type(block_length)!=int or not 1<=block_length<=n or type(draws)!=int or not 100<=draws<=10000
        or type(seed)!=int or not 0<=seed<2**32 or not .5<number(confidence)<1):fail('INVALID_ARGUMENT','Bootstrap parameters are invalid')
    rng=np.random.default_rng(seed);stats=[];blocks=math.ceil(n/block_length)
    for _ in range(draws):
        starts=rng.integers(0,n-block_length+1,size=blocks)
        sample=np.concatenate([a[s:s+block_length] for s in starts])[:n];stats.append(float(sample.mean()))
    alpha=(1-confidence)/2
    return {'mean':float(a.mean()),'confidence_interval':np.quantile(stats,[alpha,1-alpha]).tolist(),
        'method':'moving_block_percentile_mean','block_length':block_length,'draws':draws,'seed':seed,
        'limitations':['Block choice must reflect dependence; this interval does not correct strategy selection bias']}

def performance(nav,*,initial_equity,periods_per_year,risk_free_annual,benchmark=None,fills=None):
    columns(nav,['session','equity'],('session',));g=nav.sort_values('session')
    initial=number(initial_equity,positive=True);equity=g.equity.map(lambda v:number(v,nonnegative=True)).to_numpy(float)
    chain=np.r_[initial,equity]
    if (chain[:-1]<=0).any():fail('WEALTH_ZERO','Post-ruin returns need an explicit absorbing model')
    r=chain[1:]/chain[:-1]-1;annual=number(periods_per_year,positive=True);rf=number(risk_free_annual)
    if not -1<rf<=1:fail('INVALID_ARGUMENT','Risk-free annual effective rate is invalid')
    metrics=['max_drawdown'];unavailable={}
    if len(r)>=2:metrics+=['volatility','sharpe','sortino','calmar']
    else:unavailable.update({name:'FEWER_THAN_TWO_RETURN_OBSERVATIONS' for name in ('volatility','sharpe','sortino','calmar')})
    if len(r)>=20:metrics+=['var','es']
    else:unavailable.update({name:'FEWER_THAN_TWENTY_RETURN_OBSERVATIONS' for name in ('var','es')})
    facts,trajectory,warnings=_measure(r,[pd.Timestamp(v) for v in g.session],metrics=metrics,frequency='daily',A=annual,alpha_loss=.95,
        risk_free={'value':rf,'basis':'annual_effective'} if 'sharpe' in metrics else None,
        mar={'value':rf,'basis':'annual_effective'} if 'sortino' in metrics else None,baseline_date=None)
    facts['unavailable'].update(unavailable)
    trajectory['date']=trajectory['date'].map(lambda v:v.isoformat())
    facts['initial_equity']=initial;facts['final_equity']=float(equity[-1]);facts['total_return']=float(equity[-1]/initial-1)
    if fills is not None and len(fills):
        columns(fills,['session','notional','fees','slippage_cost'])
        traded=fills.groupby('session').notional.sum().reindex(g.session,fill_value=0).to_numpy(float)
        facts['turnover']={'one_way_traded_notional_over_prior_equity':(traded/chain[:-1]).tolist(),
                           'total_fees':float(fills.fees.sum()),'total_slippage_cost':float(fills.slippage_cost.sum())}
    if benchmark is not None:
        columns(benchmark,['session','simple_return'],('session',))
        if set(benchmark.session)!=set(g.session):fail('BENCHMARK_MISALIGNED','Benchmark must cover every portfolio session exactly')
        b=benchmark.set_index('session').loc[g.session].simple_return.map(number).to_numpy(float)
        if (b<-1).any():fail('INVALID_RETURN','Benchmark simple return below -100%')
        excess=r-b;sd=float(np.std(excess,ddof=1)) if len(r)>1 else 0.
        market_var=float(np.var(b,ddof=1)) if len(b)>1 else 0.
        beta=float(np.cov(r,b,ddof=1)[0,1]/market_var) if market_var>0 else None
        facts['benchmark']={'cumulative_return':float(np.prod(1+b)-1),
            'cumulative_wealth_difference':float(np.prod(1+r)-np.prod(1+b)),
            'tracking_error_annualized':sd*math.sqrt(annual),
            'information_ratio':float(np.mean(excess)/sd*math.sqrt(annual)) if sd>0 else None,
            'beta':beta,'arithmetic_alpha_annualized':float((np.mean(r)-(1+rf)**(1/annual)+1
                 -(beta or 0)*(np.mean(b)-(1+rf)**(1/annual)+1))*annual) if beta is not None else None,
            'return_basis':'caller_must_document_price_or_total_return'}
    return {'metrics':facts,'trajectory':trajectory,'warnings':warnings}

def compare_runs(results,*,selection_role):
    if selection_role not in ('training','validation','final_holdout'):fail('INVALID_ARGUMENT','Declare the purpose of comparison')
    if not isinstance(results,list) or not results or len(results)>100:fail('RESOURCE_LIMIT','Comparison count out of bounds')
    required={'run_id','parameters_hash','data_hash','status','metric','failure_reason'}
    if any(not isinstance(r,dict) or set(r)!=required for r in results):fail('INVALID_ARGUMENT','All attempts, including failures, require records')
    if len({r['run_id'] for r in results})!=len(results):fail('DUPLICATE_KEY','Repeated run IDs')
    return {'attempts':results,'attempt_count':len(results),'selection_role':selection_role,
            'best_run_id':None,'limitations':['No automatic winner is chosen from test performance',
              'Any parameter selected using final holdout invalidates its untouched-holdout claim']}

def walk_forward_fit_score(processed,labels,samples,*,sessions,split_options,combiner_options):
    """Fit frozen models per fold, score only held-out observations, retain all folds."""
    from quant_portfolio import fit_combiner,score_combiner
    columns(samples,['sample_id','session','security_id','formation_at','label_start','label_end','label_available_at'],('sample_id',))
    if samples.duplicated(['session','security_id']).any():fail('DUPLICATE_KEY','Sample identities must map uniquely to formation rows')
    folds=walk_forward_splits(samples,sessions=sessions,**split_options);models=[];scores=[];diagnostics=[]
    indexed=samples.set_index('sample_id');lab=labels.set_index(['session','security_id'])
    for fold in folds:
        training=indexed.loc[fold['train_ids']];test=indexed.loc[fold['test_ids']]
        train_keys=set(zip(training.session,training.security_id));test_keys=set(zip(test.session,test.security_id))
        mask=processed.apply(lambda r:(r.session,r.security_id) in train_keys,axis=1)
        fit_at=(timestamp(fold['fit_before'])-pd.Timedelta(microseconds=1)).isoformat()
        model=fit_combiner(processed.loc[mask],labels,names=combiner_options['names'],method=combiner_options['method'],
            fit_at=fit_at,training_start=str(training.session.min()),training_end=str(training.session.max()),
            ridge_alpha=combiner_options['ridge_alpha'])
        subset=processed[processed.apply(lambda r:(r.session,r.security_id) in test_keys,axis=1)]
        out=score_combiner(subset,model);out['fold']=fold['fold'];scores.append(out);models.append(model)
        errors=[]
        for r in out[out.reason.isna()].to_dict('records'):
            key=(r['session'],r['security_id'])
            if key in lab.index and pd.notna(lab.loc[key].forward_return):errors.append((r['score']-number(lab.loc[key].forward_return))**2)
        diagnostics.append({'fold':fold['fold'],'scored_rows':int(out.reason.isna().sum()),
            'evaluated_rows':len(errors),'mse':float(np.mean(errors)) if errors else None,
            'metric_interpretation':'Prediction error is meaningful for ridge return forecasts, not equal-weight factor score scale'})
    return {'scores':pd.concat(scores,ignore_index=True),'models':models,'folds':folds,'diagnostics':diagnostics,
        'limitations':['No automatic parameter winner; final holdout remains a separate user-defined interval',
          'All preprocessing fitted on pooled data must be inside the fold; current factors use causal cross-sections']}
