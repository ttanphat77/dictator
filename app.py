import os
import subprocess
import threading

import streamlit as st
from dotenv import load_dotenv

from utils import download_gdrive, fmt_display, to_json, to_srt, to_txt, to_vtt
from whisper_service import transcribe

load_dotenv()


def _start_ngrok():
    token = os.getenv("NGROK_AUTHTOKEN", "").strip()
    if not token:
        return
    subprocess.run(["ngrok", "config", "add-authtoken", token], capture_output=True)
    proc = subprocess.Popen(
        ["ngrok", "http", "8501", "--log=stdout", "--log-format=json"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    for line in proc.stdout:
        if '"url":"https://' in line:
            import json
            try:
                url = json.loads(line).get("url")
                if url:
                    print(f"\n🌐 Public URL: {url}\n", flush=True)
                    break
            except Exception:
                pass


if "ngrok_started" not in st.session_state:
    st.session_state.ngrok_started = True
    threading.Thread(target=_start_ngrok, daemon=True).start()

# ── Constants ──────────────────────────────────────────────────────────────────

SUPPORTED_EXT = ["mp3", "mp4", "wav", "m4a", "ogg", "webm", "flac"]

LANGUAGES: dict[str, str] = {
    "Auto-detect": "auto",
    "English": "en",
    "Vietnamese": "vi",
    "Chinese": "zh",
    "Japanese": "ja",
    "Korean": "ko",
    "French": "fr",
    "German": "de",
    "Spanish": "es",
    "Portuguese": "pt",
    "Italian": "it",
    "Russian": "ru",
    "Arabic": "ar",
    "Hindi": "hi",
    "Thai": "th",
    "Indonesian": "id",
    "Malay": "ms",
    "Dutch": "nl",
    "Polish": "pl",
    "Turkish": "tr",
    "Swedish": "sv",
    "Norwegian": "no",
    "Danish": "da",
    "Finnish": "fi",
    "Czech": "cs",
    "Hungarian": "hu",
    "Romanian": "ro",
    "Ukrainian": "uk",
    "Greek": "el",
    "Hebrew": "he",
}

DEFAULT_LANG_LABEL = next(
    (k for k, v in LANGUAGES.items() if v == os.getenv("DEFAULT_LANGUAGE", "en")),
    "English",
)

# Cost per minute of audio (USD)
COST_PER_MIN: dict[str, float] = {
    "whisper-1": 0.006,
    "gpt-4o-mini-transcribe": 0.003,
    "gpt-4o-transcribe": 0.006,
}


# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Dictator", page_icon="🎩", layout="wide")

# ── Session state ──────────────────────────────────────────────────────────────

if "history" not in st.session_state:
    st.session_state.history = []
if "active" not in st.session_state:
    st.session_state.active = None
if "edited_text" not in st.session_state:
    st.session_state.edited_text = ""

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Settings")

    MODELS = {
        "whisper-1": "Whisper v2",
        "gpt-4o-mini-transcribe": "GPT-4o mini",
        "gpt-4o-transcribe": "GPT-4o",
    }
    selected_model = st.selectbox(
        "Model",
        options=list(MODELS.keys()),
        format_func=lambda k: MODELS[k],
    )

    lang_label = st.selectbox(
        "Source language",
        options=list(LANGUAGES.keys()),
        index=list(LANGUAGES.keys()).index(DEFAULT_LANG_LABEL),
    )
    selected_lang = LANGUAGES[lang_label]

    prompt_hint = st.text_input(
        "Vocabulary hint (optional)",
        placeholder="Proper nouns, technical terms…",
        help="Helps Whisper recognise uncommon words more accurately",
    )

    # ── History ────────────────────────────────────────────────────────────────
    if st.session_state.history:
        st.divider()
        st.subheader("📋 History")
        for i, item in enumerate(reversed(st.session_state.history)):
            if st.button(f"📄 {item['filename']}", key=f"hist_{i}", use_container_width=True):
                st.session_state.active = item
                st.session_state.edited_text = item["result"]["text"]
                st.rerun()

# ── Main ───────────────────────────────────────────────────────────────────────

st.title("🎩 Dictator")
st.caption("Speech-to-text powered by OpenAI Whisper")

uploaded = st.file_uploader(
    "Upload audio file",
    type=SUPPORTED_EXT,
    help=f"Supported: {', '.join(SUPPORTED_EXT)}  ·  Files over 25 MB are split automatically",
)

gdrive_url = st.text_input(
    "Or paste Google Drive link",
    placeholder="https://drive.google.com/file/d/...",
    help="File must be shared as 'Anyone with the link'",
)

file_bytes: bytes | None = None
file_name: str | None = None

if uploaded:
    cache_key = f"{uploaded.name}_{uploaded.size}"
    if st.session_state.get("_file_key") != cache_key:
        st.session_state._file_key = cache_key
        st.session_state._file_bytes = uploaded.read()
    file_bytes = st.session_state._file_bytes
    file_name = uploaded.name
elif gdrive_url:
    cache_key = f"gdrive_{gdrive_url}"
    if st.session_state.get("_file_key") != cache_key:
        with st.spinner("Downloading from Google Drive..."):
            try:
                file_bytes, file_name = download_gdrive(gdrive_url)
                st.session_state._file_key = cache_key
                st.session_state._file_bytes = file_bytes
                st.session_state._file_name = file_name
            except Exception as exc:
                st.error(str(exc))
    else:
        file_bytes = st.session_state._file_bytes
        file_name = st.session_state._file_name

if file_bytes and file_name:
    ext = file_name.rsplit(".", 1)[-1].lower()
    size_mb = len(file_bytes) / 1024 / 1024
    c1, c2, c3 = st.columns(3)
    c1.metric("File", file_name)
    c2.metric("Size", f"{size_mb:.1f} MB")
    c3.metric("Format", ext.upper())

    if st.button("🚀 Transcribe", type="primary", use_container_width=True):
        progress_bar = st.progress(0.0)
        status_slot = st.empty()

        def on_progress(value, message):
            if value is not None:
                progress_bar.progress(float(value))
            status_slot.info(f"⏳ {message}")

        try:
            with st.spinner("Processing…"):
                result = transcribe(
                    file_bytes=file_bytes,
                    filename=file_name,
                    language=selected_lang,
                    temperature=0.0,
                    prompt=prompt_hint,
                    model=selected_model,
                    progress_callback=on_progress,
                )

            progress_bar.progress(1.0)
            status_slot.success("✅ Done!")

            item = {
                "filename": file_name,
                "lang_label": lang_label,
                "model": selected_model,
                "result": result,
            }
            st.session_state.history.append(item)
            st.session_state.active = item
            st.session_state.edited_text = result["text"]
            st.rerun()

        except RuntimeError as exc:
            progress_bar.empty()
            status_slot.empty()
            st.error(str(exc))

        except Exception as exc:
            import traceback
            progress_bar.empty()
            status_slot.empty()
            st.error(f"Unexpected error: {exc}")
            st.code(traceback.format_exc(), language="text")

# ── Results panel ──────────────────────────────────────────────────────────────

active = st.session_state.active
if active:
    segments: list[dict] = active["result"].get("segments", [])
    has_timestamps = bool(segments)

    st.divider()
    st.subheader("📝 Transcript")

    meta_cols = st.columns(2)
    meta_cols[0].caption(f"**File:** {active['filename']}")
    meta_cols[1].caption(f"**Language:** {active['lang_label']}")

    result = active["result"]
    model_used = active.get("model", "whisper-1")
    duration_sec: float | None = result.get("duration")

    tab_seg, tab_full = st.tabs(["By segment", "Full text"])

    with tab_seg:
        if has_timestamps:
            for seg in segments:
                start = fmt_display(seg["start"])
                end = fmt_display(seg["end"])
                st.markdown(f"`[{start} → {end}]`  {seg['text'].strip()}")
        else:
            st.info("No timestamp data — showing full text.")
            st.write(active["result"]["text"])

    with tab_full:
        edited = st.text_area(
            "Edit transcript",
            value=st.session_state.edited_text,
            height=320,
            label_visibility="collapsed",
        )
        st.session_state.edited_text = edited

    # ── Export ─────────────────────────────────────────────────────────────────
    st.subheader("📥 Export")

    base = active["filename"].rsplit(".", 1)[0]
    dl1, dl2, dl3, dl4 = st.columns(4)

    txt_data = (
        to_txt(segments).encode("utf-8")
        if has_timestamps
        else st.session_state.edited_text.encode("utf-8")
    )
    dl1.download_button("⬇️ .TXT", data=txt_data,
                        file_name=f"{base}.txt", mime="text/plain",
                        use_container_width=True)

    if has_timestamps:
        dl2.download_button("⬇️ .SRT", data=to_srt(segments).encode("utf-8"),
                            file_name=f"{base}.srt", mime="text/plain",
                            use_container_width=True)
        dl3.download_button("⬇️ .VTT", data=to_vtt(segments).encode("utf-8"),
                            file_name=f"{base}.vtt", mime="text/vtt",
                            use_container_width=True)
    else:
        dl2.button("⬇️ .SRT", disabled=True, use_container_width=True,
                   help="Requires timestamp data")
        dl3.button("⬇️ .VTT", disabled=True, use_container_width=True,
                   help="Requires timestamp data")

    dl4.download_button("⬇️ .JSON", data=to_json(active["result"]).encode("utf-8"),
                        file_name=f"{base}.json", mime="application/json",
                        use_container_width=True)

    if duration_sec is not None:
        mins, secs = divmod(int(duration_sec), 60)
        cost = (duration_sec / 60) * COST_PER_MIN.get(model_used, 0.006)
        st.caption(f"⏱ {mins}m {secs:02d}s · 💰 ${cost:.4f}")
