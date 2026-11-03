# Lokale Sprachkomponenten

Piper TTS 1.8.0 wird als gesonderter lokaler Sprachprozess ausgeführt. Laut Paketmetadaten gilt GPL-3.0-or-later. Der vollständige Lizenztext liegt unter `third-party-licenses/Piper-COPYING.txt`; weitere mitgelieferte Hinweise befinden sich in den Paketmetadaten des Windows-Pakets.

- Projekt und Quellcode: https://github.com/OHF-Voice/piper1-gpl
- Veröffentlichung 1.8.0: https://pypi.org/project/piper-tts/1.8.0/
- Build-Einbindung: `build-windows.spec`; Python-Abhängigkeiten: `requirements-tts.txt`.
- Stimmgewichte sind nicht Bestandteil des Downloads. Die Stimmen haben eigene Modellkarten und Lizenzangaben: https://huggingface.co/rhasspy/piper-voices

Windows-Stimmen werden von der lokal vorhandenen Windows-Sprachausgabe bereitgestellt; deren Stimmen werden nicht mitgeliefert. Mikrofon- und Wiedergaberouting verwenden das bereits vorhandene sounddevice/PortAudio. ONNX Runtime und Piper laufen auf der CPU.
