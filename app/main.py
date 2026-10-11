import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routes.health import router as health_router
from app.routes.capabilities import router as capabilities_router
from app.routes.metadata import router as metadata_router
from app.routes.downloads import router as downloads_router
from app.services.job_manager import job_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("video_downloader")


async def periodic_cleanup():
    """Background task running every 60 seconds to clean expired downloads and files."""
    while True:
        try:
            await asyncio.sleep(60)
            job_manager.cleanup_expired()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic cleanup worker: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure temporary directory exists
    temp_dir = settings.temp_dir_path
    logger.info(f"Initialized temporary downloads directory at: {temp_dir}")
    logger.info(f"CORS allowed origins: {settings.allowed_origins}")

    # Start cleanup task
    cleanup_task = asyncio.create_task(periodic_cleanup())
    yield
    # Cancel cleanup task on shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Application shutdown complete.")


app = FastAPI(
    title="Video Downloader API",
    description="High-performance, secure media downloader backend powered by FastAPI and yt-dlp.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Configure CORS
origins = settings.allowed_origins
allow_credentials = False if "*" in origins else True

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Length", "Accept-Ranges"]
)

# Include Routers
app.include_router(health_router)
app.include_router(capabilities_router)
app.include_router(metadata_router)
app.include_router(downloads_router)


@app.get("/")
def root():
    return {
        "service": "Video Downloader API",
        "status": "online",
        "documentation": "/docs",
        "health": "/healthz",
        "capabilities": "/api/capabilities"
    }
