"""Private CurrentUser DPAPI helper. Binary pipes only; no logging or packages.

Launched with Python -I -S. This entry is never registered as an Agent tool.
"""
import ctypes,sys
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]


def main():
    if sys.platform!='win32' or len(sys.argv)!=2 or sys.argv[1] not in ('encrypt','decrypt'):return 1
    raw=bytearray(sys.stdin.buffer.read(65537))
    if not raw or len(raw)>65536:return 1
    array=(ctypes.c_ubyte*len(raw)).from_buffer(raw);source=Blob(len(raw),array);target=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    protect=crypt.CryptProtectData;unprotect=crypt.CryptUnprotectData
    protect.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    unprotect.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    try:
        # CRYPTPROTECT_UI_FORBIDDEN, CurrentUser (not machine-wide).
        ok=protect(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)) if sys.argv[1]=='encrypt' else unprotect(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target))
        if not ok:return 1
        sys.stdout.buffer.write(ctypes.string_at(target.data,target.size));sys.stdout.buffer.flush();return 0
    finally:
        ctypes.memset(ctypes.addressof(array),0,len(raw))
        if target.data:ctypes.memset(target.data,0,target.size);kernel.LocalFree(target.data)


if __name__=='__main__':
    try:code=main()
    except BaseException:code=1
    raise SystemExit(code)
