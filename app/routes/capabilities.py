from typing import List, Dict, Any
from fastapi import APIRouter
from pydantic import BaseModel
import yt_dlp
from app.config import settings
from app.services.ytdlp_service import check_ffmpeg_installed

router = APIRouter(prefix="/api", tags=["Capabilities"])


class CapabilitiesFeatures(BaseModel):
    audio_extraction: bool
    video_muxing: bool
    direct_formats: bool
    resumable_downloads: bool
    transcription: bool = True


class CapabilitiesResponse(BaseModel):
    backend: str
    ytdlp_version: str
    ffmpeg_available: bool
    max_file_size_mb: int
    supported_extractors: List[str]
    features: CapabilitiesFeatures
    notes: str


@router.get("/capabilities", response_model=CapabilitiesResponse)
def get_capabilities():
    """
    Returns server capabilities, FFmpeg availability, and download constraints.
    """
    ffmpeg_ok = check_ffmpeg_installed()

    return CapabilitiesResponse(
        backend="ready",
        ytdlp_version=getattr(yt_dlp.version, "__version__", "unknown"),
        ffmpeg_available=ffmpeg_ok,
        max_file_size_mb=settings.MAX_FILE_SIZE_MB,
        supported_extractors=[
            "YouTube",
            "TikTok",
            "Instagram",
            "Pinterest",
            "X (Twitter)",
            "Facebook",
            "Reddit",
            "Vimeo",
            "SoundCloud",
            "Twitch",
            "Generic MP4/WebMedia"
        ],
        features=CapabilitiesFeatures(
            audio_extraction=ffmpeg_ok,
            video_muxing=ffmpeg_ok,
            direct_formats=True,
            resumable_downloads=False,
            transcription=True,
        ),
        notes=(
            "FFmpeg is available: full high-definition muxing and MP3 conversion enabled."
            if ffmpeg_ok
            else "FFmpeg not detected: direct pre-muxed video formats and native audio streams are served."
        )
    )
