"""Pure, bounded descriptive and correlation calculations on verified samples."""
import hashlib,math,warnings
from itertools import combinations
import numpy as np
import pandas as pd
from scipy import stats
from data_quality import columns_checked
from dataset_store import DatasetError


def _numeric(sample,columns):
    columns_checked(sample.frame,columns)
    if len(columns)>20:raise DatasetError('RESOURCE_LIMIT','At most twenty statistical fields')
    result={}
    for name in columns:
        s=sample.frame[name]
        if not pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):raise DatasetError('TYPE_MISMATCH','Statistics require numeric dtype')
        if pd.api.types.is_complex_dtype(s):raise DatasetError('TYPE_MISMATCH','Complex statistics are not supported')
        if pd.api.types.is_integer_dtype(s) and s.dropna().map(lambda v:abs(int(v))>2**53).any():
            raise DatasetError('PRECISION_LOSS','Large integer fields need an explicit upstream measurement conversion')
        a=s.to_numpy(dtype=float,na_value=np.nan)
        if np.isinf(a).any():raise DatasetError('NON_FINITE_INPUT','Infinite observations are not implicitly removed')
        result[name]=a
    return result


def describe(sample,columns,*,quantiles=(.25,.5,.75),ddof=1):
    if ddof!=1 or isinstance(ddof,bool):raise DatasetError('INVALID_ARGUMENT','Sample statistics use ddof=1')
    if (not isinstance(quantiles,(tuple,list)) or len(quantiles)>9 or not quantiles
            or any(isinstance(q,bool) or not isinstance(q,(int,float)) or not math.isfinite(q) or not 0<=q<=1 for q in quantiles)
            or sorted(set(quantiles))!=list(quantiles)):raise DatasetError('INVALID_ARGUMENT','Quantiles must be finite, ordered and unique')
    result=[]
    for name,raw in _numeric(sample,columns).items():
        values=raw[~np.isnan(raw)];n=len(values);constant=n>0 and np.ptp(values)==0
        row={'name':name,'count':n,'missing_count':int(np.isnan(raw).sum()),'finite_count':n,
             'mean':None,'median':None,'variance':None,'std':None,'min':None,'max':None,
             'quantiles':{},'skewness':None,'excess_kurtosis':None,'unavailable':{}}
        if n:
            with np.errstate(over='ignore',invalid='ignore'):
                row.update(mean=float(np.mean(values)),median=float(np.median(values)),min=float(np.min(values)),max=float(np.max(values)))
                row['quantiles']={str(q):float(np.quantile(values,q,method='linear')) for q in quantiles}
                if n>=2:row.update(variance=float(np.var(values,ddof=1)),std=float(np.std(values,ddof=1)))
                for metric,minimum,fn in [('skewness',3,lambda:stats.skew(values,bias=False)),
                                          ('excess_kurtosis',4,lambda:stats.kurtosis(values,fisher=True,bias=False))]:
                    if constant or n<minimum:row['unavailable'][metric]='CONSTANT_COLUMN' if constant else 'INSUFFICIENT_SAMPLE'
                    else:row[metric]=float(fn())
        else:row['unavailable']['all_statistics']='NO_FINITE_OBSERVATIONS'
        if n<2:row['unavailable'].update(variance='INSUFFICIENT_SAMPLE',std='INSUFFICIENT_SAMPLE')
        for value in [*[row[k] for k in ['mean','median','variance','std','min','max','skewness','excess_kurtosis']],*row['quantiles'].values()]:
            if value is not None and not math.isfinite(value):raise DatasetError('NUMERICAL_FAILURE','A descriptive statistic overflowed')
        result.append(row)
    return {'method':'describe','ddof':1,'quantile_method':'linear','bias':False,'kurtosis':'fisher_excess',
            'sample_summary':sample.summary,'columns':result}


def correlate(sample,columns,*,method='pearson',missing_policy='reject',inference='none',alpha=.05):
    arrays=_numeric(sample,columns)
    if len(columns)<2:raise DatasetError('INVALID_ARGUMENT','Correlation needs at least two columns')
    if method not in ('pearson','spearman') or missing_policy not in ('reject','listwise','pairwise') or inference not in ('none','iid_asymptotic'):
        raise DatasetError('INVALID_ARGUMENT','Unsupported correlation policy')
    if isinstance(alpha,bool) or not isinstance(alpha,(int,float)) or not 0<alpha<1:raise DatasetError('INVALID_ARGUMENT','Invalid confidence level')
    if inference!='none' and ('date' in sample.roles or 'cluster' in sample.roles):
        raise DatasetError('INFERENCE_ASSUMPTION_REQUIRED','IID inference is not enabled for declared time/cluster roles')
    complete=np.logical_and.reduce([~np.isnan(a) for a in arrays.values()])
    if missing_policy=='reject' and not complete.all():raise DatasetError('MISSING_VALUES','Choose an explicit missing policy')
    size=len(columns);matrix=[[None]*size for _ in columns];counts=[[0]*size for _ in columns];pairs=[]
    for i,name in enumerate(columns):
        values=arrays[name][complete if missing_policy=='listwise' else ~np.isnan(arrays[name])]
        counts[i][i]=len(values)
        if len(values)>=2 and np.ptp(values)>0:matrix[i][i]=1.
    for i,j in combinations(range(size),2):
        left,right=columns[i],columns[j];a,b=arrays[left],arrays[right]
        mask=complete if missing_policy=='listwise' else ~np.isnan(a)&~np.isnan(b)
        a,b=a[mask],b[mask];n=len(a)
        row={'left':left,'right':right,'n':n,'coefficient':None,'p_value':None,'confidence_interval':None,'reason':None,
             'inference_reason':'NOT_REQUESTED' if inference=='none' else None}
        row['positions_sha256']=hashlib.sha256(np.asarray(sample.positions,dtype='<i8')[mask].tobytes()).hexdigest()
        if n<2:row['reason']='INSUFFICIENT_SAMPLE'
        elif np.ptp(a)==0 or np.ptp(b)==0:row['reason']='CONSTANT_COLUMN'
        else:
            with warnings.catch_warnings(record=True) as emitted:
                r=stats.pearsonr(a,b) if method=='pearson' else stats.spearmanr(a,b)
            value=float(r.statistic)
            if not math.isfinite(value):raise DatasetError('NUMERICAL_FAILURE','Correlation is not finite')
            row['coefficient']=value
            if emitted:row['warnings']=[type(w.message).__name__ for w in emitted]
            if inference!='none':
                if n<3:row['inference_reason']='INSUFFICIENT_SAMPLE'
                elif method=='spearman' and n<=500:row['inference_reason']='PERMUTATION_REQUIRED_NOT_IMPLEMENTED'
                else:
                    row['p_value']=float(r.pvalue)
                    if method=='pearson' and n>3:
                        ci=r.confidence_interval(confidence_level=1-alpha)
                        row['confidence_interval']=[float(ci.low),float(ci.high)]
        matrix[i][j]=matrix[j][i]=row['coefficient'];counts[i][j]=counts[j][i]=n;pairs.append(row)
    eigenvalues=None;psd=None
    if all(v is not None for row in matrix for v in row):
        eigenvalues=np.linalg.eigvalsh(matrix).tolist();psd=min(eigenvalues)>=-1e-10
    return {'method':method,'missing_policy':missing_policy,'inference':inference,'alternative':'two-sided','alpha':alpha,
        'columns':columns,'matrix':matrix,'sample_counts':counts,'pairs':pairs,'eigenvalues':eigenvalues,'positive_semidefinite':psd,
        'warnings':['PAIRWISE_MATRIX_NOT_PSD'] if psd is False else [],'sample_summary':sample.summary}
