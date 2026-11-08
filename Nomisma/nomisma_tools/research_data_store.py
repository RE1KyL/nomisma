"""Additive S1 prerequisites: immutable table/bundle refs over the existing store.

Internal Python API only. It grants no Agent permissions, starts no processes and
does not register tools. Caller supplies an already authorized project store.
"""
import hashlib,importlib.metadata as md,json,os,platform,re,uuid
import pandas as pd
from analysis_result import encode_result
from dataset_store import DatasetError,file_sha256,utc_now,check_shape,io_boundary
from dataset_io import load_dataset,write_canonical_csv,read_canonical_csv

HASH=re.compile(r'[a-f0-9]{64}\Z')
BUNDLE=re.compile(r'rb_[a-f0-9]{32}\Z')
ROLES={'security_master','calendar','bars','financial_snapshot','financial_facts','pit_result',
       'state_events','state_result','coverage_ledger'}
MAX_BYTES=20_000_000

def checkpoint(store,cancelled):
    store.check_context()
    if cancelled is not None and cancelled():raise DatasetError('CANCELLED','Operation cancelled before publication')

def fingerprint_environment():
    packages={}
    for name in ('numpy','pandas','scipy','statsmodels','akshare'):
        try:packages[name]=md.version(name)
        except md.PackageNotFoundError:packages[name]=None
    return {'python':platform.python_version(),'platform':platform.system(),'packages':packages,
            'code_sha256':{name:file_sha256(__import__('pathlib').Path(__file__).with_name(name))
                            for name in ('ashare_data.py','research_data_store.py','ashare_intake.py','ashare_acquisition.py')}}

def validate_ref(ref):
    if not isinstance(ref,dict) or set(ref)!={'dataset_id','sha256'} or not isinstance(ref['sha256'],str) or not HASH.fullmatch(ref['sha256']):
        raise DatasetError('INVALID_REFERENCE','Expected a v1 dataset ID and SHA-256')

def resolve_table(store,ref):
    validate_ref(ref)
    return load_dataset(store,ref['dataset_id'],expected_sha256=ref['sha256'])

def validate_part(frame,*,raw_bytes,role,source_note,source_function=None,parameters=None,source_receipt=None):
    check_shape(frame)
    if not frame.columns.is_unique or any(not isinstance(c,str) or not 1<=len(c)<=128 for c in frame.columns):
        raise DatasetError('SCHEMA_MISMATCH','Normalized headers must be bounded unique strings')
    if role not in ROLES or not isinstance(raw_bytes,bytes) or not 0<len(raw_bytes)<=MAX_BYTES:
        raise DatasetError('RESOURCE_LIMIT','Unknown role or source payload outside the byte budget')
    if not isinstance(source_note,str) or not 1<=len(source_note)<=512:
        raise DatasetError('INVALID_ARGUMENT','A bounded source note is required')
    from ashare_acquisition import ALLOWED_FUNCTIONS,VERSION
    if source_function is not None:
        if source_function not in ALLOWED_FUNCTIONS:raise DatasetError('SOURCE_NOT_ALLOWED','Unreviewed source function')
        from ashare_data import timestamp
        if (not isinstance(source_receipt,dict) or source_receipt.get('function')!=source_function
            or source_receipt.get('sha256')!=hashlib.sha256(raw_bytes).hexdigest()
            or source_receipt.get('parameters')!=(parameters or {}) or source_receipt.get('akshare')!=VERSION
            or source_receipt.get('status')!='candidate_not_production_verified'):
            raise DatasetError('SOURCE_PROTOCOL_ERROR','Provider receipt does not bind the original payload and parameters')
        timestamp(source_receipt.get('fetched_at'))
    elif source_receipt is not None:raise DatasetError('SOURCE_PROTOCOL_ERROR','User-provided input cannot claim a provider receipt')
    if len(encode_result(parameters or {}))>4096 or len(encode_result(source_receipt))>65536:
        raise DatasetError('RESOURCE_LIMIT','Source parameters or receipt exceed budget')

@io_boundary
def publish_table(store,frame,*,raw_bytes,role,source_note,source_function=None,parameters=None,source_receipt=None,cancelled=None):
    """Retain the unmodified source payload even when its wide table exceeds 100 columns.

    Normalized data still obeys every existing row/column/cell budget. 'raw_bytes'
    is a serialized provider response or original upload, not a claim of HTTP wire capture.
    """
    checkpoint(store,cancelled)
    validate_part(frame,raw_bytes=raw_bytes,role=role,source_note=source_note,source_function=source_function,
                  parameters=parameters,source_receipt=source_receipt)
    with store.transaction() as t:
        original=t.file('source.csv');original.write_bytes(raw_bytes)
        current=t.file('normalized.csv');serialization=write_canonical_csv(frame,current)
        restored=read_canonical_csv(current,serialization)
        try:pd.testing.assert_frame_equal(frame,restored)
        except AssertionError as error:raise DatasetError('ROUNDTRIP_MISMATCH','Normalized table failed readback') from error
        checkpoint(store,cancelled)
        when=utc_now()
        provenance={'source_type':'user_provided' if source_function is None else 'akshare',
          'source_name':'user_provided' if source_function is None else 'candidate_akshare',
          'source_function':source_function,'request_parameters':parameters or {},
          'fetched_at':None if source_receipt is None else source_receipt['fetched_at'],'imported_at':when,'import_history':[{'operation':'ashare_normalize','at':when,
             'role':role,'source_payload_sha256':hashlib.sha256(raw_bytes).hexdigest()}]}
        # Existing transaction owns rollback; metadata is published last.
        result=store.commit(t,restored,dataset_name='A-share '+role,original_name='source.csv',
            current_name='normalized.csv',provenance=provenance,parser_options=serialization,
            extra={'research_data_version':'ashare-data-1','table_role':role,'source_note':source_note,
                   'source_validation_status':'candidate_not_production_verified','acquisition_receipt':source_receipt})
    return {'dataset_id':result['dataset_id'],'sha256':result['sha256']}

def _artifact(store,ref):
    if (not isinstance(ref,dict) or set(ref)!={'path','sha256'} or not isinstance(ref['sha256'],str)
        or not HASH.fullmatch(ref['sha256']) or not isinstance(ref['path'],str)
        or ref['path'].startswith(('.nomisma/','datasets/.staging/'))):
        raise DatasetError('INVALID_REFERENCE','Evidence must be a project artifact reference')
    path=store.path(ref['path'])
    if not path.is_file() or path.stat().st_size>MAX_BYTES:raise DatasetError('RESOURCE_LIMIT','Evidence artifact exceeds budget')
    if file_sha256(path)!=ref['sha256']:raise DatasetError('STALE_EVIDENCE','Evidence artifact hash changed')
    return ref.copy()

def _gate(report):
    if (not isinstance(report,dict) or report.get('schema_version')!='ashare-gate-1'
        or report.get('strict_backtest_ready') is not False or report.get('decision') not in ('blocked','research_only')
        or not isinstance(report.get('conditions'),list)):
        raise DatasetError('GATE_INVALID','A1 cannot certify strict backtest readiness')
    for c in report['conditions']:
        if (not isinstance(c,dict) or set(c)!={'id','status'} or c['status'] not in ('pass','fail','unknown')
            or not isinstance(c['id'],str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}',c['id'])):
            raise DatasetError('GATE_INVALID','Condition status is invalid')
    if len({c['id'] for c in report['conditions']})!=len(report['conditions']):raise DatasetError('GATE_INVALID','Condition IDs repeat')

@io_boundary
def publish_bundle(store,tables,*,gate,evidence=(),cancelled=None):
    checkpoint(store,cancelled);_gate(gate)
    if not isinstance(tables,dict) or not 1<=len(tables)<=len(ROLES) or not set(tables)<=ROLES:
        raise DatasetError('INVALID_REFERENCE','Bundle table roles are invalid')
    if not isinstance(evidence,(list,tuple)) or len(evidence)>64:raise DatasetError('RESOURCE_LIMIT','Too many evidence references')
    snapshots={}
    for role,ref in tables.items():
        frame,meta=resolve_table(store,ref)
        if meta.get('table_role')!=role:raise DatasetError('ROLE_MISMATCH','Dataset role differs from the bundle role')
        snapshots[role]={'dataset':ref.copy(),'metadata_sha256':file_sha256(store.path('datasets/metadata/'+meta['dataset_id']+'.json')),
                         'rows':len(frame),'columns':list(frame.columns)}
    refs=[_artifact(store,ref) for ref in evidence]
    bundle_id='rb_'+uuid.uuid4().hex
    prefix='datasets/metadata/artifacts/'+bundle_id
    with store.transaction() as t:
        env=t.file('environment.json');env.write_bytes(encode_result(fingerprint_environment()))
        report=t.file('gate.json');report.write_bytes(encode_result(gate))
        files={}
        for name,path in [('environment.json',prefix+'_environment.json'),('gate.json',prefix+'_gate.json')]:
            files[path]={'sha256':file_sha256(t.file(name)),'bytes':t.file(name).stat().st_size}
        manifest={'schema_version':'research-bundle-1','bundle_id':bundle_id,
          'project_id':store.context['project_id'],'mode':store.context['mode'],'created_at':utc_now(),
          'tables':snapshots,'evidence':refs,'files':files,'committed':True,
          'scope':'internal_3a1_sample_data_not_strict_backtest_certification'}
        file=t.file('bundle.json');file.write_bytes(encode_result(manifest))
        digest=file_sha256(file)
        if file.stat().st_size>1_000_000:raise DatasetError('RESOURCE_LIMIT','Bundle manifest exceeds budget')
        checkpoint(store,cancelled)
        # Recheck all input snapshots immediately before publishing the commit marker.
        for role,s in snapshots.items():
            resolve_table(store,s['dataset'])
            if file_sha256(store.path('datasets/metadata/'+s['dataset']['dataset_id']+'.json'))!=s['metadata_sha256']:
                raise DatasetError('STALE_DATASET','Input metadata changed')
        for ref in refs:_artifact(store,ref)
        store.publish_artifacts(t,{'environment.json':prefix+'_environment.json',
          'gate.json':prefix+'_gate.json','bundle.json':prefix+'.json'})
    return {'bundle_id':bundle_id,'sha256':digest,'manifest_path':prefix+'.json'}

def _json(store,path,limit=1_000_000):
    p=store.path(path)
    if p.stat().st_size>limit:raise DatasetError('RESOURCE_LIMIT','Manifest exceeds budget')
    try:
        def unique(pairs):
            d={}
            for k,v in pairs:
                if k in d:raise ValueError('Duplicate key')
                d[k]=v
            return d
        return json.loads(p.read_text('utf8'),object_pairs_hook=unique,
            parse_constant=lambda v:(_ for _ in ()).throw(ValueError('Nonfinite')))
    except (UnicodeError,ValueError):raise DatasetError('BUNDLE_INVALID','Manifest JSON is invalid')

@io_boundary
def read_bundle(store,ref):
    store.check_context()
    if (not isinstance(ref,dict) or set(ref)!={'bundle_id','sha256','manifest_path'}
        or not isinstance(ref['bundle_id'],str) or not BUNDLE.fullmatch(ref['bundle_id'])
        or not isinstance(ref['sha256'],str) or not HASH.fullmatch(ref['sha256'])):
        raise DatasetError('INVALID_REFERENCE','Invalid bundle reference')
    prefix='datasets/metadata/artifacts/'+ref['bundle_id']
    if ref['manifest_path']!=prefix+'.json':raise DatasetError('INVALID_PATH','Bundle manifest path mismatch')
    if file_sha256(store.path(ref['manifest_path']))!=ref['sha256']:raise DatasetError('STALE_BUNDLE','Bundle manifest changed')
    m=_json(store,ref['manifest_path'])
    if (not isinstance(m,dict) or m.get('schema_version')!='research-bundle-1' or m.get('committed') is not True
        or m.get('bundle_id')!=ref['bundle_id'] or m.get('project_id')!=store.context['project_id']
        or m.get('mode')!=store.context['mode']):raise DatasetError('BUNDLE_INVALID','Bundle identity or ownership mismatch')
    if not isinstance(m.get('tables'),dict) or not 1<=len(m['tables'])<=len(ROLES) or not set(m['tables'])<=ROLES:
        raise DatasetError('BUNDLE_INVALID','Bundle table map is invalid')
    frames={}
    for role,s in m['tables'].items():
        if (not isinstance(s,dict) or set(s)!={'dataset','metadata_sha256','rows','columns'}
            or not isinstance(s['metadata_sha256'],str) or not HASH.fullmatch(s['metadata_sha256'])
            or type(s['rows'])!=int or not isinstance(s['columns'],list)):
            raise DatasetError('BUNDLE_INVALID','Table snapshot descriptor is invalid')
        f,meta=resolve_table(store,s['dataset'])
        if (file_sha256(store.path('datasets/metadata/'+meta['dataset_id']+'.json'))!=s['metadata_sha256']
            or meta.get('table_role')!=role or len(f)!=s['rows'] or list(f.columns)!=s['columns']):
            raise DatasetError('STALE_BUNDLE','Bundle input snapshot no longer matches')
        frames[role]=f
    if set(m.get('files',{}))!={prefix+'_gate.json',prefix+'_environment.json'}:
        raise DatasetError('BUNDLE_INVALID','Unexpected artifact set')
    for path,descriptor in m['files'].items():
        if (not isinstance(descriptor,dict) or set(descriptor)!={'sha256','bytes'}
            or not isinstance(descriptor['sha256'],str) or not HASH.fullmatch(descriptor['sha256'])
            or type(descriptor['bytes'])!=int or not 0<descriptor['bytes']<=1_000_000):
            raise DatasetError('BUNDLE_INVALID','Artifact descriptor is invalid')
        p=store.path(path)
        if file_sha256(p)!=descriptor['sha256'] or p.stat().st_size!=descriptor['bytes']:
            raise DatasetError('STALE_BUNDLE','Bundle artifact changed')
    if not isinstance(m.get('evidence'),list) or len(m['evidence'])>64:raise DatasetError('BUNDLE_INVALID','Evidence list is invalid')
    for e in m['evidence']:_artifact(store,e)
    gate=_json(store,prefix+'_gate.json');_gate(gate)
    return frames,m,gate
