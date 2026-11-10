"""Paths and settings.

Everything machine-specific can be overridden in a `.env` file at the project root (see `.env.example`).
Defaults assume WSL on Windows: the library is the Windows user's Music folder and adb.exe comes from
winget's Google.PlatformTools package.
"""
import glob
import os
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_dotenv(path: pathlib.Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _windows_music_dir() -> pathlib.Path:
    """C:\\Users\\<you>\\Music as a WSL path (falls back to ~/Music outside WSL)."""
    try:
        prof = subprocess.run(["cmd.exe", "/c", "echo %USERPROFILE%"], capture_output=True, text=True,
                              timeout=10, cwd="/mnt/c").stdout.strip()
        if prof and "%" not in prof:
            wsl = subprocess.run(["wslpath", "-u", prof], capture_output=True, text=True, timeout=10).stdout.strip()
            if wsl:
                return pathlib.Path(wsl) / "Music"
    except (OSError, subprocess.SubprocessError):
        pass
    return pathlib.Path.home() / "Music"


def _find_adb() -> str:
    found = shutil.which("adb.exe") or shutil.which("adb")
    if found:
        return found
    hits = glob.glob("/mnt/c/Users/*/AppData/Local/Microsoft/WinGet/Packages/Google.PlatformTools*/platform-tools/adb.exe")
    return hits[0] if hits else "adb"


# --- user-editable config (committed) ---------------------------------------------------------
CONFIG = ROOT / "config"
GROUPS = CONFIG / "artist_groups.json"      # normalized artist -> [Language, Genre]
ALIASES = CONFIG / "artist_aliases.json"    # "wrong spelling" -> "right spelling"
OVERRIDES = CONFIG / "overrides.json"       # "phone/file.mp3" -> "Artist - Title" | "skip"

# --- local state (gitignored) -----------------------------------------------------------------
DATA = ROOT / "data"
CACHE = DATA / "cache"                      # API lookups, cover art, downloads
SPOTIFY = DATA / "spotify"                  # one JSON per Spotify track id
CATALOG = DATA / "catalog.json"             # one record per song in the organized library
PLAYLISTS = DATA / "playlists.json"         # playlist name -> Spotify track ids
IDENTIFIED = DATA / "identified.json"       # what each backed-up phone file turned out to be
REPORT = DATA / "report.md"
PUSHED = DATA / "pushed.json"               # files verified on the phone

# --- machine paths ----------------------------------------------------------------------------
LIBRARY = pathlib.Path(os.environ.get("MSYNC_LIBRARY") or _windows_music_dir())   # laptop library = what the phone mirrors
BACKUP = pathlib.Path(os.environ.get("MSYNC_BACKUP", "/mnt/e/Music/PhoneBackup"))  # where `msync backup` pulls the phone to
PHONE_MUSIC = os.environ.get("MSYNC_PHONE_MUSIC", "/sdcard/Music")
ADB = os.environ.get("MSYNC_ADB") or _find_adb()

for _p in (CONFIG, DATA, CACHE, SPOTIFY):
    _p.mkdir(parents=True, exist_ok=True)
