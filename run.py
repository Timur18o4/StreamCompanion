import argparse
import socket
import threading
import time
import webbrowser
from pathlib import Path

import httpx
import uvicorn

from companion.app import create_app
from companion.paths import runtime_root


def main():
    parser = argparse.ArgumentParser(description="Stream Companion starten")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data-dir", type=Path, default=runtime_root() / "data")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    app = create_app(args.data_dir)
    local = f"http://127.0.0.1:{args.port}"
    url = local + "/#access=" + app.state.store.access_key
    try:
        with socket.socket() as probe:
            probe.bind((args.host, args.port))
    except OSError:
        try:
            response = httpx.get(local + "/api/status", headers={"Authorization": "Bearer " + app.state.store.access_key}, timeout=3)
            if response.status_code == 200:
                print("Stream Companion läuft bereits.")
                if not args.no_browser:
                    webbrowser.open(url)
                return
        except httpx.HTTPError:
            pass
        raise SystemExit(f"Port {args.port} ist belegt. Einen anderen Port mit --port wählen.")
    (args.data_dir / "server.pid").write_text(str(__import__("os").getpid()))
    print("Stream Companion startet auf " + local)
    print("Lokaler Zugangscode steht in " + str(args.data_dir / "access.key"))
    if args.host == "0.0.0.0":
        print("LAN-Zugriff aktiv. Im Browser die IP dieses Rechners und den lokalen Zugangscode verwenden.")
    if not args.no_browser:
        def open_ready():
            for _ in range(60):
                try:
                    response = httpx.get(local + "/api/health", timeout=1)
                    if response.status_code == 200 and response.json().get("app") == "stream-companion":
                        webbrowser.open(url)
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(0.5)
        threading.Thread(target=open_ready, daemon=True).start()
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, access_log=False, ws_max_size=5 * 1024 * 1024, log_level="warning"))
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    server.run()


if __name__ == "__main__":
    main()
