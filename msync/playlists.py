"""Write .m3u8 playlists with absolute phone paths (what Android's MediaProvider imports most reliably)."""
import collections
from .config import LIBRARY, PHONE_MUSIC
from .textnorm import safe_filename

PLAYLIST_DIR = "Playlists"

def _m3u(entries, catalog) -> str:
    lines = ["#EXTM3U"]
    for rec in entries:
        lines.append(f"#EXTINF:{rec.get('duration') or -1},{', '.join(rec['artists'])} - {rec['title']}")
        lines.append(f"{PHONE_MUSIC}/{rec['path']}")
    return "\n".join(lines) + "\n"

def write_all(catalog: dict, playlists: dict) -> list[str]:
    out_dir = LIBRARY / PLAYLIST_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.m3u8"):
        old.unlink()
    from .identify import spotify_tracks, spotify_aliases
    alias = spotify_aliases(spotify_tracks())
    by_sp = {r["spotify_id"]: r for r in catalog.values() if r.get("spotify_id") and r.get("path")}
    by_sp.update({a: by_sp[c] for a, c in alias.items() if c in by_sp})
    written = []
    # 1) Spotify mirrors, in Spotify order
    for name, ids in playlists.items():
        entries = [by_sp[i] for i in ids if i in by_sp]
        (out_dir / f"{safe_filename(name)}.m3u8").write_text(_m3u(entries, catalog), encoding="utf-8")
        written.append(f"{name} ({len(entries)}/{len(ids)})")
    # 2) one per Language / Genre group, plus one per language
    groups = collections.defaultdict(list)
    for r in catalog.values():
        if r.get("path"):
            groups[(r["language"], r["genre"])].append(r)
            groups[(r["language"], None)].append(r)
    for (lang, genre), recs in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        recs.sort(key=lambda r: (r["artists"][0].lower(), r.get("album") or "", r.get("track") or 0, r["title"].lower()))
        name = f"{lang} - {genre}" if genre else f"{lang} - All"
        (out_dir / f"{safe_filename(name)}.m3u8").write_text(_m3u(recs, catalog), encoding="utf-8")
        written.append(f"{name} ({len(recs)})")
    return written
