import argparse
import urllib.error
import urllib.request
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--sender",action="store_true")
parser.add_argument("--port",type=int)
args = parser.parse_args()
root = Path(__file__).resolve().parent
data = root / ("sender-data" if args.sender else "data")
port = args.port or (8790 if args.sender else 8787)
try:
    key = (data / "access.key").read_text().strip()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown",data=b"{}",method="POST",headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
    with urllib.request.urlopen(request,timeout=15) as response:
        print("PC-Sender beendet." if args.sender else "Stream Companion beendet.")
except (OSError, urllib.error.URLError):
    raise SystemExit("Keine passende laufende Instanz erreicht. Es wurde kein fremder Prozess beendet.")
