"""Declared-availability cross-sectional IC and descriptive forward groups, not a backtest."""
import math,warnings
import numpy as np
import pandas as pd
from scipy.stats import pearsonr,spearmanr,rankdata
from data_quality import parse_dates
from finance_common import load_panel,numeric,finite,conventions,finish,balanced_panel
from dataset_store import DatasetError,MAX_ROWS


def analyze_factor(store,dataset_id,*,expected_sha256,date_column,asset_column,factor_column,price_column,
                   available_at_column,assets,universe_status,frequency,price_adjustment,
                   entry_lag,horizon,groups,ties='keep_equal',periods_per_year=None):
    c=conventions(frequency,periods_per_year)
    if (universe_status!='declared_ex_ante' or type(entry_lag)!=int or not 1<=entry_lag<=20):
        raise DatasetError('LOOKAHEAD_RISK','An ex-ante universe and strictly future entry observation are required')
    if (type(horizon)!=int or not 1<=horizon<=20 or type(groups)!=int or not 2<=groups<=10
            or ties not in ('keep_equal','reject') or price_adjustment not in ('unknown','unadjusted','forward_adjusted','backward_adjusted','index_level')
            or len({factor_column,price_column,available_at_column})!=3):
        raise DatasetError('INVALID_ARGUMENT','Factor evaluation requires bounded horizon/groups and distinct fields')
    sample,_=load_panel(store,dataset_id,expected_sha256,date_column=date_column,
        value_columns=[factor_column,price_column,available_at_column],asset_column=asset_column)
    calendar,panel=balanced_panel(sample,date_column,asset_column,assets)
    if len(assets)<5 or groups>len(assets):raise DatasetError('SAMPLE_TOO_SMALL','Cross-sectional evaluation requires at least five assets and enough assets for groups')
    if len(calendar)*groups>MAX_ROWS:raise DatasetError('RESOURCE_LIMIT','Group evaluation exceeds its row budget')
    factors={};prices={};available={}
    for asset,frame in panel.items():
        factors[asset]=numeric(frame,factor_column,allow_missing=True);prices[asset]=numeric(frame,price_column,allow_missing=True)
        if np.any(prices[asset][~np.isnan(prices[asset])]<=0):raise DatasetError('INVALID_PRICE','Factor evaluation requires positive observed prices')
        parsed=parse_dates(frame[available_at_column])
        if (frame[available_at_column].notna()&parsed.isna()).any():raise DatasetError('INVALID_DATE','Factor availability dates are malformed')
        available[asset]=parsed.tolist()
    formations=[];evaluations=[];group_rows=[];numerical_warnings=[]
    for t,date in enumerate(calendar):
        current=[]
        for asset in assets:
            factor=factors[asset][t];av=available[asset][t]
            reason=('FACTOR_MISSING' if np.isnan(factor) else 'AVAILABILITY_UNKNOWN' if pd.isna(av)
                    else 'NOT_AVAILABLE_AT_FORMATION' if av>date else None)
            current.append({'source_row':int(panel[asset].index[t]),'date':date,'asset':asset,'factor':factor,
                'available_at':av,'eligibility_reason':reason,'group':None,'entry_date':None,'exit_date':None,
                'forward_return':np.nan,'forward_reason':None})
        eligible=[row for row in current if row['eligibility_reason'] is None]
        factor_values=np.array([row['factor'] for row in eligible]);formation_reason=None
        if len(eligible)<5:formation_reason='INSUFFICIENT_FORMATION_ASSETS'
        elif np.ptp(factor_values)==0:formation_reason='CONSTANT_FACTOR'
        elif ties=='reject' and len(np.unique(factor_values))!=len(factor_values):formation_reason='FACTOR_TIES_REJECTED'
        if formation_reason is None:
            # Group assignment uses only known factor values at t, before looking at outcomes.
            ranks=rankdata(factor_values,method='average')
            for row,rank in zip(eligible,ranks):row['group']=min(groups,1+int(math.floor((rank-1)*groups/len(eligible))))
        entry=t+entry_lag;exit=entry+horizon
        for row in current:
            if exit>=len(calendar):row['forward_reason']='NO_FORWARD_WINDOW';continue
            row['entry_date']=calendar[entry];row['exit_date']=calendar[exit]
            start,end=prices[row['asset']][entry],prices[row['asset']][exit]
            if np.isnan(start) or np.isnan(end):row['forward_reason']='FORWARD_PRICE_MISSING';continue
            try:
                with np.errstate(over='raise',invalid='raise',divide='raise'):
                    value=finite(end/start-1)
                    if value<=-1:raise DatasetError('NUMERICAL_FAILURE','Positive forward prices underflowed')
                    row['forward_return']=value
            except FloatingPointError as error:raise DatasetError('NUMERICAL_FAILURE','Forward evaluation overflowed') from error
        observed=[row for row in eligible if np.isfinite(row['forward_return'])]
        reason=formation_reason;ic=rank_ic=None
        if reason is None and len(observed)<5:reason='INSUFFICIENT_FORWARD_ASSETS'
        if reason is None and len(observed)!=len(eligible):reason='INCOMPLETE_FORWARD_CROSS_SECTION'
        if reason is None:
            a=np.array([r['factor'] for r in observed]);b=np.array([r['forward_return'] for r in observed])
            if np.ptp(a)==0 or np.ptp(b)==0:reason='CONSTANT_EVALUATION_INPUT'
            else:
                with warnings.catch_warnings(record=True) as emitted:
                    ic=finite(pearsonr(a,b).statistic);rank_ic=finite(spearmanr(a,b).statistic)
                numerical_warnings.extend(type(w.message).__name__ for w in emitted)
        evaluations.append({'date':date,'formation_assets':len(eligible),'evaluated_assets':len(observed),
            'ic':ic,'rank_ic':rank_ic,'reason':reason,'p_value':None,'p_value_reason':'DEPENDENT_TIME_SERIES_INFERENCE_NOT_IMPLEMENTED'})
        for group in range(1,groups+1):
            assigned=[r for r in eligible if r['group']==group];observed_group=[r for r in assigned if np.isfinite(r['forward_return'])]
            group_reason=formation_reason or ('EMPTY_GROUP' if not assigned else 'INCOMPLETE_FORWARD_GROUP' if len(observed_group)!=len(assigned) else None)
            mean=finite(np.mean([r['forward_return'] for r in assigned])) if group_reason is None else None
            group_rows.append({'date':date,'group':group,'formed_count':len(assigned),'observed_count':len(observed_group),
                               'mean_forward_return':mean,'reason':group_reason})
        formations.extend(current)
    valid=[r for r in evaluations if r['ic'] is not None]
    computation={'method':'fin_factor','cross_sections':len(calendar),'valid_cross_sections':len(valid),
        'mean_ic':finite(np.mean([r['ic'] for r in valid])) if valid else None,
        'mean_rank_ic':finite(np.mean([r['rank_ic'] for r in valid])) if valid else None,
        'mean_unavailable_reason':None if valid else 'NO_VALID_CROSS_SECTIONS','min_assets':5,
        'warnings':['EX_ANTE_UNIVERSE_AND_AVAILABILITY_ARE_DECLARED_NOT_EXTERNALLY_VERIFIED',
            'FORWARD_GROUP_EVALUATION_IS_NOT_A_TRADABLE_BACKTEST','OVERLAPPING_HORIZONS_HAVE_NO_IID_P_VALUE',
            'PRICE_RETURN_NOT_VERIFIED_TOTAL_RETURN; SURVIVORSHIP_BIAS_NOT_RESOLVED',*sorted(set(numerical_warnings))]}
    parameters={'date_column':date_column,'asset_column':asset_column,'factor_column':factor_column,'price_column':price_column,
        'available_at_column':available_at_column,'assets':assets,'universe_status':universe_status,'entry_lag':entry_lag,
        'horizon':horizon,'groups':groups,'ties':ties,'price_adjustment':price_adjustment,
        'missing_forward_policy':'unavailable_group_not_reweighted',**c}
    return finish(sample,parameters,computation,{'formation':pd.DataFrame(formations),'factor_evaluation':pd.DataFrame(evaluations),
                                               'group_evaluation':pd.DataFrame(group_rows)})
