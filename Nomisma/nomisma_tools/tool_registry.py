"""Versioned discovery over the one real MCP registry; never a second executor."""
from pathlib import Path
import hashlib
from analysis_result import encode_result
from dataset_store import DatasetError,file_sha256

def descriptors(mode,query='',include_unavailable=False):
    from mcp_contract import SPECS,OUTPUT_SCHEMA,visible
    if mode not in ('economic','finance','big_data_explore'):raise DatasetError('MODE_NOT_ALLOWED','A registered domain is required')
    if not isinstance(query,str) or len(query)>200:raise DatasetError('INVALID_ARGUMENT','Bounded discovery query required')
    rows=[];code=b''.join(p.name.encode()+p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py')))
    for name,spec in visible(mode).items():
        domain=spec['mode'];read_only=spec['read_only']
        row={'tool_id':name,'domain':domain,'name':name,'description':spec['description'],
          'use_cases':[name.removeprefix('nomisma_').replace('_',' ')],
          'input_schema':spec['inputSchema'],'output_schema':OUTPUT_SCHEMA,
          'prerequisites':['registered project','hash-bound inputs where required','current schema validation'],
          'dependencies':['nomisma Python runtime','project store','owned Worker'] if name!='nomisma_health_check' else ['nomisma Python runtime'],
          'permission':{'policy':'ask_each_call','risk_level':1 if read_only else 3 if any(s in name for s in ('fetch','import','clean','export')) else 2},
          'version':hashlib.sha256(encode_result(spec)+code).hexdigest(),
          'available':True,'error_handling':'stable error code; no execution fallback or silent retry'}
        if name.startswith('nomisma_explore_'):
            row['supported_dataset_versions']=[2];row['prerequisites'].append('Explore v2 disk-backed dataset where required; explicit finite limits; no automatic sampling')
        elif name.startswith(('nomisma_dataset_','nomisma_analysis_')):
            row['supported_dataset_versions']=[1];row['prerequisites'].append('Legacy bounded v1 dataset only; use Explore tools for v2 disk datasets')
        if not query or query.casefold() in (name+' '+spec['description']).casefold():rows.append(row)
    if include_unavailable and mode=='finance':
        from quant_research import capability_catalog
        for c in capability_catalog(mode=mode):
            if query and query.casefold() not in encode_result(c).decode().casefold():continue
            rows.append({'tool_id':c['tool_id'],'name':c['name'],'domain':'finance','description':c['description'],
              'use_cases':c['scenarios'],'input_schema':c['input_schema'],'output_schema':c['output_schema'],
              'prerequisites':c['preconditions'],'dependencies':c['dependencies'],'permission':c['permission'],
              'version':file_sha256(Path(__file__).with_name('quant_research.py')),'available':False,
              'unavailable_reason':'Internal 3A API, not exposed as an arbitrary Agent function'})
    return rows

def require_tool(mode,name,arguments):
    from mcp_contract import validate
    validate(name,arguments,mode)
    return next(d for d in descriptors(mode) if d['tool_id']==name)
