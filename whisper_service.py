import io
import os
import subprocess
import tempfile
import time

# Remove LD_LIBRARY_PATH before pydub imports ffmpeg/ffprobe subprocesses.
# Without this, system ffmpeg fails with "symbol lookup error: libpango".
os.environ.pop("LD_LIBRARY_PATH", None)

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydub import AudioSegment
from pydub.silence import detect_silence

MAX_CHUNK_BYTES = 24 * 1024 * 1024  # 24 MB — OpenAI hard limit

# Time-based split: 10 min chunks at 128 kbps ≈ 9.6 MB, safely under limit
TARGET_CHUNK_MS = 10 * 60 * 1000
# Search ±20 s around the target split point for a silence gap
SILENCE_SEARCH_MS = 20_000
MIN_SILENCE_LEN_MS = 200


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Invalid API key. Check your .env file.")
    timeout = float(os.getenv("REQUEST_TIMEOUT_SEC", "120"))
    return OpenAI(api_key=api_key, timeout=timeout)


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp3"


def _split_on_silence(audio: AudioSegment) -> list[AudioSegment]:
    """Split audio into ~10 min chunks, cutting at silence points where possible."""
    total_ms = len(audio)
    if total_ms <= TARGET_CHUNK_MS:
        return [audio]

    chunks: list[AudioSegment] = []
    pos = 0
    while pos < total_ms:
        remaining = total_ms - pos
        if remaining <= TARGET_CHUNK_MS * 1.15:
            chunks.append(audio[pos:])
            break

        target = pos + TARGET_CHUNK_MS
        lo = max(pos, target - SILENCE_SEARCH_MS)
        hi = min(total_ms, target + SILENCE_SEARCH_MS // 2)
        window = audio[lo:hi]

        thresh = max(window.dBFS - 14, -60)
        silences = detect_silence(window, min_silence_len=MIN_SILENCE_LEN_MS, silence_thresh=thresh)

        if silences:
            best = min(silences, key=lambda s: abs(lo + (s[0] + s[1]) // 2 - target))
            split_at = lo + (best[0] + best[1]) // 2
        else:
            split_at = target

        chunks.append(audio[pos:split_at])
        pos = split_at

    return chunks


def _last_words(text: str, n: int = 100) -> str:
    """Return the last n words — used as prompt context for the next chunk."""
    words = text.split()
    return " ".join(words[-n:]) if len(words) > n else text


def _api_call(client: OpenAI, buf: io.BytesIO, filename: str,
              language: str, temperature: float, prompt: str,
              model: str = "whisper-1",
              progress_cb=None, attempt_label: str = "") -> object:
    """One API call with up to 3 retries on 429."""
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
        audio = AudioSegment.from_file(tmp_path, format=fmt)
    finally:
        os.unlink(tmp_path)
    chunks = _split_on_silence(audio)

    all_segments: list[dict] = []
    text_parts: list[str] = []
    offset_sec = 0.0
    running_prompt = prompt  # seed with user hint, then chain each chunk's tail

    for i, chunk in enumerate(chunks):
        if progress_cb:
            progress_cb(i / len(chunks) * 0.9, f"Processing chunk {i + 1} of {len(chunks)}...")

        buf = io.BytesIO()
        chunk.export(buf, format="mp3", bitrate="128k")

        # Verify exported size — re-split at half duration if somehow still too large
        if buf.tell() > MAX_CHUNK_BYTES:
            mid = len(chunk) // 2
            sub_chunks = [chunk[:mid], chunk[mid:]]
            for j, sub in enumerate(sub_chunks):
                sbuf = io.BytesIO()
                sub.export(sbuf, format="mp3", bitrate="128k")
                resp = _api_call(client, sbuf, f"chunk_{i}_{j}.mp3",
                                 language, temperature, running_prompt, model,
                                 progress_cb, attempt_label=f"(chunk {i + 1}.{j + 1})")
                text_parts.append(resp.text)
                for seg in _segments_from(resp):
                    all_segments.append({
                        "start": seg["start"] + offset_sec,
                        "end": seg["end"] + offset_sec,
                        "text": seg["text"],
                    })
                running_prompt = _last_words(resp.text)
                offset_sec += len(sub) / 1000.0
            continue

        response = _api_call(
            client, buf, f"chunk_{i}.mp3",
            language, temperature, running_prompt, model,
            progress_cb, attempt_label=f"(chunk {i + 1})",
        )

        text_parts.append(response.text)
        for seg in _segments_from(response):
            all_segments.append({
                "start": seg["start"] + offset_sec,
                "end": seg["end"] + offset_sec,
                "text": seg["text"],
            })

        # Chain the tail of this transcript as context for the next chunk
        running_prompt = _last_words(response.text)
        offset_sec += len(chunk) / 1000.0

    if progress_cb:
        progress_cb(1.0, "Done!")

    return {
        "text": " ".join(text_parts),
        "segments": all_segments,
        "duration": offset_sec,
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
