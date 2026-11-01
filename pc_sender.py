import argparse
import threading
import time
import webbrowser
from pathlib import Path

import httpx
import uvicorn

from companion.sender import create_sender_app
from companion.paths import runtime_root


def main():
    parser = argparse.ArgumentParser(description="PC-Sender für Stream Companion")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=runtime_root() / "sender-data")
    args = parser.parse_args()
    app = create_sender_app(args.data_dir)
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        def open_ready():
            for _ in range(60):
                try:
                    response = httpx.get(url + "/api/status", headers={"Authorization": "Bearer " + app.state.store.access_key}, timeout=1)
                    if response.status_code == 200:
                        webbrowser.open(url + "/#access=" + app.state.store.access_key)
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
        threading.Thread(target=open_ready, daemon=True).start()
    print("PC-Sender startet auf " + url)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning"))
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    server.run()


if __name__ == "__main__":
    main()
