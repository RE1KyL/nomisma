"""Finite MCP contracts. Names/branches are shared with the DSH policy manifest."""
from jsonschema import Draft202012Validator
from dataset_store import DatasetError
from analysis_result import encode_result


def obj(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}


def text(limit=120):return {'type':'string','minLength':1,'maxLength':limit}
def enum(*values):return {'enum':list(values)}
def array(item, maximum=20, minimum=0):return {'type':'array','items':item,'maxItems':maximum,'minItems':minimum,'uniqueItems':True}
def number(lo=-1e12,hi=1e12):return {'type':'number','minimum':lo,'maximum':hi}
def integer(lo,hi):return {'type':'integer','minimum':lo,'maximum':hi}
def nullable(schema):return {'anyOf':[schema,{'type':'null'}]}

BOOL={'type':'boolean'}
HASH={'type':'string','pattern':'^[a-f0-9]{64}$'}
UUID={'type':'string','pattern':'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'}
DATASET=obj({'dataset_id':{'type':'string','pattern':'^ds_[a-f0-9]{32}$'},'sha256':HASH},('dataset_id','sha256'))
ANALYSIS=obj({'analysis_id':{'type':'string','pattern':'^an_[a-f0-9]{32}$'},'sha256':HASH},('analysis_id','sha256'))
COLS=array(text(),20,1)
SCALAR={'anyOf':[text(256),number(),BOOL,{'type':'null'}]}
PRED=obj({'column':text(),'operator':enum('equal','not_equal','greater_than','greater_equal','less_than','less_equal','in','not_in'),'value':{'anyOf':[SCALAR,array(SCALAR,100)]}},('column','operator','value'))
FILTERS={'type':'array','items':PRED,'maxItems':10}
RATE=nullable(obj({'value':number(-.999,1),'basis':enum('annual_effective')},('value','basis')))
COMMON_FIN={'date_column':text(),'frequency':enum('daily','weekly','monthly','quarterly','yearly','irregular'),
            'periods_per_year':nullable(number(1,100000)),'asset_column':nullable(text())}
RISK={'metrics':array(enum('max_drawdown','volatility','var','es','sharpe','sortino','calmar'),7,1),
      'alpha_loss':number(.5,.9999),'risk_free':RATE,'mar':RATE}
IMPORT={'source_path':text(240),'expected_sha256':HASH,'dataset_name':text(),'encoding':enum('auto','utf-8','utf-8-sig','gbk','gb18030'),
        'delimiter':enum('auto','comma','tab','semicolon','pipe'),'sheet':nullable(text(31)),
        'header_row':integer(1,100),'column_names':nullable(array(text(),100,1)),
        'empty_fields_are_null':BOOL,'formula_policy':enum('reject'),'provenance_note':{'type':'string','maxLength':500}}


def envelope(parameters, required=(), *, dataset=True, request=True, extra=None):
    props={'parameters':obj(parameters,required)}
    needs=['parameters']
    if dataset:props['dataset']=DATASET;needs+=['dataset']
    if request:props['request_id']=UUID;needs+=['request_id']
    props.update(extra or {})
    return obj(props,needs)


SPECS={}
def add(short,schema,description,mode='shared',readonly=False):
    SPECS['nomisma_'+short]={'inputSchema':schema,'description':description,'mode':mode,'read_only':readonly}

add('health_check',obj({}),'Read the actual Python environment and return an execution receipt.',readonly=True)
add('akshare_catalog',obj({}),'List verified AKShare sources; disabled candidates cannot be called.',readonly=True)
add('dataset_inspect',envelope({'sample_rows':integer(0,5)},request=False),'Inspect a registered dataset version; bounded sample only.',readonly=True)
QUALITY={'empty_strings_are_missing':BOOL,'primary_key':array(text(),20),'outlier_columns':array(text(),20),
         'iqr_multiplier':number(.5,10),'z_threshold':number(1,10),'min_outlier_samples':integer(4,1000),
         'date_column':nullable(text()),'group_by':array(text(),5),'date_format':nullable(text(50))}
add('dataset_quality',envelope(QUALITY,extra={'save_report':BOOL})|{'required':['dataset','parameters','request_id','save_report']},
    'Check quality without cleaning. save_report explicitly controls publication of a new report.')
add('akshare_fetch',envelope({'interface_name':enum('cn_index_hs300_sina_daily'),'symbol':enum('sh000300'),
    'start_date':text(10),'end_date':text(10),'dataset_name':text(),'attempts':integer(1,2)},('interface_name','symbol'),dataset=False),
    'Fetch only the verified Sina HS300 source; never switch provider. Creates new raw data.')
add('dataset_import',envelope(IMPORT,('source_path','expected_sha256'),dataset=False,extra={'action':enum('probe','commit')})|
    {'required':['parameters','request_id','action']},'Probe or import a hash-bound project CSV/XLSX; source is user_provided, never invented.')
STEPS=[obj({'operation':enum('drop_duplicates'),'subset':COLS,'keep':enum('first','last','none')},('operation',)),
       obj({'operation':enum('drop_missing'),'subset':COLS,'how':enum('any','all'),'empty_strings_are_missing':BOOL},('operation','subset')),
       obj({'operation':enum('fill_missing'),'columns':COLS,'method':enum('constant','mean','median'),'value':SCALAR,'empty_strings_are_missing':BOOL},('operation','columns','method')),
       obj({'operation':enum('winsorize'),'columns':COLS,'lower_quantile':number(0,.49),'upper_quantile':number(.51,1)},('operation','columns','lower_quantile','upper_quantile')),
       obj({'operation':enum('sort_values'),'by':COLS,'ascending':BOOL,'na_position':enum('first','last')},('operation','by','ascending')),
       obj({'operation':enum('filter_rows'),**PRED['properties']},('operation','column','operator','value'))]
add('dataset_clean',envelope({'steps':{'type':'array','items':{'oneOf':STEPS},'minItems':1,'maxItems':10},'dataset_name':text()},('steps',)),
    'Apply an explicit reviewed plan to a new processed dataset. Never overwrite raw or silently delete outliers.')
clean=SPECS['nomisma_dataset_clean']['inputSchema']
clean['properties'].update(quality_report=obj({'quality_report_id':{'type':'string','pattern':'^qr_[a-f0-9]{32}$'},'sha256':HASH},('quality_report_id','sha256')),
    cleaning_plan=obj({'reason':text(500),'expected_impact':text(500)},('reason','expected_impact')))
clean['required']+=['quality_report','cleaning_plan']
add('dataset_export',envelope({'format':enum('csv','xlsx'),'file_name':text(120),'encoding':enum('utf-8','utf-8-sig'),
    'data_sheet_name':text(31),'include_dictionary':BOOL,'include_provenance':BOOL,
    'quality_report_id':{'type':'string','pattern':'^qr_[a-f0-9]{32}$'},'csv_formula_policy':enum('reject','escape')},('format',)),
    'Export one dataset to a new desktop-compatible CSV/XLSX; verify readback, no overwrite.')
add('analysis_describe',envelope({'columns':COLS,'quantiles':array(number(0,1),9,1),'missing_policy':enum('reject','available'),'filters':FILTERS},('columns','missing_policy')),
    'Compute descriptive statistics and publish immutable machine results; sample variance uses ddof=1.')
add('analysis_correlate',envelope({'columns':array(text(),20,2),'method':enum('pearson','spearman'),
    'missing_policy':enum('reject','listwise','pairwise'),'inference':enum('none','iid_asymptotic'),'alpha':number(.001,.2),'filters':FILTERS},
    ('columns','method','missing_policy','inference')),'Compute Pearson/Spearman with actual pair counts and PSD caveat. No permutation/BH branch.')
OLS={'y':text(),'x_numeric':array(text(),20),'categorical':array(text(),5),'reference_levels':{'type':'object','maxProperties':5,'additionalProperties':SCALAR},
     'intercept':BOOL,'missing_policy':enum('reject','complete_case'),'filters':FILTERS,'date_column':nullable(text()),
     'alpha':number(.001,.2),'covariance':enum('classical')}
add('econ_ols',envelope(OLS,('y','x_numeric','intercept','missing_policy','covariance')),
    'Fit classical OLS only; publish all coefficients including nonsignificant values. No causal identification.',mode='economic')
add('econ_panel',envelope({'y':text(),'x':array(text(),10,1),'entity':text(),'time':text(),'missing_policy':enum('reject','complete_case'),
    'alpha':number(.01,.2),'bootstrap_draws':integer(0,999),'seed':integer(0,2147483647),'filters':FILTERS},
    ('y','x','entity','time','missing_policy','alpha','bootstrap_draws','seed')),
    'Bounded two-way fixed effects with entity-cluster inference and optional explicit seeded wild-cluster bootstrap; not causal identification.',mode='economic')
add('econ_diagnostics',envelope({'tests':array(enum('design','vif','breusch_pagan','white','jarque_bera','durbin_watson','breusch_godfrey'),7,1),
    'bp_robust':BOOL,'bg_lags':nullable(integer(1,12))},('tests',),dataset=False,extra={'analysis':ANALYSIS})|
    {'required':['parameters','request_id','analysis']},'Verify and reconstruct the referenced classical OLS, then diagnose; no pickle or arbitrary model.',mode='economic')
add('fin_returns',envelope({**COMMON_FIN,'price_column':text(),'price_adjustment':enum('unknown','unadjusted','forward_adjusted','backward_adjusted','index_level'),
    'return_kind':enum('simple','log'),'missing_policy':enum('reject','strict_adjacent')},
    ('date_column','price_column','frequency','price_adjustment','return_kind','missing_policy')),
    'Compute price returns and wealth using explicit financial conventions; no implicit forward fill.',mode='finance')
add('fin_risk',envelope({**COMMON_FIN,**RISK,'return_column':text(),'return_kind':enum('simple','log')},
    ('date_column','return_column','return_kind','frequency','metrics')),
    'Measure risk on a registered return dataset. Supply explicit annualization/rates when required.',mode='finance')
risk_dataset=SPECS['nomisma_fin_risk']['inputSchema']
risk_analysis=envelope(RISK,('metrics',),dataset=False,extra={'analysis':ANALYSIS})
risk_analysis['required']+=['analysis']
SPECS['nomisma_fin_risk']['inputSchema']={'type':'object','oneOf':[risk_dataset,risk_analysis]}
TS_BASE={**COMMON_FIN,'value_column':text(),'value_kind':enum('price','simple_return','log_return','measurement'),
    'window':integer(2,252),'min_periods':integer(1,252)}
TS=[obj({'action':enum('rolling'),**TS_BASE,'statistic':enum('mean','std','quantile','correlation'),
    'missing_policy':enum('reject','preserve'),'other_column':nullable(text()),'quantile':nullable(number(0,1))},('action','date_column','value_column','frequency','value_kind','window','min_periods','statistic')),
    obj({'action':enum('moving_average'),**TS_BASE,'kind':enum('sma','ema'),'adjust':nullable(BOOL)},
        ('action','date_column','value_column','frequency','value_kind','window','min_periods','kind')),
    obj({'action':enum('distribution'),**COMMON_FIN,'return_column':text(),'return_kind':enum('simple','log'),'bins':integer(2,100)},
        ('action','date_column','return_column','return_kind','frequency','bins')),
    obj({'action':enum('asset_correlations'),**COMMON_FIN,'return_column':text(),'return_kind':enum('simple','log'),
        'assets':array(text(64),20,2),'method':enum('pearson','spearman'),'missing_policy':enum('reject','listwise','pairwise')},
        ('action','date_column','asset_column','return_column','return_kind','frequency','assets','method','missing_policy'))]
ts=envelope({});ts['properties']['parameters']={'oneOf':TS}
add('fin_timeseries',ts,'Fixed rolling/MA/distribution/asset-correlation branches; no arbitrary kwargs or charts.',mode='finance')
add('fin_factor',envelope({**COMMON_FIN,'factor_column':text(),'price_column':text(),'available_at_column':text(),
    'assets':array(text(64),100,5),'universe_status':enum('declared_ex_ante'),'price_adjustment':enum('unknown','unadjusted','forward_adjusted','backward_adjusted','index_level'),
    'entry_lag':integer(1,20),'horizon':integer(1,20),'groups':integer(2,10),'ties':enum('keep_equal','reject')},
    ('date_column','asset_column','factor_column','price_column','available_at_column','assets','universe_status','frequency','price_adjustment','entry_lag','horizon','groups')),
    'Evaluate point-in-time factor IC/RankIC and groups; availability evidence required, no backtest.',mode='finance')
add('fin_portfolio',envelope({**COMMON_FIN,**RISK,'return_column':text(),'return_kind':enum('simple','log'),
    'assets':array(text(64),100,2),'universe_status':enum('declared_ex_ante'),'weighting':enum('equal','constant','lagged_column'),
    'weights':nullable({'type':'object','maxProperties':100,'additionalProperties':number(0,1)}),'weight_column':nullable(text()),
    'weight_available_at_column':nullable(text()),'first_row':enum('returns','formation')},
    ('date_column','asset_column','return_column','return_kind','assets','universe_status','frequency','weighting','metrics')),
    'Theoretical nonnegative fully-invested portfolio; no trading/cost/optimization claims.',mode='finance')

OUTPUT_SCHEMA=obj({'ok':BOOL,'summary':{'type':'string'},'data':{'type':'object'},'artifacts':{'type':'array'},
                   'warnings':{'type':'array'},'error':{'type':['object','null']}},('ok','summary','data','artifacts','warnings','error'))

# Finite 3A adapter: multiple existing DatasetRefs and explicit reviewed config.
# Configuration fields are literal scientific options, never code or callable names.
CONFIG_KEYS=('sessions','formation_times','factors','processing','evaluation','combiner','selection','rebalance_sessions',
 'portfolio','covariance','no_actions_confirmed','backtest','performance','walk_forward')
config={k:{'type':'object','maxProperties':50} for k in CONFIG_KEYS}
config.update(sessions=array(text(10),2000,1),rebalance_sessions=array(text(10),2000),no_actions_confirmed=BOOL,
 formation_times={'type':'object','maxProperties':2000,'additionalProperties':text(50)},
 factors={'type':'array','minItems':1,'maxItems':20,'items':obj({'name':text(40),'method':enum('momentum','volatility','mean','ratio'),
    'window':integer(1,252),'skip':integer(0,60),'inputs':array(text(),2,1),'direction':enum(-1,1),'definition':text(1000)},
    ('name','method','window','skip','inputs','direction','definition'))})
config.update(
 processing=obj({'winsor':obj({'method':enum('none','mad','quantile'),'scale':number(.01,100),'lower':number(0,.49),'upper':number(.51,1)},('method',)),
   'standardize':BOOL,'min_assets':integer(3,100),'neutralize':array(enum('industry','log_size'),2)},('winsor','standardize','min_assets')),
 evaluation=obj({'groups':integer(2,10),'hac_lags':integer(0,100),'min_assets':integer(3,100)},('groups','hac_lags','min_assets')),
 combiner=obj({'names':array(text(40),20,1),'method':enum('equal','ridge'),'fit_at':text(50),'training_start':text(10),'training_end':text(10),'ridge_alpha':number(0,1e6)},('names','method','fit_at','training_start','training_end','ridge_alpha')),
 selection=obj({'top_n':integer(1,100)},('top_n',)),
 portfolio=obj({'method':enum('equal','min_variance','mean_variance'),'investment':number(.001,1),'max_weight':number(.001,1),'risk_aversion':nullable(number(.001,1e9)),
   'industries':{'type':'object','maxProperties':100,'additionalProperties':text(80)},'industry_bounds':{'type':'object','maxProperties':100,'additionalProperties':{'type':'array','items':number(0,1),'minItems':2,'maxItems':2}},
   'max_turnover':{'type':'null'}},('method','investment','max_weight')),
 covariance=obj({'lookback':integer(2,2000),'min_observations':integer(2,2000),'diagonal_shrinkage':number(0,1)},('lookback','min_observations','diagonal_shrinkage')),
 backtest=obj({'initial_cash':number(.01,1e12),'slippage_bps':number(0,1000),'stale_price_policy':enum('reject','carry_suspended'),'execution_mode':enum('declared_scenario')},('initial_cash','slippage_bps','stale_price_policy','execution_mode')),
 performance=obj({'periods_per_year':number(1,100000),'risk_free_annual':number(-.99,1)},('periods_per_year','risk_free_annual')),
 walk_forward=obj({'sessions':array(text(10),2000,1),'split_options':obj({'train_size':integer(2,2000),'test_size':integer(1,500),'step':integer(1,500),'embargo':integer(0,250),'expanding':BOOL},('train_size','test_size','step','embargo','expanding')),
   'combiner_options':obj({'names':array(text(40),20,1),'method':enum('equal','ridge'),'ridge_alpha':number(0,1e6)},('names','method','ridge_alpha'))},('sessions','split_options','combiner_options')))
add('fin_scenario',obj({'request_id':UUID,'objective':enum('factor_research','stock_selection','strategy_evaluation','out_of_sample'),
    'research_question':text(2000),'inputs':{'type':'object','minProperties':1,'maxProperties':20,
      'propertyNames':{'enum':['observations','membership','controls','labels','calendar','bars','market_state','fee_schedule','actions','returns','benchmark','processed','samples']},'additionalProperties':DATASET},
    'configuration':obj(config)},('request_id','objective','research_question','inputs','configuration')),
    'Run the existing causal factor/IC or constrained portfolio backtest research scenario with explicit data roles and frozen strategy rules; strict historical certification remains unavailable.',mode='finance')
SPECS['nomisma_fin_scenario']['inputSchema']['allOf']=[
 {'if':{'properties':{'objective':{'const':objective}}},'then':{'properties':{'configuration':{'required':required}}}}
 for objective,required in [('factor_research',['sessions','formation_times','factors','processing']),
  ('stock_selection',['sessions','formation_times','factors','processing','combiner','selection']),
  ('strategy_evaluation',['sessions','formation_times','factors','processing','combiner','selection','rebalance_sessions','portfolio','backtest','performance']),
  ('out_of_sample',['walk_forward'])]]


add('explore_probe',envelope({'source_path':text(500),'expected_sha256':HASH,'table':text(),'sheet':text(),'text_records':enum('lines')},('source_path',),dataset=False),'Probe a local hash-bound Explore source; bounded first batch, not a full quality check.',mode='big_data_explore')
add('explore_import',envelope({'source_path':text(500),'expected_sha256':HASH,'table':text(),'sheet':text(),'text_records':enum('lines'),'dataset_name':text()},('source_path','expected_sha256'),dataset=False),'Import a local source into immutable disk-backed Explore data with a real manifest.',mode='big_data_explore')

add('explore_profile',envelope({'column':text(),'other_column':text(),'method':enum('numeric','frequency','correlation')},('column','method')),'Compute an explicit full-scan numeric, paired correlation without inference, or bounded frequency profile of a disk-backed dataset.',mode='big_data_explore')
SPECS['nomisma_explore_profile']['inputSchema']['allOf']=[{'if':{'properties':{'parameters':{'properties':{'method':{'const':'correlation'}}}}},'then':{'properties':{'parameters':{'required':['other_column']}}}}]

add('explore_process',envelope({'method':enum('select','filter','group_count','deduplicate','join','fill_missing','string_trim','cast_numeric'),'columns':array(text(),50,1),'column':text(),'operator':enum('eq','gt','lt'),'value':SCALAR,'right_dataset':DATASET,'right_key':text(),'join_type':enum('left','inner')},('method',)),'Create a new version through finite projection, cleaning, explicit numeric conversion, exact group counts, global deduplication or many-to-one join.',mode='big_data_explore')
SPECS['nomisma_explore_process']['inputSchema']['allOf']=[{'if':{'properties':{'parameters':{'properties':{'method':{'const':method}}}}},'then':{'properties':{'parameters':{'required':required}}}} for method,required in [('select',['columns']),('filter',['column','operator','value']),('group_count',['column']),('join',['column','right_dataset']),('fill_missing',['column','value']),('string_trim',['column']),('cast_numeric',['column'])]]

add('explore_mine',envelope({'method':enum('pca','kmeans','text_frequency'),'columns':array(text(),20,1),'clusters':integer(2,20),'seed':integer(0,2147483647)},('method','columns')),'Full-sample bounded PCA, seeded K-means, or explicitly labelled text frequency. No automatic sampling.',mode='big_data_explore')

add('explore_fetch',envelope({'latitude':number(-90,90),'longitude':number(-180,180),'start_date':text(10),'end_date':text(10)},('latitude','longitude','start_date','end_date'),dataset=False),'Fetch at most 366 days of ERA5 reanalysis daily mean temperature from the fixed Open-Meteo archive endpoint; Celsius and GMT; no arbitrary URL.',mode='big_data_explore')

MACRO_SOURCE=enum('nbs','fred','bea','bls','world_bank')
COUNTRY={'type':'string','pattern':'^[A-Z]{2,3}$'}
MACRO_PERIOD={'indicator_id':text(50),'start_date':text(10),'end_date':text(10),'dataset_name':text()}
add('macro_discover',obj({'query':text(120),'source':MACRO_SOURCE,'country_or_region':COUNTRY}),
    'Discover registered official indicators by keywords, source or region. A local catalog result is not a fetched dataset.',mode='economic',readonly=True)
add('macro_metadata',obj({'source':MACRO_SOURCE,'indicator_id':text(50),'country_or_region':COUNTRY},('source',)),
    'Read local indicator metadata and source readiness. A configured credential does not imply live availability.',mode='economic',readonly=True)
add('macro_fetch',envelope({**MACRO_PERIOD,'source':MACRO_SOURCE,'country_or_region':COUNTRY},
    ('source','indicator_id','start_date','end_date'),dataset=False),
    'Retrieve a registered official macroeconomic series through the network policy. Saves raw response and versioned normalized observations.',mode='economic')
add('macro_import_nbs',envelope({**MACRO_PERIOD,'source_path':text(500),'expected_sha256':HASH,
    'date_column':text(),'value_column':text(),'series_label':text(),'unit':text(),'frequency':enum('daily','monthly','quarterly','annual'),
    'sheet':text(31),'header_row':integer(1,100)},('indicator_id','start_date','end_date','source_path',
    'expected_sha256','value_column','unit','frequency'),dataset=False),
    'Import a user-identified official NBS export using explicit period, value, unit and frequency columns. No network request.',mode='economic')
SPECS['nomisma_macro_import_nbs']['inputSchema']['properties']['parameters']['oneOf']=[
    {'required':['date_column']},{'required':['series_label']}]
add('explore_query',envelope({'sql':text(8000),'max_rows':integer(1,5000)},('sql',)),
    'Run one bounded read-only DuckDB SELECT on the verified Explore dataset table input. Save a result CSV and JSON execution record.',mode='big_data_explore')
add('explore_quality',envelope({'empty_strings_are_missing':BOOL,'outlier_columns':array(text(),12)}),
    'Scan every row of an Explore dataset for field types, missing cells, exact duplicates and optional numeric IQR outliers. Save a report.',mode='big_data_explore')
add('explore_transform',envelope({'method':enum('trim','date_iso','group_sum'),'column':text(),
    'group_by':text(),'date_format':text(60)},('method','column')),
    'Derive a new Explore version with Polars. Trim a category field, standardize dates using an explicit pattern, or compute a grouped numeric sum.',mode='big_data_explore')
SPECS['nomisma_explore_transform']['inputSchema']['allOf']=[
    {'if':{'properties':{'parameters':{'properties':{'method':{'const':method}}}}},
     'then':{'properties':{'parameters':{'required':[required]}}}}
    for method,required in [('date_iso','date_format'),('group_sum','group_by')]]
add('explore_export',envelope({'format':enum('csv','parquet')},('format',)),
    'Stream a verified Explore dataset to a new CSV or typed Parquet artifact with a manifest. CSV represents null as blank and protects formula-like text.',mode='big_data_explore')

def visible(mode):return {k:v for k,v in SPECS.items() if v['mode'] in ('shared',mode) and not (mode=='big_data_explore' and k in ('nomisma_akshare_catalog','nomisma_akshare_fetch'))}


def validate(name,args,mode):
    if name not in visible(mode):raise DatasetError('MODE_NOT_ALLOWED','Tool is not permitted in the current project mode')
    if not isinstance(args,dict) or len(encode_result(args))>16384:raise DatasetError('INVALID_ARGUMENT','Arguments must be a bounded object')
    if not Draft202012Validator(SPECS[name]['inputSchema']).is_valid(args):
        raise DatasetError('VALIDATION_ERROR','Arguments do not match the published finite tool schema')


def bounded(result):
    if not Draft202012Validator(OUTPUT_SCHEMA).is_valid(result):raise DatasetError('RESULT_VALIDATION_FAILED','Invalid tool result envelope')
    raw=encode_result(result)
    wire=encode_result({'content':[{'type':'text','text':raw.decode('utf8')}],'structuredContent':result,'isError':not result['ok']})
    # Reserve room for the SDK's JSON-RPC result envelope and ordinary request id.
    if len(raw)>8192 or len(wire)+256>20480:raise DatasetError('RESOURCE_LIMIT','Tool receipt exceeds the 8 KiB / 20 KiB budget')
    return result


def error_result(code,execution_id,category=None):
    if category is None:
        category=('file_permission_error' if code=='PERMISSION_DENIED' else 'tool_error' if code in (
            'TOOL_EXECUTION_FAILED','WORKER_FAILED','FILE_IO_ERROR','WRITE_FAILED','ROUNDTRIP_MISMATCH',
            'NETWORK_TIMEOUT','NETWORK_UNREACHABLE','REQUEST_INTERRUPTED','TIMEOUT','RESULT_VALIDATION_FAILED',
            'WORKER_PROTOCOL_ERROR','NUMERICAL_FAILURE') else 'validation_error')
    return bounded({'ok':False,'summary':'Nomisma operation did not complete; no successful result is claimed',
        'data':{'execution_id':execution_id,'status':'failed'},'artifacts':[],'warnings':[],
        'error':{'code':code,'category':category,'message':'Check the published arguments, project, version and permissions. No raw exception is exposed.',
                 'retryable':code in ('NETWORK_TIMEOUT','NETWORK_UNREACHABLE','PROJECT_BUSY')}})
