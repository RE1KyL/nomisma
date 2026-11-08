"""Finite connector gate; no credentials, project files or network in policy IO."""
import json,os,stat
from pathlib import Path
from dataset_store import DatasetError


def require_external_network():
    file=os.environ.get('NOMISMA_PRIVACY_SETTINGS')
    if not file:return  # Legacy offline fixtures have no configured cloud service.
    try:
        path=Path(file)
        try:
            info=path.lstat()
        except FileNotFoundError:
            return  # Default Strict permits approved public data retrieval.
        if not stat.S_ISREG(info.st_mode) or info.st_size>4096:raise ValueError()
        data=json.loads(path.read_text('utf8'))
        if data.get('version')!=1 or data.get('mode') not in ('standard','strict','local_only'):raise ValueError()
        fields=data.get('sensitive_fields',[])
        if (not isinstance(fields,list) or len(fields)>24 or
                any(not isinstance(field,str) or not 1<=len(field)<=40 or
                    not all(char.isalnum() or char in '_.-' for char in field) for field in fields)):
            raise ValueError()
        if data['mode']=='local_only':raise DatasetError('LOCAL_ONLY','External data retrieval is disabled in Local-only mode')
    except DatasetError:raise
    except Exception:raise DatasetError('LOCAL_ONLY','Invalid privacy policy; external access blocked') from None
