"""Run a child command and mirror its merged output to console and UTF-8 log."""
import argparse, os, subprocess, sys
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--log", required=True)
p.add_argument("command", nargs=argparse.REMAINDER)
a = p.parse_args()
cmd = a.command
if cmd and cmd[0] == "--": cmd = cmd[1:]
if not cmd: raise SystemExit("No command supplied")
log = Path(a.log); log.parent.mkdir(parents=True, exist_ok=True)
with log.open("a", encoding="utf-8", errors="replace", newline="") as out:
    out.write("\n--- COMMAND: " + subprocess.list2cmdline(cmd) + " ---\n"); out.flush()
    # Tell the app this file already receives its console output, so it does not also write
    # every line itself (each log line appeared twice before v2.31).
    env = dict(os.environ, LSK_MIRRORED_LOG=str(log.resolve()), PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=None, text=True, encoding="utf-8", errors="replace", bufsize=1, universal_newlines=True)
    for line in proc.stdout:
        sys.stdout.write(line); sys.stdout.flush(); out.write(line); out.flush()
    raise SystemExit(proc.wait())
