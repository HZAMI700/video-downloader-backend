import time
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: float


@router.get("/healthz", response_model=HealthResponse)
def get_health():
    """Health check endpoint for Botkeep and monitoring probes."""
    return HealthResponse(
        status="ok",
        service="playzy-backend",
        timestamp=time.time()
    )
