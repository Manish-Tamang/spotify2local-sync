"""Canonical metadata lookups: YouTube Music (identity/album/art), Spotify art, LRCLIB lyrics. All cached."""
import hashlib, json, re, time, requests
from ytmusicapi import YTMusic
from .config import CACHE
from .textnorm import title_score, artist_score, core_title, norm

UA = {"User-Agent": "msync/1.0 (personal music library organizer)"}
_ytm = None

def ytm() -> YTMusic:
    global _ytm
    if _ytm is None:
        _ytm = YTMusic()
    return _ytm

def _cached(kind: str, key: str, fn):
    d = CACHE / kind
    d.mkdir(exist_ok=True)
    f = d / (hashlib.sha1(key.encode()).hexdigest() + ".json")
    if f.exists():
        return json.loads(f.read_text())
    val = fn()
    f.write_text(json.dumps(val, ensure_ascii=False))
    return val

def _retry(fn, tries=4):
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))

def ytm_search(query: str, limit: int = 8, kind: str = "songs") -> list[dict]:
    def go():
        res = _retry(lambda: YTMusic().search(query, filter=kind, limit=limit))
        return [{
            "videoId": r.get("videoId"),
            "title": r.get("title") or "",
            "artists": [a["name"] for a in r.get("artists") or [] if a.get("name")],
            "album": (r.get("album") or {}).get("name"),
            "albumId": (r.get("album") or {}).get("id"),
            "duration": r.get("duration_seconds"),
            "year": r.get("year"),
            "thumb": (r.get("thumbnails") or [{}])[-1].get("url"),
        } for r in res if r.get("videoId")]
    return _cached("ytm_search", query if kind == "songs" else f"{kind}:{query}", go)

def ytm_album(album_id: str) -> dict | None:
    def go():
        time.sleep(0.25)
        try:
            a = _retry(lambda: ytm().get_album(album_id))
        except Exception:
            return None
        return {"title": a.get("title"), "year": a.get("year"),
                "artists": [x["name"] for x in a.get("artists") or []],
                "thumb": (a.get("thumbnails") or [{}])[-1].get("url"),
                "tracks": [{"videoId": t.get("videoId"), "title": t.get("title"), "n": t.get("trackNumber")} for t in a.get("tracks", [])]}
    return _cached("ytm_album", album_id, go)

def big_ytm_art(url: str | None) -> str | None:
    return re.sub(r"=w\d+-h\d+.*$", "=w1200-h1200-l90-rj", url) if url else None

def spotify_art_variants(url: str | None) -> list[str]:
    """Spotify i.scdn.co ids: ...b273=640px, ...82c1=original (often 1000-3000px)."""
    if not url:
        return []
    out = []
    if "ab67616d0000b273" in url:
        out.append(url.replace("ab67616d0000b273", "ab67616d000082c1"))
    out.append(url)
    return out

def best_match(cands: list[dict], local_str: str | None = None, *, title: str | None = None,
               artists: list[str] | None = None, duration: int | None = None) -> tuple[dict | None, float]:
    """Score candidates either against a raw local string, or against known title/artists."""
    best, best_s = None, 0.0
    for c in cands:
        if local_str is not None:
            ts = title_score(local_str, c["title"])
            as_ = artist_score(local_str, c["artists"])
        else:
            ts = max(title_score(c["title"], title), title_score(title, c["title"]))
            as_ = max([artist_score(" ".join(c["artists"]), [a]) for a in artists] + [0])
        s = 0.55 * ts + 0.35 * as_
        if duration and c.get("duration"):
            dd = abs(c["duration"] - duration)
            s += 10 if dd <= 3 else 5 if dd <= 6 else -5 if dd <= 12 else -30
        V = r"\b(live|acoustic|unplugged|demo|remix|session|instrumental|reprise)\b"
        want = local_str if local_str is not None else title
        if bool(re.search(V, c["title"] + " " + (c.get("album") or ""), re.I)) != bool(re.search(V, want or "", re.I)):
            s -= 30
        if re.search(r"\b(cover|karaoke|instrumental|remix|slowed|reverb|8d)\b", c["title"], re.I) and \
           not re.search(r"\b(cover|karaoke|instrumental|remix|slowed|reverb|8d)\b", (local_str or title or ""), re.I):
            s -= 20
        if s > best_s:
            best, best_s = c, s
    return best, best_s

# ---------------- lyrics ----------------
def lyrics(artist: str, title: str, album: str | None, duration: int | None) -> dict | None:
    key = f"{artist}|{title}|{album}|{duration}"
    def go():
        time.sleep(0.2)
        params = {"artist_name": artist, "track_name": core_title(title)}
        if album: params["album_name"] = album
        if duration: params["duration"] = duration
        try:
            r = _retry(lambda: requests.get("https://lrclib.net/api/get", params=params, headers=UA, timeout=30))
            if r.status_code == 200 and (r.json().get("syncedLyrics") or r.json().get("plainLyrics")):
                j = r.json(); return {"synced": j.get("syncedLyrics"), "plain": j.get("plainLyrics"), "src": "lrclib:get"}
            r = _retry(lambda: requests.get("https://lrclib.net/api/search",
                        params={"track_name": core_title(title), "artist_name": artist}, headers=UA, timeout=30))
            for j in (r.json() if r.ok else []):
                if j.get("instrumental"): continue
                okd = not duration or not j.get("duration") or abs(j["duration"] - duration) <= 6
                if okd and title_score(j.get("trackName", ""), core_title(title)) >= 85 and \
                   artist_score(j.get("artistName", ""), [artist]) >= 75 and (j.get("syncedLyrics") or j.get("plainLyrics")):
                    return {"synced": j.get("syncedLyrics"), "plain": j.get("plainLyrics"), "src": "lrclib:search"}
        except Exception as ex:
            return {"error": str(ex)}
        return None
    v = _cached("lyrics", key, go)
    if v and v.get("error"):   # don't keep transient failures cached
        (CACHE / "lyrics" / (hashlib.sha1(key.encode()).hexdigest() + ".json")).unlink(missing_ok=True)
        return None
    return v

# ---------------- cover art ----------------
def fetch_image(urls: list[str]) -> bytes | None:
    for u in urls:
        if not u: continue
        f = CACHE / "art" / (hashlib.sha1(u.encode()).hexdigest() + ".jpg")
        f.parent.mkdir(exist_ok=True)
        if f.exists() and f.stat().st_size > 0:
            return _shrink(f.read_bytes())
        try:
            r = requests.get(u, headers=UA, timeout=30)
            if r.ok and r.headers.get("content-type", "").startswith("image") and len(r.content) > 2000:
                data = _shrink(r.content)
                f.write_bytes(data)
                return data
        except Exception:
            pass
    return None

def _shrink(data: bytes, max_px: int = 1000) -> bytes:
    """JPEG <= 1000px: plenty for a phone, ~4x smaller than the 1200-3000px originals."""
    import io
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        if max(im.size) <= max_px and im.format == "JPEG" and len(data) < 400_000:
            return data
        im = im.convert("RGB")
        im.thumbnail((max_px, max_px), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88, optimize=True)
        return out.getvalue()
    except Exception:
        return data
