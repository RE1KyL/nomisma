"""Dataset-bound analysis samples; one mask for every declared role."""
from dataclasses import dataclass
import hashlib,json
import numpy as np
import pandas as pd
from data_predicates import predicate_mask
from data_quality import columns_checked,parse_dates
from dataset_io import load_dataset
from dataset_store import DatasetError,MAX_ROWS,MAX_COLUMNS,MAX_CELLS


@dataclass(frozen=True)
class LoadedAnalysisInput:
    frame: pd.DataFrame
    metadata: dict


@dataclass(frozen=True)
class AnalysisSample:
    frame: pd.DataFrame
    metadata: dict
    positions: tuple
    roles: dict
    summary: dict


def load_analysis_input(store,dataset_id,expected_sha256):
    if not isinstance(expected_sha256,str) or len(expected_sha256)!=64:
        raise DatasetError('INVALID_ARGUMENT','A dataset version is required')
    frame,metadata=load_dataset(store,dataset_id,expected_sha256=expected_sha256)
    return LoadedAnalysisInput(frame.copy(deep=True),metadata)


def prepare_sample(source,*,columns,roles=None,filters=None,missing_policy='reject'):
    frame=source.frame.copy(deep=True).reset_index(drop=True)
    if len(frame)>MAX_ROWS or len(frame.columns)>MAX_COLUMNS or frame.size>MAX_CELLS:
        raise DatasetError('RESOURCE_LIMIT','Analysis input exceeds dataset limits')
    columns_checked(frame,columns)
    if missing_policy not in ('reject','complete_case','available'):raise DatasetError('INVALID_ARGUMENT','Invalid sample missing policy')
    roles={} if roles is None else roles;filters=[] if filters is None else filters
    if not isinstance(roles,dict) or set(roles)-{'y','x','date','cluster'}:raise DatasetError('INVALID_ARGUMENT','Unknown sample role')
    role_columns=[]
    for role,value in roles.items():
        names=value if role=='x' else [value]
        columns_checked(frame,names);role_columns.extend(names)
    if roles and missing_policy=='available':raise DatasetError('INVALID_ARGUMENT','Role-aligned samples require reject or complete_case')
    selected=list(dict.fromkeys([*columns,*role_columns]))
    if not isinstance(filters,list) or len(filters)>10:raise DatasetError('INVALID_ARGUMENT','At most ten explicit filters are supported')
    mask=pd.Series(True,index=frame.index);steps=[]
    for step in filters:
        if (not isinstance(step,dict) or set(step)-{'column','operator','value'}
                or isinstance(step.get('value'),list) and len(step['value'])>100):
            raise DatasetError('INVALID_ARGUMENT','Analysis filters require finite bounded predicates')
        before=int(mask.sum());mask &= predicate_mask(frame,step)
        steps.append({'parameters':step.copy(),'rows_before':before,'rows_after':int(mask.sum()),'excluded':before-int(mask.sum())})
    for name in selected:
        series=frame.loc[mask,name]
        if pd.api.types.is_complex_dtype(series):raise DatasetError('TYPE_MISMATCH','Complex input is not supported')
        if pd.api.types.is_numeric_dtype(series):
            if np.isinf(series.to_numpy(dtype=float,na_value=np.nan)).any():raise DatasetError('NON_FINITE_INPUT','Infinite values require explicit upstream handling')
        elif any(isinstance(v,(float,np.floating)) and np.isinf(v) for v in series):
            raise DatasetError('NON_FINITE_INPUT','Infinite values require explicit upstream handling')
    if 'date' in roles:
        name=roles['date'];parsed=parse_dates(frame[name])
        if (mask & frame[name].notna() & parsed.isna()).any():raise DatasetError('INVALID_DATE','Declared date field cannot be parsed')
        frame[name]=parsed
    missing=frame[selected].isna().any(axis=1)&mask
    missing_by_column={name:int((frame[name].isna()&mask).sum()) for name in selected}
    if missing_policy=='reject' and missing.any():raise DatasetError('MISSING_VALUES','Missing observations require an explicit sample policy')
    if missing_policy=='complete_case':mask &= ~missing
    positions=tuple(int(i) for i in np.flatnonzero(mask.to_numpy()))
    if not positions:raise DatasetError('SAMPLE_TOO_SMALL','No observations remain')
    digest=hashlib.sha256(json.dumps(positions,separators=(',',':')).encode()).hexdigest()
    summary={'input_rows':len(frame),'rows_after_filters':len(frame)-sum(s['excluded'] for s in steps),
        'excluded_by_filters':sum(s['excluded'] for s in steps),'missing_rows_observed':int(missing.sum()),
        'missing_by_column':missing_by_column,
        'excluded_by_missing':int(missing.sum()) if missing_policy=='complete_case' else 0,
        'actual_sample_size':len(positions),'positions_sha256':digest,'missing_policy':missing_policy,
        'columns':selected,'roles':roles.copy(),'filters':steps}
    return AnalysisSample(frame.loc[mask,selected].reset_index(drop=True).copy(),source.metadata,positions,roles.copy(),summary)


def build_design_matrix(sample,*,x_numeric,categorical=None,reference_levels=None,intercept=True):
    frame=sample.frame;categorical=[] if categorical is None else categorical;reference_levels={} if reference_levels is None else reference_levels
    columns_checked(frame,x_numeric,allow_empty=True);columns_checked(frame,categorical,allow_empty=True)
    if not isinstance(intercept,bool) or set(x_numeric)&set(categorical) or not isinstance(reference_levels,dict) or set(reference_levels)!=set(categorical):
        raise DatasetError('INVALID_ARGUMENT','Design columns and categorical references must be explicit and distinct')
    if len(x_numeric)>20 or len(categorical)>5:raise DatasetError('RESOURCE_LIMIT','Design exceeds variable limits')
    parts={}
    if intercept:parts['__intercept__']=np.ones(len(frame))
    for name in x_numeric:
        if name in parts:raise DatasetError('INVALID_COLUMN','Reserved design column label')
        if not pd.api.types.is_numeric_dtype(frame[name]) or pd.api.types.is_bool_dtype(frame[name]):raise DatasetError('TYPE_MISMATCH','Continuous predictors need numeric dtype')
        if pd.api.types.is_complex_dtype(frame[name]):raise DatasetError('TYPE_MISMATCH','Complex predictors are not supported')
        if pd.api.types.is_integer_dtype(frame[name]) and frame[name].dropna().map(lambda v:abs(int(v))>2**53).any():
            raise DatasetError('PRECISION_LOSS','Integer predictors exceed exact float precision')
        parts[name]=frame[name].to_numpy(dtype=float,na_value=np.nan)
    coding={}
    for name in categorical:
        values=frame[name]
        if values.isna().any() or not all(isinstance(v,str) for v in values):raise DatasetError('TYPE_MISMATCH','First categorical adapter requires explicit non-missing text levels')
        levels=sorted(values.unique());reference=reference_levels[name]
        if not isinstance(reference,str) or reference not in levels:raise DatasetError('INVALID_REFERENCE','Categorical reference is not observed')
        if len(levels)>20:raise DatasetError('RESOURCE_LIMIT','Too many categorical levels')
        coding[name]={'reference':reference,'levels':levels}
        for level in levels:
            if level==reference:continue
            label=f'{name}[{level}]'
            if label in parts or label in x_numeric:raise DatasetError('INVALID_COLUMN','Design column label collision')
            parts[label]=(values==level).to_numpy(dtype=float)
    if not parts or len(parts)>100:raise DatasetError('INVALID_ARGUMENT','Design needs 1 to 100 columns')
    if len(frame)*len(parts)>MAX_CELLS:raise DatasetError('RESOURCE_LIMIT','Encoded design exceeds the analysis cell budget')
    matrix=np.column_stack(list(parts.values()))
    if not np.isfinite(matrix).all():raise DatasetError('NON_FINITE_INPUT','Design matrix contains non-finite values')
    try:rank=int(np.linalg.matrix_rank(matrix))
    except (np.linalg.LinAlgError,FloatingPointError,OverflowError) as error:
        raise DatasetError('NUMERICAL_FAILURE','Design rank calculation failed') from error
    if rank!=matrix.shape[1]:raise DatasetError('DESIGN_RANK_DEFICIENT','Design is not full rank; variables were not dropped')
    if len(frame)<=rank:raise DatasetError('SAMPLE_TOO_SMALL','Residual degrees of freedom must be positive')
    if 'y' in sample.roles:
        y=frame[sample.roles['y']]
        if not pd.api.types.is_numeric_dtype(y) or pd.api.types.is_bool_dtype(y):raise DatasetError('TYPE_MISMATCH','Dependent variable must be numeric')
        if pd.api.types.is_complex_dtype(y):raise DatasetError('TYPE_MISMATCH','Complex dependent variables are not supported')
        if pd.api.types.is_integer_dtype(y) and y.dropna().map(lambda v:abs(int(v))>2**53).any():
            raise DatasetError('PRECISION_LOSS','Integer outcomes exceed exact float precision')
        a=y.to_numpy(dtype=float,na_value=np.nan)
        if not np.isfinite(a).all():raise DatasetError('NON_FINITE_INPUT','Dependent variable is not finite')
        if np.ptp(a)==0:raise DatasetError('CONSTANT_DEPENDENT','Dependent variable is constant')
    try:condition=float(np.linalg.cond(matrix))
    except (np.linalg.LinAlgError,FloatingPointError,OverflowError) as error:
        raise DatasetError('NUMERICAL_FAILURE','Design conditioning calculation failed') from error
    if not np.isfinite(condition):raise DatasetError('NUMERICAL_FAILURE','Design conditioning is not finite')
    return {'matrix':matrix,'columns':list(parts),'rank':rank,'df_resid':len(frame)-rank,'condition_number':condition,
            'warnings':['ILL_CONDITIONED_DESIGN'] if condition>1e10 else [],'categorical_encoding':coding,
            'matrix_sha256':hashlib.sha256(np.ascontiguousarray(matrix,dtype='<f8').tobytes()).hexdigest()}
