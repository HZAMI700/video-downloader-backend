import os
import subprocess
import uvicorn
from app.config import settings

if __name__ == "__main__":
    # Attempt to pull latest changes on startup so Botkeep restarts automatically stay up to date
    try:
        print("Checking for latest repository updates...")
        subprocess.run(["git", "pull", "origin", "main"], timeout=20)
    except Exception as e:
        print(f"Git pull on startup skipped: {e}")

    # Botkeep supplies SERVER_PORT; PORT is standard PaaS fallback; default is 8000
    port_env = os.getenv("SERVER_PORT", os.getenv("PORT", "8000"))
    try:
        port = int(port_env)
    except ValueError:
        port = 8000

    host = os.getenv("HOST", "0.0.0.0")

    print(f"Starting Video Downloader Backend on {host}:{port}...")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
