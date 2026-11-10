"""msync — keep the phone's Music folder in sync with Spotify playlists (no Spotify API needed).

  python -m msync backup                 pull phone Music/ -> BACKUP (never touches the phone)
  python -m msync add NAME LINKS.txt     set a playlist from pasted Spotify track links (Ctrl+A, Ctrl+C in Spotify desktop)
  python -m msync identify               identify every backed-up phone file
  python -m msync artists                list artists that have no Language/Genre group yet
  python -m msync build                  enrich, download missing, write tagged library + playlists
  python -m msync push [--apply]         copy library to phone (dry run unless --apply)
  python -m msync cleanup [--apply]      remove old phone files that the new library replaces (only after push verified)
  python -m msync sync NAME LINKS.txt    add + build + push --apply in one go (for new songs later)
"""
import argparse, collections, json, pathlib, shutil, sys

from .config import BACKUP, LIBRARY, DATA, CATALOG, GROUPS, PLAYLISTS, PHONE_MUSIC, IDENTIFIED, REPORT, PUSHED
from . import adb, identify, build, playlists, spotify_meta, tagger
from .textnorm import norm


def jload(p, default):
    return json.loads(p.read_text()) if p.exists() else default

def jsave(p, v):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(v, ensure_ascii=False, indent=1))
    tmp.replace(p)

def backup_root() -> pathlib.Path:
    return BACKUP / "Music"

# ---------------------------------------------------------------- backup
def cmd_backup(a):
    remote = adb.list_files(PHONE_MUSIC)
    todo = [r for r, s in remote.items() if not r.startswith(".thumbnails/")
            and not ((backup_root() / r).exists() and (backup_root() / r).stat().st_size == s)]
    print(f"{len(remote)} files on phone, {len(todo)} new/changed to back up")
    for i, r in enumerate(todo, 1):
        adb.pull(f"{PHONE_MUSIC}/{r}", backup_root() / r)
        print(f"  {i}/{len(todo)} {r}")

# ---------------------------------------------------------------- playlists
def cmd_add(a):
    ids = spotify_meta.ids_from_text(pathlib.Path(a.links).read_text())
    pls = jload(PLAYLISTS, {})
    pls[a.name] = ids
    jsave(PLAYLISTS, pls)
    for i, tid in enumerate(ids, 1):
        spotify_meta.track(tid)
    print(f"{a.name}: {len(ids)} tracks")

# ---------------------------------------------------------------- identify
def cmd_identify(a):
    items = identify.scan(backup_root())
    prev = {x["rel"]: x for x in jload(IDENTIFIED, [])}
    todo = [it for it in items if a.redo or it["rel"] not in prev or prev[it["rel"]].get("size") != it["size"]]
    keep = [prev[it["rel"]] for it in items if it not in todo]
    print(f"{len(items)} audio files, {len(todo)} to identify")
    done = identify.identify_all(todo)
    allx = keep + done
    ov = identify.load_overrides()
    misses = [x for x in allx if not x.get("match") and ov.get(x["rel"]) != "skip"]
    print(f"second pass over {len(misses)} unidentified files")
    for x in misses:
        c, conf = identify.match_ytm_loose(x)
        if c:
            x["match"] = {"kind": "ytm", "conf": conf, **c}
            print(f"  [{conf}] {x['rel']}  ->  {', '.join(c['artists'])} - {c['title']}" + ("  (length differs)" if c.get("length_mismatch") else ""))
    jsave(IDENTIFIED, allx)
    done = allx
    c = collections.Counter(((x.get("match") or {}).get("kind"), (x.get("match") or {}).get("conf")) for x in done)
    print(dict(c))

# ---------------------------------------------------------------- groups
def _records(items, sp):
    """Turn identified phone files + Spotify playlist tracks into unique song records."""
    pls = jload(PLAYLISTS, {})
    alias = identify.spotify_aliases(sp)
    in_pl = {alias.get(tid, tid) for ids in pls.values() for tid in ids}
    recs = {}
    for it in items:
        m = it.get("match")
        if m and m["kind"] == "spotify":
            t = sp[alias.get(m["spotify_id"], m["spotify_id"])]
            rec = {"title": t["title"], "artists": t["artists"], "year": t["year"], "duration": t["duration"],
                   "spotify_id": t["id"], "spotify_cover": t["cover"]}
        elif m:
            rec = {k: m.get(k) for k in ("title", "artists", "album", "albumId", "videoId", "year")}
            rec["duration"] = m.get("duration") or it["duration"]
            rec["ytm_thumb"] = m.get("thumb")
        else:
            continue
        # phone copy is a video/live rip of different length -> synced lyrics would drift
        if it["duration"] and rec.get("duration") and abs(rec["duration"] - it["duration"]) > 10:
            rec["length_mismatch"] = True
        rec["conf"] = m["conf"]
        rec["artists"] = build.canon_artists(rec["artists"])
        rec["title"] = build.html.unescape(rec["title"] or "")
        k = build.song_key(rec)
        bitrate = it["size"] / max(it["duration"] or 1, 1)
        if k not in recs or bitrate > recs[k]["_bitrate"]:
            dupes = recs.get(k, {}).get("dupes", []) + ([recs[k]["source"]] if k in recs else [])
            recs[k] = {**rec, "source": it["rel"], "_bitrate": bitrate, "folder": it["folder"], "dupes": dupes}
        else:
            recs[k]["dupes"].append(it["rel"])
    for tid in in_pl:
        k = "sp:" + tid
        if k not in recs:
            t = sp[tid]
            recs[k] = {"title": t["title"], "artists": build.canon_artists(t["artists"]), "year": t["year"], "duration": t["duration"],
                       "spotify_id": tid, "spotify_cover": t["cover"], "source": None, "conf": "download", "dupes": [], "folder": ""}
    return recs

def cmd_artists(a):
    items = jload(IDENTIFIED, [])
    sp = identify.spotify_tracks()
    groups = jload(GROUPS, {})
    recs = _records(items, sp)
    pls = jload(PLAYLISTS, {})
    pl_of = {tid: name for name, ids in pls.items() for tid in ids}
    info = collections.defaultdict(lambda: {"songs": [], "folders": collections.Counter(), "playlists": collections.Counter()})
    for r in recs.values():
        a0 = r["artists"][0]
        if any(norm(x) in groups for x in r["artists"]):
            continue
        info[a0]["songs"].append(r["title"])
        info[a0]["folders"][r.get("folder") or "(root)"] += 1
        if r.get("spotify_id") in pl_of:
            info[a0]["playlists"][pl_of[r["spotify_id"]]] += 1
    out = [{"artist": k, "n": len(v["songs"]), "songs": v["songs"][:4], "folders": dict(v["folders"]), "playlists": dict(v["playlists"])}
           for k, v in sorted(info.items(), key=lambda kv: -len(kv[1]["songs"]))]
    jsave(DATA / "ungrouped_artists.json", out)
    print(f"{len(out)} ungrouped artists -> data/ungrouped_artists.json")

# ---------------------------------------------------------------- build
def cmd_build(a):
    items = jload(IDENTIFIED, [])
    sp = identify.spotify_tracks()
    groups = {k: v for k, v in jload(GROUPS, {}).items() if not k.startswith("_")}
    catalog = jload(CATALOG, {})
    recs = _records(items, sp)
    if a.limit:
        recs = dict(list(recs.items())[: a.limit])
    used_paths = {}
    failed = []
    # network-heavy work first, in parallel (all cached, so re-runs are instant)
    from concurrent.futures import ThreadPoolExecutor
    from .meta import fetch_image, lyrics as get_lyrics
    def prefetch(rec):
        try:
            rec["language"], rec["genre"] = build.group_of(rec, groups)
            build.enrich(rec)
            done = catalog.get(build.song_key(rec), {})
            if done.get("built_v") == 2 and done.get("path") and (LIBRARY / done["path"]).exists() and not a.force:
                return   # already tagged in the library; nothing to fetch
            fetch_image([u for u in rec.get("cover_urls", []) if u])
            get_lyrics(rec["artists"][0], rec["title"], rec.get("album"), rec.get("duration"))
        except Exception as ex:
            print("  prefetch error:", rec.get("title"), ex)
    print(f"fetching metadata/covers/lyrics for {len(recs)} songs ...", flush=True)
    with ThreadPoolExecutor(8) as ex:
        for n, _ in enumerate(ex.map(prefetch, recs.values()), 1):
            if n % 100 == 0:
                print(f"  {n}/{len(recs)}", flush=True)
    to_dl = [r["videoId"] for r in recs.values() if not r["source"] and r.get("videoId")]
    print(f"downloading {len(to_dl)} songs ...", flush=True)
    with ThreadPoolExecutor(4) as ex:
        for n, ok in enumerate(ex.map(build.download, to_dl), 1):
            if n % 10 == 0:
                print(f"  {n}/{len(to_dl)}", flush=True)
    for i, (k, rec) in enumerate(recs.items(), 1):
        old = catalog.get(k, {})
        path = build.target_path(rec)
        n = 2
        while str(path) in used_paths and used_paths[str(path)] != k:
            path = path.with_name(f"{path.stem} ({n}).mp3"); n += 1
        used_paths[str(path)] = k
        rec["path"] = path.relative_to(LIBRARY).as_posix()
        same = (old.get("path") == rec["path"] and old.get("source") == rec["source"] and path.exists()
                and old.get("built_v") == 2 and not a.force)
        label = f"{i}/{len(recs)} {rec['path']}"
        if same:
            catalog[k] = {**old, **{x: rec[x] for x in ("language", "genre")}}
            continue
        try:
            src = backup_root() / rec["source"] if rec["source"] else None
            if src is not None and not src.exists():
                # backup removed: the library copy is now the original
                lib_old = LIBRARY / old["path"] if old.get("path") else None
                if lib_old is None or not lib_old.exists():
                    print(label, "  SKIP (source gone)"); catalog[k] = old or catalog.get(k, {}); continue
                if lib_old != path:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(lib_old, path)
                    lib_old.with_suffix(".lrc").unlink(missing_ok=True)
                old = {**old, "path": rec["path"]}
            elif src is not None:
                build.to_mp3(src, path)
            if rec["source"]:
                pass
            elif not rec.get("videoId") or not build.download(rec["videoId"], path):
                failed.append(f"{', '.join(rec['artists'])} - {rec['title']}")
                print(label, "  DOWNLOAD FAILED / no confident match")
                rec["path"] = None
                catalog[k] = {x: v for x, v in rec.items() if not x.startswith("_")}
                continue
            build.finalize(rec, path)
            rec["built_v"] = 2
            if old.get("path") and old["path"] != rec["path"]:
                (LIBRARY / old["path"]).unlink(missing_ok=True)
                (LIBRARY / old["path"]).with_suffix(".lrc").unlink(missing_ok=True)
            print(label, "| cover" if rec["has_cover"] else "| NO COVER", "|", rec["lyrics"] or "no lyrics",
                  "| downloaded" if not rec["source"] else "")
        except Exception as ex:
            print(label, "ERROR", ex)
            failed.append(f"{rec['path']}: {ex}")
            continue
        catalog[k] = {x: v for x, v in rec.items() if not x.startswith("_")}
        if i % 25 == 0:
            jsave(CATALOG, catalog)
    # unidentified phone files: keep audio untouched, park them for review
    for it in items:
        if not it.get("match"):
            src = backup_root() / it["rel"]
            dst = LIBRARY / "Needs review" / pathlib.Path(it["rel"]).name
            if dst.suffix.lower() != ".mp3":
                dst = dst.with_suffix(".mp3")
            if not dst.exists():
                if not src.exists():
                    continue
                build.to_mp3(src, dst)
            k = "unid:" + it["rel"]
            catalog[k] = {"title": it["stem"], "artists": [it["tags"]["artist"] or "Unknown"], "language": "Needs review",
                          "genre": "", "path": dst.relative_to(LIBRARY).as_posix(), "source": it["rel"], "conf": None,
                          "duration": it["duration"], "dupes": []}
    jsave(CATALOG, catalog)
    written = playlists.write_all({k: v for k, v in catalog.items() if v.get("language") != "Needs review"}, jload(PLAYLISTS, {}))
    write_report(catalog, failed, written)
    print("\nplaylists:", *written, sep="\n  ")
    print(f"\nreport -> {REPORT}")

def write_report(catalog, failed, written):
    recs = [r for r in catalog.values() if r.get("path")]
    c = collections.Counter
    lines = ["# Music library report", "",
             f"- songs in library: **{len(recs)}**",
             f"- from phone: {sum(1 for r in recs if r.get('source'))}, downloaded: {sum(1 for r in recs if not r.get('source'))}",
             f"- with cover: {sum(1 for r in recs if r.get('has_cover'))}",
             f"- lyrics: {dict(c(r.get('lyrics') or 'none' for r in recs if r.get('language') != 'Needs review'))}",
             f"- duplicates on phone collapsed: {sum(len(r.get('dupes') or []) for r in recs)}", "",
             "## Groups", ""]
    for (l, g), n in sorted(c((r["language"], r["genre"]) for r in recs).items()):
        lines.append(f"- {l} / {g}: {n}")
    lines += ["", "## Playlists", ""] + [f"- {w}" for w in written]
    lines += ["", "## Medium-confidence matches (check these)", ""]
    lines += [f"- `{r['source']}` → {', '.join(r['artists'])} - {r['title']}" for r in recs if r.get("conf") == "medium"]
    lines += ["", "## Needs review (could not identify — audio kept untouched)", ""]
    lines += [f"- `{r['source']}`" for r in recs if r.get("language") == "Needs review"]
    lines += ["", "## Failed downloads", ""] + [f"- {f}" for f in failed]
    lines += ["", "## Duplicates (kept the higher-bitrate copy)", ""]
    lines += [f"- {r['path']} ← also: {', '.join(r['dupes'])}" for r in recs if r.get("dupes")]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

# ---------------------------------------------------------------- push / cleanup
def _local_files():
    out = {}
    for p in LIBRARY.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".mp3", ".lrc", ".m3u8"):
            out[p.relative_to(LIBRARY).as_posix()] = p.stat().st_size
    return out

def cmd_push(a):
    if adb.device_state() != "device":
        sys.exit(f"phone not ready: {adb.device_state()} (plug in + allow USB debugging)")
    local = _local_files()
    remote = adb.list_files(PHONE_MUSIC)
    todo = {r: s for r, s in local.items() if remote.get(r) != s}
    print(f"library: {len(local)} files | already on phone: {len(local) - len(todo)} | to copy: {len(todo)} "
          f"({sum(todo.values()) / 1e9:.2f} GB)")
    if not a.apply:
        for r in list(todo)[:20]:
            print("  +", r)
        print("dry run — add --apply to copy. Nothing is deleted by push.")
        return
    for i, r in enumerate(sorted(todo, key=lambda x: x.endswith(".m3u8")), 1):   # playlists last
        adb.push(LIBRARY / r, f"{PHONE_MUSIC}/{r}")
        if i % 50 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}")
    remote = adb.list_files(PHONE_MUSIC)
    bad = [r for r, s in local.items() if remote.get(r) != s]
    jsave(PUSHED, {r: s for r, s in local.items() if remote.get(r) == s})
    adb.media_scan(PHONE_MUSIC)
    print("verified on phone:", len(local) - len(bad), "| mismatched:", len(bad), *bad[:10], sep="\n  ")

def cmd_cleanup(a):
    local = _local_files()
    remote = adb.list_files(PHONE_MUSIC)
    missing = [r for r, s in local.items() if remote.get(r) != s]
    if missing:
        sys.exit(f"refusing: {len(missing)} library files are not on the phone yet (run push --apply first)")
    old = sorted(r for r in remote if r not in local and not r.startswith(".thumbnails/"))
    unbacked = [r for r in old if not (backup_root() / r).exists()]
    print(f"{len(old)} old phone files are superseded by the new library; {len(unbacked)} of them are NOT in the backup")
    for r in unbacked:
        print("  keep (not backed up):", r)
    old = [r for r in old if r not in unbacked]
    if not a.apply:
        for r in old[:30]:
            print("  -", r)
        print("dry run — add --apply to delete these from the phone (all are in", BACKUP, ")")
        return
    lst = DATA / "cleanup_list.txt"
    lst.write_text("\n".join(f"{PHONE_MUSIC}/{r}" for r in old) + "\n", encoding="utf-8")
    adb.run("push", adb.winpath(lst), "/data/local/tmp/msync_cleanup.txt")
    adb.shell("tr '\\n' '\\0' < /data/local/tmp/msync_cleanup.txt | xargs -0 rm -f; rm -f /data/local/tmp/msync_cleanup.txt")
    adb.shell(f"find {adb.q(PHONE_MUSIC)} -mindepth 1 -type d -empty -not -path '*/.thumbnails*' -delete", check=False)
    adb.media_scan(PHONE_MUSIC)
    print(f"removed {len(old)} superseded files")

def cmd_sync(a):
    cmd_add(a)
    cmd_build(argparse.Namespace(limit=0, force=False))
    cmd_artists(a)
    cmd_push(argparse.Namespace(apply=True))

def main():
    ap = argparse.ArgumentParser(prog="msync", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup").set_defaults(fn=cmd_backup)
    p = sub.add_parser("add"); p.add_argument("name"); p.add_argument("links"); p.set_defaults(fn=cmd_add)
    p = sub.add_parser("identify"); p.add_argument("--redo", action="store_true"); p.set_defaults(fn=cmd_identify)
    sub.add_parser("artists").set_defaults(fn=cmd_artists)
    p = sub.add_parser("build"); p.add_argument("--limit", type=int, default=0); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("push"); p.add_argument("--apply", action="store_true"); p.set_defaults(fn=cmd_push)
    p = sub.add_parser("cleanup"); p.add_argument("--apply", action="store_true"); p.set_defaults(fn=cmd_cleanup)
    p = sub.add_parser("sync"); p.add_argument("name"); p.add_argument("links"); p.set_defaults(fn=cmd_sync)
    a = ap.parse_args()
    a.fn(a)

if __name__ == "__main__":
    main()
