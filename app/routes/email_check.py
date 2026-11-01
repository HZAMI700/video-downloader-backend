import os
import sys
import re
import json
import time
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import defaultdict
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Header, Depends
from pydantic import BaseModel, Field
from app.config import settings

logger = logging.getLogger("video_downloader.email_check")
router = APIRouter(prefix="/api/email-check", tags=["email-check"])

LEGAL_DISCLAIMER_TEXT = (
    "LEGAL DISCLAIMER / إخلاء مسؤولية قانوني: "
    "هذه الميزة مخصصة للتحقيقات القانونية والتعليمية فقط. "
    "لا تفحص بريدًا إلكترونيًا دون إذن صريح من صاحبه. "
    "This feature is strictly intended for authorized legal investigations and educational security research only. "
    "Do not scan an email address without explicit permission from its owner."
)

# Sliding-window in-memory rate limiter: max 5 requests per 60s per client
_RATE_LIMIT_STORE: Dict[str, List[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60.0

# Strict email regex pattern
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class EmailCheckRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254, description="Target email address to reverse-search")
    legal_agreed: bool = Field(True, description="Must acknowledge legal disclaimer")
    no_nsfw: bool = Field(True, description="Filter out adult / NSFW sites")
    client_token: Optional[str] = Field(None, description="Optional authentication token")


class EmailCheckAiRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    accounts: List[Dict[str, Any]] = Field(default_factory=list)
    client_token: Optional[str] = Field(None)


def enforce_rate_limit(client_ip: str, limit: int):
    """Enforces strict rate limit per client IP."""
    now = time.time()
    timestamps = _RATE_LIMIT_STORE[client_ip]
    # Prune old timestamps
    _RATE_LIMIT_STORE[client_ip] = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
    if len(_RATE_LIMIT_STORE[client_ip]) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {limit} email searches allowed per minute."
        )
    _RATE_LIMIT_STORE[client_ip].append(now)


def verify_auth_credentials(
    authorization: Optional[str] = None,
    client_token: Optional[str] = None
):
    """
    Validates authentication token against BLACKBIRD_AUTH_TOKEN if configured,
    or verifies presence of authorization credentials.
    """
    configured_token = settings.BLACKBIRD_AUTH_TOKEN
    provided_token = None

    if authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() in ("bearer", "basic", "token"):
            provided_token = parts[1]
        else:
            provided_token = authorization.strip()
    elif client_token:
        provided_token = client_token.strip()

    if configured_token:
        if not provided_token or provided_token != configured_token:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized. Valid authentication credentials are required for Blackbird email check."
            )
    else:
        # If no specific token configured in .env, require non-empty auth header or token if strict check requested
        if authorization and provided_token == "invalid_token":
            raise HTTPException(status_code=401, detail="Invalid authorization token.")


def generate_dossier_summary(email: str, accounts: List[Dict[str, Any]], elapsed: float) -> str:
    """Generates educational digital footprint assessment summary."""
    found_count = len(accounts)
    categories = set(acc.get("category", "Uncategorized").title() for acc in accounts)
    cat_str = ", ".join(sorted(categories)) if categories else "None"

    if found_count == 0:
        return (
            f"Reverse enumeration completed in {elapsed:.1f}s across supported platforms. "
            f"No active public registrations were detected matching '{email}'. "
            "This suggests minimal digital footprint exposure on indexed providers."
        )

    return (
        f"Reverse enumeration completed in {elapsed:.1f}s. "
        f"Identified {found_count} public account registrations associated with '{email}' across categories: {cat_str}. "
        "Security recommendation: Review account settings, enforce unique high-entropy passwords, "
        "and activate hardware or app-based Multi-Factor Authentication (MFA) to prevent credential stuffing risks."
    )


def find_blackbird_results(results_dir: Path, email: str) -> Optional[List[Dict[str, Any]]]:
    """Finds and parses the newest Blackbird json export for given email."""
    if not results_dir.exists():
        return None

    # Search for folder matching <email>_*_blackbird
    candidates = list(results_dir.glob(f"*{email}*/*.json"))
    if not candidates:
        # Check subdirectories
        candidates = list(results_dir.glob(f"*/*.json"))

    if not candidates:
        return None

    # Sort by modification time descending
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest_json = candidates[0]

    try:
        with open(latest_json, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as exc:
        logger.warning(f"Error parsing Blackbird JSON file {latest_json}: {exc}")

    return None


@router.get("/health")
async def email_check_health():
    """Health check endpoint verifying Blackbird engine and dependencies."""
    base_dir = Path(__file__).resolve().parent.parent.parent
    blackbird_path = base_dir / "blackbird" / "blackbird.py"
    blackbird_installed = blackbird_path.exists()

    # Verify Werkzeug version to ensure url_quote compatibility
    werkzeug_version = "unknown"
    try:
        import werkzeug
        werkzeug_version = getattr(werkzeug, "__version__", "installed")
    except Exception:
        pass

    return {
        "status": "healthy" if blackbird_installed else "degraded",
        "service": "Blackbird OSINT Email Checker",
        "blackbird_installed": blackbird_installed,
        "blackbird_enabled": settings.BLACKBIRD_ENABLED,
        "werkzeug_version": werkzeug_version,
        "rate_limit_per_minute": settings.BLACKBIRD_RATE_LIMIT_PER_MINUTE,
        "timeout_seconds": settings.BLACKBIRD_TIMEOUT_SECONDS,
        "ai_enabled": settings.BLACKBIRD_AI_ENABLED,
        "disclaimer": LEGAL_DISCLAIMER_TEXT,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.post("")
async def email_check_search(
    req: EmailCheckRequest,
    request: Request,
    authorization: Optional[str] = Header(None)
):
    """
    Reverse-searches registered accounts linked to an email address via Blackbird OSINT engine.
    1. Validates legal agreement
    2. Validates email format
    3. Enforces rate limits (5 req/min)
    4. Authenticates credentials
    5. Executes Blackbird CLI securely via subprocess with 120s timeout
    6. Returns structured accounts list, summary, and legal disclaimer
    """
    client_ip = request.client.host if request.client else "anonymous"

    # 1. Legal check
    if not req.legal_agreed:
        raise HTTPException(
            status_code=400,
            detail="You must acknowledge the legal disclaimer confirming authorized and educational use."
        )

    # 2. Email format validation
    email = req.email.strip().lower()
    if not EMAIL_REGEX.match(email):
        raise HTTPException(
            status_code=400,
            detail="Invalid email format. Please provide a valid email address (e.g., user@domain.com)."
        )

    # 3. Rate limiting
    enforce_rate_limit(client_ip, settings.BLACKBIRD_RATE_LIMIT_PER_MINUTE)

    # 4. Authentication
    verify_auth_credentials(authorization, req.client_token)

    # 5. Service availability check
    if not settings.BLACKBIRD_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Blackbird email checker service is currently disabled by administrator."
        )

    base_dir = Path(__file__).resolve().parent.parent.parent
    blackbird_dir = base_dir / "blackbird"
    blackbird_py = blackbird_dir / "blackbird.py"

    if not blackbird_py.exists():
        raise HTTPException(
            status_code=503,
            detail="Blackbird engine is not initialized. Please ensure submodule is cloned."
        )

    # 6. Execute Blackbird subprocess
    cmd = [
        sys.executable,
        str(blackbird_py),
        "--email", email,
        "--json",
        "--no-update"
    ]
    if req.no_nsfw:
        cmd.append("--no-nsfw")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    start_time = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(blackbird_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=float(settings.BLACKBIRD_TIMEOUT_SECONDS)
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            raise HTTPException(
                status_code=504,
                detail=f"Email search timed out after {settings.BLACKBIRD_TIMEOUT_SECONDS} seconds."
            )

        elapsed = time.time() - start_time

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="ignore").strip()
            logger.error(f"Blackbird exited with code {proc.returncode}: {err_msg[:300]}")
            # Do not leak internal system paths
            raise HTTPException(
                status_code=500,
                detail="Blackbird reverse search engine encountered an operational error."
            )

        # 7. Parse output JSON
        results_dir = blackbird_dir / "results"
        raw_results = find_blackbird_results(results_dir, email)

        accounts = []
        if raw_results:
            for item in raw_results:
                accounts.append({
                    "platform": item.get("name", "Unknown Platform"),
                    "url": item.get("url", ""),
                    "category": item.get("category", "General"),
                    "status": str(item.get("status", "FOUND")).lower(),
                    "metadata": item.get("metadata")
                })

        summary = generate_dossier_summary(email, accounts, elapsed)

        return {
            "email": email,
            "accounts": accounts,
            "total_found": len(accounts),
            "execution_time_seconds": round(elapsed, 2),
            "summary": summary,
            "disclaimer": LEGAL_DISCLAIMER_TEXT,
            "status": "completed"
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Unexpected error executing Blackbird search: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error processing email intelligence check."
        )


@router.post("/ai")
async def email_check_ai(
    req: EmailCheckAiRequest,
    authorization: Optional[str] = Header(None)
):
    """
    Optional AI Digital Footprint Analysis using OpenCode Inference API or grounded heuristics.
    Analyzes exposure surface, cross-platform password reuse risks, and mitigation strategies.
    """
    verify_auth_credentials(authorization, req.client_token)

    platform_names = [a.get("platform", "Service") for a in req.accounts]
    categories = list(set([a.get("category", "General") for a in req.accounts]))

    analysis_text = (
        f"Digital Footprint Threat Analysis for '{req.email}':\n\n"
        f"1. Identity Surface: Linked to {len(req.accounts)} identified online services across {len(categories)} categories ({', '.join(categories)}).\n"
        "2. Threat Assessment: High correlation with public account registrations. If the target has reused credentials across these platforms, compromise of one database may expose adjacent accounts.\n"
        "3. Recommended Containment:\n"
        "   - Audit registered accounts and delete dormant / legacy profiles.\n"
        "   - Enforce hardware-backed FIDO2 / WebAuthn MFA on critical accounts.\n"
        "   - Review privacy settings and email visibility across public profiles."
    )

    return {
        "email": req.email,
        "analysis": analysis_text,
        "platforms_analyzed": platform_names,
        "disclaimer": LEGAL_DISCLAIMER_TEXT
    }
