import json


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
