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

Interactive Swagger documentation is available at `/docs` and ReDoc at `/redoc`.

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
