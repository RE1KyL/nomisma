"""Historical, decimal-return risk. No fitted distributions or implicit annualization."""
import math
import numpy as np
import pandas as pd
from finance_common import (load_panel,numeric,simple_returns,finite,conventions,finish,
                            require_finance,validate_finance)
from dataset_store import DatasetError

METRICS={'volatility','max_drawdown','var','es','sharpe','sortino','calmar'}


def _rate(spec,A,label):
    if (not isinstance(spec,dict) or set(spec)!={'basis','value'} or spec['basis']!='annual_effective'
            or isinstance(spec['value'],bool) or not isinstance(spec['value'],(int,float))
            or not math.isfinite(spec['value']) or not -1<spec['value']<=1):
        raise DatasetError('INVALID_FINANCIAL_CONVENTION',label+' requires an explicit annual-effective decimal rate in (-1,1]')
    return finite(math.expm1(math.log1p(spec['value'])/A))


def _spec(metrics,frequency,A,alpha_loss,risk_free,mar):
    if not isinstance(metrics,list) or not metrics or any(not isinstance(v,str) or v not in METRICS for v in metrics) or len(set(metrics))!=len(metrics):
        raise DatasetError('INVALID_ARGUMENT','Risk metrics must be a fixed unique list')
    c=conventions(frequency,A,annual=bool(set(metrics)&{'volatility','sharpe','sortino','calmar'}))
    if isinstance(alpha_loss,bool) or not isinstance(alpha_loss,(int,float)) or not .5<=alpha_loss<1:
        raise DatasetError('INVALID_FINANCIAL_CONVENTION','Historical loss confidence must be in [0.5,1)')
    rf=_rate(risk_free,A,'risk_free') if 'sharpe' in metrics else None
    target=_rate(mar,A,'MAR') if 'sortino' in metrics else None
    if 'sharpe' not in metrics and risk_free is not None or 'sortino' not in metrics and mar is not None:
        raise DatasetError('INVALID_ARGUMENT','Rate parameters must correspond to requested metrics')
    return c,rf,target


def _measure(returns,dates,*,metrics,frequency,A,alpha_loss,risk_free,mar,baseline_date=None):
    c,rf,target=_spec(metrics,frequency,A,alpha_loss,risk_free,mar)
    r=np.asarray(returns,dtype=float);n=len(r)
    if n==0:raise DatasetError('SAMPLE_TOO_SMALL','Risk requires at least one complete return interval')
    if not np.isfinite(r).all() or (r<-1).any():raise DatasetError('INVALID_RETURN','Risk requires finite simple returns not below -1')
    if set(metrics)&{'volatility','sharpe'} and n<2:raise DatasetError('SAMPLE_TOO_SMALL','Sample volatility requires at least two returns')
    if set(metrics)&{'var','es'} and n<20:raise DatasetError('SAMPLE_TOO_SMALL','Historical tail metrics require at least twenty returns')
    ruin=np.flatnonzero(r==-1)
    if len(ruin) and np.any(r[ruin[0]+1:]!=0):raise DatasetError('INVALID_RETURN_AFTER_RUIN','Non-zero returns after total loss need a different economic model')
    notes=['ANNUAL_SCALING_ASSUMPTIONS_NOT_VERIFIED'] if A is not None else []
    facts={};unavailable={}
    try:
        with np.errstate(over='raise',invalid='raise',divide='raise',under='ignore'):
            wealth=np.cumprod(1+r)
            if np.any((wealth==0)&(np.cumsum(r==-1)==0)):
                raise DatasetError('NUMERICAL_FAILURE','Wealth underflowed without an actual total loss')
            if not np.isfinite(wealth).all():raise DatasetError('NUMERICAL_FAILURE','Wealth overflowed')
            full=np.r_[1.,wealth];peak=np.maximum.accumulate(full);drawdown=1-full/peak
            trough=int(np.argmax(drawdown));mdd=finite(drawdown[trough])
            peak_index=int(np.argmax(full[:trough+1]));recovery=None
            if mdd>0:
                later=np.flatnonzero(full[trough+1:]>=full[peak_index]*(1-8*np.finfo(float).eps))
                if len(later):recovery=trough+1+int(later[0])
            def at(i):return baseline_date if i==0 else dates[i-1].isoformat()
            facts.update(n=n,cumulative_return=finite(wealth[-1]-1),wealth_end=finite(wealth[-1]),
                return_unit='decimal_simple_return',sample_start=dates[0].isoformat(),sample_end=dates[-1].isoformat())
            if 'max_drawdown' in metrics:
                facts['max_drawdown']={'loss':mdd,'includes_initial_wealth':True,
                    'peak_date':at(peak_index) if mdd else None,'peak_observation':peak_index-1 if mdd else None,
                    'trough_date':at(trough) if mdd else None,'trough_observation':trough-1 if mdd else None,
                    'recovery_date':at(recovery) if recovery is not None else None,
                    'recovered':recovery is not None if mdd else None,
                    'peak_to_trough_observations':trough-peak_index if mdd else 0,
                    'peak_to_recovery_observations':recovery-peak_index if recovery is not None else None}
            if 'volatility' in metrics:
                sd=0. if np.ptp(r)==0 else finite(np.std(r,ddof=1));facts['volatility']={'period':sd,'annualized':finite(sd*math.sqrt(A)),'ddof':1}
            if set(metrics)&{'var','es'}:
                losses=-r;tail=n*(1-alpha_loss)
                if round(tail)>0 and abs(tail-round(tail))<=1e-12:tail=float(round(tail))
                worst=np.sort(losses)[::-1];whole=int(math.floor(tail));fraction=tail-whole
                expected=finite((np.sum(worst[:whole])+(fraction*worst[whole] if fraction else 0))/tail)
                value=finite(np.quantile(losses,alpha_loss,method='linear'))
                if 'var' in metrics:facts['var']={'loss':value,'confidence':alpha_loss,'method':'historical_linear','horizon':'one_observation'}
                if 'es' in metrics:facts['es']={'loss':expected,'confidence':alpha_loss,'tail_mass':tail,'whole_observations':whole,
                    'fractional_boundary_weight':fraction,'method':'worst_empirical_probability_mass','horizon':'one_observation'}
                if tail<5:notes.append('FEWER_THAN_FIVE_EFFECTIVE_TAIL_OBSERVATIONS')
            if 'sharpe' in metrics:
                excess=r-rf;sd=0. if np.ptp(excess)==0 else finite(np.std(excess,ddof=1))
                facts['sharpe']={'value':finite(math.sqrt(A)*np.mean(excess)/sd) if sd>0 else None,
                    'risk_free_per_period':rf,'risk_free':risk_free,'ddof':1}
                if sd==0:unavailable['sharpe']='ZERO_EXCESS_STANDARD_DEVIATION'
            if 'sortino' in metrics:
                excess=r-target;down=np.minimum(excess,0);denom=finite(np.sqrt(np.mean(down**2)))
                facts['sortino']={'value':finite(math.sqrt(A)*np.mean(excess)/denom) if denom>0 else None,
                    'MAR_per_period':target,'MAR':mar,'downside_period_deviation':denom,'denominator_observations':n,
                    'downside_observations':int((excess<0).sum())}
                if denom==0:unavailable['sortino']='NO_DOWNSIDE_DEVIATION'
                if (excess<0).sum()<5:notes.append('FEWER_THAN_FIVE_DOWNSIDE_OBSERVATIONS')
            if 'calmar' in metrics:
                cagr=None if len(ruin) else finite(np.expm1(A/n*np.sum(np.log1p(r))))
                facts['calmar']={'value':finite(cagr/mdd) if cagr is not None and mdd>0 else None,'CAGR':cagr,
                    'years_from_period_count':n/A,'annualization':'period_count_not_elapsed_calendar_days'}
                if len(ruin):unavailable['calmar']='WEALTH_ZERO'
                elif mdd==0:unavailable['calmar']='NO_DRAWDOWN'
                if n<A:notes.append('LESS_THAN_ONE_DECLARED_YEAR')
    except (FloatingPointError,OverflowError,ValueError) as error:raise DatasetError('NUMERICAL_FAILURE','Risk calculation exceeded its numeric domain') from error
    if len(ruin):notes.append('TOTAL_LOSS; ZERO_WEALTH_IS_ABSORBING')
    facts.update(unavailable=unavailable,conventions=c)
    trajectory=pd.DataFrame({'date':dates,'simple_return':r,'wealth':wealth,'drawdown':drawdown[1:]})
    return facts,trajectory,list(dict.fromkeys(notes))


def analyze_risk(store,dataset_id,*,expected_sha256,date_column,return_column,return_kind,frequency,metrics,
                 periods_per_year=None,asset_column=None,alpha_loss=.95,risk_free=None,mar=None):
    require_finance(store);_spec(metrics,frequency,periods_per_year,alpha_loss,risk_free,mar)
    sample,groups=load_panel(store,dataset_id,expected_sha256,date_column=date_column,value_columns=[return_column],asset_column=asset_column)
    facts=[];tables=[];notes=[]
    for asset,frame in groups:
        values=simple_returns(numeric(frame,return_column),return_kind)
        row,table,warnings=_measure(values,frame[date_column].tolist(),metrics=metrics,frequency=frequency,A=periods_per_year,
            alpha_loss=alpha_loss,risk_free=risk_free,mar=mar)
        facts.append({'asset':asset,**row});table['asset']=asset;tables.append(table);notes.extend(warnings)
    parameters={'date_column':date_column,'return_column':return_column,'return_kind':return_kind,'asset_column':asset_column,
        'frequency':frequency,'periods_per_year':periods_per_year,'metrics':metrics,'alpha_loss':alpha_loss,
        'risk_free':risk_free,'MAR':mar,'missing_policy':'reject'}
    return finish(sample,parameters,{'method':'fin_risk','assets':facts,'warnings':list(dict.fromkeys(notes))},
                  {'wealth':pd.concat(tables,ignore_index=True)})


def risk_from_returns(store,analysis,*,metrics,alpha_loss=.95,risk_free=None,mar=None):
    validate_finance(store,analysis)
    if analysis.computation['method']!='fin_returns':raise DatasetError('ANALYSIS_INVALID','A verified price-return analysis is required')
    p=analysis.parameters;_spec(metrics,p['frequency'],p['periods_per_year'],alpha_loss,risk_free,mar)
    facts=[];tables=[];notes=[]
    for asset,frame in analysis.tables['returns'].groupby('asset',sort=False):
        if frame.reason.iloc[0]!='FIRST_PRICE' or frame.iloc[1:].reason.notna().any():
            raise DatasetError('INCOMPLETE_RETURN_CHAIN','Risk does not concatenate intervals across missing prices')
        values=frame.simple_return.iloc[1:].to_numpy(dtype=float)
        row,table,warnings=_measure(values,frame.date.iloc[1:].tolist(),metrics=metrics,frequency=p['frequency'],A=p['periods_per_year'],
            alpha_loss=alpha_loss,risk_free=risk_free,mar=mar,baseline_date=frame.date.iloc[0].isoformat())
        facts.append({'asset':asset,**row});table['asset']=asset;tables.append(table);notes.extend(warnings)
    parameters={'source_returns_seal':analysis.seal,'metrics':metrics,'alpha_loss':alpha_loss,'risk_free':risk_free,'MAR':mar,
                'frequency':p['frequency'],'periods_per_year':p['periods_per_year'],'return_kind':'simple',
                'missing_policy':'structural_first_price_only_excluded','price_basis':analysis.computation['return_basis']}
    return finish(analysis.sample,parameters,{'method':'fin_risk','assets':facts,'warnings':list(dict.fromkeys([
        *notes,*analysis.computation['warnings']]))},{'wealth':pd.concat(tables,ignore_index=True)})
