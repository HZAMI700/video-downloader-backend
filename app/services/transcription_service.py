import io
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import httpx
from app.config import settings
from app.services.ytdlp_service import check_ffmpeg_installed

logger = logging.getLogger("video_downloader.transcription_service")


def format_timestamp_srt(seconds: float) -> str:
    """Format seconds into SRT format: 00:00:00,000"""
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def format_timestamp_vtt(seconds: float) -> str:
    """Format seconds into WebVTT format: 00:00:00.000"""
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{millis:03d}"


def generate_srt(segments: List[Dict[str, Any]], fallback_text: str = "") -> str:
    """Generate standard .srt subtitle text from segments."""
    if not segments and fallback_text.strip():
        # Fallback single block
        return f"1\n00:00:00,000 --> 00:00:10,000\n{fallback_text.strip()}\n"

    lines = []
    for idx, seg in enumerate(segments, 1):
        start = format_timestamp_srt(float(seg.get("start", 0.0)))
        end = format_timestamp_srt(float(seg.get("end", 0.0)))
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        lines.append(f"{idx}\n{start} --> {end}\n{text}\n")

    return "\n".join(lines).strip() + "\n" if lines else ""


def generate_vtt(segments: List[Dict[str, Any]], fallback_text: str = "") -> str:
    """Generate standard WebVTT .vtt subtitle text from segments."""
    header = "WEBVTT\n\n"
    if not segments and fallback_text.strip():
        return f"{header}1\n00:00:00.000 --> 00:00:10.000\n{fallback_text.strip()}\n"

    blocks = []
    for idx, seg in enumerate(segments, 1):
        start = format_timestamp_vtt(float(seg.get("start", 0.0)))
        end = format_timestamp_vtt(float(seg.get("end", 0.0)))
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        blocks.append(f"{idx}\n{start} --> {end}\n{text}\n")

    return header + "\n".join(blocks).strip() + "\n" if blocks else header


def extract_compressed_audio(source_path: Path, output_path: Path) -> Path:
    """
    Extract or compress audio using FFmpeg to 64kbps mono MP3.
    This dramatically reduces file size before sending to cloud speech APIs,
    saving network latency and ensuring payloads fit well under API limits.
    """
    if not check_ffmpeg_installed():
        logger.warning("FFmpeg is not installed; returning original file for transcription.")
        return source_path

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(source_path),
        "-vn",                   # No video
        "-ac", "1",              # Mono channel
        "-ar", "16000",          # 16kHz optimal for Whisper/speech models
        "-b:a", "64k",           # 64kbps MP3
        "-f", "mp3",
        str(output_path)
    ]

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            logger.info(f"Compressed audio generated: {output_path.name} ({output_path.stat().st_size} bytes)")
            return output_path
        else:
            logger.warning(f"FFmpeg audio extraction failed (code {proc.returncode}): {proc.stderr.decode('utf-8', errors='ignore')[:200]}")
            return source_path
    except Exception as e:
        logger.warning(f"FFmpeg execution error: {e}. Falling back to original audio.")
        return source_path


async def transcribe_with_groq(audio_path: Path, language: Optional[str] = None) -> Dict[str, Any]:
    """Transcribe using Groq Whisper API (whisper-large-v3). Ultra-fast & cost-effective."""
    api_key = settings.GROQ_API_KEY
    if not api_key:
        raise ValueError("GROQ_API_KEY is not configured.")

    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    data = {
        "model": "whisper-large-v3",
        "response_format": "verbose_json",
    }
    if language and language.strip() and language.lower() != "auto":
        data["language"] = language.strip().lower()

    async with httpx.AsyncClient(timeout=180.0) as client:
        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/mpeg")}
            response = await client.post(url, headers=headers, data=data, files=files)

        if response.status_code != 200:
            err_text = response.text
            logger.error(f"Groq API error ({response.status_code}): {err_text}")
            raise RuntimeError(f"Groq transcription failed ({response.status_code}): {err_text[:200]}")

        res_json = response.json()
        segments = []
        for s in res_json.get("segments", []):
            segments.append({
                "id": s.get("id", len(segments)),
                "start": round(float(s.get("start", 0.0)), 2),
                "end": round(float(s.get("end", 0.0)), 2),
                "text": s.get("text", "").strip()
            })

        return {
            "text": res_json.get("text", "").strip(),
            "language": res_json.get("language", "auto"),
            "duration": round(float(res_json.get("duration", 0.0)), 2),
            "segments": segments,
            "provider": "groq",
        }


async def transcribe_with_openai(audio_path: Path, language: Optional[str] = None) -> Dict[str, Any]:
    """Transcribe using OpenAI Whisper API (whisper-1)."""
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured.")

    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    data = {
        "model": "whisper-1",
        "response_format": "verbose_json",
    }
    if language and language.strip() and language.lower() != "auto":
        data["language"] = language.strip().lower()

    async with httpx.AsyncClient(timeout=180.0) as client:
        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/mpeg")}
            response = await client.post(url, headers=headers, data=data, files=files)

        if response.status_code != 200:
            err_text = response.text
            logger.error(f"OpenAI API error ({response.status_code}): {err_text}")
            raise RuntimeError(f"OpenAI transcription failed ({response.status_code}): {err_text[:200]}")

        res_json = response.json()
        segments = []
        for s in res_json.get("segments", []):
            segments.append({
                "id": s.get("id", len(segments)),
                "start": round(float(s.get("start", 0.0)), 2),
                "end": round(float(s.get("end", 0.0)), 2),
                "text": s.get("text", "").strip()
            })

        return {
            "text": res_json.get("text", "").strip(),
            "language": res_json.get("language", "auto"),
            "duration": round(float(res_json.get("duration", 0.0)), 2),
            "segments": segments,
            "provider": "openai",
        }


async def transcribe_with_deepgram(audio_path: Path, language: Optional[str] = None) -> Dict[str, Any]:
    """Transcribe using Deepgram Nova-2 API."""
    api_key = settings.DEEPGRAM_API_KEY
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY is not configured.")

    query_params = "model=nova-2&smart_format=true&punctuate=true&utterances=true"
    if language and language.strip() and language.lower() != "auto":
        query_params += f"&language={language.strip().lower()}"

    url = f"https://api.deepgram.com/v1/listen?{query_params}"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/mpeg"
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()
        response = await client.post(url, headers=headers, content=audio_bytes)

        if response.status_code != 200:
            err_text = response.text
            logger.error(f"Deepgram API error ({response.status_code}): {err_text}")
            raise RuntimeError(f"Deepgram transcription failed ({response.status_code}): {err_text[:200]}")

        res_json = response.json()
        results = res_json.get("results", {})
        channels = results.get("channels", [{}])
        channel_0 = channels[0] if channels else {}
        alternatives = channel_0.get("alternatives", [{}])
        alt_0 = alternatives[0] if alternatives else {}

        full_text = alt_0.get("transcript", "").strip()
        segments = []
        utterances = results.get("utterances", [])

        if utterances:
            for idx, u in enumerate(utterances):
                segments.append({
                    "id": idx,
                    "start": round(float(u.get("start", 0.0)), 2),
                    "end": round(float(u.get("end", 0.0)), 2),
                    "text": u.get("transcript", "").strip(),
                    "speaker": f"Speaker {u.get('speaker', 0)}" if "speaker" in u else None
                })
        else:
            # Fallback to word groupings if utterances not returned
            words = alt_0.get("words", [])
            chunk_size = 10
            for i in range(0, len(words), chunk_size):
                chunk = words[i:i + chunk_size]
                if not chunk:
                    continue
                w_start = round(float(chunk[0].get("start", 0.0)), 2)
                w_end = round(float(chunk[-1].get("end", 0.0)), 2)
                chunk_text = " ".join(w.get("punctuated_word", w.get("word", "")) for w in chunk)
                segments.append({
                    "id": len(segments),
                    "start": w_start,
                    "end": w_end,
                    "text": chunk_text
                })

        duration = round(float(res_json.get("metadata", {}).get("duration", 0.0)), 2)

        return {
            "text": full_text,
            "language": language or "en",
            "duration": duration,
            "segments": segments,
            "provider": "deepgram",
        }


async def transcribe_with_assemblyai(audio_path: Path, language: Optional[str] = None) -> Dict[str, Any]:
    """Transcribe using AssemblyAI API."""
    api_key = settings.ASSEMBLYAI_API_KEY
    if not api_key:
        raise ValueError("ASSEMBLYAI_API_KEY is not configured.")

    headers = {"authorization": api_key}
    async with httpx.AsyncClient(timeout=180.0) as client:
        # Step 1: Upload audio
        with open(audio_path, "rb") as f:
            upload_res = await client.post("https://api.assemblyai.com/v2/upload", headers=headers, content=f.read())
        if upload_res.status_code != 200:
            raise RuntimeError(f"AssemblyAI upload failed: {upload_res.text[:200]}")
        upload_url = upload_res.json().get("upload_url")

        # Step 2: Request transcription
        tx_payload = {"audio_url": upload_url, "speaker_labels": True}
        if language and language.strip() and language.lower() != "auto":
            tx_payload["language_code"] = language.strip().lower()
        else:
            tx_payload["language_detection"] = True

        tx_res = await client.post("https://api.assemblyai.com/v2/transcript", headers=headers, json=tx_payload)
        if tx_res.status_code != 200:
            raise RuntimeError(f"AssemblyAI job creation failed: {tx_res.text[:200]}")
        tx_id = tx_res.json().get("id")

        # Step 3: Poll for completion
        status_url = f"https://api.assemblyai.com/v2/transcript/{tx_id}"
        for _ in range(60):  # Wait up to 2 minutes
            await httpx.AsyncClient().get(status_url)
            poll_res = await client.get(status_url, headers=headers)
            poll_json = poll_res.json()
            curr_status = poll_json.get("status")
            if curr_status == "completed":
                segments = []
                utterances = poll_json.get("utterances") or []
                for idx, u in enumerate(utterances):
                    segments.append({
                        "id": idx,
                        "start": round(u.get("start", 0) / 1000.0, 2),
                        "end": round(u.get("end", 0) / 1000.0, 2),
                        "text": u.get("text", "").strip(),
                        "speaker": f"Speaker {u.get('speaker', '')}"
                    })
                return {
                    "text": poll_json.get("text", "").strip(),
                    "language": poll_json.get("language_code", "en"),
                    "duration": round(float(poll_json.get("audio_duration", 0.0)), 2),
                    "segments": segments,
                    "provider": "assemblyai",
                }
            elif curr_status == "error":
                raise RuntimeError(f"AssemblyAI transcription error: {poll_json.get('error')}")
            time.sleep(2)

        raise TimeoutError("AssemblyAI transcription timed out.")


async def execute_transcription(audio_path: Path, language: Optional[str] = None) -> Dict[str, Any]:
    """
    Selects the best available external transcription provider based on configured API keys,
    executes the transcription, and returns standard structured output.
    """
    provider = (settings.TRANSCRIPTION_PROVIDER or "auto").lower()

    # Determine provider
    chosen = None
    if provider == "groq" and settings.GROQ_API_KEY:
        chosen = "groq"
    elif provider == "openai" and settings.OPENAI_API_KEY:
        chosen = "openai"
    elif provider == "deepgram" and settings.DEEPGRAM_API_KEY:
        chosen = "deepgram"
    elif provider == "assemblyai" and settings.ASSEMBLYAI_API_KEY:
        chosen = "assemblyai"
    elif provider == "auto":
        if settings.GROQ_API_KEY:
            chosen = "groq"
        elif settings.OPENAI_API_KEY:
            chosen = "openai"
        elif settings.DEEPGRAM_API_KEY:
            chosen = "deepgram"
        elif settings.ASSEMBLYAI_API_KEY:
            chosen = "assemblyai"

    if chosen == "groq":
        return await transcribe_with_groq(audio_path, language)
    elif chosen == "openai":
        return await transcribe_with_openai(audio_path, language)
    elif chosen == "deepgram":
        return await transcribe_with_deepgram(audio_path, language)
    elif chosen == "assemblyai":
        return await transcribe_with_assemblyai(audio_path, language)

    # If no external API key is provided, return an explicit informative configuration message
    raise ValueError(
        "No external transcription API key is configured. "
        "Please configure GROQ_API_KEY, OPENAI_API_KEY, DEEPGRAM_API_KEY, or ASSEMBLYAI_API_KEY "
        "in your backend environment (.env file). Botkeep free tier (2 GB RAM) requires external APIs "
        "to prevent memory exhaustion."
    )


async def translate_text(text: str, target_language: str, source_language: str = "auto") -> str:
    """
    Translates transcript text into the target language using Groq ultra-fast LLM or OpenAI.
    """
    if not text or not text.strip():
        return ""

    if settings.GROQ_API_KEY:
        headers = {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        prompt = (
            f"You are a professional audio translator. Translate the following text accurately into {target_language}. "
            "Preserve tone, names, and punctuation. Return ONLY the translated text without commentary or preamble."
        )
        payload = {
            "model": "qwen/qwen3.8-27b",
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text}
            ],
            "temperature": 0.2
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    return data["choices"][0]["message"]["content"].strip()
                # Fallback model if qwen is busy
                payload["model"] = "openai/gpt-oss-20b"
                res2 = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                if res2.status_code == 200:
                    return res2.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"Groq translation error: {e}")

    # Fallback to OpenAI if configured
    if settings.OPENAI_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": f"Translate into {target_language}. Return ONLY translated text."},
                            {"role": "user", "content": text}
                        ]
                    }
                )
                if res.status_code == 200:
                    return res.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"OpenAI translation error: {e}")

    return text
