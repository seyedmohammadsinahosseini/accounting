"""Local development launcher for Accounting Platform.

Starts the FastAPI ASGI application on an available TCP port (default 8000)
and launches the default browser interface.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import threading
import webbrowser

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def say(text: str = "") -> None:
    print(text, flush=True)


def free_port(start: int = 8000) -> int:
    for port in range(start, start + 20):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def demo_logins() -> list[str]:
    from app import config

    if os.environ.get("ACCT_DEMO_SEED", "1") != "1":
        return []
    if config.DB_PATH.exists() and not (config.DATA_DIR / "DEMO-credentials.txt").is_file():
        return []
    return [
        "admin       ->  admin123       (System Administrator / مدیر سیستم)",
        "accountant  ->  accountant123  (Accountant / حسابدار)",
        "manager     ->  manager123     (Agency Manager / مدیر آژانس)",
        "employee    ->  employee123    (Sales Staff / کارمند فروش)",
        "auditor     ->  auditor123     (Auditor - Read-Only / حسابرس)",
    ]


def main() -> None:
    if sys.version_info < (3, 10):
        say("Python 3.10+ is required to run Accounting Platform.")
        say("Please upgrade your Python installation from https://python.org.")
        return

    port = free_port()
    url = f"http://127.0.0.1:{port}"

    say("")
    say("  ================================================================")
    say("    Accounting Platform - Enterprise Double-Entry Financial Engine")
    say("  ================================================================")
    say(f"  Web Interface  : {url}")
    say(f"  API Docs (UI)  : {url}/docs")
    say(f"  Health Check   : {url}/api/health")
    if port != 8000:
        say(f"  [Notice] Port 8000 was busy; bound to fallback port {port}.")
    say("  ----------------------------------------------------------------")
    say("  Demo Credentials (Development / Evaluation Mode):")
    for line in demo_logins():
        say(f"    {line}")
    say("  ----------------------------------------------------------------")
    say("  Press Ctrl+C to terminate the server.")
    say("")

    # Automatically open browser after 1.5 seconds if running interactively
    if os.environ.get("NO_BROWSER", "0") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    import uvicorn

    try:
        uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    say("Accounting Platform server terminated gracefully.")


if __name__ == "__main__":
    main()
