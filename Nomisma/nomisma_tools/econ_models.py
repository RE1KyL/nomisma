"""Dataset-bound classical OLS. Internal API; no formula/code/kwargs dispatch."""
from dataclasses import dataclass,replace
import hashlib,importlib.metadata as md,math,warnings
import numpy as np
import pandas as pd
from analysis_sample import load_analysis_input,prepare_sample,build_design_matrix
from analysis_result import encode_result,normalize_result
from dataset_store import DatasetError


@dataclass(frozen=True)
class OLSAnalysis:
    sample: object
    design: dict
    fitted: object
    parameters: dict
    computation: dict
    seal: str


def require_economic(store):
    store.check_context()
    if store.context['mode']!='economic':raise DatasetError('MODE_NOT_ALLOWED','Economic analysis requires an active economic project')


def _ols_library():
    try:
        if md.version('statsmodels')!='0.15.0':raise DatasetError('DEPENDENCY_VERSION_MISMATCH','The validated statsmodels version is required')
        from statsmodels.regression.linear_model import OLS
        return OLS
    except (ImportError,md.PackageNotFoundError,OSError) as error:
        raise DatasetError('DEPENDENCY_UNAVAILABLE','Validated statsmodels could not be loaded') from error


def _finite(value):
    result=float(value)
    if not math.isfinite(result):raise DatasetError('NUMERICAL_FAILURE','OLS returned a non-finite statistic')
    return result


def _seal(analysis):
    digest=hashlib.sha256(encode_result({'metadata':analysis.sample.metadata,'summary':analysis.sample.summary,
        'roles':analysis.sample.roles,'positions':analysis.sample.positions,'parameters':analysis.parameters,
        'computation':analysis.computation,'design':{k:v for k,v in analysis.design.items() if k!='matrix'},
        'fit_state':{'df_resid':float(analysis.fitted.df_resid),'df_model':float(analysis.fitted.df_model),
                     'scale':float(analysis.fitted.scale),'use_t':bool(analysis.fitted.use_t),
                     'cov_type':analysis.fitted.cov_type}}))
    digest.update(pd.util.hash_pandas_object(analysis.sample.frame,index=True).to_numpy().tobytes())
    for a in [analysis.design['matrix'],analysis.fitted.model.exog,analysis.fitted.model.endog,
              analysis.fitted.params,analysis.fitted.resid,analysis.fitted.fittedvalues,analysis.fitted.cov_params(),
              analysis.fitted.bse,analysis.fitted.tvalues,analysis.fitted.pvalues,
              analysis.fitted.conf_int(alpha=analysis.parameters['alpha'])]:
        digest.update(np.ascontiguousarray(a,dtype='<f8').tobytes())
    return digest.hexdigest()


def validate_analysis(store,analysis):
    require_economic(store)
    if not isinstance(analysis,OLSAnalysis):raise DatasetError('ANALYSIS_INVALID','A verified in-memory OLS analysis is required')
    if any(analysis.sample.metadata[k]!=store.context[k] for k in ('mode','project_id','session_id')):
        raise DatasetError('PROJECT_CONTEXT_INVALID','OLS analysis belongs to another project or session')
    if _seal(analysis)!=analysis.seal:raise DatasetError('STALE_ANALYSIS','OLS sample or fitted result changed after computation')
    metadata=store.metadata(analysis.sample.metadata['dataset_id'])
    if metadata!=analysis.sample.metadata:raise DatasetError('STALE_DATASET','OLS source metadata changed')


def fit_ols(store,dataset_id,*,expected_sha256,y,x_numeric,categorical=None,reference_levels=None,
            intercept=True,missing_policy='reject',filters=None,date_column=None,alpha=.05,covariance='classical'):
    require_economic(store)
    categorical=[] if categorical is None else categorical
    reference_levels={} if reference_levels is None else reference_levels
    if (not isinstance(y,str) or not isinstance(x_numeric,list) or not isinstance(categorical,list)
            or any(not isinstance(v,str) for v in [*x_numeric,*categorical])
            or y in [*x_numeric,*categorical] or not isinstance(intercept,bool)
            or date_column is not None and not isinstance(date_column,str)
            or covariance!='classical' or missing_policy not in ('reject','complete_case')
            or isinstance(alpha,bool) or not isinstance(alpha,(int,float)) or not 0<alpha<1):
        raise DatasetError('INVALID_ARGUMENT','OLS requires explicit variables, classical covariance and valid sample/alpha policies')
    source=load_analysis_input(store,dataset_id,expected_sha256)
    roles={'y':y}
    if x_numeric or categorical:roles['x']=[*x_numeric,*categorical]
    if date_column is not None:roles['date']=date_column
    columns=list(dict.fromkeys([y,*x_numeric,*categorical,*([date_column] if date_column is not None else [])]))
    sample=prepare_sample(source,columns=columns,roles=roles,missing_policy=missing_policy,filters=filters)
    design=build_design_matrix(sample,x_numeric=x_numeric,categorical=categorical,
                               reference_levels=reference_levels,intercept=intercept)
    matrix=design['matrix'];dependent=sample.frame[y].to_numpy(dtype=float,na_value=np.nan)
    if not intercept and any(np.ptp(matrix[:,j])==0 for j in range(matrix.shape[1])):
        raise DatasetError('IMPLICIT_CONSTANT','Constant predictors require an explicitly declared intercept')
    OLS=_ols_library()
    try:
        with warnings.catch_warnings(record=True) as emitted,np.errstate(over='raise',invalid='raise',divide='raise'):
            fitted=OLS(dependent,matrix,hasconst=intercept,missing='raise').fit(method='qr',cov_type='nonrobust',use_t=True)
            scale=float(np.max(np.abs(dependent)))
            if np.linalg.norm(fitted.resid/scale)<=64*np.finfo(float).eps*np.sqrt(len(dependent)):
                raise DatasetError('PERFECT_FIT','Residual scale is numerically zero; classical inference is not reported')
            sse=_finite(fitted.ssr)
            if sse<=0:raise DatasetError('NUMERICAL_FAILURE','Residual variance underflowed')
            intervals=fitted.conf_int(alpha=alpha);coefficients=[]
            for i,name in enumerate(design['columns']):
                coefficients.append({'term':name,'estimate':_finite(fitted.params[i]),'standard_error':_finite(fitted.bse[i]),
                    'statistic':_finite(fitted.tvalues[i]),'p_value':_finite(fitted.pvalues[i]),
                    'confidence_interval':[_finite(v) for v in intervals[i]],'null_value':0.,'alternative':'two-sided'})
            df_model=int(fitted.df_model);df_resid=int(fitted.df_resid)
            overall=({'statistic':_finite(fitted.fvalue),'p_value':_finite(fitted.f_pvalue),'df_num':df_model,'df_den':df_resid,
                      'reason':None} if df_model>0 else {'statistic':None,'p_value':None,'df_num':0,'df_den':df_resid,'reason':'NO_SLOPE_TERMS'})
            computation={'method':'ols','analysis_domain':'economic','algorithm_version':'nomisma-ols-classical-1',
                'coefficients':coefficients,'covariance_matrix':[[_finite(v) for v in row] for row in fitted.cov_params()],
                'covariance':{'kind':'classical','library_kind':'nonrobust','use_t':True,'distribution':'Student-t','df':df_resid},
                'n':len(dependent),'rank':design['rank'],'df_model':df_model,'df_resid':df_resid,'sse':sse,
                'residual_variance':_finite(fitted.scale),'r_squared':_finite(fitted.rsquared),
                'adjusted_r_squared':_finite(fitted.rsquared_adj),'r_squared_definition':'centered' if intercept else 'uncentered',
                'overall_f':overall,'condition_number':_finite(design['condition_number']),
                'design_columns':design['columns'],'design_sha256':design['matrix_sha256'],
                'categorical_encoding':design['categorical_encoding'],'alpha':alpha,
                'assumptions':['Linear conditional mean and exogeneity are not established by fitting.',
                    'Classical finite-sample t/F inference assumes independent homoskedastic normal disturbances.'],
                'warnings':[*design['warnings'],'CLASSICAL_COVARIANCE_ASSUMPTIONS_NOT_TESTED',
                    *(['TIME_ORDER_NOT_YET_VALIDATED'] if date_column else []),
                    *(['SMALL_SAMPLE'] if len(dependent)<30 else []),*[type(w.message).__name__ for w in emitted]]}
    except (np.linalg.LinAlgError,FloatingPointError,OverflowError,ValueError) as error:
        raise DatasetError('NUMERICAL_FAILURE','Classical OLS computation failed') from error
    parameters={'y':y,'x_numeric':x_numeric.copy(),'categorical':categorical.copy(),'reference_levels':reference_levels.copy(),
        'intercept':intercept,'missing_policy':missing_policy,'filters':filters or [],'date_column':date_column,
        'alpha':alpha,'covariance':'classical','use_t':True}
    analysis=OLSAnalysis(sample,design,fitted,normalize_result(parameters),normalize_result(computation),'')
    if store.metadata(dataset_id)!=sample.metadata:raise DatasetError('STALE_DATASET','OLS input changed during computation')
    return replace(analysis,seal=_seal(analysis))


def coefficient_test(store,analysis,*,term,null_value=0.,alternative='two-sided',alpha=.05):
    validate_analysis(store,analysis)
    if (not isinstance(term,str) or term not in analysis.design['columns'] or alternative not in ('two-sided','greater','less')
            or isinstance(null_value,bool) or not isinstance(null_value,(int,float)) or not math.isfinite(null_value)
            or isinstance(alpha,bool) or not isinstance(alpha,(int,float)) or not 0<alpha<1):
        raise DatasetError('INVALID_ARGUMENT','Invalid named coefficient hypothesis')
    index=analysis.design['columns'].index(term);row=np.zeros((1,len(analysis.design['columns'])));row[0,index]=1
    try:
        test=analysis.fitted.t_test((row,np.array([null_value])),use_t=True)
        statistic=_finite(np.asarray(test.tvalue).item())
        from scipy.stats import t
        p=(_finite(np.asarray(test.pvalue).item()) if alternative=='two-sided' else
            _finite(t.sf(statistic,analysis.fitted.df_resid) if alternative=='greater' else t.cdf(statistic,analysis.fitted.df_resid)))
        interval=analysis.fitted.conf_int(alpha=alpha)[index]
    except (ValueError,FloatingPointError,OverflowError) as error:raise DatasetError('NUMERICAL_FAILURE','Coefficient test failed') from error
    return {'term':term,'null_value':null_value,'alternative':alternative,'statistic':statistic,'p_value':p,
        'df':int(analysis.fitted.df_resid),'distribution':'Student-t','covariance':'classical','alpha':alpha,
        'two_sided_confidence_interval':[_finite(v) for v in interval]}


def joint_test(store,analysis,*,restrictions):
    validate_analysis(store,analysis);columns=analysis.design['columns']
    if not isinstance(restrictions,list) or not 1<=len(restrictions)<=min(20,len(columns)):
        raise DatasetError('INVALID_ARGUMENT','A bounded list of named linear restrictions is required')
    matrix=[];values=[]
    for restriction in restrictions:
        if (not isinstance(restriction,dict) or set(restriction)!={'weights','value'}
                or not isinstance(restriction['weights'],dict) or not restriction['weights']
                or set(restriction['weights'])-set(columns)):
            raise DatasetError('INVALID_ARGUMENT','Restrictions use only existing coefficient names')
        scalars=[*restriction['weights'].values(),restriction['value']]
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>1e12 for v in scalars):
            raise DatasetError('INVALID_ARGUMENT','Restriction weights and values must be bounded finite numbers')
        matrix.append([restriction['weights'].get(name,0.) for name in columns]);values.append(restriction['value'])
    matrix=np.asarray(matrix,dtype=float)
    if np.linalg.matrix_rank(matrix)!=len(matrix):raise DatasetError('INVALID_RESTRICTION','Linear restrictions are not independent')
    try:
        test=analysis.fitted.f_test((matrix,np.array(values,dtype=float)))
        return {'restrictions':restrictions,'statistic':_finite(np.asarray(test.fvalue).item()),
            'p_value':_finite(np.asarray(test.pvalue).item()),'df_num':int(test.df_num),'df_den':int(test.df_denom),
            'distribution':'F','covariance':'classical'}
    except (ValueError,np.linalg.LinAlgError,FloatingPointError,OverflowError) as error:
        raise DatasetError('NUMERICAL_FAILURE','Joint hypothesis test failed') from error


def residual_table(store,analysis):
    validate_analysis(store,analysis)
    data={'source_row':list(analysis.sample.positions),'observed':analysis.fitted.model.endog.copy(),
          'fitted':analysis.fitted.fittedvalues.copy(),'residual':analysis.fitted.resid.copy()}
    if 'date' in analysis.sample.roles:
        # ISO text avoids loss of timezone/precision in the existing canonical CSV adapter.
        data['date']=[v.isoformat() for v in analysis.sample.frame[analysis.sample.roles['date']]]
    return pd.DataFrame(data)


def publish_ols(store,analysis):
    """Trusted internal caller only; not an approval/cancel/job implementation."""
    validate_analysis(store,analysis)
    from analysis_result import make_analysis_result
    from analysis_store import save_analysis
    result=make_analysis_result(analysis.sample,analysis.computation,parameters=analysis.parameters)
    table=pd.DataFrame([{k:v for k,v in row.items() if k!='confidence_interval'}|
                        {'ci_low':row['confidence_interval'][0],'ci_high':row['confidence_interval'][1]}
                        for row in analysis.computation['coefficients']])
    return save_analysis(store,result,tables={'coefficients':table,'residuals':residual_table(store,analysis)},
        preview=table.to_dict(orient='records'),before_commit=lambda:validate_analysis(store,analysis))
