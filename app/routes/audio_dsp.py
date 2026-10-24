import asyncio
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.security import sanitize_filename, is_safe_url
from app.services.ytdlp_service import check_ffmpeg_installed

logger = logging.getLogger("video_downloader.routes.audio_dsp")

router = APIRouter(prefix="/api/audio", tags=["Audio DSP & Voice Tools"])

ALLOWED_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov", ".mkv", ".webm"
}


def remove_temp_file(path: Path):
    try:
        if path.exists():
            path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed to remove temp file {path}: {e}")


@router.post("/boost")
async def boost_volume(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    boost_percent: int = Form(200),  # 100 to 500%
):
    """
    Amplify audio volume cleanly using FFmpeg volume normalization.
    """
    raw_filename = file.filename or "audio.mp3"
    sanitized = sanitize_filename(raw_filename)
    stem = Path(sanitized).stem
    ext = Path(sanitized).suffix.lower()

    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format '{ext}'.")

    temp_dir = settings.temp_dir_path
    uid = uuid.uuid4().hex[:8]
    input_path = temp_dir / f"boost_in_{uid}_{sanitized}"
    output_path = temp_dir / f"boosted_{uid}_{stem}.mp3"

    try:
        with open(input_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        # Convert boost percent to dB (e.g., 200% = +6dB, 300% = +9.5dB, 500% = +14dB)
        boost = max(100, min(500, boost_percent))
        gain_factor = boost / 100.0

        # Run FFmpeg with soft clipping limiter to prevent harsh distortion
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-filter:a", f"volume={gain_factor:.2f},alimiter=limit=0.95:level=disabled",
            "-c:a", "libmp3lame", "-b:a", "320k",
            str(output_path)
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if proc.returncode != 0 or not output_path.exists():
            logger.error(f"FFmpeg volume boost failed: {proc.stderr.decode('utf-8', errors='ignore')}")
            raise HTTPException(status_code=500, detail="Audio volume boost processing failed.")

    finally:
        remove_temp_file(input_path)

    background_tasks.add_task(remove_temp_file, output_path)
    return FileResponse(
        path=output_path,
        media_type="audio/mpeg",
        filename=f"{stem}_boosted_{boost}pct.mp3"
    )


@router.post("/vocal-remover")
async def separate_vocals(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    mode: str = Form("instrumental"),  # 'instrumental' (karaoke) or 'vocals'
):
    """
    Separate vocals or extract karaoke instrumental track from songs.
    """
    raw_filename = file.filename or "song.mp3"
    sanitized = sanitize_filename(raw_filename)
    stem = Path(sanitized).stem
    ext = Path(sanitized).suffix.lower()

    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format '{ext}'.")

    temp_dir = settings.temp_dir_path
    uid = uuid.uuid4().hex[:8]
    input_path = temp_dir / f"vocal_in_{uid}_{sanitized}"
    output_path = temp_dir / f"vocal_out_{uid}_{stem}_{mode}.mp3"

    try:
        with open(input_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        if mode == "vocals":
            # Vocal isolation bandpass & stereo center emphasis
            audio_filter = "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,bandpass=f=1100:width_type=h:w=2600"
        else:
            # Karaoke / Instrumental: phase cancel center-panned lead vocal
            audio_filter = "pan=stereo|c0=c0-c1|c1=c1-c0"

        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-filter:a", audio_filter,
            "-c:a", "libmp3lame", "-b:a", "320k",
            str(output_path)
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if proc.returncode != 0 or not output_path.exists():
            logger.error(f"FFmpeg vocal remover failed: {proc.stderr.decode('utf-8', errors='ignore')}")
            raise HTTPException(status_code=500, detail="Vocal separation failed.")

    finally:
        remove_temp_file(input_path)

    background_tasks.add_task(remove_temp_file, output_path)
    download_name = f"{stem}_instrumental.mp3" if mode != "vocals" else f"{stem}_vocals_only.mp3"
    return FileResponse(
        path=output_path,
        media_type="audio/mpeg",
        filename=download_name
    )


@router.post("/clean")
async def clean_voice(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Remove background hiss, rumble, traffic noise, and breath artifacts for studio-quality voice.
    """
    raw_filename = file.filename or "voice.mp3"
    sanitized = sanitize_filename(raw_filename)
    stem = Path(sanitized).stem
    ext = Path(sanitized).suffix.lower()

    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format '{ext}'.")

    temp_dir = settings.temp_dir_path
    uid = uuid.uuid4().hex[:8]
    input_path = temp_dir / f"clean_in_{uid}_{sanitized}"
    output_path = temp_dir / f"cleaned_{uid}_{stem}.mp3"

    try:
        with open(input_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        # Highpass 80Hz (cuts mic rumble), Lowpass 12kHz, afftdn noise reduction, and compressor
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-filter:a", "highpass=f=80,lowpass=f=12000,afftdn=nf=-24,acompressor=threshold=-18dB:ratio=3:attack=5:release=50",
            "-c:a", "libmp3lame", "-b:a", "256k",
            str(output_path)
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if proc.returncode != 0 or not output_path.exists():
            logger.error(f"FFmpeg audio cleaning failed: {proc.stderr.decode('utf-8', errors='ignore')}")
            raise HTTPException(status_code=500, detail="Audio noise reduction failed.")

    finally:
        remove_temp_file(input_path)

    background_tasks.add_task(remove_temp_file, output_path)
    return FileResponse(
        path=output_path,
        media_type="audio/mpeg",
        filename=f"{stem}_studio_clean.mp3"
    )


@router.post("/voice-change")
async def change_voice(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    preset: str = Form("deep"),  # 'deep', 'chipmunk', 'robot', 'radio'
):
    """
    Transform voice pitch, formant, and frequency for creative voice changer effects.
    """
    raw_filename = file.filename or "voice.mp3"
    sanitized = sanitize_filename(raw_filename)
    stem = Path(sanitized).stem
    ext = Path(sanitized).suffix.lower()

    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format '{ext}'.")

    temp_dir = settings.temp_dir_path
    uid = uuid.uuid4().hex[:8]
    input_path = temp_dir / f"vchange_in_{uid}_{sanitized}"
    output_path = temp_dir / f"vchange_out_{uid}_{stem}_{preset}.mp3"

    try:
        with open(input_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        if preset == "chipmunk":
            audio_filter = "asetrate=44100*1.35,aresample=44100,atempo=1/1.1"
        elif preset == "robot":
            audio_filter = "flanger=delay=10:depth=5:regen=70:width=70:speed=0.5,highpass=f=200,lowpass=f=3500"
        elif preset == "radio":
            audio_filter = "highpass=f=500,lowpass=f=3000,acrusher=bits=10:mode=log"
        else:  # deep
            audio_filter = "asetrate=44100*0.80,aresample=44100,atempo=1.25"

        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-filter:a", audio_filter,
            "-c:a", "libmp3lame", "-b:a", "256k",
            str(output_path)
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if proc.returncode != 0 or not output_path.exists():
            logger.error(f"FFmpeg voice changer failed: {proc.stderr.decode('utf-8', errors='ignore')}")
            raise HTTPException(status_code=500, detail="Voice changer transformation failed.")

    finally:
        remove_temp_file(input_path)

    background_tasks.add_task(remove_temp_file, output_path)
    return FileResponse(
        path=output_path,
        media_type="audio/mpeg",
        filename=f"{stem}_{preset}_voice.mp3"
    )
