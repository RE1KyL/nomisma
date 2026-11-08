"""Trusted finance binding, explicit conventions and immutable internal results."""
from dataclasses import dataclass,replace
import hashlib,math
import numpy as np
import pandas as pd
from analysis_sample import load_analysis_input,prepare_sample
from analysis_result import encode_result,normalize_result,make_analysis_result
from analysis_store import save_analysis
from data_quality import parse_dates
from dataset_store import DatasetError


@dataclass(frozen=True)
class FinanceAnalysis:
    sample: object
    parameters: dict
    computation: dict
    tables: dict
    seal: str


def require_finance(store):
    store.check_context()
    if store.context['mode']!='finance':raise DatasetError('MODE_NOT_ALLOWED','Finance analysis requires an active finance project')


def finite(value):
    value=float(value)
    if not math.isfinite(value):raise DatasetError('NUMERICAL_FAILURE','Finance computation produced a non-finite value')
    return value


def conventions(frequency,periods_per_year,*,annual=False):
    if frequency not in ('daily','weekly','monthly','quarterly','yearly','irregular'):
        raise DatasetError('INVALID_FINANCIAL_CONVENTION','An explicit supported frequency is required')
    if periods_per_year is not None and (isinstance(periods_per_year,bool) or not isinstance(periods_per_year,(int,float))
            or not math.isfinite(periods_per_year) or not 0<periods_per_year<=100000):
        raise DatasetError('INVALID_FINANCIAL_CONVENTION','Annualization factor must be an explicit positive finite number')
    if frequency=='irregular' and periods_per_year is not None:
        raise DatasetError('INVALID_FINANCIAL_CONVENTION','Irregular observations have no approved annualization rule')
    if annual and (frequency=='irregular' or periods_per_year is None):
        raise DatasetError('INVALID_FINANCIAL_CONVENTION','Annual metrics require an explicit regular frequency and annualization factor')
    return {'frequency':frequency,'periods_per_year':periods_per_year,'calendar_status':'unverified',
            'frequency_basis':'caller_declared_not_calendar_certified','annualization':'period_count' if periods_per_year else 'not_applicable'}


def numeric(frame,name,*,allow_missing=False):
    if not isinstance(name,str) or name not in frame:raise DatasetError('INVALID_COLUMN','A known numeric field is required')
    series=frame[name]
    if (not pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series)
            or pd.api.types.is_complex_dtype(series)):
        raise DatasetError('TYPE_MISMATCH','Financial measures must have real numeric dtype')
    if pd.api.types.is_integer_dtype(series) and series.dropna().map(lambda v:abs(int(v))>2**53).any():
        raise DatasetError('PRECISION_LOSS','Financial values exceed exact float precision')
    values=series.to_numpy(dtype=float,na_value=np.nan)
    if np.isinf(values).any():raise DatasetError('NON_FINITE_INPUT','Infinite financial inputs are not accepted')
    if not allow_missing and np.isnan(values).any():raise DatasetError('MISSING_VALUES','Missing financial values require an explicit supported policy')
    return values


def load_panel(store,dataset_id,expected_sha256,*,date_column,value_columns,asset_column=None):
    require_finance(store)
    if (not isinstance(date_column,str) or not isinstance(value_columns,list) or not value_columns
            or any(not isinstance(v,str) for v in value_columns) or date_column in value_columns
            or asset_column is not None and (not isinstance(asset_column,str) or asset_column in [date_column,*value_columns])):
        raise DatasetError('INVALID_ARGUMENT','Date, asset and measure fields must be explicit and distinct')
    source=load_analysis_input(store,dataset_id,expected_sha256)
    names=list(dict.fromkeys([date_column,*value_columns,*([asset_column] if asset_column else [])]))
    sample=prepare_sample(source,columns=names,missing_policy='available')
    dates=parse_dates(sample.frame[date_column])
    if dates.isna().any():raise DatasetError('INVALID_DATE','Every financial observation requires a parseable date')
    sample.frame[date_column]=dates
    if asset_column:
        assets=sample.frame[asset_column]
        if assets.isna().any() or not all(isinstance(v,str) and 0<len(v)<=64 for v in assets):
            raise DatasetError('INVALID_ASSET','Asset identities must be bounded non-missing text')
        if assets.nunique()>100:raise DatasetError('RESOURCE_LIMIT','At most one hundred assets are supported')
        groups=list(sample.frame.groupby(asset_column,sort=False,observed=True))
    else:groups=[('__single__',sample.frame)]
    for _,frame in groups:
        date=frame[date_column]
        if date.duplicated().any():raise DatasetError('DUPLICATE_TIME_KEY','Duplicate date/asset observations are not accepted')
        if not date.is_monotonic_increasing:raise DatasetError('TIME_ORDER_INVALID','Dates must already increase within each asset; no sorting occurred')
    return sample,groups


def simple_returns(values,kind):
    if kind not in ('simple','log'):raise DatasetError('INVALID_FINANCIAL_CONVENTION','Returns must be explicitly simple or log')
    try:
        with np.errstate(over='raise',invalid='raise',under='ignore'):
            result=values.copy() if kind=='simple' else np.expm1(values)
    except FloatingPointError as error:raise DatasetError('NUMERICAL_FAILURE','Return conversion overflowed') from error
    if np.isinf(result).any() or np.any(result[~np.isnan(result)] < -1):raise DatasetError('INVALID_RETURN','Simple returns cannot be below -100 percent')
    if kind=='log' and np.any(result[~np.isnan(result)]<=-1):raise DatasetError('NUMERICAL_FAILURE','Finite log returns underflowed to total loss')
    return result


def balanced_panel(sample,date_column,asset_column,assets):
    if (not isinstance(assets,list) or not assets or len(assets)>100 or any(not isinstance(a,str) for a in assets)
            or len(set(assets))!=len(assets) or set(assets)!=set(sample.frame[asset_column])):
        raise DatasetError('INVALID_ASSET','The declared ex-ante universe must exactly match the panel assets')
    groups={a:sample.frame[sample.frame[asset_column]==a] for a in assets}
    calendar=groups[assets[0]][date_column].tolist()
    if any(frame[date_column].tolist()!=calendar for frame in groups.values()):
        raise DatasetError('ASSET_ALIGNMENT_REQUIRED','This adapter requires a balanced exact-date panel; no missing asset was reweighted')
    return calendar,groups


def _seal(analysis):
    digest=hashlib.sha256(encode_result({'metadata':analysis.sample.metadata,'summary':analysis.sample.summary,
        'positions':analysis.sample.positions,'parameters':analysis.parameters,'computation':analysis.computation}))
    for frame in [analysis.sample.frame,*analysis.tables.values()]:
        digest.update(encode_result({'columns':list(frame.columns),'dtypes':[str(d) for d in frame.dtypes],'shape':list(frame.shape)}))
        digest.update(pd.util.hash_pandas_object(frame,index=True).to_numpy().tobytes())
    return digest.hexdigest()


def finish(sample,parameters,computation,tables):
    computation={**computation,'analysis_domain':'finance','algorithm_version':'nomisma-finance-1'}
    analysis=FinanceAnalysis(sample,normalize_result(parameters),normalize_result(computation),tables,'')
    return replace(analysis,seal=_seal(analysis))


def validate_finance(store,analysis):
    require_finance(store)
    if not isinstance(analysis,FinanceAnalysis):raise DatasetError('ANALYSIS_INVALID','A verified internal finance analysis is required')
    if any(analysis.sample.metadata[k]!=store.context[k] for k in ('mode','project_id','session_id')):
        raise DatasetError('PROJECT_CONTEXT_INVALID','Finance analysis belongs to another project or session')
    if _seal(analysis)!=analysis.seal:raise DatasetError('STALE_ANALYSIS','Finance input or computed results changed')
    if store.metadata(analysis.sample.metadata['dataset_id'])!=analysis.sample.metadata:
        raise DatasetError('STALE_DATASET','Finance source data changed')


def publish_finance(store,analysis):
    validate_finance(store,analysis)
    tables={}
    for name,source in analysis.tables.items():
        frame=source.copy(deep=True)
        for column in frame:
            if pd.api.types.is_datetime64_any_dtype(frame[column]):
                frame[column]=frame[column].map(lambda v:None if pd.isna(v) else v.isoformat()).astype(object)
        tables[name]=frame
    result=make_analysis_result(analysis.sample,analysis.computation,parameters=analysis.parameters)
    return save_analysis(store,result,tables=tables,before_commit=lambda:validate_finance(store,analysis))
