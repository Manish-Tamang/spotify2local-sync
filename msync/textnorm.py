"""Filename/title cleanup and fuzzy matching helpers."""
import re, unicodedata
from rapidfuzz import fuzz

JUNK = [
    r"spotidown(?:loader)?\.\w+\s*-\s*", r"\bspotidown\w*\b",
    r"[\(\[][^\)\]]*(official|lyric|lyrics|audio|video|visuali[sz]er|hd|hq|4k|m/?v|full song|with lyrics)[^\)\]]*[\)\]]",
    r"\b(official\s+)?(music\s+)?(lyric(al)?\s+)?video\b", r"\bwith lyrics\b", r"\blyrics?\b",
    r"\bofficial\b", r"\bnepali song\b", r"\bnew nepali song\b", r"[\(\[]\s*(19|20)\d\d\s*[\)\]]",
    r"\(\s*\)", r"\[\s*\]",
]
FEAT = r"\s*[\(\[]?\s*(feat\.?|ft\.?|featuring)\s+[^\)\]]*[\)\]]?"

def clean_stem(stem: str) -> str:
    s = re.sub(r"\.(mp4|mp3|m4a|webm)$", "", stem, flags=re.I)
    s = re.sub(r"\s*\(\d\)$", "", s)
    m = re.match(r"^(.+?)\s+by\s+(.+)$", s, flags=re.I)
    if m and " - " not in s:
        s = f"{m.group(2)} - {m.group(1)}"
    for p in JUNK:
        s = re.sub(p, " ", s, flags=re.I)
    s = re.sub(r"\s*\.\.\.?$", "", s)
    s = re.sub(r"[_]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -_.")
    return s

VARIANT = [r"\b(saal session|tuborg|arbitrary|barahsinghe|retake)\b.*$", r"\b(live|session|sessions|studio recording|raw|acoustic|unplugged|remake|demo|special version|extended|extented)\b.*$",
           r"\b(saal session|tuborg|arbitrary|barahsinghe|retake)\b.*$", r"#", r"\bprod\.?\s.*$", r"\b(ft|feat)\.?\s.*$"]

def loose_stem(stem: str) -> str:
    """More aggressive cleanup for a second identification pass."""
    s = clean_stem(stem)
    for p in VARIANT:
        s = re.sub(p, " ", s, flags=re.I)
    s = re.sub(r"[\(\)\[\]_'\"]", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip(" -.")

def core_title(t: str) -> str:
    """'1979 - Remastered 2012' -> '1979'; drop feat. and version suffixes for matching."""
    t = re.sub(FEAT, "", t, flags=re.I)
    t = re.sub(r"\s+-\s+(\d{4}\s+)?(remaster(ed)?|live|radio edit|single version|mono|stereo|version|edit|from|performance|demo|original|raw|acoustic|special|alternate|nepali version).*$", "", t, flags=re.I)
    t = re.sub(r"\s*[\(\[](from|original soundtrack|ost)\b[^\)\]]*[\)\]]", "", t, flags=re.I)
    t = re.sub(r"[\(\[](\d{4}\s+)?(remaster(ed)?|live|radio edit|single version|mono|stereo|original)[^\)\]]*[\)\]]", "", t, flags=re.I)
    return t.strip()

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

def sq(s: str) -> str:
    """Space-less form: Nepali transliterations vary in spacing ('timi lai' vs 'timilai')."""
    return norm(s).replace(" ", "")

def title_score(local: str, title: str) -> float:
    a, b = norm(local), norm(core_title(title))
    if not b:
        return 0
    return max(fuzz.partial_ratio(b, a) if len(b) >= 4 else fuzz.ratio(b, a), fuzz.partial_ratio(sq(core_title(title)), sq(local)) if len(b) >= 4 else 0)

def artist_score(local: str, artists: list[str]) -> float:
    a = norm(local)
    best = 0
    for ar in artists:
        n = norm(re.sub(r"^the\s+", "", ar, flags=re.I))
        if n:
            best = max(best, fuzz.partial_ratio(n, a), fuzz.partial_ratio(sq(n), sq(local)))
    return best

def safe_filename(s: str, maxlen: int = 150) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s).strip().rstrip(".")
    return s[:maxlen].strip()


def no_parens(s: str) -> str:
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", s or "").strip() or s

def _ratio(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b), fuzz.ratio(a.replace(" ", ""), b.replace(" ", "")))

def split_artists(s: str) -> list[str]:
    parts = re.split(r",|&|\band\b|\bx\b|\bfeat\.?|\bft\.?|;|/", s, flags=re.I)
    return [p.strip() for p in parts if p.strip()]

def pair_score(local: str, cand_title: str, cand_artists: list[str]) -> tuple[float, float | None]:
    """Score a local 'A - B' string against a candidate. Tries every artist/title split of the local string.
    Returns (title_score, artist_score or None when the local string has no artist part)."""
    ct = core_title(cand_title)
    cand_arts = [a for ca in cand_artists for a in [ca, *split_artists(ca)]]
    parts = [p.strip() for p in re.split(r"\s+-\s+|\s+-(?=\S)|(?<=\S)-\s+", local) if p.strip()]
    best = (max(_ratio(core_title(local), ct), _ratio(no_parens(local), no_parens(cand_title))), None)
    if len(parts) < 2 or not cand_arts:
        return best
    best_sum = -1
    for i in range(len(parts)):
        art = parts[i]
        title = " - ".join(parts[:i] + parts[i + 1:])
        ts = max(_ratio(core_title(title), ct), _ratio(title, cand_title), _ratio(no_parens(title), no_parens(cand_title)))
        la = split_artists(art) or [art]
        as_ = max(max(_ratio(x, a), _ratio(re.sub(r"^the\s+", "", x, flags=re.I), re.sub(r"^the\s+", "", a, flags=re.I)))
                  for x in la for a in cand_arts)
        if ts + as_ > best_sum:
            best_sum, best = ts + as_, (ts, as_)
    return best
