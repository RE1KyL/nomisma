"""Trusted-caller Finance intake. Internal API, deliberately absent from MCP tools.

Acquisition review does not enable sources; publication keeps candidate status.
Cancellation before bundle commit leaves prior committed datasets intact and
returns their refs in the caller's progress list; it never reports a finished bundle.
"""
from dataclasses import dataclass,field
import pandas as pd
from dataset_store import DatasetError
from ashare_data import validate_master,validate_calendar,validate_financials,VERSION
from research_data_store import checkpoint,publish_table,publish_bundle,read_bundle,validate_part,_gate,_artifact

@dataclass
class IntakeProgress:
    tables:dict=field(default_factory=dict)
    bundle:dict|None=None

def require_finance(store):
    store.check_context()
    if store.context['mode']!='finance':raise DatasetError('MODE_NOT_ALLOWED','A-share intake requires a Finance project')

def save_intake(store,parts,*,gate,evidence=(),cancelled=None,progress=None):
    """parts: role -> {frame,raw_bytes,source_note[,source_function,parameters]}.

    An explicit progress object makes already committed refs visible on interruption.
    Re-invocation creates new identities: this internal API is NOT a request-id job API.
    """
    require_finance(store);checkpoint(store,cancelled)
    if not isinstance(parts,dict) or not parts or len(parts)>9:raise DatasetError('INVALID_ARGUMENT','A bounded table mapping is required')
    progress=IntakeProgress() if progress is None else progress
    if progress.tables or progress.bundle is not None:raise DatasetError('INVALID_ARGUMENT','A fresh progress record is required')
    _gate(gate)
    if not isinstance(evidence,(list,tuple)) or len(evidence)>64:raise DatasetError('RESOURCE_LIMIT','Invalid evidence list')
    for ref in evidence:_artifact(store,ref)
    # Validate all parts before any write. Expensive normalization is performed by pure adapters.
    for role,part in parts.items():
        if not isinstance(part,dict) or not {'frame','raw_bytes','source_note'}<=set(part) or set(part)-{'frame','raw_bytes','source_note','source_function','parameters','source_receipt'}:
            raise DatasetError('INVALID_ARGUMENT','Unknown intake fields')
        validate_part(role=role,**part)
        if role=='security_master':validate_master(part['frame'])
        if role=='calendar':
            for exchange in part['frame'].exchange.unique():validate_calendar(part['frame'],exchange)
        if role=='financial_facts':
            f=validate_financials(part['frame'])
            reviewed=f[f.evidence_status=='reviewed_original']
            if not set(reviewed.evidence_id)<={r['path'] for r in evidence}:
                raise DatasetError('EVIDENCE_MISSING','Reviewed facts must bind the original evidence artifacts')
    for role,part in parts.items():
        checkpoint(store,cancelled)
        progress.tables[role]=publish_table(store,role=role,cancelled=cancelled,**part)
    require_finance(store);checkpoint(store,cancelled)
    progress.bundle=publish_bundle(store,progress.tables,gate=gate,evidence=evidence,cancelled=cancelled)
    read_bundle(store,progress.bundle)
    return {'ok':True,'summary':'Candidate A-share intake saved and read back',
        'data':{'version':VERSION,'tables':progress.tables,'bundle':progress.bundle,'strict_backtest_ready':False},
        'artifacts':[{'path':progress.bundle['manifest_path'],'sha256':progress.bundle['sha256']}],
        'warnings':['Internal API only; not enabled as an Agent tool','Candidate source data is not PIT certification'], 'error':None}
