import logging
import time
import uuid
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from app.config import settings

logger = logging.getLogger("video_downloader.access")

# Max requests per window per IP
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60


class SecurityAndRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.request_history = defaultdict(list)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate or capture unique request ID
        req_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:10]}"
        request.state.request_id = req_id
        start_time = time.time()
        client_ip = request.client.host if request.client else "anonymous"

        # Bypass rate limiter for health check and CORS preflights
        if request.url.path == "/healthz" or request.method == "OPTIONS":
            response = await call_next(request)
            self._apply_security_headers(response, req_id)
            return response

        # Enforce max content length on request body
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                cl_int = int(content_length)
                if request.url.path.startswith("/api/transcribe/upload"):
                    max_allowed = settings.MAX_TRANSCRIPTION_FILE_SIZE_MB * 1024 * 1024
                    limit_desc = f"{settings.MAX_TRANSCRIPTION_FILE_SIZE_MB} MB"
                else:
                    max_allowed = 10 * 1024 * 1024  # 10 MB for metadata / json
                    limit_desc = "10 MB"

                if cl_int > max_allowed:
                    logger.warning(f"[{req_id}] Payload too large ({content_length} bytes) on {request.url.path} from {client_ip}")
                    response = JSONResponse(
                        status_code=413,
                        content={"detail": f"Payload too large. Maximum allowed size is {limit_desc}."}
                    )
                    self._apply_security_headers(response, req_id)
                    return response
            except (ValueError, TypeError):
                pass

        # Rate Limiting
        now = time.time()

        # Clean timestamps older than window
        timestamps = self.request_history[client_ip]
        while timestamps and timestamps[0] <= now - RATE_LIMIT_WINDOW_SECONDS:
            timestamps.pop(0)

        if len(timestamps) >= RATE_LIMIT_REQUESTS:
            logger.warning(f"[{req_id}] Rate limit exceeded for IP {client_ip}")
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Too many requests, please slow down."}
            )
            self._apply_security_headers(response, req_id)
            return response

        timestamps.append(now)

        # Process request
        try:
            response = await call_next(request)
        except Exception as e:
            elapsed_ms = round((time.time() - start_time) * 1000, 1)
            logger.error(f"[{req_id}] Unhandled error on {request.method} {request.url.path} after {elapsed_ms}ms: {e}")
            raise e

        elapsed_ms = round((time.time() - start_time) * 1000, 1)
        logger.info(f"[{req_id}] {request.method} {request.url.path} -> {response.status_code} ({elapsed_ms}ms) IP={client_ip}")

        # Apply standard security headers
        self._apply_security_headers(response, req_id)
        return response

    def _apply_security_headers(self, response: Response, req_id: str):
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

