from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import version

HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{port}/api/health", timeout=1
        ) as response:
            import json

            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _free_port(start: int) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free local port found")


def _open_browser_when_ready(url: str) -> None:
    for _ in range(80):
        if _health(int(url.rsplit(":", 1)[1])):
            try:
                webbrowser.open(url)
            except Exception:
                pass
            return
        time.sleep(0.25)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ComfyUI Model Migrator")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    if os.name == "nt":
        try:
            os.system("title ComfyUI Model Migrator v" + version.__version__)
        except Exception:
            pass

    existing = _health(args.port)
    if existing and existing.get("status") == "ok":
        url = f"http://{HOST}:{args.port}"
        print(f"Already running: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    port = _free_port(args.port)
    url = f"http://{HOST}:{port}"
    print("=" * 58)
    print(f"  ComfyUI Model Migrator v{version.__version__}")
    print(f"  {url}")
    print("  Keep this window open. Press Ctrl+C to stop.")
    print("=" * 58)

    if not args.no_browser:
        threading.Thread(
            target=_open_browser_when_ready, args=(url,), daemon=True
        ).start()

    try:
        import uvicorn

        from main import app

        uvicorn.run(
            app,
            host=HOST,
            port=port,
            log_level="info",
            access_log=False,
        )
    except KeyboardInterrupt:
        return 0
    except Exception:
        traceback.print_exc()
        if getattr(sys, "frozen", False):
            try:
                input("Startup failed. Press Enter to exit...")
            except EOFError:
                pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
