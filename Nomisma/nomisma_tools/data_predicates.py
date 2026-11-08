"""Shared, finite predicates. No expressions, file access or persistence."""
import math
import re
from numbers import Real
import pandas as pd
from data_quality import columns_checked
from dataset_store import DatasetError


def scalar_literal(value):
    if value is None or isinstance(value,bool):return value
    if isinstance(value,int) and abs(value)<=10**100:return value
    if isinstance(value,float) and math.isfinite(value):return value
    if isinstance(value,str) and len(value)<=256:return value
    raise DatasetError('INVALID_ARGUMENT','Only finite scalar values and bounded text are allowed')


def predicate_mask(frame,step):
    if not isinstance(step,dict) or not {'column','operator'}<=set(step) or set(step)-{'column','operator','value','operation'}:
        raise DatasetError('INVALID_ARGUMENT','Invalid filter parameters')
    name=step['column'];columns_checked(frame,[name]);operator=step['operator'];series=frame[name]
    comparisons={'equal':'eq','not_equal':'ne','greater_than':'gt','greater_equal':'ge','less_than':'lt','less_equal':'le'}
    if operator in ('is_null','not_null'):
        if 'value' in step:raise DatasetError('INVALID_ARGUMENT','Null predicates do not accept a value')
        return series.isna() if operator=='is_null' else series.notna()
    if operator in ('in','not_in'):
        value=step.get('value')
        if not isinstance(value,list) or not 1<=len(value)<=1000:raise DatasetError('INVALID_ARGUMENT','Membership requires a bounded value list')
        mask=series.isin([scalar_literal(item) for item in value])
        return (~mask if operator=='not_in' else mask)&series.notna()
    if not isinstance(operator,str) or operator not in comparisons:
        raise DatasetError('INVALID_OPERATION','Unsupported filter operator; expressions are not accepted')
    if 'value' not in step:raise DatasetError('INVALID_ARGUMENT','Comparison requires a value')
    value=scalar_literal(step['value'])
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        if isinstance(value,bool) or not isinstance(value,Real):raise DatasetError('TYPE_MISMATCH','Numeric comparison requires a number')
    if pd.api.types.is_datetime64_any_dtype(series):
        if not isinstance(value,str) or not re.match(r'^\d{4}-\d{2}-\d{2}($|[ T])',value):raise DatasetError('TYPE_MISMATCH','Date comparison requires ISO text')
        try:
            value=pd.Timestamp(value)
            if pd.isna(value):raise ValueError('Missing date')
        except (ValueError,OverflowError) as error:raise DatasetError('INVALID_ARGUMENT','Invalid date comparison') from error
    try:return getattr(series,comparisons[operator])(value).fillna(False)&series.notna()
    except (ValueError,TypeError) as error:raise DatasetError('TYPE_MISMATCH','Comparison value does not match its field') from error
