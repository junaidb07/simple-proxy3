#!/usr/bin/env python3
"""iPad (a-Shell) fetcher: pulls a link list from GitHub, grabs captions with
yt-dlp from the iPad's own connection, and pushes the results back to GitHub.

Flow:
  1. Read tools/ipad/links.txt from the repo branch (one URL per line).
  2. Skip links already done (tools/ipad/out/<id>.txt or .m4a exists).
  3. Try captions. If none, download audio (m4a) instead, for Whisper later.
  4. Upload each result to tools/ipad/out/ on the same branch.

One-time setup in a-Shell:
  pip install yt-dlp
  echo YOUR_GITHUB_TOKEN > ~/Documents/.gh_token
Run:
  python ~/Documents/fetch.py
"""
import base64, json, os, re, sys, tempfile, urllib.request, urllib.error
from pathlib import Path

REPO = "junaidb07/simple-proxy3"
BRANCH = "claude/h-4vq21d"
DIR = "tools/ipad"
MAX_AUDIO_MB = 40  # GitHub API uploads get unreliable above this
TOKEN_FILE = Path.home() / "Documents" / ".gh_token"


def gh(method, path, body=None):
    token = TOKEN_FILE.read_text().strip()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}"
        + (f"?ref={BRANCH}" if method == "GET" else ""),
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "ipad-fetch"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"GitHub {method} {path}: {e.code} {e.read()[:200]}")


def upload(path, data: bytes, msg):
    gh("PUT", path, {"message": msg, "branch": BRANCH,
                     "content": base64.b64encode(data).decode()})


def clean_vtt(text):
    out, last = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if (not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE"))
                or "-->" in line or re.fullmatch(r"\d+", line)):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line and line != last:
            out.append(line)
            last = line
    return " ".join(out)


def main():
    try:
        import yt_dlp
    except ImportError:
        sys.exit("yt-dlp missing: run  pip install yt-dlp")
    if not TOKEN_FILE.exists():
        sys.exit(f"token missing: put your GitHub token in {TOKEN_FILE}")

    meta = gh("GET", f"{DIR}/links.txt")
    if not meta:
        sys.exit("no links.txt in repo yet")
    links = [l.strip() for l in base64.b64decode(meta["content"]).decode().splitlines()
             if l.strip() and not l.startswith("#")]
    done = {f["name"].rsplit(".", 1)[0] for f in (gh("GET", f"{DIR}/out") or [])}
    print(f"{len(links)} links, {len(done)} already done")

    for url in links:
        try:
            with tempfile.TemporaryDirectory() as t:
                base = {"quiet": True, "no_warnings": True, "outtmpl": f"{t}/%(id)s.%(ext)s"}
                with yt_dlp.YoutubeDL({**base, "skip_download": True}) as y:
                    info = y.extract_info(url, download=False)
                vid = re.sub(r"[^\w.-]", "_", info["id"])[:80]
                if vid in done:
                    continue
                print(f"[+] {info.get('title', url)}")
                with yt_dlp.YoutubeDL({**base, "skip_download": True,
                                       "writesubtitles": True, "writeautomaticsub": True,
                                       "subtitleslangs": ["en.*", "en", "ar"],
                                       "subtitlesformat": "vtt"}) as y:
                    y.download([url])
                vtts = sorted(Path(t).glob("*.vtt"))
                header = f"URL: {url}\nTitle: {info.get('title')}\nUploader: {info.get('uploader')}\n"
                if vtts:
                    text = clean_vtt(vtts[0].read_text(encoding="utf-8", errors="ignore"))
                    upload(f"{DIR}/out/{vid}.txt", (header + "Source: captions\n\n" + text).encode(),
                           f"transcript: {vid}")
                    print("    captions uploaded")
                else:
                    with yt_dlp.YoutubeDL({**base, "format": "bestaudio[ext=m4a]/bestaudio"}) as y:
                        y.download([url])
                    audio = next(p for p in Path(t).iterdir() if p.suffix != ".vtt")
                    if audio.stat().st_size > MAX_AUDIO_MB * 1e6:
                        print("    no captions, audio too big, skipped"); continue
                    upload(f"{DIR}/out/{vid}{audio.suffix}", audio.read_bytes(), f"audio: {vid}")
                    upload(f"{DIR}/out/{vid}.meta", header.encode(), f"meta: {vid}")
                    print("    no captions, audio uploaded")
        except Exception as e:
            print(f"    FAILED {url}: {e}")


if __name__ == "__main__":
    main()
