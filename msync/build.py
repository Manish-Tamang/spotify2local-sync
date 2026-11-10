"""Enrich identified songs, download missing ones, and write the organized, tagged library."""
import html, json, pathlib, shutil, subprocess, re, sys
from .config import LIBRARY, CACHE, ALIASES
from .textnorm import norm, core_title, safe_filename
from .meta import ytm_search, ytm_album, best_match, big_ytm_art, spotify_art_variants, fetch_image, lyrics
from . import tagger

YTDLP = str(pathlib.Path(sys.executable).parent / "yt-dlp")   # installed next to this Python

_ALIASES = None

def canon_artists(artists: list[str]) -> list[str]:
    """Unescape HTML, re-join '&amp;' splits, apply config/artist_aliases.json, drop dupes."""
    global _ALIASES
    if _ALIASES is None:
        f = ALIASES
        _ALIASES = {k: v for k, v in json.loads(f.read_text()).items() if not k.startswith("_")} if f.exists() else {}
    out = []
    for a in artists or []:
        a = html.unescape(a).strip()
        if a.lower().startswith("amp; ") and out:
            out[-1] = f"{out[-1]} & {a[5:]}"
            continue
        a = _ALIASES.get(a, a)
        if a and a not in out:
            out.append(a)
    for i, a in enumerate(out):   # re-alias merged names ("Sabin Rai & The Pharaoh")
        out[i] = _ALIASES.get(a, a)
    return list(dict.fromkeys(out)) or ["Unknown Artist"]

def song_key(rec) -> str:
    if rec.get("spotify_id"):
        return "sp:" + rec["spotify_id"]
    if rec.get("videoId"):
        return "yt:" + rec["videoId"]
    return "local:" + norm(", ".join(rec["artists"]) + " " + rec["title"])

def enrich(rec: dict) -> dict:
    """Fill album / track / year / videoId / cover urls. rec has title, artists, duration (and maybe spotify cover)."""
    if not rec.get("videoId") or not rec.get("albumId"):
        live = re.search(r"\b(live|acoustic|unplugged|demo|session|reprise)\b", rec["title"], re.I)
        q = f"{rec['artists'][0]} {re.sub(r' - ', ' ', rec['title']) if live else core_title(rec['title'])}"
        c, score = best_match(ytm_search(q), title=rec["title"], artists=rec["artists"], duration=rec.get("duration"))
        if not (c and score >= 80) and not rec.get("source"):
            # download fallback: official music video uploads (common for Nepali songs); be strict
            from .textnorm import pair_score
            vids = ytm_search(q, kind="videos")
            best = None
            for v in vids:
                ts, as_ = pair_score(f"{', '.join(v['artists'])} - {v['title']}", rec["title"], rec["artists"])
                ts2 = max(ts, pair_score(v["title"], rec["title"], rec["artists"])[0])
                dd = abs((v.get("duration") or 0) - (rec.get("duration") or 0)) if rec.get("duration") else 0
                if ts2 >= 85 and (as_ or 0) >= 80 and dd <= 15:
                    best = v; break
            if best:
                rec["videoId"] = best["videoId"]; rec["ytm_score"] = "video"
                return _finish(rec)
        if c and score >= 80:
            rec.setdefault("videoId", c["videoId"])
            rec.setdefault("albumId", c.get("albumId"))
            rec.setdefault("album", c.get("album"))
            rec["ytm_thumb"] = c.get("thumb")
            rec["ytm_score"] = round(score)
    if rec.get("albumId"):
        a = ytm_album(rec["albumId"])
        if a:
            rec["album"] = rec.get("album") or a["title"]
            rec["year"] = rec.get("year") or a.get("year")
            rec["album_artist"] = canon_artists(a.get("artists") or [])[0] if a.get("artists") else None
            rec["ytm_thumb"] = a.get("thumb") or rec.get("ytm_thumb")
            for t in a["tracks"]:
                if t["videoId"] == rec.get("videoId") or norm(t["title"] or "") == norm(rec["title"]):
                    rec["track"] = t["n"]; break
    return _finish(rec)

def _finish(rec: dict) -> dict:
    rec["cover_urls"] = spotify_art_variants(rec.get("spotify_cover")) + [big_ytm_art(rec.get("ytm_thumb")), rec.get("ytm_thumb")]
    return rec

def group_of(rec: dict, groups: dict) -> tuple[str, str]:
    for a in rec["artists"]:
        g = groups.get(norm(a))
        if g:
            return g[0], g[1]
    return "Unsorted", "Unsorted"

def target_path(rec: dict) -> pathlib.Path:
    name = safe_filename(f"{', '.join(rec['artists'][:3])} - {rec['title']}") + ".mp3"
    return LIBRARY / safe_filename(rec["language"]) / safe_filename(rec["genre"]) / name

def download(video_id: str, out: pathlib.Path | None = None) -> bool:
    """YouTube Music audio -> MP3 (VBR ~245kbps). Returns True on success."""
    tmp = CACHE / "dl"
    tmp.mkdir(exist_ok=True)
    stem = tmp / video_id
    if not (stem.with_suffix(".mp3")).exists():
        r = subprocess.run([YTDLP, "--js-runtimes", "node", "--no-warnings", "-q", "--no-playlist",
                            "-f", "bestaudio", "-x", "--audio-format", "mp3", "--audio-quality", "0",
                            "-o", str(stem) + ".%(ext)s", f"https://music.youtube.com/watch?v={video_id}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("   yt-dlp:", r.stderr.strip()[-300:], file=sys.stderr)
            return False
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(stem.with_suffix(".mp3"), out)
    return True

def to_mp3(src: pathlib.Path, out: pathlib.Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".mp3":
        shutil.copyfile(src, out)
    else:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-codec:a", "libmp3lame", "-q:a", "0", str(out)], check=True)

def finalize(rec: dict, path: pathlib.Path) -> None:
    """Tag the file at path with cover + lyrics."""
    cover = fetch_image([u for u in rec.get("cover_urls", []) if u])
    rec["has_cover"] = bool(cover)
    lyr = lyrics(rec["artists"][0], rec["title"], rec.get("album"), rec.get("duration"))
    if lyr and rec.get("length_mismatch"):
        lyr = {"plain": lyr.get("plain") or _strip_lrc(lyr.get("synced")), "src": lyr.get("src")}
    rec["lyrics"] = "synced" if lyr and lyr.get("synced") else "plain" if lyr and lyr.get("plain") else None
    tagger.write(path, rec, cover, lyr)

def _strip_lrc(text):
    return re.sub(r"^\[\d+:\d+(?:\.\d+)?\]\s?", "", text or "", flags=re.M) or None
