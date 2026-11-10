"""Identify local files: match against Spotify playlist tracks first, then YouTube Music search."""
import json, pathlib, re
from .config import SPOTIFY, OVERRIDES
from .textnorm import pair_score, loose_stem, clean_stem, title_score, artist_score, norm, core_title
from .meta import ytm_search, best_match
from . import tagger

AUDIO = {".mp3", ".webm", ".m4a", ".opus", ".flac", ".ogg", ".wav"}

VERSION = r"\b(live|acoustic|unplugged|demo|remix|session|sessions|instrumental|reprise|lofi|slowed)\b"

def spotify_tracks() -> dict[str, dict]:
    out = {}
    for f in SPOTIFY.glob("*.json"):
        t = json.loads(f.read_text())
        out[t["id"]] = t
    return out

def spotify_aliases(sp: dict[str, dict]) -> dict[str, str]:
    """Same song listed under several Spotify IDs (album vs compilation, or in both playlists) -> one canonical id."""
    canon, groups = {}, {}
    for t in sorted(sp.values(), key=lambda t: t["id"]):
        ver = " ".join(sorted(set(m.lower() for m in re.findall(VERSION, t["title"], re.I))))
        key = (norm(t["artists"][0]), norm(core_title(t["title"])), ver)
        for c in groups.get(key, []):
            if abs(sp[c]["duration"] - t["duration"]) <= 8:
                canon[t["id"]] = c
                break
        else:
            groups.setdefault(key, []).append(t["id"])
            canon[t["id"]] = t["id"]
    return canon

def scan(root: pathlib.Path) -> list[dict]:
    items = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and p.suffix.lower() in AUDIO and not rel.startswith(".thumbnails"):
            tags = tagger.read_basic(p) if p.suffix.lower() == ".mp3" else {"title": "", "artist": "", "album": ""}
            items.append({"rel": rel, "folder": rel.split("/")[0] if "/" in rel else "", "size": p.stat().st_size,
                          "duration": tagger.duration(p) if p.suffix.lower() == ".mp3" else None,
                          "stem": clean_stem(p.stem), "tags": tags})
    return items

def _accept(ts, as_, dd) -> str | None:
    """ts/as_ from textnorm.pair_score; as_ None = filename has no artist; dd = |duration diff| or None."""
    if as_ is not None:
        if ts >= 85 and as_ >= 85 and (dd is None or dd <= 10):
            return "high"
        if ts >= 90 and as_ >= 75 and dd is not None and dd <= 3:
            return "high"
        return None
    if ts >= 90 and dd is not None and dd <= 3:
        return "medium"
    return None

def _local_strings(it) -> list[str]:
    s = [it["stem"]]
    t = it["tags"]
    if t["title"] and t["artist"] and not re.search(r"spotidown|youtube|\.com|\.app|track\s*\d+|unknown", t["title"] + t["artist"], re.I):
        s.append(clean_stem(f"{t['artist']} - {t['title']}"))
    return list(dict.fromkeys(x for x in s if x))

BAD_VERSION = r"\b(cover|karaoke|instrumental|remix|slowed|reverb|8d|lofi|sped up)\b"

def _pick(cands, it, strings, accept=_accept):
    best = (None, -1, None)
    for ls in strings:
        pool, seen = [], set()
        for q in dict.fromkeys([ls, re.sub(r"\s+-\s+", " ", ls)]):
            for c in cands(q):
                key = c.get("videoId") or c.get("id")
                if key not in seen:
                    seen.add(key); pool.append(c)
            if cands is not ytm_search:
                break
        for c in pool:
            if re.search(BAD_VERSION, c["title"], re.I) and not re.search(BAD_VERSION, ls, re.I):
                continue
            if re.search(VERSION, c["title"], re.I) and not re.search(VERSION, it["rel"], re.I):
                continue   # playlist wants the live/acoustic/... version; a studio copy doesn't count
            if len(norm(core_title(c["title"]))) >= 4 and title_score(ls, c["title"]) < 70:   # cheap prefilter (skip for short titles)
                continue
            ts, as_ = pair_score(ls, c["title"], c["artists"])
            dd = abs(c["duration"] - it["duration"]) if it["duration"] and c.get("duration") else None
            conf = accept(ts, as_, dd)
            sc = ts + (as_ or 0) - (dd or 0) * 0.5 + (5 if conf == "high" else 0)
            if conf and sc > best[1]:
                best = (c, sc, conf)
    return best[0], best[2]

def _accept_spotify(ts, as_, dd):
    conf = _accept(ts, as_, dd)
    if not conf and as_ is not None and ts >= 95 and as_ >= 95 and dd is not None and dd <= 40:
        conf = "medium"     # same song, phone copy is a longer/shorter rip (intro/outro)
    return conf

def match_spotify(it, sp: dict[str, dict]):
    vals = list(sp.values())
    t, conf = _pick(lambda ls: vals, it, _local_strings(it), _accept_spotify)
    if t is not None and it["duration"] and abs(t["duration"] - it["duration"]) > 10:
        t = {**t, "length_mismatch": True}
    return t, conf

def match_ytm(it):
    return _pick(ytm_search, it, _local_strings(it))

def load_overrides() -> dict:
    f = OVERRIDES
    return {k: v for k, v in json.loads(f.read_text()).items() if not k.startswith("_")} if f.exists() else {}

def match_override(query: str, it):
    """User said what the song is ('Artist - Title'): trust it, just find the canonical release."""
    def acc(ts, as_, dd):
        return "manual" if ts >= 85 and (as_ is None or as_ >= 80) else None
    return _pick(ytm_search, it, [query], acc)

def identify_all(items: list[dict], log=print, workers: int = 6) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor
    sp = spotify_tracks()
    ov = load_overrides()
    def one(it):
        if it["rel"] in ov:
            q = ov[it["rel"]]
            if q == "skip":
                it["match"] = None
                return it
            t, conf = match_spotify({**it, "stem": q, "tags": {"title": "", "artist": ""}, "duration": None}, sp)
            if t:
                it["match"] = {"kind": "spotify", "conf": "manual", "spotify_id": t["id"], "title": t["title"], "artists": t["artists"]}
            else:
                c, conf = match_override(q, it)
                it["match"] = {"kind": "ytm", "conf": conf, **c} if c else None
            return it
        t, conf = match_spotify(it, sp)
        if t:
            it["match"] = {"kind": "spotify", "conf": conf, "spotify_id": t["id"], "title": t["title"], "artists": t["artists"],
                           "length_mismatch": t.get("length_mismatch", False)}
        else:
            c, conf = match_ytm(it)
            it["match"] = {"kind": "ytm", "conf": conf, **c} if c else None
        return it
    with ThreadPoolExecutor(workers) as ex:
        for i, it in enumerate(ex.map(one, items), 1):
            m = it["match"]
            log(f"{i}/{len(items)} [{(m or {}).get('conf','  ?  ')}] {it['rel']}  ->  "
                + (f"{', '.join(m['artists'])} - {m['title']}" if m else "UNIDENTIFIED"), flush=True)
    return items

def _accept_loose(ts, as_, dd):
    if as_ is not None and ts >= 88 and as_ >= 88 and (dd is None or dd <= 90):
        return "medium"
    if as_ is None and ts >= 95 and dd is not None and dd <= 2:
        return "medium"
    return None

def match_ytm_loose(it):
    """Second pass for misses: aggressive cleanup; allow length mismatch (video/live rips) when title AND artist agree."""
    strings = list(dict.fromkeys([loose_stem(it["rel"].rsplit("/", 1)[-1].rsplit(".", 1)[0])] + _local_strings(it)))
    c, conf = _pick(ytm_search, it, [x for x in strings if x], _accept_loose)
    if c is not None and it["duration"] and c.get("duration") and abs(c["duration"] - it["duration"]) > 10:
        c["length_mismatch"] = True
    return c, conf
