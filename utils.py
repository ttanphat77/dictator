import json
import re

import requests


def download_gdrive(share_url: str) -> tuple[bytes, str]:
    """Download a file from a Google Drive share link. Returns (bytes, filename)."""
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", share_url) or \
        re.search(r"[?&]id=([a-zA-Z0-9_-]+)", share_url)
    if not m:
        raise ValueError("Cannot extract file ID from Google Drive URL")

    file_id = m.group(1)
    dl_url = (
        f"https://drive.usercontent.google.com/download"
        f"?id={file_id}&export=download&authuser=0&confirm=t"
    )

    resp = requests.get(dl_url, timeout=300)
    resp.raise_for_status()

    if "text/html" in resp.headers.get("content-type", ""):
        raise RuntimeError(
            "Google Drive returned an HTML page. "
            "Make sure the file is shared as 'Anyone with the link'."
        )

    cd = resp.headers.get("content-disposition", "")
    name_match = re.search(r'filename\*?=(?:UTF-8\'\')?\"?([^\";\n]+)\"?', cd)
    filename = name_match.group(1).strip('"') if name_match else f"{file_id}.mp3"

    return resp.content, filename


def _fmt_srt(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt(sec: float) -> str:
    return _fmt_srt(sec).replace(",", ".")


def fmt_display(sec: float) -> str:
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m:02d}:{s:02d}"


def to_txt(segments: list[dict]) -> str:
    lines = [
        f"[{fmt_display(s['start'])} → {fmt_display(s['end'])}] {s['text'].strip()}"
        for s in segments
    ]
    return "\n".join(lines)


def to_srt(segments: list[dict]) -> str:
    blocks = []
    for i, s in enumerate(segments, 1):
        blocks.append(f"{i}\n{_fmt_srt(s['start'])} --> {_fmt_srt(s['end'])}\n{s['text'].strip()}")
    return "\n\n".join(blocks)


def to_vtt(segments: list[dict]) -> str:
    lines = ["WEBVTT", ""]
    for s in segments:
        lines.append(f"{_fmt_vtt(s['start'])} --> {_fmt_vtt(s['end'])}\n{s['text'].strip()}")
    return "\n\n".join(lines)


def to_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)
