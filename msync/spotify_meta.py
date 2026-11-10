"""Resolve Spotify track IDs to basic metadata via the public track embed page (cached)."""
import json, re, sys, time, pathlib, requests

from .config import SPOTIFY as CACHE
UA = {"User-Agent": "Mozilla/5.0 (personal music library sync)"}

def track(tid: str) -> dict:
    f = CACHE / f"{tid}.json"
    if f.exists():
        return json.loads(f.read_text())
    for attempt in range(4):
        r = requests.get(f"https://open.spotify.com/embed/track/{tid}", headers=UA, timeout=30)
        if r.status_code == 200:
            break
        time.sleep(5 * (attempt + 1))
    r.raise_for_status()
    m = re.search(r'__NEXT_DATA__" type="application/json">(.*?)</script>', r.text)
    e = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    imgs = (e.get("visualIdentity") or {}).get("image") or []
    out = {
        "id": tid,
        "title": e["title"],
        "artists": [a["name"] for a in e.get("artists", [])],
        "year": (e.get("releaseDate") or {}).get("isoString", "")[:4],
        "duration": round(e.get("duration", 0) / 1000),
        "cover": max(imgs, key=lambda i: i.get("maxWidth", 0))["url"] if imgs else None,
    }
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    time.sleep(0.6)
    return out

def ids_from_text(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"(?:track[/:])?([A-Za-z0-9]{22})", text)))

def main(paths: list[str]) -> None:
    for path in paths:
        ids = ids_from_text(pathlib.Path(path).read_text())
        for i, tid in enumerate(ids, 1):
            try:
                t = track(tid)
                print(f"{i}/{len(ids)} {', '.join(t['artists'])} - {t['title']}", flush=True)
            except Exception as ex:
                print(f"{i}/{len(ids)} FAIL {tid}: {ex}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
