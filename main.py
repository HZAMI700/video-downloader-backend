import os
import uvicorn
from app.config import settings

if __name__ == "__main__":
    # Botkeep supplies SERVER_PORT; PORT is standard PaaS fallback; default is 8000
    port_env = os.getenv("SERVER_PORT", os.getenv("PORT", "8000"))
    try:
        port = int(port_env)
    except ValueError:
        port = 8000

    host = os.getenv("HOST", "0.0.0.0")

    print(f"Starting Video Downloader Backend on {host}:{port}...")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
