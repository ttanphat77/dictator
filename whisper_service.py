import io
import os
import re
import subprocess
import tempfile
import time

os.environ.pop("LD_LIBRARY_PATH", None)

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

MAX_CHUNK_BYTES = 24 * 1024 * 1024  # 24 MB — OpenAI hard limit

TARGET_CHUNK_MS = 10 * 60 * 1000
SILENCE_SEARCH_MS = 20_000
MIN_SILENCE_LEN_MS = 200


def _get_client() -> OpenAI:
    base_url = os.getenv("WHISPER_BASE_URL", "").strip() or None
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not base_url and not api_key:
        raise RuntimeError("Invalid API key. Check your .env file.")
    timeout = float(os.getenv("REQUEST_TIMEOUT_SEC", "120"))
    return OpenAI(api_key=api_key or "local", base_url=base_url, timeout=timeout)


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp3"


def _get_duration_ms(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip()) * 1000


def _detect_silence_in_window(path: str, start_ms: float, end_ms: float) -> list[tuple[float, float]]:
    """Return absolute (start_ms, end_ms) silence intervals found within the window."""
    start_s = start_ms / 1000
    duration_s = (end_ms - start_ms) / 1000
    result = subprocess.run(
        ["ffmpeg", "-ss", str(start_s), "-t", str(duration_s), "-i", path,
         "-af", f"silencedetect=n=-40dB:d={MIN_SILENCE_LEN_MS / 1000}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    output = result.stderr
    starts = [float(v) for v in re.findall(r"silence_start: ([\d.]+)", output)]
    ends   = [float(v) for v in re.findall(r"silence_end: ([\d.]+)", output)]
    # timestamps from ffmpeg are relative to the seek point → add start_s for absolute
    return [(( s + start_s) * 1000, (e + start_s) * 1000) for s, e in zip(starts, ends)]


def _export_segment_mp3(path: str, start_ms: float, end_ms: float) -> bytes:
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start_ms / 1000), "-t", str((end_ms - start_ms) / 1000),
         "-i", path, "-b:a", "128k", "-f", "mp3", "pipe:1"],
        capture_output=True, check=True,
    )
    return result.stdout


def _split_segments(path: str, total_ms: float) -> list[tuple[float, float]]:
    """Return list of (start_ms, end_ms) segments split at silence points."""
    if total_ms <= TARGET_CHUNK_MS:
        return [(0.0, total_ms)]

    segments: list[tuple[float, float]] = []
    pos = 0.0
    while pos < total_ms:
        remaining = total_ms - pos
        if remaining <= TARGET_CHUNK_MS * 1.15:
            segments.append((pos, total_ms))
            break

        target = pos + TARGET_CHUNK_MS
        lo = max(pos, target - SILENCE_SEARCH_MS)
        hi = min(total_ms, target + SILENCE_SEARCH_MS // 2)

        silences = _detect_silence_in_window(path, lo, hi)
        if silences:
            best = min(silences, key=lambda s: abs((s[0] + s[1]) / 2 - target))
            split_at = (best[0] + best[1]) / 2
        else:
            split_at = target

        segments.append((pos, split_at))
        pos = split_at

    return segments


def _last_words(text: str, n: int = 100) -> str:
    words = text.split()
    return " ".join(words[-n:]) if len(words) > n else text


def _api_call(client: OpenAI, buf: io.BytesIO, filename: str,
              language: str, temperature: float, prompt: str,
              model: str = "whisper-1",
              progress_cb=None, attempt_label: str = "") -> object:
    for attempt in range(1, 4):
        try:
            buf.seek(0)
            buf.name = filename
            kwargs: dict = dict(
                model=model,
                file=buf,
                response_format="verbose_json",
                temperature=temperature,
            )
            if language and language != "auto":
                kwargs["language"] = language
            if prompt:
                kwargs["prompt"] = prompt
            return client.audio.transcriptions.create(**kwargs)

        except APIStatusError as e:
            if e.status_code == 429 and attempt < 3:
                label = f"{attempt_label} " if attempt_label else ""
                if progress_cb:
                    progress_cb(None, f"OpenAI rate limited. Retrying... {label}(attempt {attempt}/3)")
                time.sleep(5 * attempt)
                continue
            if e.status_code == 429:
                raise RuntimeError("OpenAI rate limit reached. Please try again later.")
            if e.status_code == 401:
                raise RuntimeError("Invalid API key. Check your .env file.")
            raise RuntimeError(f"API error ({e.status_code}): {getattr(e, 'message', str(e))}")
        except APITimeoutError:
            raise RuntimeError("Request timed out. Try again with a shorter file.")
        except APIConnectionError:
            raise RuntimeError("Could not connect to OpenAI API. Check your internet connection.")


def _segments_from(response) -> list[dict]:
    if not response.segments:
        return []
    return [{"start": s.start, "end": s.end, "text": s.text} for s in response.segments]


def _transcribe_single(client, file_bytes: bytes, filename: str,
                       language: str, temperature: float,
                       prompt: str, model: str, progress_cb=None) -> dict:
    if progress_cb:
        progress_cb(0.1, "Uploading to OpenAI...")
    buf = io.BytesIO(file_bytes)
    response = _api_call(client, buf, filename, language, temperature, prompt, model, progress_cb)
    if progress_cb:
        progress_cb(1.0, "Done!")
    return {
        "text": response.text,
        "segments": _segments_from(response),
        "duration": getattr(response, "duration", None),
    }


def _transcribe_chunked(client, file_bytes: bytes, filename: str,
                        language: str, temperature: float,
                        prompt: str, model: str, progress_cb=None) -> dict:
    ext = _ext(filename)
    fmt = ext if ext in ("mp3", "wav", "ogg", "flac", "m4a", "mp4", "webm") else "mp3"

    with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        if progress_cb:
            progress_cb(0.0, "Analyzing audio...")
        total_ms = _get_duration_ms(tmp_path)

        if progress_cb:
            progress_cb(0.05, "Splitting audio into chunks...")
        segments = _split_segments(tmp_path, total_ms)

        all_segments: list[dict] = []
        text_parts: list[str] = []
        running_prompt = prompt

        for i, (seg_start, seg_end) in enumerate(segments):
            if progress_cb:
                progress_cb(i / len(segments) * 0.9, f"Processing chunk {i + 1} of {len(segments)}...")

            mp3_bytes = _export_segment_mp3(tmp_path, seg_start, seg_end)

            # Re-split at half duration if chunk is still somehow too large
            if len(mp3_bytes) > MAX_CHUNK_BYTES:
                mid = (seg_start + seg_end) / 2
                sub_segs = [(seg_start, mid), (mid, seg_end)]
                for j, (ss, se) in enumerate(sub_segs):
                    sbuf = io.BytesIO(_export_segment_mp3(tmp_path, ss, se))
                    resp = _api_call(client, sbuf, f"chunk_{i}_{j}.mp3",
                                     language, temperature, running_prompt, model,
                                     progress_cb, attempt_label=f"(chunk {i + 1}.{j + 1})")
                    text_parts.append(resp.text)
                    for seg in _segments_from(resp):
                        all_segments.append({
                            "start": seg["start"] + ss / 1000,
                            "end":   seg["end"]   + ss / 1000,
                            "text":  seg["text"],
                        })
                    running_prompt = _last_words(resp.text)
                continue

            buf = io.BytesIO(mp3_bytes)
            response = _api_call(
                client, buf, f"chunk_{i}.mp3",
                language, temperature, running_prompt, model,
                progress_cb, attempt_label=f"(chunk {i + 1})",
            )
            text_parts.append(response.text)
            for seg in _segments_from(response):
                all_segments.append({
                    "start": seg["start"] + seg_start / 1000,
                    "end":   seg["end"]   + seg_start / 1000,
                    "text":  seg["text"],
                })
            running_prompt = _last_words(response.text)

    finally:
        os.unlink(tmp_path)

    if progress_cb:
        progress_cb(1.0, "Done!")

    return {
        "text": " ".join(text_parts),
        "segments": all_segments,
        "duration": total_ms / 1000,
    }


def transcribe(
    file_bytes: bytes,
    filename: str,
    language: str = "auto",
    temperature: float = 0.0,
    prompt: str = "",
    model: str = "whisper-1",
    progress_callback=None,
) -> dict:
    """
    Transcribe audio. Automatically chunks files larger than 24 MB.

    Returns {"text": str, "segments": [{"start", "end", "text"}]}.
    Raises RuntimeError with a user-facing message on failure.
    """
    client = _get_client()

    if len(file_bytes) <= MAX_CHUNK_BYTES:
        return _transcribe_single(client, file_bytes, filename, language,
                                  temperature, prompt, model, progress_callback)

    return _transcribe_chunked(client, file_bytes, filename, language,
                               temperature, prompt, model, progress_callback)
