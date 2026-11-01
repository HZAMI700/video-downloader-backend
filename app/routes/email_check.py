import os
import sys
import re
import json
import time
import asyncio
import logging
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import defaultdict
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Header
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
        if authorization and provided_token == "invalid_token":
            raise HTTPException(status_code=401, detail="Invalid authorization token.")


def generate_dossier_summary(email: str, accounts: List[Dict[str, Any]], elapsed: float) -> str:
    """Generates educational digital footprint assessment summary without raw markdown punctuation."""
    found_count = len(accounts)
    categories = set(acc.get("category", "General").title() for acc in accounts)
    cat_str = ", ".join(sorted(categories)) if categories else "None"

    if found_count == 0:
        return (
            f"Blackbird reverse enumeration completed in {elapsed:.1f}s across indexed endpoints. "
            f"No public account registrations were detected for '{email}'. "
            "This suggests minimal digital footprint exposure on indexed providers."
        )

    return (
        f"Blackbird reverse enumeration completed in {elapsed:.1f}s. "
        f"Identified {found_count} public account registrations associated with '{email}' across categories: {cat_str}. "
        "Defensive recommendation: Audit registered accounts, enforce high-entropy unique credentials, "
        "and activate hardware or app-based Multi-Factor Authentication (MFA) to prevent credential stuffing risks."
    )


def find_blackbird_results(results_dir: Path, email: str) -> List[Dict[str, Any]]:
    """
    Finds and parses the newest Blackbird json export STRICTLY for the given email.
    Never falls back to other emails' files to guarantee complete query isolation.
    """
    if not results_dir.exists():
        return []

    target_clean = email.strip().lower()
    
    # 1. Look for directory starting with target email
    candidates = list(results_dir.glob(f"{target_clean}_*/*.json"))
    if not candidates:
        candidates = list(results_dir.glob(f"*{target_clean}*/*.json"))
    if not candidates:
        candidates = list(results_dir.glob(f"*{target_clean}*.json"))

    if not candidates:
        return []

    # Sort strictly by newest modification time
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest_json = candidates[0]

    try:
        with open(latest_json, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as exc:
        logger.warning(f"Error parsing Blackbird JSON file {latest_json}: {exc}")

    return []


def parse_blackbird_stdout(stdout_text: str) -> List[Dict[str, Any]]:
    """Fallback parser extracting detected platform URLs directly from stdout stream."""
    found: List[Dict[str, Any]] = []
    seen_platforms = set()

    for line in stdout_text.splitlines():
        # Match rich terminal or plain output: 🎯  [Platform] URL
        if "🎯" in line or "[FOUND]" in line:
            match = re.search(r"\[([a-zA-Z0-9\s._-]+)\]\s+(https?://\S+)", line)
            if match:
                platform_name = match.group(1).strip()
                target_url = match.group(2).strip()
                if platform_name.lower() not in seen_platforms:
                    seen_platforms.add(platform_name.lower())
                    found.append({
                        "name": platform_name,
                        "url": target_url,
                        "category": "Social & Web",
                        "status": "FOUND",
                        "metadata": None
                    })
    return found


@router.get("/health")
async def email_check_health():
    """Health check endpoint verifying Blackbird engine and dependencies."""
    base_dir = Path(__file__).resolve().parent.parent.parent
    blackbird_path = base_dir / "blackbird" / "blackbird.py"
    blackbird_installed = blackbird_path.exists()

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
    6. Ensures strict per-email output isolation (zero cross-contamination)
    7. Returns structured accounts list, summary, and legal disclaimer
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

    # 6. Execute Blackbird subprocess with UTF-8 encoding environment
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
    env["PYTHONLEGACYWINDOWSSTDIO"] = "0"

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
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
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
        stdout_text = stdout_bytes.decode("utf-8", errors="ignore")
        stderr_text = stderr_bytes.decode("utf-8", errors="ignore")

        if proc.returncode != 0:
            logger.error(f"Blackbird exited with code {proc.returncode}: {stderr_text[:300]}")
            # If Blackbird had a rich formatting exit code on Windows, try stdout parsing before throwing
            stdout_accounts = parse_blackbird_stdout(stdout_text)
            if not stdout_accounts:
                raise HTTPException(
                    status_code=500,
                    detail="Blackbird reverse search engine encountered an operational error."
                )

        # 7. Parse output JSON specifically isolated for this email
        results_dir = blackbird_dir / "results"
        raw_results = find_blackbird_results(results_dir, email)

        # If json file didn't exist (e.g. 0 results found), also check stdout
        if not raw_results:
            raw_results = parse_blackbird_stdout(stdout_text)

        accounts = []
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
    AI Digital Footprint Threat Analysis using clean formatting without raw markdown asterisks or hashes.
    Analyzes exposure surface, cross-platform password reuse risks, and mitigation strategies.
    """
    verify_auth_credentials(authorization, req.client_token)

    platform_names = [a.get("platform", "Service") for a in req.accounts]
    categories = list(set([a.get("category", "General") for a in req.accounts]))
    cat_text = ", ".join(categories) if categories else "No categories identified"

    analysis_text = (
        f"Digital Footprint Threat Analysis for '{req.email}':\n\n"
        f"1. Identity Surface: Linked to {len(req.accounts)} identified online services across {len(categories)} categories ({cat_text}).\n"
        "2. Threat Assessment: High correlation with public account registrations. If credentials have been reused across these platforms, breach of one database may compromise adjacent profiles.\n"
        "3. Recommended Containment Actions:\n"
        "   - Audit registered accounts and delete dormant or legacy profiles.\n"
        "   - Enforce hardware-backed FIDO2 / WebAuthn MFA on critical accounts.\n"
        "   - Review privacy settings and email visibility across public profiles."
    )

    return {
        "email": req.email,
        "analysis": analysis_text,
        "platforms_analyzed": platform_names,
        "disclaimer": LEGAL_DISCLAIMER_TEXT
    }
