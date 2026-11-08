"""Explicit finite cleaning operations; each step is retained as an immutable file."""
from __future__ import annotations

from numbers import Real
import math
import uuid

import numpy as np
import pandas as pd

from data_quality import columns_checked,missing_mask
from data_predicates import predicate_mask,scalar_literal as _literal
from dataset_io import load_dataset,write_canonical_csv,read_canonical_csv
from dataset_store import DatasetError,ProjectDatasetStore,file_sha256,io_boundary,packages,utc_now


def _numeric(frame: pd.DataFrame, columns: list[str]) -> None:
    columns_checked(frame,columns)
    if any(not pd.api.types.is_numeric_dtype(frame[name]) or pd.api.types.is_bool_dtype(frame[name]) for name in columns):
        raise DatasetError('NOT_NUMERIC','This operation requires numeric dtype columns')


def _apply_step(frame: pd.DataFrame, step: dict) -> tuple[pd.DataFrame,int,list[str],dict]:
    schemas={
        'drop_duplicates':({'operation'},{'subset','keep'}),
        'drop_missing':({'operation','subset'},{'how','empty_strings_are_missing'}),
        'fill_missing':({'operation','columns','method'},{'value','empty_strings_are_missing'}),
        'winsorize':({'operation','columns','lower_quantile','upper_quantile'},set()),
        'sort_values':({'operation','by','ascending'},{'na_position'}),
        'filter_rows':({'operation','column','operator'},{'value'}),
    }
    if not isinstance(step,dict) or not isinstance(step.get('operation'),str) or step['operation'] not in schemas:
        raise DatasetError('INVALID_OPERATION','Unknown cleaning operation')
    operation=step['operation'];required,optional=schemas[operation]
    if not required<=set(step) or set(step)-required-optional:
        raise DatasetError('INVALID_ARGUMENT','Cleaning parameters do not match the operation schema')
    result=frame.copy(deep=True);warnings=[];details={}
    if operation=='drop_duplicates':
        subset=step.get('subset')
        if subset is not None:columns_checked(frame,subset)
        keep=step.get('keep','first')
        if keep not in ('first','last','none'):raise DatasetError('INVALID_ARGUMENT','Invalid duplicate keep policy')
        result=result.drop_duplicates(subset=subset,keep=False if keep=='none' else keep)
        affected=len(frame)-len(result)
    elif operation=='drop_missing':
        columns=columns_checked(frame,step['subset']);how=step.get('how','any')
        empty=step.get('empty_strings_are_missing',False)
        if how not in ('any','all') or not isinstance(empty,bool):raise DatasetError('INVALID_ARGUMENT','Invalid missing-row policy')
        masks=pd.DataFrame({name:missing_mask(frame[name],empty) for name in columns})
        remove=masks.any(axis=1) if how=='any' else masks.all(axis=1)
        result=result.loc[~remove];affected=int(remove.sum())
    elif operation=='fill_missing':
        columns=columns_checked(frame,step['columns']);method=step['method'];empty=step.get('empty_strings_are_missing',False)
        if method not in ('constant','mean','median') or not isinstance(empty,bool):raise DatasetError('INVALID_ARGUMENT','A valid fill method must be explicit')
        if method=='constant' and 'value' not in step:raise DatasetError('INVALID_ARGUMENT','Constant fill requires a value')
        if method!='constant' and 'value' in step:raise DatasetError('INVALID_ARGUMENT','Computed fill does not accept a value')
        if method!='constant':_numeric(frame,columns)
        changed=pd.Series(False,index=frame.index);details['fill_values']={}
        for name in columns:
            mask=missing_mask(frame[name],empty)
            if method=='constant':value=_literal(step['value'])
            else:
                array=frame[name].to_numpy(dtype=float,na_value=np.nan)
                finite=array[np.isfinite(array)]
                if not len(finite):raise DatasetError('INSUFFICIENT_DATA','No finite observations for computed filling')
                with np.errstate(over='ignore',invalid='ignore'):
                    value=float(np.mean(finite) if method=='mean' else np.median(finite))
                if not math.isfinite(value):raise DatasetError('NUMERIC_OVERFLOW','Fill statistic is not finite; no output was committed')
                if np.isinf(array).any():warnings.append(f'{name}: infinite values excluded from fill statistic, not replaced')
            if pd.api.types.is_numeric_dtype(frame[name]) and not pd.api.types.is_bool_dtype(frame[name]):
                if value is not None and (isinstance(value,bool) or not isinstance(value,Real)):
                    raise DatasetError('TYPE_MISMATCH','Numeric fill requires a numeric scalar')
                if value is not None and float(value).is_integer() is False:
                    result[name]=result[name].astype('Float64')
            try:result.loc[mask,name]=value
            except (ValueError,TypeError) as error:raise DatasetError('TYPE_MISMATCH','Fill value does not match the column dtype') from error
            changed |= mask if value is not None else False
            details['fill_values'][name]=value
        affected=int(changed.sum())
    elif operation=='winsorize':
        columns=columns_checked(frame,step['columns']);_numeric(frame,columns)
        low,high=step['lower_quantile'],step['upper_quantile']
        if (any(isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value) for value in (low,high))
                or not 0<=low<high<=1):raise DatasetError('INVALID_ARGUMENT','Quantile bounds must satisfy 0 <= lower < upper <= 1')
        changed=pd.Series(False,index=frame.index);details['bounds']={};warnings.append('Winsorization changes values explicitly; statistical outliers are not necessarily errors')
        for name in columns:
            values=frame[name].to_numpy(dtype=float,na_value=np.nan);finite=np.isfinite(values)
            if not finite.any():raise DatasetError('INSUFFICIENT_DATA','No finite observations for winsorization')
            with np.errstate(over='ignore',invalid='ignore'):
                lower,upper=np.quantile(values[finite],[low,high],method='linear')
            if not np.isfinite([lower,upper]).all():raise DatasetError('NUMERIC_OVERFLOW','Quantile bounds are not finite; no output was committed')
            mask=finite & ((values<lower)|(values>upper))
            result[name]=result[name].astype('Float64')
            result.loc[finite,name]=np.clip(values[finite],lower,upper)
            changed |= pd.Series(mask,index=frame.index)
            details['bounds'][name]={'lower':float(lower),'upper':float(upper),'quantile_method':'linear'}
        affected=int(changed.sum())
    elif operation=='sort_values':
        columns=columns_checked(frame,step['by']);ascending=step['ascending'];position=step.get('na_position','last')
        if not isinstance(ascending,bool) and not (isinstance(ascending,list) and len(ascending)==len(columns) and all(isinstance(value,bool) for value in ascending)):
            raise DatasetError('INVALID_ARGUMENT','Sort direction must be boolean or one boolean per key')
        if position not in ('first','last'):raise DatasetError('INVALID_ARGUMENT','Invalid null sort position')
        try:result=result.sort_values(columns,ascending=ascending,kind='stable',na_position=position)
        except TypeError as error:raise DatasetError('TYPE_MISMATCH','Mixed values cannot be safely sorted') from error
        affected=int(np.sum(result.index.to_numpy()!=frame.index.to_numpy()))
    else:
        mask=predicate_mask(frame,step)
        result=result.loc[mask];affected=len(frame)-len(result)
    details['dtype_changes']={name:{'before':str(frame[name].dtype),'after':str(result[name].dtype)} for name in frame if frame[name].dtype!=result[name].dtype}
    return result.reset_index(drop=True),affected,warnings,details


@io_boundary
def clean_dataset(store: ProjectDatasetStore, dataset_id: str, *, expected_sha256: str,
                  steps: list[dict], dataset_name: str | None=None) -> dict:
    if not isinstance(steps,list) or not 1<=len(steps)<=10:
        raise DatasetError('INVALID_ARGUMENT','Cleaning requires 1 to 10 explicit operation objects')
    frame,metadata=load_dataset(store,dataset_id,expected_sha256=expected_sha256)
    history=[];software=packages();input_file=metadata['current_file_path'];input_sha=metadata['sha256']
    with store.transaction() as transaction:
        names=[];canonical=None
        for index,step in enumerate(steps):
            started=utc_now();before=len(frame)
            frame,affected,warnings,details=_apply_step(frame,step)
            name='data.csv' if index==len(steps)-1 else f'step_{index+1:03}.csv'
            staged=transaction.file(name);canonical=write_canonical_csv(frame,staged)
            restored=read_canonical_csv(staged,canonical)
            try:pd.testing.assert_frame_equal(frame,restored)
            except AssertionError as error:raise DatasetError('ROUNDTRIP_MISMATCH','Processed CSV failed its typed round trip') from error
            relative=f'datasets/processed/{transaction.dataset_id}/{name}';digest=file_sha256(staged)
            history.append({'operation_id':'op_'+uuid.uuid4().hex,'dataset_id':transaction.dataset_id,
                'input_dataset_id':dataset_id if index==0 else transaction.dataset_id,
                'input_file':input_file,'input_sha256':input_sha,'output_file':relative,'output_sha256':digest,
                'operation':step['operation'],'parameters':{key:value for key,value in step.items() if key!='operation'},
                'started_at':started,'completed_at':utc_now(),'rows_before':before,'rows_after':len(frame),
                'affected_rows':affected,'warnings':warnings,'software_versions':software,'details':details,
                'serialization':canonical})
            names.append(name);input_file=relative;input_sha=digest
        store.metadata(dataset_id)
        provenance={key:metadata[key] for key in ('source_type','source_name','source_function','request_parameters',
                                                 'fetched_at','imported_at','import_history')}
        # These operations retain columns and their meanings. Reuse verified
        # source annotations; store.commit recalculates actual dtype/count/range.
        inference={column['name']:{key:column[key] for key in ('semantic_type','confidence','format') if key in column}
                   for column in metadata['column_schema']}
        result=store.commit(transaction,frame,dataset_name=dataset_name or metadata['dataset_name']+' processed',
            original_name='original.csv',current_name='data.csv',provenance=provenance,parser_options=canonical,
            inference=inference,
            storage_area='processed',parent_metadata=metadata,additional_files=names,
            extra={'processing_history':[*metadata.get('processing_history',[]),*history]})
        result['processing_history']=history
        return result
