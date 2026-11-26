"""Remember which dependency sets the installer has already installed AND verified, so
re-running INSTALL.bat does not reinstall (or uninstall/reinstall) anything unchanged.

  python -m app.install_state check main          exit 0 if up to date
  python -m app.install_state save diarization    record success
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".venv" / "lsk_install_state.json"
FILES = {"main": ["requirements.txt"],
         "diarization": ["requirements-torch.txt", "requirements-diarization.txt"]}


def digest(kind):
    h = hashlib.sha256()
    for name in FILES[kind]:
        path = ROOT / name
        h.update(name.encode()); h.update(path.read_bytes() if path.exists() else b"<missing>")
    return h.hexdigest()


def _load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def is_current(kind):
    return _load().get(kind) == digest(kind)


def save(kind):
    state = _load(); state[kind] = digest(kind)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state), encoding="utf-8")


def main(argv):
    if len(argv) != 3 or argv[1] not in {"check", "save"} or argv[2] not in FILES:
        print(__doc__); return 2
    if argv[1] == "check":
        return 0 if is_current(argv[2]) else 1
    save(argv[2]); return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
