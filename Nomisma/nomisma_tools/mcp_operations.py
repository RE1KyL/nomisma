"""Fixed adapters over accepted data/statistics/economic/finance implementations.

This module runs in an owned job workspace. It never accepts Python, commands,
roots, environment, function names, or unchecked output destinations from a model.
"""
from dataclasses import replace
import json
import pandas as pd
from dataset_store import DatasetError,file_sha256
from dataset_io import load_dataset,_read_table,import_dataset
from analysis_result import make_analysis_result,encode_result
from analysis_store import save_analysis,read_analysis
from analysis_sample import load_analysis_input,prepare_sample
from mcp_contract import bounded


def wrap(data,execution_id,*,artifacts=None,warnings=None):
    return {'ok':True,'summary':'Nomisma operation computed and verified',
            'data':{'execution_id':execution_id,**data},'artifacts':artifacts or [],'warnings':warnings or [],'error':None}


def small(result):
    """Trim only display fields, never counts, facts, warnings or error semantics."""
    data=result['data']
    for key in ('sample','fields','preview'):
        if key in data:
            data[key+'_omitted']=data.get(key+'_omitted',0)
    while True:
        try:return bounded(result)
        except DatasetError as error:
            if error.code!='RESOURCE_LIMIT':raise
            for key in ('sample','fields','preview'):
                if isinstance(data.get(key),list) and data[key]:
                    data[key].pop();data[key+'_omitted']+=1;break
            else:raise


def _analysis(store,analysis,execution_id,request_id,*,source_analysis=None):
    from finance_common import validate_finance
    from econ_models import validate_analysis,residual_table
    domain=analysis.computation['analysis_domain']
    if domain=='finance':validate_finance(store,analysis);tables=analysis.tables
    else:
        validate_analysis(store,analysis)
        tables={'residuals':residual_table(store,analysis)}
    tables={k:v.copy(deep=True) for k,v in tables.items()}
    for frame in tables.values():
        for column in frame:
            if pd.api.types.is_datetime64_any_dtype(frame[column]):
                frame[column]=frame[column].map(lambda v:None if pd.isna(v) else v.isoformat()).astype(object)
    parameters=analysis.parameters.copy()
    if source_analysis is not None:parameters['source_analysis']=source_analysis
    result=make_analysis_result(analysis.sample,analysis.computation,parameters=parameters,
                                execution_id=execution_id,request_id=request_id)
    # Important facts stay visible in the bounded receipt. Full computation is
    # immutable results.json; no invented coefficient/metric/diagnostic summary.
    if domain=='economic':facts={k:v for k,v in analysis.computation.items() if k not in ('covariance_matrix','warnings','assumptions','categorical_encoding')}
    else:facts={k:v for k,v in analysis.computation.items() if k not in ('warnings',)}
    receipt=save_analysis(store,result,tables=tables)
    receipt['data']['facts']=facts
    return small(receipt)


def execute(store,name,args,execution_id,*,credential_root=None):
    """Only a literal whitelist reaches real functions; no dynamic getattr/eval."""
    if name in ('nomisma_explore_fetch','nomisma_akshare_fetch'):
        from privacy_policy import require_external_network
        require_external_network()
    p=args.get('parameters',{}).copy();ref=args.get('dataset',{})
    dataset=ref.get('dataset_id');sha=ref.get('sha256');request=args.get('request_id')
    if name.startswith('nomisma_macro_'):
        from economic_sources import acquire,describe,discover,import_nbs,status
        if name=='nomisma_macro_discover':data={'indicators':discover(**{('country' if k=='country_or_region' else k):v for k,v in args.items()})}
        elif name=='nomisma_macro_metadata':
            data={'source_status':status(args['source'],credential_root)}
            if 'indicator_id' in args:data['indicator']=describe(args['source'],args['indicator_id'],args.get('country_or_region'))
        elif name=='nomisma_macro_fetch':data=acquire(store,p,credential_root=credential_root)
        elif name=='nomisma_macro_import_nbs':data=import_nbs(store,p)
        else:raise DatasetError('TOOL_NOT_ALLOWED','Unknown macro tool')
        return small(wrap(data,execution_id))
    if name=='nomisma_explore_fetch':
        from explore_api import fetch
        return small(wrap(fetch(store,p,execution_id,request),execution_id))
    if name=='nomisma_explore_mine':
        from explore_mining import mine
        data,artifacts=mine(store,ref,p,execution_id,request)
        return small(wrap(data,execution_id,artifacts=artifacts))
    if name in ('nomisma_explore_query','nomisma_explore_quality','nomisma_explore_transform','nomisma_explore_export'):
        from explore_engines import query,quality,transform,export
        if name=='nomisma_explore_transform':return small(wrap(transform(store,ref,p,execution_id,request),execution_id))
        data,artifacts={'nomisma_explore_query':query,'nomisma_explore_quality':quality,
            'nomisma_explore_export':export}[name](store,ref,p,execution_id,request)
        return small(wrap(data,execution_id,artifacts=artifacts))
    if name=='nomisma_explore_process':
        from explore_processing import process
        return small(wrap(process(store,ref,p,execution_id,request),execution_id))
    if name=='nomisma_explore_profile':
        from explore_analysis import profile
        data,artifacts=profile(store,ref,p,execution_id,request)
        return small(wrap(data,execution_id,artifacts=artifacts))
    if name in ('nomisma_explore_probe','nomisma_explore_import'):
        from explore_store import ingest
        return small(wrap(ingest(store,p,execution_id,request,probe=name.endswith('probe')),execution_id))
    if name=='nomisma_econ_panel':
        from econ_panel import compute_panel
        return compute_panel(store,args['dataset'],p,execution_id,request)
    if name=='nomisma_fin_scenario':
        from quant_research import research_plan,run_research
        from quant_experiments import read_result
        cfg=args['configuration'];refs=args['inputs']
        plan=research_plan({'objective':args['objective'],'research_question':args['research_question'],'requested_methods':[]},available_roles=refs,locked_constraints=cfg)
        outcome=run_research(store,plan=plan,inputs=refs,configuration=cfg,locked_constraints=cfg)
        if not outcome['ok']:raise DatasetError(outcome['error_code'],'Research scenario failed without published success')
        tables,report=read_result(store,outcome['result'])
        artifacts=[{'path':d['path'],'sha256':d['sha256']} for d in report['tables'].values()]
        artifacts += [{'path':outcome['result']['path'],'sha256':outcome['result']['sha256']},
          {'path':outcome['attempt']['attempt_path'],'sha256':outcome['attempt']['sha256']}]
        return small(wrap({'execution_id':execution_id,'run_id':outcome['result']['run_id'],'result':outcome['result'],
          'attempt':outcome['attempt'],'strict_backtest_ready':False,'tables':list(report['tables']),
          'performance':report['summary'].get('performance')},execution_id,artifacts=artifacts,
          warnings=['Research scenario only; not certified historical A-share tradability. No broker or live-trading capability.']))
    if name=='nomisma_akshare_catalog':
        from akshare_registry import source_catalog
        rows=source_catalog()
        return wrap({'verified':[r for r in rows if r['status']=='verified'],
                     'disabled_candidates':[r for r in rows if r['status']=='disabled_candidate'],
                     'planned_callable':False},execution_id)
    if name=='nomisma_dataset_inspect':
        frame,meta=load_dataset(store,dataset,expected_sha256=sha)
        data=store.summary(meta,frame);data['sample']=data['sample'][:p.get('sample_rows',5)]
        data.update({k:meta[k] for k in ('data_start','data_end','fetched_at','request_parameters','package_version')})
        return small(wrap(data,execution_id))
    if name=='nomisma_akshare_fetch':
        from akshare_adapter import fetch_hs300
        return small(wrap(fetch_hs300(store,**p),execution_id))
    if name=='nomisma_dataset_import':
        path=store.path(p['source_path'])
        if file_sha256(path)!=p['expected_sha256']:raise DatasetError('STALE_SOURCE','Import source version changed')
        if args['action']=='probe':
            table=_read_table(path,**{k:v for k,v in p.items() if k in ('encoding','delimiter','sheet','header_row','column_names','empty_fields_are_null','formula_policy')})
            return small(wrap({'source_path':p['source_path'],'source_sha256':p['expected_sha256'],
                'row_count':len(table.frame),'column_count':len(table.frame.columns),'column_names':list(table.frame.columns),
                'parser_options':table.parser_options,'source_type':'user_provided','committed':False},execution_id))
        return small(wrap(import_dataset(store,**p),execution_id))
    if name=='nomisma_dataset_quality':
        from data_quality import evaluate_quality,check_dataset_quality
        if args['save_report']:
            report=check_dataset_quality(store,dataset,expected_sha256=sha,**p)
            full=report.pop('report');artifacts=[{'path':report['report_path'],'sha256':report['sha256']}]
        else:
            frame,_=load_dataset(store,dataset,expected_sha256=sha)
            full=evaluate_quality(frame,dataset_id=dataset,**p);report={};artifacts=[]
        return small(wrap({'dataset_id':dataset,'input_sha256':sha,**report,'quality':full},execution_id,
                          artifacts=artifacts,warnings=['OUTLIER_CANDIDATES_ARE_NOT_ERRORS; NO_CLEANING_PERFORMED']))
    if name=='nomisma_dataset_clean':
        from data_cleaning import clean_dataset
        return small(wrap(clean_dataset(store,dataset,expected_sha256=sha,**p),execution_id))
    if name=='nomisma_dataset_export':
        from data_export import export_dataset
        data=export_dataset(store,dataset,expected_sha256=sha,compatibility_profile='desktop',**p)
        # Detailed provenance/history remain in the verified artifact descriptor.
        data={k:v for k,v in data.items() if k not in ('column_schema','processing_history','serialization','escaped_cells')}
        return small(wrap(data,execution_id,artifacts=[{'path':data['output_file'],'sha256':data['output_sha256']}]))
    if name in ('nomisma_analysis_describe','nomisma_analysis_correlate'):
        from analysis_statistics import describe,correlate
        source=load_analysis_input(store,dataset,sha)
        missing=p.pop('missing_policy');filters=p.pop('filters',[]);columns=p.pop('columns')
        sample=prepare_sample(source,columns=columns,filters=filters,missing_policy='available' if missing in ('available','listwise','pairwise') else missing)
        computation=(describe(sample,columns,**p) if name.endswith('describe') else correlate(sample,columns,missing_policy=missing,**p))
        parameters={'columns':columns,'missing_policy':missing,'filters':filters,**p}
        result=make_analysis_result(sample,computation,parameters=parameters,execution_id=execution_id,request_id=request)
        receipt=save_analysis(store,result);receipt['data']['facts']=computation
        return small(receipt)
    if name=='nomisma_econ_ols':
        from econ_models import fit_ols
        return _analysis(store,fit_ols(store,dataset,expected_sha256=sha,**p),execution_id,request)
    if name=='nomisma_econ_diagnostics':
        from econ_models import fit_ols
        from econ_diagnostics import diagnose_ols
        ref=args['analysis'];original,_=read_analysis(store,ref['analysis_id'],expected_sha256=ref['sha256'],namespace='economic')
        if original['method']!='ols':raise DatasetError('ANALYSIS_INVALID','Diagnostics require a classical OLS reference')
        params=original['parameters'].copy();params.pop('use_t',None)
        analysis=fit_ols(store,original['dataset_id'],expected_sha256=original['input_sha256'],**params)
        if encode_result(analysis.computation)!=encode_result(original['results']):
            raise DatasetError('STALE_ANALYSIS','Reconstructed OLS differs from the recorded computation')
        computation=diagnose_ols(store,analysis,**p)
        result=make_analysis_result(analysis.sample,computation,parameters={**p,'source_analysis':ref},execution_id=execution_id,request_id=request)
        receipt=save_analysis(store,result);receipt['data']['facts']=computation
        return small(receipt)
    if name=='nomisma_fin_returns':
        from finance_returns import analyze_returns
        analysis=analyze_returns(store,dataset,expected_sha256=sha,**p)
    elif name=='nomisma_fin_risk':
        from finance_risk import analyze_risk,risk_from_returns
        if 'analysis' in args:
            from finance_returns import analyze_returns
            ref=args['analysis'];original,_=read_analysis(store,ref['analysis_id'],expected_sha256=ref['sha256'],namespace='finance')
            if original['method']!='fin_returns':raise DatasetError('ANALYSIS_INVALID','Risk reference must be a verified price-return analysis')
            parameters={k:original['parameters'][k] for k in ('date_column','price_column','asset_column','return_kind',
                'missing_policy','price_adjustment','frequency','periods_per_year')}
            prior=analyze_returns(store,original['dataset_id'],expected_sha256=original['input_sha256'],**parameters)
            if encode_result(prior.computation)!=encode_result(original['results']):
                raise DatasetError('STALE_ANALYSIS','Reconstructed returns differ from recorded results')
            analysis=risk_from_returns(store,prior,**p)
        else:analysis=analyze_risk(store,dataset,expected_sha256=sha,**p)
    elif name=='nomisma_fin_timeseries':
        from finance_timeseries import rolling_statistics,moving_average,return_distribution,asset_correlations
        action=p.pop('action')
        handlers={'rolling':rolling_statistics,'moving_average':moving_average,'distribution':return_distribution,'asset_correlations':asset_correlations}
        analysis=handlers[action](store,dataset,expected_sha256=sha,**p)
    elif name=='nomisma_fin_factor':
        from finance_factor import analyze_factor
        analysis=analyze_factor(store,dataset,expected_sha256=sha,**p)
    elif name=='nomisma_fin_portfolio':
        from finance_portfolio import analyze_portfolio
        analysis=analyze_portfolio(store,dataset,expected_sha256=sha,**p)
    else:raise DatasetError('MODE_NOT_ALLOWED','Tool does not have an approved adapter')
    return _analysis(store,analysis,execution_id,request,source_analysis=args.get('analysis'))
