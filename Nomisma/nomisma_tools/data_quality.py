"""Independent, non-mutating quality statistics and project-local report artifacts."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import json
import math
from numbers import Real
from pathlib import Path
import uuid

import numpy as np
import pandas as pd

from dataset_io import load_dataset
from dataset_store import (DatasetError, ProjectDatasetStore, MAX_ROWS, MAX_COLUMNS, MAX_CELLS,
                           file_sha256, io_boundary, packages, utc_now)


def columns_checked(frame: pd.DataFrame, columns: list[str], *, allow_empty: bool=False) -> list[str]:
    if (not isinstance(columns,list) or (not columns and not allow_empty) or len(columns)>MAX_COLUMNS
            or any(not isinstance(name,str) or name not in frame.columns for name in columns)
            or len(set(columns))!=len(columns)):
        raise DatasetError('INVALID_COLUMN','Columns must be a unique list of existing field names')
    return columns


def missing_kind(value: object) -> str | None:
    if value is None:return 'none'
    if value is pd.NA:return 'pandas_na'
    if value is pd.NaT:return 'nat'
    if isinstance(value,(float,np.floating)) and math.isnan(float(value)):return 'nan'
    return None


def missing_mask(series: pd.Series, empty_strings: bool=False) -> pd.Series:
    mask=series.isna()
    if empty_strings:mask |= series.map(lambda value:isinstance(value,str) and value=='')
    return mask.astype(bool)


def safe_json(value):
    if value is pd.NA or value is pd.NaT or value is None:return None
    if isinstance(value,dict):return {str(k):safe_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [safe_json(v) for v in value]
    if isinstance(value,(datetime,date,pd.Timestamp)):return value.isoformat()
    if hasattr(value,'item'):value=value.item()
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,int) and abs(value)>2**53:return str(value)
    if isinstance(value,str):return value[:256]
    return value


def parse_dates(series: pd.Series, date_format: str | None=None) -> pd.Series:
    import re
    parsed=[]
    for value in series:
        if missing_kind(value):parsed.append(pd.NaT);continue
        if isinstance(value,(date,datetime,pd.Timestamp)):
            parsed.append(value);continue
        if not isinstance(value,str) or (date_format is None and not re.match(r'^\d{4}[-/]\d{2}[-/]\d{2}($|[ T])',value)):
            parsed.append(pd.NaT);continue
        try:parsed.append(pd.to_datetime(value,format=date_format,errors='raise'))
        except (ValueError,TypeError,OverflowError):parsed.append(pd.NaT)
    return pd.Series(pd.to_datetime(parsed,utc=True),index=series.index)


def infer_frequency(dates: pd.Series) -> dict:
    unique=pd.DatetimeIndex(dates.dropna().unique()).sort_values()
    result={'inferred':'unknown','confidence':0.0,'unique_dates':len(unique),'basis':{},
            'calendar_status':'unverified','missing_trading_dates':None,
            'reason':'Insufficient observed dates; no trading calendar was checked'}
    if len(unique)<4:return result
    gaps=unique.to_series().diff().dropna().dt.total_seconds().to_numpy()/86400
    result['basis']={'interval_days':{str(float(k)):int(v) for k,v in Counter(gaps).items()},
                     'median_interval_days':float(np.median(gaps)),'confidence_kind':'heuristic_not_probability'}
    months=np.array([item.year*12+item.month for item in unique])
    month_gaps=np.diff(months)
    anchor=bool(np.ptp(unique.day)<=3 or np.all((unique.days_in_month-unique.day)<=4) or np.all(unique.day<=4))
    if len(set(months))==len(months) and anchor:
        for stride,label in [(12,'yearly'),(3,'quarterly'),(1,'monthly')]:
            if float(np.mean(month_gaps==stride))>=0.8:
                result.update(inferred=label,confidence=0.85,reason='Month strides and date anchors agree')
                return result
    median=float(np.median(gaps))
    if median==1 and float(np.mean((gaps>=1)&(gaps<=4)))>=0.8:
        result.update(inferred='daily',confidence=0.85,
                      reason='Observed short-day intervals; weekends/holidays are not classified as missing')
    elif 5<=median<=9 and float(np.mean((gaps>=5)&(gaps<=9)))>=0.8:
        result.update(inferred='weekly',confidence=0.8,reason='Observed intervals cluster around seven days')
    elif np.any(gaps<1):
        result.update(reason='Sub-daily observations are outside this frequency classifier')
    else:
        result.update(inferred='irregular',confidence=0.6,reason='Observed intervals do not match supported regular patterns')
    return result


def _samples(series: pd.Series, mask: pd.Series) -> list[dict]:
    positions=np.flatnonzero(mask.to_numpy(dtype=bool))[:5]
    return [{'row_position':int(position),'value':safe_json(series.iloc[position]),
             'missing_kind':missing_kind(series.iloc[position])} for position in positions]


def _time_report(frame: pd.DataFrame, column: str, date_format: str | None) -> dict:
    raw=frame[column];dates=parse_dates(raw,date_format);valid=dates.dropna()
    failed=raw.notna() & dates.isna()
    sorted_dates=valid.sort_values().drop_duplicates()
    gaps=sorted_dates.diff().dropna().dt.total_seconds()/86400
    typical=float(gaps.median()) if len(gaps) else None
    long_gap= max(7.0,typical*3) if typical is not None else None
    return {'date_column':column,'comparison_timezone':'UTC; naive values compared as UTC',
            'parse_failure_count':int(failed.sum()),'parse_failure_samples':_samples(raw,failed),
            'duplicate_date_excess':int(valid.duplicated().sum()),
            'duplicate_date_affected_rows':int(valid.duplicated(keep=False).sum()),
            'descending_pairs':int((valid.diff().dropna()<pd.Timedelta(0)).sum()),
            'data_start':safe_json(valid.min()) if len(valid) else None,
            'data_end':safe_json(valid.max()) if len(valid) else None,
            'interval_distribution_days':{str(float(k)):int(v) for k,v in Counter(gaps).items()},
            'possible_long_gaps_days':[float(value) for value in gaps[gaps>long_gap].iloc[:10]] if long_gap is not None else [],
            'frequency':infer_frequency(dates),'missing_trading_dates':None,
            'note':'Long gaps are candidates only; holiday closures and suspensions were not verified'}


def evaluate_quality(frame: pd.DataFrame, *, dataset_id: str | None=None,
                     empty_strings_are_missing: bool=False, primary_key: list[str] | None=None,
                     outlier_columns: list[str] | None=None, iqr_multiplier: float=1.5,
                     z_threshold: float=3.0, min_outlier_samples: int=8,
                     expected_types: dict[str,str] | None=None, date_column: str | None=None,
                     group_by: list[str] | None=None, date_format: str | None=None) -> dict:
    """Pure inspection. No file writes, row removal, filling or implicit sorting."""
    if len(frame)>MAX_ROWS or len(frame.columns)>MAX_COLUMNS or frame.size>MAX_CELLS:
        raise DatasetError('RESOURCE_LIMIT','Quality input exceeds the dataset limits')
    if not isinstance(empty_strings_are_missing,bool):raise DatasetError('INVALID_ARGUMENT','Missing policy must be boolean')
    if (isinstance(iqr_multiplier,bool) or not isinstance(iqr_multiplier,Real) or not math.isfinite(iqr_multiplier) or not 0.5<=iqr_multiplier<=10
            or isinstance(z_threshold,bool) or not isinstance(z_threshold,Real) or not math.isfinite(z_threshold) or not 1<=z_threshold<=10
            or isinstance(min_outlier_samples,bool) or not isinstance(min_outlier_samples,int) or not 4<=min_outlier_samples<=1000):
        raise DatasetError('INVALID_ARGUMENT','Invalid outlier thresholds or minimum sample size')
    if primary_key is not None:columns_checked(frame,primary_key)
    if date_column is not None:columns_checked(frame,[date_column])
    if group_by is not None:
        columns_checked(frame,group_by)
        if date_column is None:raise DatasetError('INVALID_ARGUMENT','group_by requires an explicit date column')
    expected_types=expected_types or {}
    if not isinstance(expected_types,dict) or any(value not in ('number','date','text','boolean') for value in expected_types.values()):
        raise DatasetError('INVALID_ARGUMENT','Unsupported expected field type')
    columns_checked(frame,list(expected_types),allow_empty=True)
    if date_format is not None and (not isinstance(date_format,str) or len(date_format)>64):
        raise DatasetError('INVALID_ARGUMENT','Invalid date format')
    numeric=[name for name in frame if pd.api.types.is_numeric_dtype(frame[name]) and not pd.api.types.is_bool_dtype(frame[name])]
    selected=numeric if outlier_columns is None else columns_checked(frame,outlier_columns,allow_empty=True)
    if any(name not in numeric for name in selected):raise DatasetError('NOT_NUMERIC','Outlier detection only accepts numeric dtype columns')
    missing={};types={};total=0
    for name in frame:
        series=frame[name];mask=missing_mask(series,empty_strings_are_missing);count=int(mask.sum());total+=count
        kinds=Counter(missing_kind(value) or ('empty_string' if isinstance(value,str) and value=='' else 'present') for value in series)
        observed=Counter(type(value).__name__ for value in series if missing_kind(value) is None)
        missing[name]={'count':count,'ratio':count/len(frame) if len(frame) else None,'non_missing':len(frame)-count,
                       'observed_representations':dict(kinds),'samples':_samples(series,mask)}
        nonmissing=series[~mask]
        parsed=pd.to_numeric(nonmissing,errors='coerce')
        failures=int(parsed.isna().sum())
        infinite=int(np.isinf(parsed.to_numpy(dtype=float,na_value=np.nan)).sum())
        date_fail=None
        if name==date_column or expected_types.get(name)=='date':
            date_fail=int((series.notna() & parse_dates(series,date_format).isna()).sum())
        semantic='number' if name in numeric else 'boolean' if pd.api.types.is_bool_dtype(series) else 'unknown'
        types[name]={'pandas_dtype':str(series.dtype),'semantic_type':semantic,'observed_python_types':dict(observed),
                     'mixed_python_types':len(observed)>1,'all_missing':count==len(frame),
                     'numeric_candidate_parse_failures':failures,'expected_type':expected_types.get(name),
                     'numeric_type_mismatch_count':failures if expected_types.get(name)=='number' or name in numeric else None,
                     'date_parse_failure_count':date_fail,'infinite_count':infinite}
    def duplicates(columns):
        excess=frame.duplicated(subset=columns,keep='first');affected=frame.duplicated(subset=columns,keep=False)
        return {'columns':list(columns) if columns is not None else list(frame.columns),
                'duplicate_excess':int(excess.sum()),'affected_rows':int(affected.sum()),
                'duplicate_groups':len(frame.loc[affected].drop_duplicates(subset=columns)),
                'sample_row_positions':[int(value) for value in np.flatnonzero(affected.to_numpy())[:10]]}
    outliers={}
    for name in selected:
        array=frame[name].to_numpy(dtype=float,na_value=np.nan);finite_mask=np.isfinite(array);values=array[finite_mask]
        result={'finite_count':len(values),'excluded_non_finite':int((~finite_mask).sum()),
                'iqr':{'status':'insufficient_samples'},'z_score':{'status':'insufficient_samples'},
                'note':'Statistical candidates only; no normal-distribution assumption or deletion'}
        if len(values)>=min_outlier_samples:
            with np.errstate(over='ignore',invalid='ignore'):
                q1,q3=np.quantile(values,[.25,.75],method='linear');spread=float(q3-q1)
                lower=float(q1-iqr_multiplier*spread);upper=float(q3+iqr_multiplier*spread)
                mean=float(np.mean(values));std=float(np.std(values,ddof=1))
                low=mean-z_threshold*std;high=mean+z_threshold*std
            if np.isfinite([q1,q3,spread,lower,upper]).all():
                candidate=finite_mask & ((array<lower)|(array>upper))
                result['iqr']={'status':'evaluated','q1':float(q1),'q3':float(q3),'iqr':spread,'lower':lower,'upper':upper,
                               'multiplier':iqr_multiplier,'quantile_method':'linear','candidate_count':int(candidate.sum()),
                               'candidate_ratio':float(candidate.sum()/len(values)),'samples':_samples(frame[name],pd.Series(candidate,index=frame.index))}
            else:result['iqr']={'status':'numerical_overflow','reason':'Finite bounds could not be calculated reliably'}
            if np.isfinite([mean,std,low,high]).all():
                candidate=finite_mask & ((array<low)|(array>high)) if std else np.zeros(len(array),dtype=bool)
                result['z_score']={'status':'evaluated' if std else 'constant','mean':mean,'std':std,'ddof':1,
                                   'lower':low,'upper':high,'threshold':z_threshold,'candidate_count':int(candidate.sum()),
                                   'candidate_ratio':float(candidate.sum()/len(values)),'samples':_samples(frame[name],pd.Series(candidate,index=frame.index))}
            else:result['z_score']={'status':'numerical_overflow','reason':'Finite moments could not be calculated reliably'}
        outliers[name]=result
    temporal=None
    if date_column is not None:
        if group_by:
            grouped=frame.groupby(group_by,dropna=False,sort=False)
            if grouped.ngroups>100:raise DatasetError('RESOURCE_LIMIT','Time-series report supports at most 100 explicit groups')
            temporal={'group_by':group_by,'groups':[{'key':safe_json(key),'report':_time_report(group,date_column,date_format)} for key,group in grouped]}
        else:temporal=_time_report(frame,date_column,date_format)
    report=safe_json({'dataset_id':dataset_id,'quality_status':'completed','row_count':len(frame),'column_count':len(frame.columns),
        'missing':{'total_missing_cells':total,'ratio':total/frame.size if frame.size else None,
                   'empty_strings_are_missing':empty_strings_are_missing,'representation_basis':'observed_loaded_dataframe','columns':missing},
        'duplicates':{'full_rows':duplicates(None),'primary_key':duplicates(primary_key) if primary_key else None},
        'outliers':outliers,'data_types':types,'time_series':temporal,'checked_at':utc_now(),
        'warnings':['Outliers are candidates, not confirmed errors.','No trading calendar or complete trading-day coverage was verified.']})
    if len(json.dumps(report,ensure_ascii=False,allow_nan=False).encode('utf8'))>512_000:
        raise DatasetError('RESOURCE_LIMIT','Quality report exceeds 512 KB; narrow fields or groups')
    return report


@io_boundary
def check_dataset_quality(store: ProjectDatasetStore, dataset_id: str, *, expected_sha256: str, **options) -> dict:
    frame,metadata=load_dataset(store,dataset_id,expected_sha256=expected_sha256)
    report=evaluate_quality(frame,dataset_id=dataset_id,**options)
    quality_id='qr_'+uuid.uuid4().hex
    path=f'datasets/metadata/quality/{quality_id}.json'
    report.update(quality_report_id=quality_id,project_context=store.context,input_file=metadata['current_file_path'],
                  input_sha256=metadata['sha256'],software_versions=packages())
    with store.transaction() as transaction:
        staged=transaction.file('quality.json')
        with staged.open('x',encoding='utf8') as stream:json.dump(report,stream,ensure_ascii=False,allow_nan=False,indent=2)
        digest=file_sha256(staged)
        if store.metadata(dataset_id)!=metadata:
            raise DatasetError('STALE_DATASET','Dataset metadata changed during inspection')
        store.publish_artifacts(transaction,{'quality.json':path})
    return {'quality_report_id':quality_id,'report_path':path,'sha256':digest,'report':report}
