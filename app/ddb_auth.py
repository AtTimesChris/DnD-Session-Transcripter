"""Optional D&D Beyond account connection (v2.33).

The user pastes their own D&D Beyond session cookie (CobaltSession) once. With it the program asks
D&D Beyond's auth service for a short-lived token and reads characters the account can see, including
other players' campaign-only characters. The same route is used by other community tools; D&D Beyond
has no official public API, so it may stop working if they change their site.

Safety:
- No password is ever asked for or stored.
- The cookie is stored outside the program folder, in the user's Windows profile
  (%LOCALAPPDATA%\\LocalSessionKeeper\\ddb_session.bin), encrypted with Windows DPAPI for this Windows
  user only. It is never written to config.json, the database, logs or exports, so a copy of the
  program (for example from Git) never carries it.
- Disconnect deletes the file. Logging out of D&D Beyond in the browser also ends the session.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_URL = "https://auth-service.dndbeyond.com/v1/cobalt-token"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
_ENTROPY = b"LocalSessionKeeper/ddb-session/v1"


class NotConnected(Exception):
    pass


class SessionExpired(Exception):
    pass


def session_path():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "LocalSessionKeeper" / "ddb_session.bin"


def clean_cookie(pasted):
    """Accept the bare value, 'CobaltSession=value', or a whole Cookie header; return the value."""
    text = str(pasted or "").strip().strip('"').strip("'")
    match = re.search(r"CobaltSession\s*=\s*([^;\s]+)", text, re.I)
    value = match.group(1) if match else text
    value = value.strip()
    if not value or len(value) < 20 or re.search(r"[\s;,]", value):
        raise ValueError("That does not look like a CobaltSession cookie value. Copy only the Value column of the "
                         "cookie named CobaltSession.")
    return value


# ---------- Windows DPAPI (per-user encryption) ---------------------------------------------

def _dpapi(data, protect):
    if sys.platform != "win32":
        raise RuntimeError("Saving the D&D Beyond connection needs Windows (it is encrypted with Windows' own per-user protection).")
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b):
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, _keep = blob(data)
    ent, _keep2 = blob(_ENTROPY)
    out = BLOB()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = (fn(ctypes.byref(src), "LocalSessionKeeper D&D Beyond", ctypes.byref(ent), None, None, flags, ctypes.byref(out))
          if protect else fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, flags, ctypes.byref(out)))
    if not ok:
        raise RuntimeError("Windows could not " + ("encrypt" if protect else "decrypt") + " the D&D Beyond connection.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def save_session(cookie_value, path=None):
    path = Path(path or session_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(_dpapi(cookie_value.encode("utf-8"), True))
    tmp.replace(path)
    return path


def load_session(path=None):
    path = Path(path or session_path())
    if not path.is_file():
        raise NotConnected("No D&D Beyond account is connected.")
    try:
        return _dpapi(path.read_bytes(), False).decode("utf-8")
    except Exception as exc:
        raise NotConnected("The saved D&D Beyond connection could not be read (it only works for the Windows user who saved it). "
                           "Connect again.") from exc


def has_session(path=None):
    return Path(path or session_path()).is_file()


def clear_session(path=None):
    path = Path(path or session_path())
    if path.is_file():
        path.unlink()
        return True
    return False


# ---------- token ----------------------------------------------------------------------------

def _post_for_token(cookie_value):
    request = urllib.request.Request(TOKEN_URL, data=b"", method="POST", headers={
        "User-Agent": USER_AGENT, "Accept": "application/json", "Cookie": f"CobaltSession={cookie_value}"})
    with urllib.request.urlopen(request, timeout=18) as response:
        return json.loads(response.read(100_000).decode("utf-8", "replace"))


def token_from_cookie(cookie_value, post=None):
    """A short-lived bearer token for the character service. Raises SessionExpired when D&D Beyond refuses."""
    try:
        data = (post or _post_for_token)(cookie_value)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise SessionExpired("D&D Beyond did not accept the saved session (logged out or expired). Connect again "
                                 "with a fresh CobaltSession value.") from exc
        raise
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        raise SessionExpired("D&D Beyond answered without a token: the session has probably expired. Connect again.")
    return token


def account_token():
    """Token for the connected account, or None when no account is connected."""
    if not has_session():
        return None
    return token_from_cookie(load_session())
