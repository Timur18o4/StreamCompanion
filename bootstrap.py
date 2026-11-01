"""First-run setup and native launcher; no Twitch credentials are distributed."""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
import webbrowser
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sender", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--lan", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 oder neuer wird benötigt.")
    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Einmalige Vorbereitung: Python-Umgebung wird angelegt.", flush=True)
        venv.create(environment, with_pip=True)
    fingerprint = hashlib.sha256((root / "requirements.txt").read_bytes() + (root / "requirements-audio.txt").read_bytes()).hexdigest()
    marker = environment / "companion-requirements.sha256"
    if not marker.exists() or marker.read_text() != fingerprint:
        print("Benötigte Pakete werden installiert. Das kann beim ersten Start einige Minuten dauern.", flush=True)
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(root / "requirements-audio.txt")], cwd=root, check=True)
        marker.write_text(fingerprint)
    script = root / ("pc_sender.py" if args.sender else "run.py")
    data = root / ("sender-data" if args.sender else "data")
    port = args.port or (8790 if args.sender else 8787)
    local = f"http://127.0.0.1:{port}"
    command = [str(python), str(script), "--no-browser"]
    if args.port:
        command.extend(["--port", str(port)])
    if args.lan and not args.sender:
        command.extend(["--host", "0.0.0.0"])
    if args.foreground or os.name != "nt":
        if not args.no_browser:
            command.remove("--no-browser")
        raise SystemExit(subprocess.call(command, cwd=root))
    data.mkdir(exist_ok=True)
    # Only reuse an authenticated instance belonging to this data directory.
    keyfile = data / "access.key"
    running = False
    if keyfile.exists():
        try:
            request = urllib.request.Request(local + "/api/status", headers={"Authorization": "Bearer " + keyfile.read_text().strip()})
            with urllib.request.urlopen(request, timeout=2) as response:
                running = response.status == 200
        except (OSError, urllib.error.URLError):
            pass
    process = None
    if not running:
        with socket.socket() as check:
            try:
                check.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Port {port} ist bereits anderweitig belegt. Siehe README für einen anderen Port.")
        with (data / "runtime.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=root, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    print("Stream Companion wird geöffnet …", flush=True)
    for _ in range(90):
        try:
            if keyfile.exists():
                key = keyfile.read_text().strip()
                request = urllib.request.Request(local + "/api/status", headers={"Authorization": "Bearer " + key})
                with urllib.request.urlopen(request, timeout=1) as response:
                    if response.status == 200:
                        if not args.no_browser:
                            webbrowser.open(local + "/#access=" + key)
                        print("Bereit: " + local)
                        return
        except (OSError, urllib.error.URLError):
            pass
        if process and process.poll() is not None:
            break
        time.sleep(0.5)
    raise SystemExit("Der Start wurde nicht bestätigt. Bitte " + str(data / "runtime.log") + " prüfen.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError:
        raise SystemExit("Die Paketinstallation ist fehlgeschlagen. Internetverbindung und Python-Version prüfen.")
