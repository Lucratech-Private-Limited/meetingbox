"""
Test configuration and shared stubs.

Dependency stubs: several server modules pull in packages (jose, redis,
pydantic, fastapi, anthropic …) that are only available inside the Docker
container. We inject minimal stubs here so the pure-logic tests can run
outside the container in a lightweight local Python environment.
"""

import sys
import types
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_rs = str(_root)
if _rs in sys.path:
    sys.path.remove(_rs)
sys.path.insert(0, _rs)


def _stub_module(name: str, **attrs):
    """Register a minimal stub module so imports don't fail."""
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
    return sys.modules[name]


# --- jose (JWT) ---
if "jose" not in sys.modules:
    jose = _stub_module("jose")
    jose_jwt = _stub_module("jose.jwt")
    jose_exc = _stub_module("jose.exceptions")

    class _JWTError(Exception):
        pass

    jose.JWTError = _JWTError  # type: ignore[attr-defined]
    jose_exc.JWTError = _JWTError  # type: ignore[attr-defined]

    class _FakeJWT:
        @staticmethod
        def decode(*a, **kw):
            raise _JWTError("stub")
        @staticmethod
        def encode(*a, **kw):
            return "stub"

    jose.jwt = _FakeJWT  # type: ignore[attr-defined]
    jose_jwt.decode = _FakeJWT.decode  # type: ignore[attr-defined]
    jose_jwt.encode = _FakeJWT.encode  # type: ignore[attr-defined]

# --- passlib ---
if "passlib" not in sys.modules:
    pl = _stub_module("passlib")
    plc = _stub_module("passlib.context")

    class _CryptCtx:
        def __init__(self, **kw): pass
        def hash(self, v): return v
        def verify(self, plain, hashed): return plain == hashed

    plc.CryptContext = _CryptCtx  # type: ignore[attr-defined]
    pl.context = plc  # type: ignore[attr-defined]

# --- redis ---
if "redis" not in sys.modules:
    r = _stub_module("redis")
    re = _stub_module("redis.exceptions")

    class _RedisError(Exception): pass

    class _FakeRedis:
        def __init__(self, *a, **kw): pass
        def get(self, *a, **kw): return None
        def set(self, *a, **kw): return True
        def delete(self, *a, **kw): return 0
        def ping(self): return True

    r.Redis = _FakeRedis  # type: ignore[attr-defined]
    r.StrictRedis = _FakeRedis  # type: ignore[attr-defined]
    re.ConnectionError = _RedisError  # type: ignore[attr-defined]
    r.exceptions = re  # type: ignore[attr-defined]

# --- slowapi (rate limiter) ---
if "slowapi" not in sys.modules:
    sa = _stub_module("slowapi")
    sal = _stub_module("slowapi.util")

    class _FakeLimiter:
        def __init__(self, *a, **kw): pass
        def limit(self, *a, **kw):
            def _deco(fn): return fn
            return _deco

    sa.Limiter = _FakeLimiter  # type: ignore[attr-defined]
    sal.get_remote_address = lambda r: "127.0.0.1"  # type: ignore[attr-defined]

# --- anthropic ---
if "anthropic" not in sys.modules:
    an = _stub_module("anthropic")

    class _FakeAnthropicMsg:
        content = []
        def __init__(self, *a, **kw): pass

    class _FakeAnthropicMessages:
        def create(self, *a, **kw): return _FakeAnthropicMsg()

    class _FakeAnthropic:
        def __init__(self, *a, **kw):
            self.messages = _FakeAnthropicMessages()

    an.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]

# --- mem0ai ---
if "mem0" not in sys.modules:
    _stub_module("mem0")
    _stub_module("mem0.client")
    _stub_module("mem0.client.utils")

# --- googleapiclient (Gmail API) ---
if "googleapiclient" not in sys.modules:
    _stub_module("googleapiclient")
    gd = _stub_module("googleapiclient.discovery")

    def _fake_build(*a, **kw):
        class _Stub:
            def __getattr__(self, name):
                return lambda *a, **kw: _Stub()
            def execute(self):
                return {}
        return _Stub()

    gd.build = _fake_build  # type: ignore[attr-defined]

# --- google.oauth2.credentials ---
if "google" not in sys.modules:
    g = _stub_module("google")
    go = _stub_module("google.oauth2")
    goc = _stub_module("google.oauth2.credentials")

    class _FakeCreds:
        def __init__(self, *a, **kw): pass

    goc.Credentials = _FakeCreds  # type: ignore[attr-defined]
    go.credentials = goc  # type: ignore[attr-defined]
    g.oauth2 = go  # type: ignore[attr-defined]
