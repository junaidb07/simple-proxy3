#!/usr/bin/env python3
"""Save transcripts for video URLs (YouTube, TikTok, Instagram, etc.).

Order of attempts per URL:
  1. Existing captions via yt-dlp (fast, works for most YouTube videos).
  2. Download audio (yt-dlp, or a cobalt instance if --cobalt is set),
     then transcribe locally with faster-whisper.

Usage:
  python transcripts.py URL [URL ...]
  python transcripts.py -f urls.txt -o out --model small
  python transcripts.py URL --cobalt http://localhost:9000

Install:
  pip install yt-dlp faster-whisper requests
  (ffmpeg must be on PATH)
"""
import argparse
import re
import sys
import tempfile
from pathlib import Path

import yt_dlp


def clean_vtt(text: str) -> str:
    """Strip WebVTT timing/markup and collapse repeated caption lines."""
    lines, last = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if (not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE"))
                or "-->" in line or re.fullmatch(r"\d+", line)):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line and line != last:
            lines.append(line)
            last = line
    return " ".join(lines)


def get_captions(url: str, tmp: Path):
    opts = {
        "skip_download": True, "quiet": True, "no_warnings": True,
        "writesubtitles": True, "writeautomaticsub": True,
        "subtitleslangs": ["en.*", "en"], "subtitlesformat": "vtt",
        "outtmpl": str(tmp / "%(id)s.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    vtts = sorted(tmp.glob("*.vtt"))
    text = clean_vtt(vtts[0].read_text(encoding="utf-8", errors="ignore")) if vtts else ""
    return info, text


def download_audio(url: str, tmp: Path, cobalt: str | None) -> Path:
    if cobalt:
        import requests
        r = requests.post(
            cobalt.rstrip("/") + "/",
            json={"url": url, "downloadMode": "audio"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") not in ("tunnel", "redirect") or "url" not in data:
            raise RuntimeError(f"cobalt refused: {data}")
        out = tmp / "audio.bin"
        with requests.get(data["url"], stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(out, "wb") as f:
                for chunk in resp.iter_content(1 << 16):
                    f.write(chunk)
        return out
    opts = {
        "format": "bestaudio/best", "quiet": True, "no_warnings": True,
        "outtmpl": str(tmp / "audio.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return next(tmp.glob("audio.*"))


def transcribe(audio: Path, model_name: str) -> str:
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, compute_type="int8")
    segments, _ = model.transcribe(str(audio))
    return " ".join(s.text.strip() for s in segments)


def process(url: str, outdir: Path, model: str, cobalt: str | None) -> Path:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        info, text, source = {}, "", "captions"
        try:
            info, text = get_captions(url, tmp)
        except Exception as e:
            print(f"  captions failed: {e}", file=sys.stderr)
        if not text:
            source = "whisper"
            audio = download_audio(url, tmp, cobalt)
            text = transcribe(audio, model)
        vid = re.sub(r"[^\w.-]", "_", str(info.get("id") or url))[:80]
        out = outdir / f"{vid}.txt"
        header = f"URL: {url}\nTitle: {info.get('title', '?')}\nSource: {source}\n\n"
        out.write_text(header + text + "\n", encoding="utf-8")
        return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="*")
    ap.add_argument("-f", "--file", help="text file with one URL per line")
    ap.add_argument("-o", "--out", default="transcripts_out")
    ap.add_argument("--model", default="small", help="faster-whisper model size")
    ap.add_argument("--cobalt", help="base URL of a cobalt instance for audio download")
    args = ap.parse_args()

    urls = list(args.urls)
    if args.file:
        urls += [l.strip() for l in Path(args.file).read_text().splitlines() if l.strip()]
    if not urls:
        ap.error("no URLs given")

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for url in urls:
        print(f"[+] {url}")
        try:
            print(f"    saved {process(url, outdir, args.model, args.cobalt)}")
        except Exception as e:
            failed += 1
            print(f"    FAILED: {e}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
