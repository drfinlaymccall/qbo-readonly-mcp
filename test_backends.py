"""Offline tests for the secret-storage backends. Simulates Windows Credential Manager and the Linux Secret Service."""
import ctypes, sys, os, types, subprocess
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import qbo_mcp as q
from ctypes import wintypes

# --- fake advapi32 that behaves like CredReadW/CredWriteW
store = {}
_le=[0]
ctypes.set_last_error=lambda v: _le.__setitem__(0,v)
ctypes.get_last_error=lambda: _le[0]
class FakeFn:
    def __init__(self, f): self.f=f; self.argtypes=None; self.restype=None
    def __call__(self,*a): return self.f(*a)
class FakeAdv:
    def __init__(self):
        self._keep=[]
        def write(pcred, flags):
            c = pcred._obj if hasattr(pcred,'_obj') else pcred.contents
            store[c.TargetName] = (ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize), c.UserName, c.Persist, c.Type)
            return True
        def read(target, typ, flags, ppcred):
            if target not in store: ctypes.set_last_error(1168); return False
            blob, user, persist, t = store[target]
            CRED = ppcred._obj._type_
            cred = CRED(); buf=(ctypes.c_ubyte*len(blob)).from_buffer_copy(blob); self._keep.append((cred,buf))
            cred.CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)); cred.CredentialBlobSize=len(blob)
            ppcred._obj.contents = cred
            return True
        self.CredWriteW=FakeFn(write); self.CredReadW=FakeFn(read); self.CredFree=FakeFn(lambda p: None)
ctypes.WinDLL = lambda name, use_last_error=False: FakeAdv()
os.environ["QBO_SECRET_BACKEND"]="wincred"
assert q.kc_get("refresh_token") is None
q.kc_set("refresh_token","RT1-abc-123")
q.kc_set("client_secret","sëcret")
assert q.kc_get("refresh_token")=="RT1-abc-123", q.kc_get("refresh_token")
assert q.kc_get("client_secret")=="sëcret"
assert "qbo-readonly:refresh_token" in store and store["qbo-readonly:refresh_token"][2]==2 and store["qbo-readonly:refresh_token"][3]==1
q.kc_set("refresh_token","RT1-rotated"); assert q.kc_get("refresh_token")=="RT1-rotated"
try: q.kc_set("x","a"*3000); raise SystemExit("no size check")
except RuntimeError: pass
print("wincred marshalling OK")

# --- secret-tool via fake subprocess
ss={}
def fake_run(args, input=None, capture_output=False, text=False, check=False):
    r=types.SimpleNamespace(returncode=0,stdout="",stderr="")
    acct=args[-1]
    if args[1]=="store": ss[acct]=input
    elif args[1]=="lookup":
        if acct in ss: r.stdout=ss[acct]+"\n"
        else: r.returncode=1
    return r
q.subprocess.run=fake_run
os.environ["QBO_SECRET_BACKEND"]="secret-tool"
assert q.kc_get("realm_id") is None
q.kc_set("realm_id","123"); assert q.kc_get("realm_id")=="123"
print("secret-tool OK")
os.environ["QBO_SECRET_BACKEND"]="env"; os.environ["QBO_REALM_ID"]="999"
assert q.kc_get("realm_id")=="999"
try: q.kc_set("realm_id","1"); raise SystemExit("env should refuse")
except RuntimeError: pass
os.environ.pop("QBO_SECRET_BACKEND")
for platform, expected in [("darwin", "keychain"), ("win32", "wincred"), ("linux", "secret-tool")]:
    sys.platform = platform
    assert q._backend() == expected
print("ALL BACKEND TESTS OK")
