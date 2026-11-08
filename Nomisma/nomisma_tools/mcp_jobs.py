"""Prepare in an owned snapshot, validate in parent, commit under project lock.

Accepted data/analysis algorithms are unchanged. Their internal publication only
completes the disposable candidate workspace, never the registered real project.
The MCP parent exclusively owns promotion and durable request receipts.
"""
from contextlib import contextmanager
import hashlib,json,os,re,shutil,time,uuid
from pathlib import Path
import anyio
from dataset_store import ProjectDatasetStore,DatasetError,file_sha256,_reparse
from dataset_io import load_dataset
from analysis_store import read_analysis
from analysis_result import encode_result
from mcp_contract import bounded,error_result
from mcp_process import Worker

MAX_BYTES=64_000_000
MAX_FILES=128
DEADLINE=90


def write_json(path,value):
    with path.open('xb') as stream:
        stream.write(encode_result(value));stream.flush();os.fsync(stream.fileno())


def read_json(path,limit=1_000_000):
    if path.stat().st_size>limit:raise DatasetError('RESOURCE_LIMIT','Bounded JSON exceeds limit')
    def unique(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError('Duplicate key')
            result[k]=v
        return result
    return json.loads(path.read_text('utf8'),object_pairs_hook=unique,
                      parse_constant=lambda v:(_ for _ in ()).throw(ValueError('Nonfinite')))


class SnapshotStore(ProjectDatasetStore):
    def __init__(self,authority,root):self.authority=authority;self.context=authority.context.copy();self.root=root
    def check_context(self):
        self.authority.check_context()
        if _reparse(self.root):raise DatasetError('LINK_NOT_ALLOWED','Candidate root is redirected')


@contextmanager
def lock(store,relative):
    """Nonblocking OS lock. Released by the kernel on process crash."""
    store.directory(str(Path(relative).parent).replace('\\','/'))
    file=store.path(relative,allow_missing=True)
    with file.open('a+b') as stream:
        stream.seek(0,2)
        if not stream.tell():stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:raise DatasetError('PROJECT_BUSY','Another request owns this project/request lock') from error
        try:yield
        finally:
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def inputs(store,args):
    paths=set();ref=args.get('dataset');analysis=args.get('analysis');p=args.get('parameters',{})
    if p.get('right_dataset'):paths.update(inputs(store,{'dataset':p['right_dataset']}))
    if args.get('inputs'):
        for input_ref in args['inputs'].values():paths.update(inputs(store,{'dataset':input_ref}))
    if analysis:
        namespace=store.context['mode']
        result,manifest=read_analysis(store,analysis['analysis_id'],expected_sha256=analysis['sha256'],namespace=namespace)
        paths.update(v['path'] for v in manifest['files'].values())
        paths.add('analyses/'+namespace+'/'+analysis['analysis_id']+'/metadata.json')
        ref={'dataset_id':result['dataset_id'],'sha256':result['input_sha256']}
    if ref and store.metadata(ref['dataset_id']).get('schema_version')==2:
        from explore_store import input_files
        paths.update(input_files(store,ref));ref=None
    if ref:
        _,meta=load_dataset(store,ref['dataset_id'],expected_sha256=ref['sha256'])
        # Ancestors are needed for raw provenance and parent validation.
        seen=set()
        while meta['dataset_id'] not in seen:
            seen.add(meta['dataset_id']);paths.update([meta['current_file_path'],meta['original_file_path'],f"datasets/metadata/{meta['dataset_id']}.json"])
            if not meta.get('parent_dataset_id'):break
            meta=store.metadata(meta['parent_dataset_id'])
            if len(seen)>20:raise DatasetError('RESOURCE_LIMIT','Dataset ancestry exceeds job budget')
    if 'source_path' in p:
        if p['source_path'].startswith('.nomisma/'):
            raise DatasetError('INVALID_PATH','Internal job state is not a user import source')
        path=store.path(p['source_path'])
        if p.get('expected_sha256') and file_sha256(path)!=p['expected_sha256']:raise DatasetError('STALE_SOURCE','Import source version changed')
        paths.add(p['source_path'])
    if p.get('quality_report_id'):paths.add('datasets/metadata/quality/'+p['quality_report_id']+'.json')
    if args.get('quality_report'):
        q=args['quality_report'];relative='datasets/metadata/quality/'+q['quality_report_id']+'.json'
        path=store.path(relative);report=read_json(path)
        if (file_sha256(path)!=q['sha256'] or report.get('dataset_id')!=ref['dataset_id']
                or report.get('input_sha256')!=ref['sha256']
                or any(report.get('project_context',{}).get(k)!=store.context[k] for k in ('mode','project_id'))):
            raise DatasetError('STALE_QUALITY_REPORT','Cleaning requires a verified report for the same dataset version and project')
        paths.add(relative)
    if len(paths)>MAX_FILES:raise DatasetError('RESOURCE_LIMIT','Too many input files')
    total=0;manifest={}
    for relative in sorted(paths):
        path=store.path(relative)
        if not path.is_file() or path.stat().st_size>(128_000_000 if store.context['mode']=='big_data_explore' else 20_000_000):raise DatasetError('RESOURCE_LIMIT','Input is not a bounded regular file')
        total+=path.stat().st_size
        if total>(256_000_000 if store.context['mode']=='big_data_explore' else MAX_BYTES):raise DatasetError('RESOURCE_LIMIT','Input snapshot exceeds 64 MB')
        manifest[relative]=file_sha256(path)
    return manifest


def snapshot(store,job,args):
    baseline=inputs(store,args);root=job/'workspace';root.mkdir()
    for relative,digest in baseline.items():
        dest=root/relative;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(store.path(relative),dest)
        if file_sha256(dest)!=digest:raise DatasetError('STALE_DATASET','Input changed while snapshotting')
        # Legacy dataset metadata belongs to its original importer. Analyses are
        # bound to the current request session without modifying source metadata.
        if not args.get('inputs') and re.fullmatch(r'datasets/metadata/ds_[a-f0-9]{32}\.json',relative):
            value=read_json(dest);value['session_id']=store.context['session_id']
            dest.write_bytes(encode_result(value))
    state=job/'Nomisma/desktop-dev/openacpui-data';state.mkdir(parents=True)
    write_json(state/'projects.json',[{'id':store.context['project_id'],'mode':store.context['mode'],'status':'active','path':str(root)}])
    return baseline,{r:file_sha256(root/r) for r in baseline}


def changed_files(view,baseline):
    result={};total=0
    for directory,dirs,names in os.walk(view.root,followlinks=False):
        for name in [*dirs,*names]:
            path=Path(directory)/name
            if _reparse(path):raise DatasetError('LINK_NOT_ALLOWED','Candidate contains a redirected path')
        for name in names:
            path=Path(directory)/name;relative=path.relative_to(view.root).as_posix()
            view.path(relative)
            digest=file_sha256(path)
            if relative in baseline:
                if digest!=baseline[relative]:raise DatasetError('STALE_DATASET','Worker changed an input snapshot')
                continue
            if '/.staging/' in relative:raise DatasetError('STAGING_INVALID','Unfinished staged output remains')
            total+=path.stat().st_size
            if total>(256_000_000 if view.context['mode']=='big_data_explore' else MAX_BYTES) or len(result)>=MAX_FILES:raise DatasetError('RESOURCE_LIMIT','Candidate output budget exceeded')
            result[relative]=digest
    if any(not (view.root/r).is_file() for r in baseline):raise DatasetError('STALE_DATASET','Worker removed an input')
    return result


def validate_candidate(view,name,args,result,files):
    bounded(result);data=result['data'];expected=set()
    if name in ('nomisma_explore_profile','nomisma_explore_mine','nomisma_explore_query','nomisma_explore_quality','nomisma_explore_export'):
        path=data['result_path'];saved=read_json(view.path(path))
        if saved['execution_id']!=data['execution_id'] or saved['request_id']!=args['request_id'] or saved['dataset']!=args['dataset'] or saved['facts']!=data['facts'] or saved['parameters']!=args['parameters']:raise DatasetError('RESULT_VALIDATION_FAILED','Profile identity differs')
        suffix=args['parameters']['format'] if name=='nomisma_explore_export' else 'csv'
        expected={path,path[:-5]+'.'+suffix}
        if name=='nomisma_explore_export' and (data.get('output_file')!=path[:-5]+'.'+suffix
            or saved.get('output_sha256')!=files.get(data['output_file'])):
            raise DatasetError('RESULT_VALIDATION_FAILED','Explore export digest differs')
        if set(files)!=expected or any(files.get(item['path'])!=item['sha256'] for item in result['artifacts']):
            raise DatasetError('RESULT_VALIDATION_FAILED','Explore report candidate files differ')
        return
    if name in ('nomisma_explore_import','nomisma_explore_process','nomisma_explore_fetch','nomisma_explore_transform'):
        from explore_store import input_files,read
        ref={'dataset_id':data['dataset_id'],'sha256':data['sha256']}
        m,manifest=read(view,ref)
        if manifest['execution_id']!=data['execution_id'] or manifest['request_id']!=args['request_id']:raise DatasetError('RESULT_VALIDATION_FAILED','Explore execution identity differs')
        expected=input_files(view,ref)
        if set(files)!=expected:raise DatasetError('RESULT_VALIDATION_FAILED','Explore candidate files differ')
        return
    if not result['ok']:raise DatasetError('RESULT_VALIDATION_FAILED','Candidate did not succeed')
    if name in ('nomisma_macro_fetch','nomisma_macro_import_nbs'):
        frame,meta=load_dataset(view,data['dataset_id'],expected_sha256=data['sha256'])
        p=args['parameters'];source=p.get('source','nbs')
        economic=meta.get('economic_source',{})
        base=f"datasets/raw/{data['dataset_id']}/"
        extension=Path(p['source_path']).suffix.lower() if name.endswith('import_nbs') else '.json'
        expected={f"datasets/metadata/{data['dataset_id']}.json",base+'observations.csv',base+'original'+extension}
        if source in ('fred','world_bank'):expected.add(base+'upstream_metadata_1.json')
        if (meta['source_type']!='official_economic' or economic.get('source')!=source or
            economic.get('indicator_id')!=p['indicator_id'] or list(frame.columns)!=['observation_date','value'] or
            len(frame)!=data['row_count'] or meta['current_file_path']!=base+'observations.csv' or
            file_sha256(view.path(base+'original'+extension))!=meta['original_sha256'] or set(files)!=expected):
            raise DatasetError('RESULT_VALIDATION_FAILED','Economic snapshot does not match the approved source')
        if source=='nbs' and meta['original_sha256']!=p['expected_sha256']:
            raise DatasetError('RESULT_VALIDATION_FAILED','NBS export hash changed')
        return
    if name=='nomisma_fin_scenario':
        from quant_experiments import read_result
        frames,report=read_result(view,data['result'])
        if report['status']!='completed' or report['strict_backtest_ready'] is not False:raise DatasetError('RESULT_VALIDATION_FAILED','Scenario result is not a verified research outcome')
        expected={v['path'] for v in report['tables'].values()}|{data['result']['path'],data['attempt']['attempt_path']}
    elif name in ('nomisma_akshare_fetch','nomisma_dataset_clean') or name=='nomisma_dataset_import' and args['action']=='commit':
        frame,meta=load_dataset(view,data['dataset_id'],expected_sha256=data['sha256'])
        area='processed' if name.endswith('clean') else 'raw';prefix=f'datasets/{area}/'+data['dataset_id']+'/'
        if not meta['current_file_path'].startswith(prefix):raise DatasetError('RESULT_VALIDATION_FAILED','Dataset namespace mismatch')
        if name.endswith('clean') and (meta.get('parent_dataset_id')!=args['dataset']['dataset_id'] or meta.get('parent_sha256')!=args['dataset']['sha256']):
            raise DatasetError('RESULT_VALIDATION_FAILED','Cleaning lineage mismatch')
        expected={f"datasets/metadata/{data['dataset_id']}.json"}
        if name=='nomisma_akshare_fetch':
            from akshare_registry import SINA_ID
            from akshare_adapter import _validate_sina_frame
            if (meta['source_type']!='akshare' or meta['source_name']!='sina'
                    or meta['source_function']!='stock_zh_index_daily'
                    or meta.get('source_registry',{}).get('interface_name')!=SINA_ID):
                raise DatasetError('RESULT_VALIDATION_FAILED','Fetch provenance differs from the verified source')
            _validate_sina_frame(frame)
            expected|={prefix+'original.csv',prefix+'selection.csv'}
        elif name=='nomisma_dataset_import':
            source_name='source'+Path(args['parameters']['source_path']).suffix.lower()
            if (meta['source_type']!='user_provided' or meta['source_function'] is not None
                    or meta['original_sha256']!=args['parameters']['expected_sha256']):
                raise DatasetError('RESULT_VALIDATION_FAILED','Import provenance or source hash mismatch')
            expected.add(prefix+source_name)
        else:
            steps=args['parameters']['steps'];history=meta.get('processing_history',[])[-len(steps):]
            if len(history)!=len(steps):raise DatasetError('RESULT_VALIDATION_FAILED','Cleaning history is incomplete')
            previous=args['dataset']['sha256']
            for index,(step,entry) in enumerate(zip(steps,history)):
                basename='data.csv' if index==len(steps)-1 else f'step_{index+1:03}.csv'
                relative=prefix+basename;expected.add(relative)
                if (entry['operation']!=step['operation']
                        or entry['parameters']!={k:v for k,v in step.items() if k!='operation'}
                        or entry['input_sha256']!=previous or entry['output_file']!=relative
                        or entry['output_sha256']!=file_sha256(view.path(relative))):
                    raise DatasetError('RESULT_VALIDATION_FAILED','Cleaning operation, hash chain or file set mismatch')
                previous=entry['output_sha256']
        if data['row_count']!=meta['row_count'] or data['column_count']!=meta['column_count']:
            raise DatasetError('RESULT_VALIDATION_FAILED','Dataset receipt counts differ from verified data')
        if len(expected)<2:raise DatasetError('RESULT_VALIDATION_FAILED','Missing dataset artifacts')
    elif name.startswith(('nomisma_analysis_','nomisma_econ_','nomisma_fin_')):
        namespace='shared' if name.startswith('nomisma_analysis_') else view.context['mode']
        result_json,manifest=read_analysis(view,data['analysis_id'],namespace=namespace)
        if result_json['execution_id']!=data['execution_id'] or result_json['request_id']!=args['request_id']:
            raise DatasetError('RESULT_VALIDATION_FAILED','Analysis request identity mismatch')
        expected={v['path'] for v in manifest['files'].values()}|{f"analyses/{namespace}/{data['analysis_id']}/metadata.json"}
    elif name=='nomisma_dataset_quality' and args['save_report']:
        path=data['report_path'];report=read_json(view.path(path))
        if (path!='datasets/metadata/quality/'+data['quality_report_id']+'.json' or report['input_sha256']!=args['dataset']['sha256']
                or report['dataset_id']!=args['dataset']['dataset_id'] or file_sha256(view.path(path))!=data['sha256']):
            raise DatasetError('RESULT_VALIDATION_FAILED','Quality identity mismatch')
        expected={path}
    elif name=='nomisma_dataset_export':
        path=data['metadata_path'];descriptor=read_json(view.path(path))
        expected={path,data['output_file']}
        if (path!='datasets/metadata/artifacts/'+data['artifact_id']+'.json'
            or data['output_file']!='datasets/exports/'+Path(data['output_file']).name
            or descriptor['output_sha256']!=file_sha256(view.path(data['output_file']))
            or descriptor['source_sha256']!=args['dataset']['sha256'] or not descriptor['roundtrip_verified']):
            raise DatasetError('RESULT_VALIDATION_FAILED','Export descriptor mismatch')
        from dataset_io import read_canonical_csv
        frame,_=load_dataset(view,args['dataset']['dataset_id'],expected_sha256=args['dataset']['sha256'])
        if data['format']=='csv':
            restored=read_canonical_csv(view.path(data['output_file']),descriptor['serialization'])
            for cell in descriptor['escaped_cells']:restored.loc[cell['row'],cell['column']]=restored.loc[cell['row'],cell['column']][1:]
            import pandas as pd
            pd.testing.assert_frame_equal(frame,restored)
        else:
            from openpyxl import load_workbook
            wb=load_workbook(view.path(data['output_file']),read_only=True,data_only=False)
            try:
                if wb.sheetnames!=[s['name'] for s in descriptor['sheets']]:raise DatasetError('ROUNDTRIP_MISMATCH','Excel sheets changed')
                sheet=wb[descriptor['sheets'][0]['name']]
                if sheet.max_row!=len(frame)+1 or sheet.max_column!=len(frame.columns):raise DatasetError('ROUNDTRIP_MISMATCH','Excel dimensions changed')
            finally:wb.close()
    if set(files)!=expected:raise DatasetError('RESULT_VALIDATION_FAILED','Unexpected or missing candidate files')
    for artifact in result['artifacts']:
        if artifact['path'] not in files or files[artifact['path']]!=artifact['sha256']:
            raise DatasetError('RESULT_VALIDATION_FAILED','Artifact receipt differs from candidate bytes')


def ordered_files(files):
    # Commit markers are always last, including an export's provenance receipt.
    def marker(path):return path.endswith('/metadata.json') or bool(re.fullmatch(r'datasets/metadata/(ds_[a-f0-9]{32}|artifacts/art_[a-f0-9]{32})\.json',path))
    return sorted(files,key=lambda p:(marker(p),p))


def promote(store,job,files,result,ledger,name,args):
    """Synchronous bounded critical section: no async checkpoint in publication.

    The prepared journal is fsynced before the first exclusive publish. Recovery
    either returns an identical committed receipt or removes only matching owned
    bytes listed in that journal; conflicting paths are never touched.
    """
    published=[]
    try:
        owned=ledger/'owned';owned.mkdir()
        for index,relative in enumerate(ordered_files(files)):
            destination=store.path(relative,allow_missing=True)
            store.directory(str(Path(relative).parent).replace('\\','/'))
            source=job/'workspace'/relative
            if file_sha256(source)!=files[relative]:raise DatasetError('STALE_RESULT','Prepared bytes changed')
            store._publish_file(source,destination)
            identity=destination.stat();published.append((relative,identity.st_dev,identity.st_ino))
            write_json(owned/(str(index)+'.json'),{'path':relative,'sha256':files[relative],
                       'device':str(identity.st_dev),'inode':str(identity.st_ino)})
        # Verify final metadata and data, not only the staging candidate.
        for relative in files:
            if file_sha256(store.path(relative))!=files[relative]:raise DatasetError('ROUNDTRIP_MISMATCH','Published bytes changed')
        validate_candidate(store,name,args,result,files)
        write_json(ledger/'committed.json',result)
        return result
    except BaseException:
        for relative,device,inode in reversed(published):
            path=store.path(relative)
            identity=path.stat()
            if (identity.st_dev,identity.st_ino)!=(device,inode) or file_sha256(path)!=files[relative]:
                raise DatasetError('RECOVERY_CONFLICT','Published bytes changed; cleanup stopped')
            path.unlink()
        raise


def recover(store,ledger,fingerprint):
    header=ledger/'request.json'
    if not header.exists():return None
    request=read_json(header)
    if request['fingerprint']!=fingerprint:raise DatasetError('REQUEST_CONFLICT','Request ID was used with different parameters')
    committed=ledger/'committed.json'
    if committed.exists():
        result=read_json(committed,16384)
        prepared=read_json(ledger/'prepared.json')
        for relative,digest in prepared['files'].items():
            if file_sha256(store.path(relative))!=digest:raise DatasetError('STALE_RESULT','Committed receipt no longer matches files')
        return bounded(result)
    if (ledger/'failed.json').exists():return bounded(read_json(ledger/'failed.json',16384))
    if (ledger/'prepared.json').exists():
        prepared=read_json(ledger/'prepared.json')
        owned={}
        directory=store.path(ledger.relative_to(store.root).as_posix()+'/owned',allow_missing=True)
        if directory.exists():
            for child in directory.iterdir():
                relative=child.relative_to(store.root).as_posix();store.path(relative)
                value=read_json(child);owned[value['path']]=value
        for relative,digest in reversed(list(prepared['files'].items())):
            path=store.path(relative,allow_missing=True)
            if path.exists():
                info=path.stat();owner=owned.get(relative)
                if (owner is None or (str(info.st_dev),str(info.st_ino))!=(owner['device'],owner['inode'])
                        or owner['sha256']!=digest or file_sha256(path)!=digest):
                    raise DatasetError('RECOVERY_CONFLICT','Uncommitted path ownership is uncertain; manual review required')
                path.unlink()
    # A pre-commit crash/cancel is a terminal honest receipt, not silent retry.
    return error_result('REQUEST_INTERRUPTED',request['execution_id'],'tool_error')


async def run_job(store,name,args,execution_id,record=lambda *a,**k:None):
    fingerprint=hashlib.sha256(encode_result({'tool':name,'arguments':args,'mode':store.context['mode'],
                                           'project_id':store.context['project_id']})).hexdigest()
    key=args.get('request_id') or str(uuid.uuid4())
    ledger_relative='.nomisma/requests/'+key
    with lock(store,ledger_relative+'/lock'):
        ledger=store.path(ledger_relative)
        with lock(store,'.nomisma/commit.lock'):
            replay=recover(store,ledger,fingerprint)
        if replay is not None:record('request_replayed',execution_id=execution_id,tool=name);return replay
        write_json(ledger/'request.json',{'fingerprint':fingerprint,'execution_id':execution_id,'tool':name})
        relative='.nomisma/jobs/job_'+uuid.uuid4().hex
        job=store.directory(relative);worker=None;cancelled=False
        try:
            original,baseline=snapshot(store,job,args)
            write_json(job/'request.json',{'tool':name,'arguments':args,'execution_id':execution_id})
            worker=Worker();worker.start(relative)
            record('worker_started',execution_id=execution_id,tool=name,worker_pid=worker.process.pid)
            with anyio.fail_after(DEADLINE):
                while worker.process.poll() is None:await anyio.sleep(.025)
            if worker.process.returncode:raise DatasetError('WORKER_FAILED','Owned worker failed before preparing results')
            reply=read_json(job/'reply.json',16384)
            provenance=read_json(job/'environment.json',64000)
            from shared_store import environment
            if (provenance.get('execution_id')!=execution_id or provenance.get('tool_id')!=name
                    or provenance.get('environment')!=environment()):raise DatasetError('ENVIRONMENT_CHANGED','Worker code or dependencies changed during execution')
            write_json(ledger/'environment.json',provenance)
            if not reply['ok']:raise DatasetError(reply['code'],'Fixed operation failed; no result was published: '+reply['code'])
            result=reply['result'];view=SnapshotStore(store,job/'workspace')
            files=changed_files(view,baseline);validate_candidate(view,name,args,result,files)
            if result['data']['execution_id']!=execution_id:raise DatasetError('RESULT_VALIDATION_FAILED','Execution identity mismatch')
            record('worker_prepared',execution_id=execution_id,tool=name,file_count=len(files))
            await anyio.lowlevel.checkpoint()  # Cancellation wins before the commit lock.
            with lock(store,'.nomisma/commit.lock'):
                store.check_context()
                for r,digest in original.items():
                    if file_sha256(store.path(r))!=digest:raise DatasetError('STALE_DATASET','Input changed before commit')
                for r in files:
                    if store.path(r,allow_missing=True).exists():raise DatasetError('FILE_EXISTS','Destination exists; nothing is overwritten')
                write_json(ledger/'prepared.json',{'files':files})
                # Linearization point: synchronous commit from here; a later
                # cancel cannot roll back a committed receipt or erase facts.
                result=promote(store,job,files,result,ledger,name,args)
                record('operation_committed',execution_id=execution_id,tool=name,file_count=len(files))
                return result
        except anyio.get_cancelled_exc_class():
            cancelled=True
            record('operation_cancelled',execution_id=execution_id,tool=name);raise
        except DatasetError as error:
            if not (ledger/'committed.json').exists():
                failure=error_result(error.code,execution_id)
                if 'request_id' in args:failure['data']['request_id']=args['request_id']
                write_json(ledger/'failed.json',bounded(failure))
            raise
        finally:
            if worker is not None:worker.close()
            if cancelled and not (ledger/'committed.json').exists():
                # Durable cancellation proof is written only after our worker tree exits.
                write_json(ledger/'cancelled.json',error_result('TASK_CANCELLED',execution_id,'tool_error'))
            # Only the exclusive job tree created by this invocation. Registry,
            # input snapshots, candidates are private temporary copies, never users' files.
            safe=store.path(relative)
            if safe!=job or not re.fullmatch(r'job_[a-f0-9]{32}',job.name):raise DatasetError('CLEANUP_CONFLICT','Job identity changed')
            for directory,dirs,names in os.walk(job,topdown=False,followlinks=False):
                for name in names:
                    p=Path(directory)/name
                    if _reparse(p):raise DatasetError('CLEANUP_CONFLICT','Job contains a redirected file')
                    p.unlink()
                for name in dirs:
                    p=Path(directory)/name
                    if _reparse(p):raise DatasetError('CLEANUP_CONFLICT','Job contains a redirected directory')
                    p.rmdir()
            job.rmdir()
