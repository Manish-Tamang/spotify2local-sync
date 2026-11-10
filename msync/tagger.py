"""Write clean ID3v2.3 tags (best compatibility with Android/vivo players) + .lrc sidecar."""
import pathlib
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TPE2, TALB, TRCK, TDRC, TCON, APIC, USLT, TXXX, COMM
from mutagen.mp3 import MP3

def duration(path) -> int | None:
    try:
        return round(MP3(path).info.length)
    except Exception:
        return None

def read_basic(path) -> dict:
    try:
        t = ID3(path)
        g = lambda k: str(t[k].text[0]) if k in t and t[k].text else ""
        return {"title": g("TIT2"), "artist": g("TPE1"), "album": g("TALB")}
    except Exception:
        return {"title": "", "artist": "", "album": ""}

def write(path, rec: dict, cover: bytes | None, lyr: dict | None) -> None:
    path = pathlib.Path(path)
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delete(path)              # wipe junk (site watermarks, old art, stray comments)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=rec["title"]))
    tags.add(TPE1(encoding=3, text="; ".join(rec["artists"])))
    tags.add(TPE2(encoding=3, text=rec.get("album_artist") or rec["artists"][0]))
    if rec.get("album"): tags.add(TALB(encoding=3, text=rec["album"]))
    if rec.get("track"): tags.add(TRCK(encoding=3, text=str(rec["track"])))
    if rec.get("year"): tags.add(TDRC(encoding=3, text=str(rec["year"])))
    if rec.get("genre"): tags.add(TCON(encoding=3, text=rec["genre"]))
    if rec.get("spotify_id"): tags.add(TXXX(encoding=3, desc="SPOTIFY_TRACK_ID", text=rec["spotify_id"]))
    if rec.get("videoId"): tags.add(TXXX(encoding=3, desc="YTM_VIDEO_ID", text=rec["videoId"]))
    if cover:
        mime = "image/png" if cover[:4] == b"\x89PNG" else "image/jpeg"
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
    if lyr:
        text = lyr.get("synced") or lyr.get("plain")
        tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
    tags.save(path, v2_version=3)
    lrc = path.with_suffix(".lrc")
    if lyr and (lyr.get("synced") or lyr.get("plain")):
        head = f"[ar:{', '.join(rec['artists'])}]\n[ti:{rec['title']}]\n" + (f"[al:{rec['album']}]\n" if rec.get("album") else "")
        lrc.write_text(head + (lyr.get("synced") or lyr.get("plain")) + "\n", encoding="utf-8")
