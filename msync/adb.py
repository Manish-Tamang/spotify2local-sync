"""Thin wrapper around Windows adb.exe (called from WSL)."""
import subprocess, shlex, pathlib
from .config import ADB

def winpath(p) -> str:
    return subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True, check=True).stdout.strip()

def run(*args, check=True, **kw):
    return subprocess.run([ADB, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=check, **kw)

def shell(cmd: str, check=True) -> str:
    return run("shell", cmd, check=check).stdout

def device_state() -> str:
    out = run("devices", check=False).stdout.strip().splitlines()[1:]
    return out[0].split()[1] if out else "none"

def q(p: str) -> str:
    return shlex.quote(p)

def list_files(remote_dir: str) -> dict[str, int]:
    """relative path -> size, recursive."""
    out = shell(f"find {q(remote_dir)} -type f -exec stat -c '%s|%n' {{}} +", check=False)
    res = {}
    for line in out.splitlines():
        if "|" in line:
            size, path = line.split("|", 1)
            res[path[len(remote_dir.rstrip('/')) + 1:]] = int(size)
    return res

def pull(remote: str, local) -> None:
    pathlib.Path(local).parent.mkdir(parents=True, exist_ok=True)
    run("pull", "-a", remote, winpath(local))

def push(local, remote: str, tries: int = 4) -> bool:
    import time
    for i in range(tries):
        try:
            shell(f"mkdir -p {q(remote.rsplit('/', 1)[0])}")
            run("push", winpath(local), remote)
            return True
        except Exception:
            time.sleep(2 * (i + 1))
    return False

def media_scan(remote_path: str) -> None:
    """Ask Android's MediaProvider to (re)index a file or folder."""
    shell(f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d {q('file://' + remote_path)}", check=False)
    shell(f"content call --uri content://media --method scan_volume --arg external_primary", check=False)
