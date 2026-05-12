# 🎙️ Audio Dictation

Ứng dụng web chạy local để chuyển giọng nói thành văn bản bằng OpenAI Whisper hoặc Whisper self-host, xây dựng trên Streamlit.

## Tính năng

- Upload audio bằng drag-and-drop hoặc click chọn file
- Nhập link Google Drive (file phải được chia sẻ công khai)
- Hỗ trợ 7 định dạng: `mp3`, `mp4`, `wav`, `m4a`, `ogg`, `webm`, `flac`
- Chọn model: Whisper v2, GPT-4o mini, GPT-4o
- Chọn ngôn ngữ nguồn (30+ ngôn ngữ) hoặc để auto-detect
- Hiển thị transcript theo từng đoạn có timestamp `[MM:SS → MM:SS]`
- Chỉnh sửa transcript trực tiếp trên giao diện
- Xuất kết quả ra `.txt`, `.srt`, `.vtt`, `.json`
- Tự động chia nhỏ file > 25 MB thành các chunk ≤ 24 MB
- Lưu lịch sử các file đã xử lý trong phiên làm việc
- Tự động expose ra internet qua ngrok khi có `NGROK_AUTHTOKEN`

## Yêu cầu hệ thống

- Python 3.11+
- ffmpeg

```bash
# Ubuntu / Debian
sudo apt install ffmpeg

# Arch Linux
sudo pacman -S ffmpeg
```

## Cài đặt

```bash
# 1. Clone hoặc tải source về
git clone <repo-url>
cd Dictator

# 2. Tạo và kích hoạt virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Cài Python dependencies
pip install -r requirements.txt

# 4. Tạo file .env
cp .env.example .env
```

Mở file `.env` và điền thông tin:

```dotenv
OPENAI_API_KEY=sk-...
```

## Chạy ứng dụng

```bash
streamlit run app.py
```

Truy cập `http://localhost:8501` trên trình duyệt.

Nếu có `NGROK_AUTHTOKEN` trong `.env`, public URL sẽ tự động in ra terminal.

## Cấu trúc dự án

```
Dictator/
├── app.py                # UI Streamlit + tự động khởi động ngrok
├── whisper_service.py    # Gọi Whisper API, xử lý chunking
├── utils.py              # Download Google Drive, xuất .srt/.vtt/.json/.txt
├── .env                  # Biến môi trường (không commit)
├── .env.example          # Mẫu biến môi trường
├── requirements.txt      # Python dependencies
└── runtime.txt           # Phiên bản Python (3.11)
```

## Biến môi trường

| Biến | Mặc định | Mô tả |
|------|----------|-------|
| `OPENAI_API_KEY` | _(bắt buộc nếu không self-host)_ | OpenAI API key |
| `DEFAULT_LANGUAGE` | `en` | Ngôn ngữ mặc định trong dropdown |
| `REQUEST_TIMEOUT_SEC` | `120` | Timeout mỗi API call (giây) |
| `WHISPER_BASE_URL` | _(trống)_ | URL Whisper server local (bỏ trống để dùng OpenAI) |
| `NGROK_AUTHTOKEN` | _(trống)_ | Token từ ngrok.com — tự động expose app ra internet |

## Self-host Whisper (tuỳ chọn)

Chạy model Whisper local thay vì OpenAI API — miễn phí, không gửi data ra ngoài.

**Yêu cầu:** NVIDIA GPU, CUDA, `~/.venv-whisper` đã cài `faster-whisper-server`.

```bash
# Khởi động Whisper server
LD_LIBRARY_PATH=/opt/cuda/lib64 WHISPER__DEVICE=cuda \
  ~/.venv-whisper/bin/faster-whisper-server large-v3
```

Thêm vào `.env`:

```dotenv
WHISPER_BASE_URL=http://localhost:8000/v1
```

Model hỗ trợ: `tiny`, `base`, `small`, `medium`, `large-v3`.

## Chi phí (OpenAI API)

| Model | Giá / phút |
|-------|-----------|
| Whisper v2 (`whisper-1`) | $0.006 |
| GPT-4o mini (`gpt-4o-mini-transcribe`) | $0.003 |
| GPT-4o (`gpt-4o-transcribe`) | $0.006 |

Ví dụ: file 10 phút với GPT-4o mini ≈ $0.03. Self-host miễn phí.

## Lưu ý

- File `.env` chứa API key — **không commit** lên git.
- Lịch sử phiên chỉ tồn tại trong tab trình duyệt hiện tại, mất khi refresh trang.
- SRT và VTT chỉ khả dụng khi API trả về timestamp (`verbose_json`).
- Link Google Drive phải được chia sẻ với quyền "Anyone with the link".
