# Video Downloader Backend (FastAPI + yt-dlp)

A production-grade, secure media extraction and download API powered by **FastAPI** and **yt-dlp**, engineered specifically for seamless deployment to **Botkeep Cloud** and integration with **Vercel** frontends.

---

## Features

- **Asynchronous Download Engine:** Background job queue with progress tracking (`queued`, `processing`, `completed`, `failed`).
- **Runtime FFmpeg Detection:** Dynamically checks for system FFmpeg binaries. When available, supports full audio/video muxing and MP3 transcode; when absent, safely falls back to direct single-stream video and native audio extraction without crashing.
- **Botkeep Cloud Native:** Reads Botkeep's dynamic `SERVER_PORT`, binds to `0.0.0.0`, and uses simple startup: `python main.py`.
- **SSRF & Security Hardening:** Strict URL validation rejects private IP ranges, loopback addresses, local domain resolutions, and cloud metadata services (RFC 1918, RFC 3927, 169.254.169.254).
- **Path Traversal Protection:** Canonical path verification ensures downloaded files cannot escape the configured temporary directory.
- **Automatic Lifecycle Cleanup:** Files and jobs automatically expire after `DOWNLOAD_TTL_SECONDS` (default: 15 minutes), with periodic orphan purge.
- **Bounded Concurrency & Size Limits:** Limits maximum file size (`MAX_FILE_SIZE_MB`) and active concurrent downloads (`MAX_CONCURRENT_DOWNLOADS`).

---

## API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/healthz` | Health check endpoint for Botkeep uptime probes |
| `GET` | `/api/capabilities` | Inspects yt-dlp version, FFmpeg availability, and platform limits |
| `POST` | `/api/metadata` | Validates URL and returns media metadata, thumbnail, duration, and formats |
| `POST` | `/api/downloads` | Queues a download job and returns a unique `job_id` |
| `GET` | `/api/downloads/{job_id}` | Real-time job status, percentage, download speed, and ETA |
| `GET` | `/api/downloads/{job_id}/file` | Streams the completed file with safe filename and headers |
| `DELETE` | `/api/downloads/{job_id}` | Manually cleans up a completed or failed job and deletes disk files |
| `POST` | `/api/transcribe/upload` | Uploads an audio/video file (up to 100MB) to transcribe with AI speech recognition |
| `POST` | `/api/transcribe/url` | Ingests direct media link or video platform URL to extract audio and transcribe |
| `GET` | `/api/transcribe/{job_id}` | Polls transcription progress, stage, detected language, and timestamped segments |
| `GET` | `/api/transcribe/{job_id}/export` | Exports completed transcript as plain TXT, SRT subtitles, or WebVTT subtitles |

| `POST` | `/api/robin/search` | Robin darknet OSINT investigation via Tor SOCKS5 & OpenCode synthesis |
| `POST` | `/api/robin/followup` | Grounded follow-up inquiries on retrieved dark web intelligence |
| `GET` | `/api/robin/health` | Status probe for Tor SOCKS5 circuit & OpenCode Inference API |
| `POST` | `/api/email-check` | Blackbird OSINT email reverse lookup across 16+ online services |
| `POST` | `/api/email-check/ai` | AI digital footprint risk assessment and identity exposure matrix |
| `GET` | `/api/email-check/health` | Health probe for Blackbird CLI engine & dependency readiness |

Interactive Swagger documentation is available at `/docs` and ReDoc at `/redoc`.

---

## 🔍 Blackbird OSINT Email Checker Integration

### Overview / نظرة عامة
تم دمج أداة **Blackbird OSINT** مفتوحة المصدر (المستودع الرسمي: [https://github.com/antoniaci/blackbird](https://github.com/antoniaci/blackbird)) كخدمة Backend قوية تقوم بالبحث العكسي عن الحسابات المسجلة المرتبطة بأي بريد إلكتروني عبر منصات التواصل والخدمات الرقمية.

### Mandatory Legal Disclaimer / إخلاء مسؤولية قانوني إلزامي
> [!WARNING]
> **هذه الميزة مخصصة للتحقيقات القانونية والتعليمية فقط. لا تفحص بريدًا إلكترونيًا دون إذن صريح من صاحبه.**
> **This feature is strictly intended for authorized legal investigations and educational security research only. Do not scan an email address without explicit permission from its owner.**

### Known Dependency Fix: `Werkzeug==2.2.2`
> [!IMPORTANT]
> يعتمد مستودع Blackbird على دوال من مكتبة Werkzeug القديمة. عند تثبيت إصدار أحدث من Werkzeug (3.0+)، يظهر الخطأ:
> `ImportError: cannot import name 'url_quote' from 'werkzeug.urls'`
> تم تثبيت وتثبيت الإصدار المتوافق `Werkzeug==2.2.2` داخل متطلبات المشروع و Dockerfile لتفادي هذا الخطأ نهائياً:
> ```bash
> pip install Werkzeug==2.2.2
> ```

### Rate Limiting & Authentication
- **Rate Limit:** حد أقصى **5 طلبات في الدقيقة** لكل عميل لمنع إساءة الاستخدام.
- **Authentication:** يدعم حماية المسار عبر `BLACKBIRD_AUTH_TOKEN` (Bearer token أو Basic Auth).

---

## 🚀 VPS Deployment with Docker Compose / النشر على خادم VPS

### 1. Requirements / المتطلبات
- خادم VPS يعمل بنظام Linux (Ubuntu 22.04 / 24.04 LTS موصى به) مع Docker و Docker Compose مثبتين.
- **Tor Client Policy Note:** تأكد من مراجعة سياسة مزود الـ VPS (Terms of Service / AUP) للتأكد من سماحهم بتشغيل Tor كعميل SOCKS5 (`Tor Client`) وليس كـ Exit Node. (معظم المزودين مثل Hetzner و DigitalOcean و OVH يسمحون باستخدام Tor كعميل بحث).

### 2. Deployment Steps / خطوات النشر
1. استنسخ المستودع مع الـ Submodules:
```bash
git clone --recursive https://github.com/HZAMI700/video-downloader-backend.git
cd video-downloader-backend
```
إذا كنت قد استنسخت المستودع مسبقاً، حدّث الـ Submodule:
```bash
git submodule update --init --recursive
```

2. أنشئ ملف `.env`:
```bash
cp .env.example .env
nano .env
```
عيّن المتغيرات المطلوبة:
```ini
OPENCODE_API_KEY=oc_sk_your_key
TOR_SOCKS_PROXY=socks5h://tor:9050
BLACKBIRD_ENABLED=true
BLACKBIRD_TIMEOUT_SECONDS=120
BLACKBIRD_RATE_LIMIT_PER_MINUTE=5
```

3. شغّل الخدمات عبر Docker Compose:
```bash
docker compose up -d --build
```

4. تحقق من حالة الخدمات:
```bash
docker compose ps
curl http://localhost:8000/api/email-check/health
curl http://localhost:8000/api/robin/health
```

---

## AI Speech Transcription Engine & Botkeep Free-Tier Constraints

> [!IMPORTANT]
> **Botkeep Cloud Free-Tier Resource Constraints:**
> - CPU: 1.5 vCore
> - Memory: 2 GB RAM
> - Storage: 2 GB Disk
>
> Running local heavyweight PyTorch speech models (like `openai-whisper` or `faster-whisper` on CPU) would quickly allocate > 1.8-2.2 GB RAM, triggering immediate Out-Of-Memory (OOM) termination on Botkeep's container.
> 
> Therefore, this backend utilizes **external AI cloud speech APIs** (`GROQ_API_KEY`, `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, or `ASSEMBLYAI_API_KEY`). This keeps backend RAM usage < 150 MB and returns transcriptions in seconds.
> All temporary uploaded files and extracted audio chunks are automatically deleted from disk immediately upon completion or failure to stay well within Botkeep's 2 GB disk envelope.

---

## Local Setup & Development

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.11)
- Optional: `ffmpeg` installed on system PATH

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Environment Configuration
Create a `.env` file based on `.env.example`:
```ini
SERVER_PORT=8000
HOST=0.0.0.0
FRONTEND_ORIGINS=http://localhost:3000,https://video-downloader-frontend.vercel.app
MAX_FILE_SIZE_MB=500
DOWNLOAD_TIMEOUT_SECONDS=600
DOWNLOAD_TTL_SECONDS=900
MAX_CONCURRENT_DOWNLOADS=3
TEMP_DOWNLOAD_DIR=./downloads_temp
```

### 4. Run the Server
```bash
python main.py
```
Or with Uvicorn directly:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Run the Automated Tests
```bash
python -m pytest -v
```

---

## Botkeep Cloud Deployment Guide

Follow these steps to deploy this backend to Botkeep Cloud:

1. **Push to GitHub:** Ensure the repository `video-downloader-backend` is pushed to GitHub.
2. **Create New Project in Botkeep:**
   - Select **Python** runtime (Python 3.10, 3.11, or newer).
   - Connect your GitHub repository `HZAMI700/video-downloader-backend`.
3. **Configure Build & Start Commands:**
   - **Build / Install Command:**
     ```bash
     pip install -r requirements.txt
     ```
   - **Start Command:**
     ```bash
     python main.py
     ```
4. **Environment Variables on Botkeep:**
   - `SERVER_PORT`: Botkeep automatically injects this variable. The application reads `os.getenv("SERVER_PORT", "8000")`.
   - `FRONTEND_ORIGINS`: Set this to your Vercel deployment URL (e.g. `https://video-downloader-frontend.vercel.app,http://localhost:3000`).
   - `MAX_FILE_SIZE_MB`: `500` (or your preferred ceiling).
   - `DOWNLOAD_TTL_SECONDS`: `900` (15 minutes).
5. **Health Check Configuration:**
   - Health check path: `/healthz`
6. **Obtain Public URL:**
   - Once deployed, copy your Botkeep backend HTTPS domain (e.g., `https://video-downloader-backend.botkeep.app`).
   - Place this URL into your Vercel frontend's `NEXT_PUBLIC_BACKEND_URL` environment variable!

---

## FFmpeg Verification on Botkeep

The backend dynamically checks whether FFmpeg is installed at runtime:
1. Call `GET /api/capabilities`.
2. Inspect the `ffmpeg_available` field:
   - If `true`: Full 1080p+ merging and MP3 conversion are active.
   - If `false`: The backend continues serving direct single-stream video formats (720p/360p pre-merged) and native audio streams (M4A/WebM) without throwing errors.
