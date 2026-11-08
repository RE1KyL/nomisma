"""Owned worker process tree. Windows Job Object closes all nested fetch children.

The child waits on stdin; it cannot start work before successful Job assignment.
This is process lifecycle control, not an OS filesystem/network sandbox.
"""
import ctypes
import os
import signal
import subprocess
import sys
from pathlib import Path


class Worker:
    def __init__(self):
        allowed={'PATH','SYSTEMROOT','WINDIR','SYSTEMDRIVE','TEMP','TMP','USERPROFILE','LOCALAPPDATA',
                 'PROCESSOR_ARCHITECTURE','PROCESSOR_ARCHITEW6432',
                 'NOMISMA_PRIVACY_SETTINGS','NOMISMA_STATE_ROOT','NOMISMA_PROJECT_ID','NOMISMA_MODE','NOMISMA_SESSION_ID','NOMISMA_WORKSPACE'}
        env={k:os.environ[k] for k in os.environ if k.upper() in allowed}
        env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',POLARS_MAX_THREADS='1')
        self.process=subprocess.Popen([sys.executable,'-X','utf8','-B',str(Path(__file__).with_name('mcp_worker.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')
        self.job=None
        try:
            if os.name=='nt':self._assign()
        except BaseException:
            self.close();raise

    def _assign(self):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_=[('ProcessTime',ctypes.c_int64),('JobTime',ctypes.c_int64),('Flags',w.DWORD),
                ('MinWS',ctypes.c_size_t),('MaxWS',ctypes.c_size_t),('Active',w.DWORD),
                ('Affinity',ctypes.c_size_t),('Priority',w.DWORD),('Scheduling',w.DWORD)]
        class IO(ctypes.Structure):_fields_=[(n,ctypes.c_uint64) for n in ('ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes')]
        class Extended(ctypes.Structure):
            _fields_=[('Basic',Basic),('IO',IO),('ProcessMemory',ctypes.c_size_t),('JobMemory',ctypes.c_size_t),
                      ('PeakProcessMemory',ctypes.c_size_t),('PeakJobMemory',ctypes.c_size_t)]
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
        k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        k.CloseHandle.argtypes=[w.HANDLE]
        self.kernel=k;self.job=k.CreateJobObjectW(None,None)
        if not self.job:raise ctypes.WinError(ctypes.get_last_error())
        limits=Extended();limits.Basic.Flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if os.environ.get('NOMISMA_MODE')=='big_data_explore':
            limits.Basic.Flags|=0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
            limits.ProcessMemory=1024*1024*1024
        if not k.SetInformationJobObject(self.job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not k.AssignProcessToJobObject(self.job,w.HANDLE(int(self.process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def start(self,relative_job):
        self.process.stdin.write((relative_job+'\n').encode('utf8'));self.process.stdin.close()

    def close(self):
        if self.job:
            self.kernel.CloseHandle(self.job);self.job=None
        if self.process.poll() is None:
            if os.name=='nt':self.process.kill()
            else:
                try:os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
        self.process.wait(timeout=10)
