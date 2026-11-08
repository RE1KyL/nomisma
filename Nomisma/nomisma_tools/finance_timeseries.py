"""Trailing-only summaries and exact-date aligned return correlations."""
from dataclasses import replace
import hashlib
import numpy as np
import pandas as pd
from finance_common import load_panel,numeric,simple_returns,finite,conventions,finish
from analysis_sample import AnalysisSample
from analysis_statistics import describe,correlate
from dataset_store import DatasetError,MAX_CELLS


def _window(window,min_periods):
    if type(window)!=int or not 2<=window<=252 or type(min_periods)!=int or not 1<=min_periods<=window:
        raise DatasetError('INVALID_ARGUMENT','Trailing windows require 2..252 observations and explicit min_periods')


def _valid_kind(values,kind):
    present=values[~np.isnan(values)]
    if kind=='price' and np.any(present<=0):raise DatasetError('INVALID_PRICE','Observed prices must be positive')
    if kind=='simple_return' and np.any(present<-1):raise DatasetError('INVALID_RETURN','Simple returns cannot be below -1')


def rolling_statistics(store,dataset_id,*,expected_sha256,date_column,value_column,frequency,value_kind,
                       window,min_periods,statistic,asset_column=None,other_column=None,quantile=None,
                       periods_per_year=None,missing_policy='reject'):
    _window(window,min_periods);c=conventions(frequency,periods_per_year)
    if (statistic not in ('mean','std','quantile','correlation') or missing_policy not in ('reject','preserve')
            or value_kind not in ('price','simple_return','log_return','measurement')
            or statistic in ('std','correlation') and min_periods<2
            or statistic=='correlation' and (not isinstance(other_column,str) or other_column==value_column)
            or statistic!='correlation' and other_column is not None
            or statistic=='quantile' and (isinstance(quantile,bool) or not isinstance(quantile,(int,float)) or not 0<=quantile<=1)
            or statistic!='quantile' and quantile is not None):
        raise DatasetError('INVALID_ARGUMENT','Unsupported trailing statistic or cross-field parameters')
    sample,groups=load_panel(store,dataset_id,expected_sha256,date_column=date_column,
        value_columns=[value_column,*([other_column] if other_column else [])],asset_column=asset_column)
    tables=[];counts=[]
    try:
        for asset,frame in groups:
            raw=numeric(frame,value_column,allow_missing=missing_policy=='preserve');_valid_kind(raw,value_kind);a=pd.Series(raw)
            rolling=a.rolling(window=window,min_periods=min_periods,center=False)
            if statistic=='mean':values=rolling.mean()
            elif statistic=='std':values=rolling.std(ddof=1)
            elif statistic=='quantile':values=rolling.quantile(quantile,interpolation='linear')
            else:
                b=pd.Series(numeric(frame,other_column,allow_missing=missing_policy=='preserve'))
                _valid_kind(b.to_numpy(),value_kind)
                values=rolling.corr(b,ddof=1)
            valid=(a.notna() & b.notna()) if statistic=='correlation' else a.notna()
            n=valid.astype(int).rolling(window,min_periods=1).sum().astype(int)
            if np.isinf(values.to_numpy()).any():raise DatasetError('NUMERICAL_FAILURE','Rolling statistic overflowed')
            for i,(value,count) in enumerate(zip(values,n)):
                if pd.isna(value) and count>=min_periods:
                    if statistic!='correlation':raise DatasetError('NUMERICAL_FAILURE','A valid rolling window unexpectedly returned NaN')
                    first=max(0,i-window+1);left=a.iloc[first:i+1];right=b.iloc[first:i+1];paired=left.notna()&right.notna()
                    if np.ptp(left[paired])!=0 and np.ptp(right[paired])!=0:
                        raise DatasetError('NUMERICAL_FAILURE','A varying correlation window unexpectedly returned NaN')
            if statistic=='correlation':
                if (values.dropna().abs()>1+1e-10).any():raise DatasetError('NUMERICAL_FAILURE','Rolling correlation exceeded its numeric range')
                values=values.clip(-1,1)
            reasons=['INSUFFICIENT_WINDOW' if count<min_periods else 'CONSTANT_WINDOW' if pd.isna(v) else None for v,count in zip(values,n)]
            tables.append(pd.DataFrame({'source_row':list(frame.index),'date':frame[date_column].tolist(),'asset':asset,
                'value':values,'valid_window_count':n,'reason':reasons}))
            counts.append({'asset':asset,'observations':len(frame),'unavailable_count':int(values.isna().sum())})
    except (FloatingPointError,OverflowError,ValueError) as error:raise DatasetError('NUMERICAL_FAILURE','Rolling computation failed') from error
    parameters={'date_column':date_column,'value_column':value_column,'asset_column':asset_column,'value_kind':value_kind,
        'other_column':other_column,'window':window,'min_periods':min_periods,'statistic':statistic,'quantile':quantile,
        'missing_policy':missing_policy,'center':False,'ddof':1,**c}
    return finish(sample,parameters,{'method':'fin_timeseries','action':'rolling','assets':counts,
        'warnings':['WINDOW_IS_OBSERVATIONS_NOT_CALENDAR_DAYS']},{'timeseries':pd.concat(tables,ignore_index=True)})


def moving_average(store,dataset_id,*,expected_sha256,date_column,value_column,frequency,value_kind,
                   window,min_periods,kind,adjust=None,asset_column=None,periods_per_year=None):
    _window(window,min_periods);c=conventions(frequency,periods_per_year)
    if kind not in ('sma','ema') or kind=='ema' and not isinstance(adjust,bool) or kind=='sma' and adjust is not None:
        raise DatasetError('INVALID_ARGUMENT','EMA requires explicit adjust; SMA does not accept it')
    if value_kind not in ('price','simple_return','log_return','measurement'):raise DatasetError('INVALID_ARGUMENT','An explicit value kind is required')
    sample,groups=load_panel(store,dataset_id,expected_sha256,date_column=date_column,value_columns=[value_column],asset_column=asset_column)
    tables=[]
    for asset,frame in groups:
        raw=numeric(frame,value_column);_valid_kind(raw,value_kind);a=pd.Series(raw)
        values=(a.rolling(window,min_periods=min_periods,center=False).mean() if kind=='sma' else
                a.ewm(span=window,min_periods=min_periods,adjust=adjust,ignore_na=False).mean())
        if np.isinf(values.to_numpy()).any() or values.iloc[min_periods-1:].isna().any():
            raise DatasetError('NUMERICAL_FAILURE','Moving average overflowed or produced an unexpected NaN')
        tables.append(pd.DataFrame({'source_row':list(frame.index),'date':frame[date_column].tolist(),'asset':asset,
            'value':values,'reason':['INSUFFICIENT_WARMUP' if pd.isna(v) else None for v in values]}))
    return finish(sample,{'date_column':date_column,'value_column':value_column,'asset_column':asset_column,
        'value_kind':value_kind,'kind':kind,'window':window,'min_periods':min_periods,'adjust':adjust,
        'ignore_na':False,'missing_policy':'reject',**c},
        {'method':'fin_timeseries','action':'moving_average','warnings':['TRAILING_OBSERVATION_WINDOW; NO_FUTURE_VALUES']},
        {'timeseries':pd.concat(tables,ignore_index=True)})


def return_distribution(store,dataset_id,*,expected_sha256,date_column,return_column,return_kind,frequency,
                        bins,asset_column=None,periods_per_year=None):
    c=conventions(frequency,periods_per_year)
    if type(bins)!=int or not 2<=bins<=100:raise DatasetError('INVALID_ARGUMENT','Histogram bins must be 2..100')
    sample,groups=load_panel(store,dataset_id,expected_sha256,date_column=date_column,value_columns=[return_column],asset_column=asset_column)
    results=[];tables=[]
    for asset,frame in groups:
        values=simple_returns(numeric(frame,return_column),return_kind)
        sub=replace(sample,frame=pd.DataFrame({'simple_return':values}))
        statistics=describe(sub,['simple_return'])['columns'][0]
        try:counts,edges=np.histogram(values,bins=bins)
        except (ValueError,OverflowError,FloatingPointError) as error:raise DatasetError('NUMERICAL_FAILURE','Histogram domain is not representable') from error
        if not np.isfinite(edges).all():raise DatasetError('NUMERICAL_FAILURE','Histogram edges are not finite')
        results.append({'asset':asset,'statistics':statistics,'histogram_observations':int(counts.sum())})
        tables.extend({'asset':asset,'left':finite(edges[i]),'right':finite(edges[i+1]),'count':int(counts[i]),
            'right_closed':i==len(counts)-1} for i in range(len(counts)))
    return finish(sample,{'date_column':date_column,'return_column':return_column,'return_kind':return_kind,
        'asset_column':asset_column,'bins':bins,'histogram_range':'observed_min_max','missing_policy':'reject',**c},
        {'method':'fin_timeseries','action':'distribution','assets':results,'warnings':['DESCRIPTIVE_DISTRIBUTION; NOT_A_NORMALITY_ASSUMPTION']},
        {'histogram':pd.DataFrame(tables)})


def asset_correlations(store,dataset_id,*,expected_sha256,date_column,asset_column,return_column,return_kind,
                       frequency,assets,method='pearson',missing_policy='reject',periods_per_year=None):
    c=conventions(frequency,periods_per_year)
    if (not isinstance(assets,list) or not 2<=len(assets)<=20 or any(not isinstance(v,str) for v in assets)
            or len(set(assets))!=len(assets) or missing_policy not in ('reject','listwise','pairwise')
            or method not in ('pearson','spearman')):raise DatasetError('INVALID_ARGUMENT','Select 2..20 unique assets and an explicit correlation policy')
    sample,_=load_panel(store,dataset_id,expected_sha256,date_column=date_column,value_columns=[return_column],asset_column=asset_column)
    frame=sample.frame.copy();observed=set(frame[asset_column])
    if not set(assets)<=observed:raise DatasetError('INVALID_ASSET','A requested asset was not observed')
    frame[return_column]=simple_returns(numeric(frame,return_column,allow_missing=missing_policy!='reject'),return_kind)
    chosen=frame[frame[asset_column].isin(assets)]
    wide=chosen.pivot(index=date_column,columns=asset_column,values=return_column).reindex(columns=assets)
    if wide.size+len(wide)>MAX_CELLS:raise DatasetError('RESOURCE_LIMIT','Aligned return panel exceeds the cell budget')
    anchor=pd.Series(chosen.index.to_numpy(),index=pd.DatetimeIndex(chosen[date_column])).groupby(level=0).min()
    positions=tuple(int(anchor.loc[d]) for d in wide.index)
    aligned=AnalysisSample(wide.reset_index(drop=True),sample.metadata,positions,{'date':'aligned_date'},
        {'actual_sample_size':len(wide),'alignment':'exact_date','original_rows':len(frame)})
    result=correlate(aligned,assets,method=method,missing_policy=missing_policy,inference='none')
    for pair in result['pairs']:
        mask=wide[pair['left']].notna()&wide[pair['right']].notna()
        if missing_policy=='listwise':mask=wide.notna().all(axis=1)
        dates=wide.index[mask]
        pair['dates_sha256']=hashlib.sha256(dates.asi8.tobytes()).hexdigest()
        pair['start']=dates[0].isoformat() if len(dates) else None;pair['end']=dates[-1].isoformat() if len(dates) else None
    matrix=pd.DataFrame(result['matrix'],columns=['r'+str(i) for i in range(len(assets))]);matrix.insert(0,'asset',assets)
    counts=pd.DataFrame(result['sample_counts'],columns=['n'+str(i) for i in range(len(assets))]);counts.insert(0,'asset',assets)
    presentation=wide.copy();presentation.columns=['asset_'+str(i) for i in range(len(assets))];presentation.insert(0,'date',wide.index)
    return finish(sample,{'date_column':date_column,'asset_column':asset_column,'return_column':return_column,
        'return_kind':return_kind,'assets':assets,'method':method,'missing_policy':missing_policy,**c},
        {'method':'fin_timeseries','action':'asset_correlation','correlation':result,'column_order':assets,
         'warnings':[*result['warnings'],'TIME_SERIES_IID_P_VALUES_NOT_PROVIDED']},
        {'correlations':matrix,'correlation_counts':counts,'aligned_returns':presentation.reset_index(drop=True)})
