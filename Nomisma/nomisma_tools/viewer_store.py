"""Explicit read-only authority, including archived projects; cannot create files."""
import os
from pathlib import Path
from dataset_store import ProjectDatasetStore,DatasetError,_reparse
from project_context import project_context
class ViewerStore(ProjectDatasetStore):
    def __init__(self):
        try:
            state=Path(os.environ['NOMISMA_STATE_ROOT'])
            for suffix in ('','desktop-dev','desktop-dev/openacpui-data','desktop-dev/openacpui-data/projects.json'):
                if _reparse(state/suffix):raise ValueError()
            binding=project_context(read_only=True)
            if binding is None:raise ValueError()
            self.context,self.root=binding
            if _reparse(self.root):raise ValueError()
        except (ValueError,OSError,KeyError):raise DatasetError('PROJECT_CONTEXT_INVALID','Registered viewer context required')
    def check_context(self):
        if project_context(read_only=True)!=(self.context,self.root):raise DatasetError('PROJECT_CONTEXT_INVALID','Viewer project context changed')
    def directory(self,relative):
        p=self.path(relative)
        if not p.is_dir():raise DatasetError('PERMISSION_DENIED','Viewer cannot create directories')
        return p
    def transaction(self):raise DatasetError('PERMISSION_DENIED','Viewer cannot create or modify artifacts')
    def _publish_file(self,*args):raise DatasetError('PERMISSION_DENIED','Viewer cannot publish files')
