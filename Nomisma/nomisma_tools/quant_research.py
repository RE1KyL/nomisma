"""Finite Finance research orchestration for trusted application callers.

Model intent is a structured hypothesis, not an authority. Conditions come from
actual tables and validators. Registered production tools remain unchanged until
the unified acceptance and existing manual gates have passed.
"""
from dataclasses import asdict
from pathlib import Path
from dataset_store import file_sha256
import pandas as pd
from ashare_data import fail,columns,timestamp,number
from ashare_intake import require_finance
from quant_factors import FactorSpec,form_factors,process_cross_sections,evaluate_factors
from quant_portfolio import fit_combiner,score_combiner,select_assets,estimate_covariance,construct_weights
from quant_backtest import simulate
from quant_validation import performance,walk_forward_splits,walk_forward_fit_score,block_bootstrap,compare_runs
from quant_experiments import begin_attempt,finish_attempt,read_attempt,read_result,digest
from research_data_store import resolve_table
from dataset_store import DatasetError

CAPABILITIES={
 'factor_research':{'domain':'finance','implementation':'internal','requires':['observations','membership'],
     'steps':['form_factors','process_cross_sections'],'knowledge':'finance_factor_research.md'},
 'stock_selection':{'domain':'finance','implementation':'internal','requires':['observations','membership'],
     'steps':['form_factors','process_cross_sections','fit_combiner','score_combiner','select_assets'],'knowledge':'finance_factor_research.md'},
 'strategy_evaluation':{'domain':'finance','implementation':'internal','requires':['observations','membership','bars','market_state','calendar','fee_schedule'],
     'steps':['form_factors','process_cross_sections','fit_combiner','score_combiner','select_assets','construct_weights','simulate','performance'],
     'knowledge':'finance_trading_research.md'},
 'out_of_sample':{'domain':'finance','implementation':'internal','requires':['samples','processed','labels'],
     'steps':['walk_forward_splits','fit_combiner','score_combiner'],'knowledge':'finance_validation.md'}}

def capability_catalog(*,mode):
    import json
    if mode!='finance':return []
    root=Path(__file__).resolve().parents[1]/'knowledge'
    document=json.loads((root/'finance_capabilities.json').read_text('utf8'))
    required={'tool_id','name','domain','description','scenarios','input_schema','output_schema','preconditions',
              'dependencies','validation_rules','permission','error_handling','adapter','knowledge'}
    entries=document.get('entries',[])
    if (document.get('production_enabled') is not False or {e.get('name') for e in entries}!=set(CAPABILITIES)
        or len({e.get('tool_id') for e in entries})!=len(entries)):
        fail('REGISTRY_INVALID','Capability catalog drifted from reviewed finite adapters')
    for entry in entries:
        if set(entry)!=required or entry['domain']!='finance' or entry['adapter']!='quant_research.run_research':
            fail('REGISTRY_INVALID','Capability descriptor is incomplete or unreviewed')
        if entry['knowledge']!=CAPABILITIES[entry['name']]['knowledge'] or not (root/entry['knowledge']).is_file():
            fail('REGISTRY_INVALID','Knowledge resource not found')
    return entries

def research_plan(intent,*,available_roles,locked_constraints):
    """Caller/model supplies a semantic intent; keyword classification is deliberately absent."""
    if not isinstance(intent,dict) or set(intent)!={'objective','research_question','requested_methods'}:
        fail('INVALID_INTENT','Structured research objective and question required')
    name=intent['objective']
    if name not in CAPABILITIES:fail('CAPABILITY_UNAVAILABLE','No implemented capability meets this intent')
    capability_catalog(mode='finance')
    if not isinstance(intent['research_question'],str) or not 1<=len(intent['research_question'])<=2000:
        fail('INVALID_INTENT','A bounded research question is required')
    cap=CAPABILITIES[name];missing=sorted(set(cap['requires'])-set(available_roles))
    if (not isinstance(intent['requested_methods'],list) or
        any(not isinstance(m,str) or m not in cap['steps'] for m in intent['requested_methods'])):
        fail('CAPABILITY_UNAVAILABLE','Requested method is not implemented for this capability')
    if not isinstance(locked_constraints,dict) or not locked_constraints:fail('CONSTRAINTS_REQUIRED','User research definitions must be explicit')
    plan={'schema_version':'quant-plan-1','intent':intent,'locked_constraints':locked_constraints,
      'constraints_hash':digest(locked_constraints),'steps':cap['steps'],'missing_roles':missing,
      'status':'needs_input' if missing else 'proposed','knowledge_resource':cap['knowledge'],
      'knowledge_sha256':file_sha256(Path(__file__).resolve().parents[1]/'knowledge'/cap['knowledge']),
      'execution_permission':'ask_each_registered_tool_call','production_callable':False,
      'method_rationale':'Use only the minimal implemented chain for the stated objective; do not add trading to a factor-only question'}
    plan['plan_hash']=digest(plan);return plan

def validate_plan(plan,locked_constraints):
    if not isinstance(plan,dict) or plan.get('schema_version')!='quant-plan-1':fail('INVALID_PLAN','Unknown research plan')
    data={k:v for k,v in plan.items() if k!='plan_hash'}
    if digest(data)!=plan.get('plan_hash') or digest(locked_constraints)!=plan.get('constraints_hash'):
        fail('PLAN_CHANGED','Changed research definitions need a revised plan and user direction confirmation')
    if plan['status']!='proposed':fail('PRECONDITIONS_FAILED','Plan has unresolved input requirements')

def run_research(store,*,plan,inputs,configuration,locked_constraints,cancelled=None,experiment_id=None,parent_run=None):
    """Trusted caller must own execution authorization; this is NOT an ACP permission bypass."""
    require_finance(store);validate_plan(plan,locked_constraints)
    if configuration!=locked_constraints:fail('PLAN_CHANGED','Exact computational configuration must be the reviewed locked configuration')
    required=set(CAPABILITIES[plan['intent']['objective']]['requires'])
    if not required<=set(inputs):fail('PRECONDITIONS_FAILED','Required input role missing')
    attempt=begin_attempt(store,plan=plan,inputs=inputs,experiment_id=experiment_id,parent_run=parent_run)
    tables={};summary={'capability':plan['intent']['objective'],'strict_backtest_ready':False}
    def checkpoint():
        require_finance(store)
        if cancelled and cancelled():fail('CANCELLED','Research cancelled between computational stages')
    try:
        checkpoint();data={role:resolve_table(store,ref)[0] for role,ref in inputs.items()};cfg=configuration
        if plan['intent']['objective']=='out_of_sample':
            oos=walk_forward_fit_score(data['processed'],data['labels'],data['samples'],**cfg['walk_forward'])
            folds=oos['folds'];summary['folds']=folds;summary['models']=oos['models'];summary['diagnostics']=oos['diagnostics']
            tables['oos_scores']=oos['scores']
            tables['split_membership']=pd.DataFrame([{'fold':f['fold'],'sample_id':sample,'role':role}
                for f in folds for role,key in [('train','train_ids'),('test','test_ids')] for sample in f[key]])
        else:
            specs=[FactorSpec(**{**s,'inputs':tuple(s['inputs'])}) for s in cfg['factors']]
            factors=form_factors(data['observations'],data['membership'],sessions=cfg['sessions'],specs=specs,
                formation_times=cfg['formation_times'],cancelled=cancelled);tables['factors']=factors;checkpoint()
            processed=process_cross_sections(factors,controls=data.get('controls'),**cfg['processing']);tables['processed']=processed
            if 'labels' in data and 'evaluation' in cfg:
                e=evaluate_factors(processed,data['labels'],**cfg['evaluation']);tables['ic']=e['ic'];tables['factor_groups']=e['groups'];summary['factor_evaluation']=e['summary']
                tables['group_returns']=e['group_returns'];tables['group_turnover']=e['group_turnover']
            if plan['intent']['objective'] in ('stock_selection','strategy_evaluation'):
                checkpoint();model=fit_combiner(processed,data.get('labels'),**cfg['combiner']);summary['combiner']=model
                scores=score_combiner(processed,model);tables['scores']=scores
                selection=select_assets(scores,**cfg['selection']);tables['selection']=selection
                if plan['intent']['objective']=='strategy_evaluation':
                    calendar=data['calendar'].sort_values('session');sessions=calendar.session.tolist();asset_order=sorted(data['bars'].security_id.unique());targets=[];weight_diagnostics=[]
                    for session,g in selection.groupby('session',sort=True):
                        checkpoint()
                        if session not in cfg['rebalance_sessions']:continue
                        selected=g[g.selected]
                        if selected.empty:fail('SELECTION_FAILED','A scheduled rebalance has no valid selected portfolio')
                        t=sessions.index(session)
                        if t+1>=len(sessions):fail('CALENDAR_UNAVAILABLE','No future execution session')
                        assets=sorted(selected.security_id);params=cfg['portfolio'].copy();method=params['method']
                        if params.get('max_turnover') is not None:fail('PRIOR_HOLDINGS_REQUIRED','Pipeline cannot treat previous target weights as realized holdings')
                        cov=None;means=None
                        if method!='equal':
                            if 'returns' not in data:fail('COVARIANCE_REQUIRED','Optimization needs causal realized returns')
                            estimate=estimate_covariance(data['returns'],assets=assets,decision_at=g.formation_at.iloc[0],**cfg['covariance'])
                            cov=estimate['covariance']
                            if method=='mean_variance':
                                if model['method']!='ridge':fail('EXPECTED_RETURN_UNCALIBRATED','Raw factor scores are not calibrated return forecasts')
                                means=selected.set_index('security_id').loc[assets].score.tolist()
                        weights=construct_weights(assets=assets,covariance=cov,expected_returns=means,**params);weight_diagnostics.append({'session':session,**weights})
                        for a in asset_order:targets.append({'signal_session':session,'execution_session':sessions[t+1],
                            'formation_at':g.formation_at.iloc[0],'security_id':a,'target_weight':weights['weights'].get(a,0.)})
                    if not targets:fail('NO_REBALANCE','No scheduled rebalance produced a target')
                    if 'actions' not in data and cfg.get('no_actions_confirmed') is not True:
                        fail('CORPORATE_ACTIONS_UNKNOWN','Missing actions must be explicitly confirmed as none for this scenario')
                    if 'actions' in data and cfg.get('no_actions_confirmed') is True:
                        fail('CORPORATE_ACTIONS_CONFLICT','Explicit actions conflict with a no-actions declaration')
                    action_table=data.get('actions',pd.DataFrame(columns=['event_id','security_id','kind','session','known_at','evidence_id']))
                    checkpoint();back=simulate(data['bars'],pd.DataFrame(targets),data['market_state'],calendar,data['fee_schedule'],action_table,cancelled=cancelled,**cfg['backtest'])
                    tables.update({n:v for n,v in back.items() if isinstance(v,pd.DataFrame)});summary['backtest']=back['summary'];summary['weight_diagnostics']=weight_diagnostics
                    perf=performance(back['nav'],initial_equity=cfg['backtest']['initial_cash'],benchmark=data.get('benchmark'),fills=back['fills'],**cfg['performance'])
                    summary['performance']=perf['metrics'];summary['warnings']=perf['warnings'];tables['performance']=perf['trajectory']
        checkpoint();ref=finish_attempt(store,attempt,status='completed',tables=tables,summary=summary)
        read_result(store,ref);return {'ok':True,'result':ref,'attempt':attempt,'summary':summary}
    except DatasetError as exc:
        status='cancelled' if exc.code=='CANCELLED' else 'failed'
        outcome=finish_attempt(store,attempt,status=status,tables={},summary={'scope':'No success tables committed'},error_code=exc.code)
        return {'ok':False,'result':outcome,'attempt':attempt,'error_code':exc.code}
    except Exception:
        outcome=finish_attempt(store,attempt,status='failed',tables={},summary={'scope':'No success tables committed'},error_code='COMPUTATION_FAILED')
        return {'ok':False,'result':outcome,'attempt':attempt,'error_code':'COMPUTATION_FAILED'}

def run_sensitivity(store,*,intent,inputs,configurations,cancelled=None):
    """Each complete configuration is independently reviewed by the trusted caller.
    No hidden change of strategy definitions and no best-test-run selection.
    """
    if not isinstance(configurations,list) or not 1<=len(configurations)<=20:fail('RESOURCE_LIMIT','Explicit sensitivity set must be bounded')
    outcomes=[];experiment_id=None
    for config in configurations:
        if cancelled and cancelled():break
        plan=research_plan(intent,available_roles=inputs,locked_constraints=config)
        result=run_research(store,plan=plan,inputs=inputs,configuration=config,locked_constraints=config,
                            experiment_id=experiment_id,cancelled=cancelled)
        _,record=read_result(store,result['result']);experiment_id=record['experiment_id']
        outcomes.append({'result':result['result'],'ok':result['ok'],'error_code':result.get('error_code'),
                        'configuration_hash':digest(config)})
    return {'experiment_id':experiment_id,'attempts':outcomes,'status':'cancelled' if len(outcomes)<len(configurations) else 'completed',
        'winner':None,'limitation':'All configurations are research attempts; inspecting holdout outcomes consumes the holdout'}
