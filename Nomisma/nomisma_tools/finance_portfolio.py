"""Ex-ante, long-only, cost-free each-period theoretical portfolios; not a trading engine."""
import math
import numpy as np
import pandas as pd
from data_quality import parse_dates
from finance_common import load_panel,numeric,simple_returns,finite,finish,balanced_panel
from finance_risk import _measure,_spec
from dataset_store import DatasetError,MAX_CELLS


def _weights(values):
    if not np.isfinite(values).all() or (values<0).any() or (values>1).any():
        raise DatasetError('INVALID_WEIGHTS','Weights must be finite, non-negative and at most one')
    if not math.isclose(float(np.sum(values)),1.,rel_tol=0,abs_tol=1e-12):
        raise DatasetError('INVALID_WEIGHTS','Weights must sum to one; automatic normalization is disabled')


def analyze_portfolio(store,dataset_id,*,expected_sha256,date_column,asset_column,return_column,return_kind,
                      assets,universe_status,frequency,weighting,metrics,periods_per_year=None,weights=None,
                      weight_column=None,weight_available_at_column=None,first_row='returns',alpha_loss=.95,risk_free=None,mar=None):
    if universe_status!='declared_ex_ante':raise DatasetError('LOOKAHEAD_RISK','A declared ex-ante asset universe is required')
    if weighting not in ('equal','constant','lagged_column') or first_row not in ('returns','formation'):
        raise DatasetError('INVALID_ARGUMENT','Only fixed each-period long-only weight policies are supported')
    _spec(metrics,frequency,periods_per_year,alpha_loss,risk_free,mar)
    if weighting=='lagged_column':
        if (weights is not None or first_row!='formation' or not isinstance(weight_column,str)
                or not isinstance(weight_available_at_column,str)
                or len({return_column,weight_column,weight_available_at_column})!=3):
            raise DatasetError('INVALID_ARGUMENT','Lagged weights require distinct weight/availability fields and an initial formation row')
    elif weight_column is not None or weight_available_at_column is not None or first_row!='returns':
        raise DatasetError('INVALID_ARGUMENT','Fixed weights are declared available before the first return row')
    if weighting=='equal' and weights is not None:raise DatasetError('INVALID_ARGUMENT','Equal weighting does not accept explicit weights')
    sample,_=load_panel(store,dataset_id,expected_sha256,date_column=date_column,
        value_columns=[return_column,*([weight_column,weight_available_at_column] if weighting=='lagged_column' else [])],asset_column=asset_column)
    calendar,panel=balanced_panel(sample,date_column,asset_column,assets)
    if len(calendar)*(len(assets)+3)>MAX_CELLS:raise DatasetError('RESOURCE_LIMIT','Portfolio output exceeds its cell budget')
    start=1 if weighting=='lagged_column' else 0
    if len(calendar)<=start:raise DatasetError('SAMPLE_TOO_SMALL','No portfolio return periods remain')
    returns=np.column_stack([simple_returns(numeric(panel[a],return_column,allow_missing=True),return_kind) for a in assets])
    if not np.isfinite(returns[start:]).all():raise DatasetError('MISSING_ASSET_RETURN','Every declared asset needs a return; missing assets were not reweighted')
    for j in range(len(assets)):
        series=returns[start:,j];ruin=np.flatnonzero(series==-1)
        if len(ruin) and np.any(series[ruin[0]+1:]!=0):raise DatasetError('INVALID_RETURN_AFTER_RUIN','Asset returns after total loss need an explicit different economic model')
    if weighting=='constant':
        if not isinstance(weights,dict) or set(weights)!=set(assets) or any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in weights.values()):
            raise DatasetError('INVALID_WEIGHTS','Constant weights must explicitly cover exactly the declared universe')
        vector=np.array([weights[a] for a in assets],dtype=float);_weights(vector)
    elif weighting=='equal':vector=np.ones(len(assets))/len(assets)
    else:
        declared=np.column_stack([numeric(panel[a],weight_column) for a in assets]);available={}
        for row in declared:_weights(row)
        for asset,frame in panel.items():
            parsed=parse_dates(frame[weight_available_at_column])
            if parsed.isna().any():raise DatasetError('LOOKAHEAD_RISK','Every formation weight requires known availability')
            available[asset]=parsed.tolist()
    output=[];used=[]
    for t in range(start,len(calendar)):
        if weighting=='lagged_column':
            vector=declared[t-1]
            if any(available[a][t-1]>calendar[t-1] for a in assets):
                raise DatasetError('LOOKAHEAD_RISK','A weight was unavailable at the preceding formation observation')
        value=finite(np.dot(vector,returns[t]));output.append(value)
        for j,asset in enumerate(assets):
            used.append({'date':calendar[t],'asset':asset,'formation_date':calendar[t-1] if start else None,
                'weight':float(vector[j]),'simple_return':float(returns[t,j]),'contribution':finite(vector[j]*returns[t,j]),
                'availability_basis':'previous_observation_column' if start else 'constant_declared_before_sample'})
    facts,trajectory,notes=_measure(np.array(output),calendar[start:],metrics=metrics,frequency=frequency,A=periods_per_year,
        alpha_loss=alpha_loss,risk_free=risk_free,mar=mar,baseline_date=calendar[0].isoformat() if start else None)
    covariance_check=None
    if not start and len(output)>1:
        covariance=np.atleast_2d(np.cov(returns,rowvar=False,ddof=1))
        covariance_check={'portfolio_variance':finite(np.var(output,ddof=1)),
                          'weights_covariance_weights':finite(vector@covariance@vector)}
    computation={'method':'fin_portfolio','risk':facts,'asset_count':len(assets),'portfolio_periods':len(output),
        'excluded_initial_formation_periods':start,'covariance_consistency':covariance_check,
        'warnings':[*notes,'THEORETICAL_EACH_PERIOD_REBALANCED; NO_COSTS_SLIPPAGE_OR_TRADING',
            'EX_ANTE_UNIVERSE_AND_FIXED_WEIGHT_AVAILABILITY_ARE_DECLARATIONS; SURVIVORSHIP_NOT_VERIFIED']}
    parameters={'date_column':date_column,'asset_column':asset_column,'return_column':return_column,'return_kind':return_kind,
        'assets':assets,'universe_status':universe_status,'frequency':frequency,'periods_per_year':periods_per_year,
        'weighting':weighting,'weights':weights,'weight_column':weight_column,'weight_available_at_column':weight_available_at_column,
        'first_row':first_row,'weight_lag':1 if start else 'constant_pre_sample','rebalance':'each_period',
        'missing_policy':'reject','metrics':metrics,'alpha_loss':alpha_loss,'risk_free':risk_free,'MAR':mar}
    return finish(sample,parameters,computation,{'portfolio':trajectory,'weights':pd.DataFrame(used)})
