import os
import tempfile
import pytest
from starlette.testclient import TestClient

# Ensure test environment
os.environ["TEMP_DOWNLOAD_DIR"] = tempfile.mkdtemp(prefix="test_ytdl_")
os.environ["SERVER_PORT"] = "8000"
os.environ["FRONTEND_ORIGINS"] = "http://localhost:3000"

from app.main import app
from app.services.job_manager import job_manager


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_jobs():
    """Clear memory jobs between tests."""
    with job_manager._lock:
        job_manager._jobs.clear()
    yield
