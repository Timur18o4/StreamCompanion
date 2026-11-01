#!/usr/bin/env sh
set -eu
cd -- "$(dirname -- "$0")"
printf '%s\n' 'Installiert Systempakete fuer Audio, Python und FFmpeg.'
sudo apt-get update
sudo apt-get install -y python3 python3-venv libportaudio2 ffmpeg
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-audio.txt
printf '%s\n' 'Vorbereitung fertig. Starten mit: sh start-linux.sh'
