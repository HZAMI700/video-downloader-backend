from typing import List, Optional
from fastapi import APIRouter
from pydantic import BaseModel, HttpUrl
from app.security import validate_media_url
from app.services.ytdlp_service import extract_media_metadata

router = APIRouter(prefix="/api", tags=["Metadata"])


class MetadataRequest(BaseModel):
    url: str


class FormatOptionSchema(BaseModel):
    format_id: str
    type: str  # video or audio
    label: str
    resolution: str
    ext: str
    quality_note: Optional[str] = None
    requires_ffmpeg: bool = False
    available: bool = True
    approx_size_bytes: Optional[int] = None


class MetadataResponse(BaseModel):
    id: str
    title: str
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    duration_formatted: Optional[str] = None
    uploader: Optional[str] = None
    view_count: Optional[int] = None
    platform: str
    original_url: str
    formats: List[FormatOptionSchema]
    ffmpeg_available: bool


@router.post("/metadata", response_model=MetadataResponse)
def get_metadata(payload: MetadataRequest):
    """
    Validate URL and retrieve video information and available formats without downloading.
    """
    valid_url = validate_media_url(payload.url)
    data = extract_media_metadata(valid_url)
    return MetadataResponse(**data)
