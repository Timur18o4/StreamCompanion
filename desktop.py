"""Native portable entrypoint: python desktop.py, or bundled StreamCompanion.exe."""
import os
import sys
from pathlib import Path


def main():
    if "--streamlink-worker" in sys.argv:
        sys.argv.remove("--streamlink-worker")
        # CLI plugins are imported dynamically, so the build collects them explicitly.
        from streamlink_cli.main import main as worker
        worker()
        return
    sender = "--sender" in sys.argv
    if sender:
        sys.argv.remove("--sender")
    if "--stop" in sys.argv:
        import urllib.request
        import argparse
        from companion.paths import runtime_root
        sys.argv.remove("--stop")
        parser = argparse.ArgumentParser()
        parser.add_argument("--port",type=int)
        args = parser.parse_args()
        port = args.port or (8790 if sender else 8787)
        key = (runtime_root() / ("sender-data" if sender else "data") / "access.key").read_text().strip()
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown",data=b"{}",method="POST",headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
        with urllib.request.urlopen(request,timeout=20):
            pass
        return
    if sender:
        from pc_sender import main as run
    else:
        from run import main as run
    run()


if __name__ == "__main__":
    # GUI executables have no console stream. A log is useful for startup failures.
    if getattr(sys,"frozen",False) and "--streamlink-worker" not in sys.argv:
        root = Path(sys.executable).resolve().parent
        folder = root / ("sender-data" if "--sender" in sys.argv else "data")
        folder.mkdir(exist_ok=True)
        sys.stdout = (folder / "runtime.log").open("a",encoding="utf-8",buffering=1)
        sys.stderr = sys.stdout
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        if getattr(sys,"frozen",False) and os.name=="nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,"Stream Companion konnte nicht gestartet werden. Details stehen in data/runtime.log bzw. sender-data/runtime.log.","Stream Companion",0x10)
        else:
            raise
