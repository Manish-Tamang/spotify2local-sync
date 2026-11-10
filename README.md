# spotify2local-sync

Mirror your Spotify playlists to a **local, fully tagged MP3 library** and keep an **Android phone** in sync with it —
no Spotify API, no developer app, no Premium.

Every song ends up with its real title / artist / album / year / track number, the official cover art, and lyrics
(time-synced `.lrc` next to the file, plus embedded), filed into `Language/Genre` folders with `.m3u8` playlists.

```
Music/
├── English/
│   ├── Classic Rock/        Guns N' Roses - Don't Cry.mp3  (+ .lrc)
│   ├── Grunge & Alt Rock/
│   └── Pop/ …
├── Nepali/
│   ├── Grunge & Alt Rock/
│   ├── Indie & Acoustic/ …
├── Hindi/ …
├── Needs review/            files that couldn't be identified — audio left untouched
└── Playlists/               one per Spotify playlist (Spotify order) + one per Language/Genre
```

## How it works

| step | source |
|---|---|
| playlist contents | track links you paste from the Spotify desktop app (`Ctrl+A`, `Ctrl+C` inside a playlist) |
| track title / artist / length / cover | Spotify's public track pages |
| identifying files you already have | filename + tags + duration, fuzzy-matched against your playlists, then YouTube Music |
| album, track number, audio for missing songs | YouTube Music (`ytmusicapi` + `yt-dlp`) |
| lyrics | [LRCLIB](https://lrclib.net) |
| phone transfer | `adb` over USB |

Nothing is ever deleted from the phone by a sync. Removing old files is a separate `cleanup` command that refuses to run
unless every library file is verified on the phone and every file it would remove is in the backup.

## Setup (Windows + WSL)

1. Install [uv](https://docs.astral.sh/uv/), `ffmpeg` and Node.js (yt-dlp uses it for YouTube) in WSL, and
   Android platform-tools on Windows (`winget install Google.PlatformTools`).
2. On the phone: **Settings → About phone → tap the build/software version 7×**, then
   **Developer options → USB debugging**. Plug in and tap **Allow**.
3. Install:

```bash
git clone https://github.com/Manish-Tamang/spotify2local-sync.git && cd spotify2local-sync && uv venv && uv pip install -e .
```

4. Optional: `cp .env.example .env` to change the library folder (default: your Windows `Music` folder), backup folder or adb path.

## First run — organize the music already on your phone

```bash
.venv/bin/msync backup                         # copy phone Music/ to the backup folder (read-only on the phone)
.venv/bin/msync add "Road trip" links.txt      # once per playlist: paste its track links into links.txt
.venv/bin/msync identify                       # figure out what every phone file really is
.venv/bin/msync artists                        # artists without a Language/Genre -> add them to config/artist_groups.json
.venv/bin/msync build                          # tag, add covers + lyrics, download missing songs, write library + playlists
.venv/bin/msync push --apply                   # copy the library to the phone and verify every file
.venv/bin/msync cleanup                        # dry run: what old phone files would be removed
.venv/bin/msync cleanup --apply                # actually swap
```

Check `data/report.md` after `build`: it lists medium-confidence matches, unidentified files, failed downloads and merged duplicates.

## Everyday use — new songs in a playlist

```bash
.venv/bin/msync-web
```

Open <http://127.0.0.1:8765>, choose the playlist, paste its links, press **Sync to phone**.
Only new songs are downloaded and tagged; the laptop library and the phone are updated together.

Terminal equivalent: `.venv/bin/msync sync "Road trip" links.txt`

## Configuration

| file | purpose |
|---|---|
| `config/artist_groups.json` | normalized artist name → `["Language", "Genre"]` — decides the folder |
| `config/artist_aliases.json` | fix artist spellings coming from YouTube Music (`"1974 Ad": "1974 AD"`) |
| `config/overrides.json` | force what a phone file is: `"Grunge/3AM - Matchbox Twenty.mp3": "Matchbox Twenty - 3AM"`, or `"skip"` |
| `.env` | machine paths (see `.env.example`) |

`data/` holds local state (catalog, caches, report) and is not committed.

## Project layout

```
msync/
  cli.py           commands: backup, add, identify, artists, build, push, cleanup, sync
  web.py, ui/      local web UI
  spotify_meta.py  Spotify track ids -> title / artists / length / cover
  identify.py      match phone files to Spotify tracks / YouTube Music songs
  meta.py          YouTube Music search + albums, LRCLIB lyrics, cover download
  build.py         enrich, download, place files in Language/Genre folders
  tagger.py        ID3v2.3 tags, embedded cover + lyrics, .lrc sidecar
  playlists.py     .m3u8 playlists
  adb.py           adb wrapper (list / pull / push / media scan)
  textnorm.py      filename cleanup and fuzzy matching
  config.py        paths and settings
config/            user-editable mappings
```

## Notes

- Built for personal use with music you already stream. Respect the terms of the services involved and your local law.
- Spotify's Web API now requires the app owner to have Premium (Feb 2026), which is why this tool reads only public track pages and links you paste.
