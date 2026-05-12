# 🎙️ Audio Dictation

Ứng dụng web chạy local để chuyển giọng nói thành văn bản bằng OpenAI Whisper, xây dựng trên Streamlit.

## Tính năng

- Upload audio bằng drag-and-drop hoặc click chọn file
- Hỗ trợ 7 định dạng: `mp3`, `mp4`, `wav`, `m4a`, `ogg`, `webm`, `flac`
- Chọn ngôn ngữ nguồn (30+ ngôn ngữ) hoặc để auto-detect
- Dịch sang tiếng Anh qua tính năng translation của Whisper
- Hiển thị transcript theo từng đoạn có timestamp `[MM:SS → MM:SS]`
- Chỉnh sửa transcript trực tiếp trên giao diện
- Xuất kết quả ra `.txt`, `.srt`, `.vtt`, `.json`
- Tự động chia nhỏ file > 25 MB thành các chunk ≤ 24 MB
- Lưu lịch sử các file đã xử lý trong phiên làm việc

## Yêu cầu hệ thống

- Python 3.10+
- ffmpeg (cần cho pydub)

```bash
# Ubuntu / Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

## Cài đặt

```bash
# 1. Clone hoặc tải source về
git clone <repo-url>
cd Dictator

# 2. Tạo và kích hoạt virtual environment
python3 -m venv .venv
source .venv/bin/activate      # Linux / macOS
# .venv\Scripts\activate       # Windows

# 3. Cài Python dependencies
pip install -r requirements.txt

# 4. Tạo file .env
cp .env.example .env
```

Mở file `.env` và điền API key:

```dotenv
OPENAI_API_KEY=sk-...
```

## Chạy ứng dụng

```bash
streamlit run app.py
```

Truy cập `http://localhost:8501` trên trình duyệt.

## Cấu trúc dự án

```
Dictator/
├── app.py                # UI Streamlit
├── whisper_service.py    # Logic gọi OpenAI API, xử lý chunking
├── utils.py              # Xuất .srt, .vtt, .json, .txt
├── .env                  # API key (không commit)
├── .env.example          # Mẫu biến môi trường
└── requirements.txt
```

## Biến môi trường

| Biến | Mặc định | Mô tả |
|------|----------|-------|
| `OPENAI_API_KEY` | _(bắt buộc)_ | OpenAI API key |
| `DEFAULT_LANGUAGE` | `vi` | Ngôn ngữ mặc định trong dropdown |
| `MAX_FILE_MB` | `25` | Ngưỡng kích thước file (MB) |
| `REQUEST_TIMEOUT_SEC` | `120` | Timeout mỗi API call (giây) |

## Chi phí

Whisper tính phí theo thời lượng audio: **$0.006 / phút**.  
Ví dụ: file 10 phút ≈ $0.06.

## Lưu ý

- File `.env` chứa API key — **không commit** lên git.
- Lịch sử phiên chỉ tồn tại trong tab trình duyệt hiện tại, mất khi refresh trang.
- SRT và VTT chỉ khả dụng khi API trả về timestamp (`verbose_json`).
