"""Fixed diagnostics for a verified OLS object; no arbitrary function dispatch."""
import warnings
import numpy as np
from econ_models import validate_analysis,_finite
from dataset_store import DatasetError


def _ordered(analysis):
    name=analysis.sample.roles.get('date')
    if name is None:raise DatasetError('TIME_ORDER_REQUIRED','Declare a date column before serial diagnostics')
    values=analysis.sample.frame[name]
    if values.isna().any() or values.duplicated().any() or not values.is_monotonic_increasing:
        raise DatasetError('TIME_ORDER_INVALID','Serial diagnostics require unique increasing dates; no automatic sorting occurred')
    return {'date_column':name,'order':'strictly_increasing','lag_unit':'observation',
            'calendar_frequency':'unverified','warning':'Observation lags do not establish equal calendar spacing.'}


def diagnose_ols(store,analysis,*,tests=None,bp_robust=True,bg_lags=None):
    validate_analysis(store,analysis)
    tests=['design','vif','jarque_bera'] if tests is None else tests
    allowed={'design','vif','breusch_pagan','white','jarque_bera','durbin_watson','breusch_godfrey'}
    if (not isinstance(tests,list) or not tests or any(not isinstance(t,str) or t not in allowed for t in tests)
            or len(set(tests))!=len(tests) or not isinstance(bp_robust,bool)
            or bg_lags is not None and ('breusch_godfrey' not in tests or type(bg_lags)!=int or not 1<=bg_lags<=12)):
        raise DatasetError('INVALID_ARGUMENT','Diagnostics require a fixed unique test list and explicit bounded lags')
    if 'breusch_godfrey' in tests and bg_lags is None:raise DatasetError('INVALID_ARGUMENT','Breusch-Godfrey requires explicit lags')
    from statsmodels.stats.diagnostic import het_breuschpagan,het_white,acorr_breusch_godfrey
    from statsmodels.stats.stattools import jarque_bera,durbin_watson
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    matrix=analysis.design['matrix'];residuals=analysis.fitted.resid;n,p=matrix.shape
    output={};notes=[]
    try:
        with warnings.catch_warnings(record=True) as emitted,np.errstate(over='raise',divide='raise',invalid='raise'):
            for test in tests:
                if test in ('vif','breusch_pagan','white') and not analysis.parameters['intercept']:
                    raise DatasetError('DIAGNOSTIC_REQUIREMENT','This diagnostic adapter requires an explicit intercept')
                if test=='design':
                    output[test]={'rank':analysis.design['rank'],'columns':analysis.design['columns'],
                        'condition_number':_finite(analysis.design['condition_number']),
                        'singular_values':[_finite(v) for v in np.linalg.svd(matrix,compute_uv=False)],
                        'warnings':analysis.design['warnings']}
                elif test=='vif':
                    rows=[]
                    for j,name in enumerate(analysis.design['columns']):
                        if name=='__intercept__':continue
                        value=_finite(variance_inflation_factor(matrix,j))
                        rows.append({'term':name,'vif':value,'auxiliary_r_squared':1-1/value,
                                     'warning':'HIGH_VIF' if value>10 else None})
                    output[test]={'columns':rows,'intercept_excluded':True,'warning_threshold':10,'threshold_is_not_a_test':True}
                elif test in ('breusch_pagan','white'):
                    if p<2:raise DatasetError('DIAGNOSTIC_REQUIREMENT','Variance diagnostics need at least one slope')
                    if np.ptp(residuals**2)==0:raise DatasetError('DIAGNOSTIC_DEGENERATE','Squared residuals are constant')
                    if test=='white':
                        q=p*(p+1)//2
                        if q>100 or n*q>2_000_000:raise DatasetError('RESOURCE_LIMIT','White auxiliary expansion exceeds its dimension limit')
                        if n<=q:raise DatasetError('SAMPLE_TOO_SMALL','White auxiliary regression needs positive residual degrees of freedom')
                        left,right=np.triu_indices(p);auxiliary=matrix[:,left]*matrix[:,right]
                        rank=int(np.linalg.matrix_rank(auxiliary))
                        if rank!=q:raise DatasetError('DIAGNOSTIC_RANK_DEFICIENT','White auxiliary terms are dependent; none were silently dropped')
                        values=het_white(residuals,matrix,interaction_terms=True);lm_df=rank-1
                        details={'interaction_terms':True,'auxiliary_columns':q,'auxiliary_rank':rank,'df_resid':n-rank}
                    else:
                        values=het_breuschpagan(residuals,matrix,robust=bp_robust);lm_df=p-1
                        details={'variant':'Koenker' if bp_robust else 'Breusch-Pagan normality-based','robust':bp_robust,'df_resid':n-p}
                    output[test]={'lm':_finite(values[0]),'lm_p_value':_finite(values[1]),'lm_df':lm_df,
                        'f':_finite(values[2]),'f_p_value':_finite(values[3]),'f_df_num':lm_df,**details}
                    notes.append('HETEROSKEDASTICITY_TEST_P_VALUES_ARE_ASYMPTOTIC')
                elif test=='jarque_bera':
                    if n<8:raise DatasetError('SAMPLE_TOO_SMALL','Jarque-Bera adapter requires at least eight observations')
                    jb,p_value,skew,kurtosis=jarque_bera(residuals)
                    output[test]={'statistic':_finite(jb),'p_value':_finite(p_value),'df':2,'skewness':_finite(skew),
                        'kurtosis':_finite(kurtosis),'kurtosis_definition':'Pearson, normal=3','distribution':'chi-square asymptotic'}
                    notes.append('NORMALITY_NOT_ESTABLISHED_BY_A_NON_SIGNIFICANT_TEST')
                    if n<2000:notes.append('JARQUE_BERA_ASYMPTOTIC_SMALL_SAMPLE_LIMITATION')
                elif test=='durbin_watson':
                    order=_ordered(analysis)
                    output[test]={'statistic':_finite(durbin_watson(residuals)),'p_value':None,
                                  'p_value_reason':'NOT_PROVIDED_BY_DURBIN_WATSON','ordering':order}
                elif test=='breusch_godfrey':
                    order=_ordered(analysis)
                    if n<=p+bg_lags:raise DatasetError('SAMPLE_TOO_SMALL','BG auxiliary regression needs positive residual degrees of freedom')
                    lagged=np.zeros((n,bg_lags))
                    for j in range(1,bg_lags+1):lagged[j:,j-1]=residuals[:-j]
                    auxiliary=np.column_stack([matrix,*([] if analysis.parameters['intercept'] else [np.ones(n)]),lagged])
                    rank=int(np.linalg.matrix_rank(auxiliary))
                    if rank!=auxiliary.shape[1] or n<=rank:raise DatasetError('DIAGNOSTIC_RANK_DEFICIENT','BG auxiliary regression is not identifiable')
                    values=acorr_breusch_godfrey(analysis.fitted,nlags=bg_lags,result_object=True)
                    output[test]={'lm':_finite(values.lm),'lm_p_value':_finite(values.lmpval),'lm_df':bg_lags,
                        'f':_finite(values.fval),'f_p_value':_finite(values.fpval),'f_df_num':bg_lags,'f_df_den':n-rank,
                        'lags':bg_lags,'n':n,'initial_residual_lags':'zero-padded, statsmodels 0.15.0 convention','ordering':order}
                    notes.append('BG_P_VALUES_ARE_ASYMPTOTIC; OBSERVATION_LAGS_DO_NOT_PROVE_A_REGULAR_CALENDAR')
            notes.extend(type(w.message).__name__ for w in emitted)
    except (np.linalg.LinAlgError,FloatingPointError,OverflowError,ValueError) as error:
        raise DatasetError('NUMERICAL_FAILURE','Residual diagnostic computation failed') from error
    validate_analysis(store,analysis)
    return {'method':'ols_diagnostics','analysis_domain':'economic','algorithm_version':'nomisma-ols-diagnostics-1',
        'source_model_seal':analysis.seal,'source_model_parameters':analysis.parameters,
        'design_sha256':analysis.design['matrix_sha256'],'n':n,'tests':output,'warnings':list(dict.fromkeys(notes))}


def publish_diagnostics(store,analysis,*,tests=None,bp_robust=True,bg_lags=None):
    import pandas as pd
    from analysis_result import make_analysis_result,encode_result
    from analysis_store import save_analysis
    computation=diagnose_ols(store,analysis,tests=tests,bp_robust=bp_robust,bg_lags=bg_lags)
    parameters={'tests':list(computation['tests']),'bp_robust':bp_robust,'bg_lags':bg_lags}
    result=make_analysis_result(analysis.sample,computation,parameters=parameters)
    table=pd.DataFrame([{'diagnostic':name,'machine_result':encode_result(value).decode('utf8')}
                        for name,value in computation['tests'].items()])
    return save_analysis(store,result,tables={'diagnostics':table},
        before_commit=lambda:validate_analysis(store,analysis))
