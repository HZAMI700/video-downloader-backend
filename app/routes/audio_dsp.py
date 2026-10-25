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


@router.post("/convert")
async def convert_media_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    target_format: str = Form("mp3"),  # "mp3", "wav", "m4a", "aac"
    bitrate: str = Form("320k"),        # "320k", "256k", "128k"
):
    """
    Convert any uploaded audio or video file (MP4, MOV, MKV, WebM, etc.) to pure MP3 or WAV audio.
    """
    raw_filename = file.filename or "media_file"
    sanitized = sanitize_filename(raw_filename)
    stem = Path(sanitized).stem
    ext = Path(sanitized).suffix.lower()

    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format '{ext}'.")

    temp_dir = settings.temp_dir_path
    uid = uuid.uuid4().hex[:8]
    input_path = temp_dir / f"conv_in_{uid}_{sanitized}"

    clean_fmt = target_format.lower().replace(".", "")
    if clean_fmt not in {"mp3", "wav", "m4a", "aac", "flac", "mp4"}:
        clean_fmt = "mp3"

    output_path = temp_dir / f"converted_{uid}_{stem}.{clean_fmt}"

    try:
        with open(input_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        if clean_fmt == "wav":
            cmd = ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-c:a", "pcm_s16le", str(output_path)]
            media_type = "audio/wav"
        elif clean_fmt == "mp4":
            cmd = ["ffmpeg", "-y", "-i", str(input_path), "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_path)]
            media_type = "video/mp4"
        elif clean_fmt == "m4a":
            cmd = ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-c:a", "aac", "-b:a", "192k", str(output_path)]
            media_type = "audio/mp4"
        else:
            cmd = ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-c:a", "libmp3lame", "-b:a", bitrate, str(output_path)]
            media_type = "audio/mpeg"

        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        if proc.returncode != 0 or not output_path.exists():
            logger.error(f"FFmpeg conversion failed: {proc.stderr.decode('utf-8', errors='ignore')}")
            raise HTTPException(status_code=500, detail="Media conversion failed.")

    finally:
        remove_temp_file(input_path)

    background_tasks.add_task(remove_temp_file, output_path)
    return FileResponse(
        path=output_path,
        media_type=media_type,
        filename=f"{stem}.{clean_fmt}"
    )


class TtsRequest(BaseModel):
    text: str
    lang: Optional[str] = "en"
    speed: Optional[float] = 1.0
    pitch: Optional[float] = 1.0
    voice_id: Optional[str] = None


@router.post("/tts")
async def generate_speech(req: TtsRequest, background_tasks: BackgroundTasks):
    """
    Playzy AI Speech & Voice Generator.
    Converts text to realistic studio MP3 audio using neural TTS synthesis.
    """
    clean_text = req.text.strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    clean_lang = (req.lang or "en").split("-")[0].lower()
    temp_dir = settings.temp_dir_path
    uid = uuid.uuid4().hex[:8]
    raw_tts_path = temp_dir / f"tts_raw_{uid}.mp3"
    final_output_path = temp_dir / f"playzy_speech_{uid}.mp3"

    try:
        from gtts import gTTS
        try:
            tts = gTTS(text=clean_text[:3000], lang=clean_lang, slow=False)
            tts.save(str(raw_tts_path))
        except Exception as e:
            logger.warning(f"gTTS with lang '{clean_lang}' failed: {e}. Falling back to 'en'.")
            tts = gTTS(text=clean_text[:3000], lang="en", slow=False)
            tts.save(str(raw_tts_path))

        speed = max(0.5, min(2.0, float(req.speed or 1.0)))
        user_pitch = max(0.5, min(1.5, float(req.pitch or 1.0)))

        # Voice Profile Timbre Tuning based on voice_id
        voice_id_clean = (req.voice_id or "").lower()
        is_deep_male = any(name in voice_id_clean for name in ["adam", "marcus", "george", "charlie", "daniel", "clyde", "callum", "male", "deep"])
        is_bright_female = any(name in voice_id_clean for name in ["rachel", "charlotte", "emily", "sophia", "bella", "freya", "elli", "female", "serena"])

        base_voice_pitch = 1.0
        if is_deep_male:
            base_voice_pitch = 0.88
        elif is_bright_female:
            base_voice_pitch = 1.16

        effective_pitch = user_pitch * base_voice_pitch

        if check_ffmpeg_installed():
            audio_filters = []
            if abs(effective_pitch - 1.0) > 0.03:
                sample_rate = int(24000 * effective_pitch)
                audio_filters.append(f"asetrate={sample_rate},aresample=24000")
            if abs(speed - 1.0) > 0.03:
                audio_filters.append(f"atempo={speed}")
            if is_deep_male:
                audio_filters.append("equalizer=f=120:t=q:w=1:g=3.5")
            elif is_bright_female:
                audio_filters.append("equalizer=f=3200:t=q:w=1:g=2.8")

            if audio_filters:
                filter_str = ",".join(audio_filters)
                cmd = ["ffmpeg", "-y", "-i", str(raw_tts_path), "-af", filter_str, "-b:a", "192k", str(final_output_path)]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
                if proc.returncode == 0 and final_output_path.exists():
                    chosen_path = final_output_path
                else:
                    chosen_path = raw_tts_path
            else:
                chosen_path = raw_tts_path
        else:
            chosen_path = raw_tts_path

    except Exception as err:
        logger.error(f"Speech generation error: {err}")
        remove_temp_file(raw_tts_path)
        remove_temp_file(final_output_path)
        raise HTTPException(status_code=500, detail="Failed to synthesize speech.")

    background_tasks.add_task(remove_temp_file, raw_tts_path)
    background_tasks.add_task(remove_temp_file, final_output_path)

    return FileResponse(
        path=chosen_path,
        media_type="audio/mpeg",
        filename="playzy_speech.mp3",
        headers={"Content-Disposition": 'inline; filename="playzy_speech.mp3"'}
    )

