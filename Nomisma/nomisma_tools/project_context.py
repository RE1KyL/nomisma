"""Read only the explicit project registry; never inspect credentials or global state."""
import json
import os
from pathlib import Path
import re


def project_context(*,read_only=False):
    names=('NOMISMA_PROJECT_ID','NOMISMA_MODE','NOMISMA_SESSION_ID')
    if not any(os.environ.get(name) for name in names):
        return None
    project_id, mode, session_id=(os.environ.get(name,'') for name in names)
    if not all(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}', value) for value in (project_id,session_id)) or mode not in ('economic','finance','big_data_explore'):
        raise ValueError('Invalid project context')
    state=Path(os.environ.get('NOMISMA_STATE_ROOT',''))
    if not state.is_absolute() or state.name.lower()!='nomisma':
        raise ValueError('Independent state root required')
    projects=json.loads((state/'desktop-dev/openacpui-data/projects.json').read_text(encoding='utf-8-sig'))
    entries=[project for project in projects if project.get('id')==project_id]
    if len(entries)!=1:
        raise ValueError('Project is not registered')
    project=entries[0]
    if project.get('mode','finance' if project_id=='nomisma-hs300' else 'economic')!=mode or project.get('status','active') not in (('active','archived') if read_only else ('active',)):
        raise ValueError('Project mode/status mismatch')
    root=Path(project['path']).absolute()
    declared=Path(os.environ.get('NOMISMA_WORKSPACE','')).absolute()
    if os.path.normcase(str(root))!=os.path.normcase(str(declared)) or os.path.normcase(str(root))!=os.path.normcase(str(root.resolve(strict=True))) or root.is_symlink() or not root.is_dir():
        raise ValueError('Project root mismatch or redirection')
    return {'mode':mode,'project_id':project_id,'session_id':session_id},root
