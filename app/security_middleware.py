import time
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

# Max requests per window per IP
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60


class SecurityAndRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.request_history = defaultdict(list)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Bypass rate limiter for health check and CORS preflights
        if request.url.path == "/healthz" or request.method == "OPTIONS":
            response = await call_next(request)
            self._apply_security_headers(response)
            return response

        # Enforce max content length on request body (1 MB max for metadata/json)
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 1024 * 1024:
            return JSONResponse(
                status_code=413,
                content={"detail": "Payload too large. Maximum allowed size is 1MB."}
            )

        # Rate Limiting
        client_ip = request.client.host if request.client else "anonymous"
        now = time.time()

        # Clean timestamps older than window
        timestamps = self.request_history[client_ip]
        while timestamps and timestamps[0] <= now - RATE_LIMIT_WINDOW_SECONDS:
            timestamps.pop(0)

        if len(timestamps) >= RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Too many requests, please slow down."}
            )

        timestamps.append(now)

        # Process request
        response = await call_next(request)

        # Apply standard security headers
        self._apply_security_headers(response)
        return response

    def _apply_security_headers(self, response: Response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
