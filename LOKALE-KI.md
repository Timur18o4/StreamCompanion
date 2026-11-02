# Lokale KI mit Version 0.2.3

Textmodell und Spracherkennung sind zwei getrennte Komponenten. Im Windows-Paket sind deren Python-Bibliotheken enthalten, aber weder Ollama noch Modellgewichte. Der erste Start enthält keine Twitch-Konten, API-Schlüssel oder persönlichen Erinnerungen.

## Gesprächsmodell

1. [Ollama](https://ollama.com/download) installieren und einen lokalen Server starten.
2. Ein passendes Chatmodell separat herunterladen. Auf dem geprüften Windows-PC wurde `gemma4:26b` mit CPU-Ausführung verwendet. Das ist kein Nachweis ausreichender Leistung auf kleineren Rechnern oder einem Raspberry Pi.
3. Unter Einrichtung den lokalen Anbieter, `http://127.0.0.1:11434/v1` und den tatsächlich installierten Modellnamen auswählen.
4. CPU-Threads passend zur Hardware einstellen. Die Dropdowns begrenzen die Auswahl auf die ermittelte Zahl logischer Prozessoren. Mehr Threads sind nicht automatisch schneller.
5. KI-Verbindung testen; danach zunächst Vorschau verwenden.

Auf dem Vergleichsrechner mit 16 physischen Kernen und 32 logischen Prozessoren lief das Modell mit acht Threads und 4.096 Token Kontext. Das verwendete CPU-Modellprofil setzte außerdem `num_gpu 0` und `draft_num_predict 0`. Acht Modell-Threads und acht Erkennungs-Threads waren dort eine brauchbare Kombination. Die Modellanfragen halten das Modell bis zu zwei Stunden im Speicher; das benötigt entsprechend RAM.

Das Skript `scripts/local-ai/Start-LocalAI.ps1` ist ein optionaler Helfer für eine eigene portable Ollama-Installation: zusammen mit `runtime/ollama.exe` unter einem Ordner `local-ai` ablegen. Es erwartet Modelle unter `models/ollama`; optional kann `models-location.json` mit `{"modelsPath":"D:\\KI-Modelle"}` einen bestehenden Modellordner auswählen. Es installiert oder lädt keine Modelle herunter und wird vom allgemeinen Start.cmd nicht automatisch aufgerufen.

## Fortlaufende Spracherkennung

1. Das [mehrsprachige Nemotron-Streaming-Modell für sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/nemo/nemotron-streaming.html) separat herunterladen und entpacken. Hinweise und Lizenz stehen auch in der [Modellkarte](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b).
2. Der Modellordner muss `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx` und `tokens.txt` enthalten.
3. Im Quellpaket zusätzlich `python -m pip install -r requirements-streaming.txt` in der verwendeten Python-Umgebung ausführen. Beim Windows-Paket entfällt dieser Schritt.
4. Unter Einrichtung **Lokal · fortlaufend (Nemotron)** wählen, Modellordner und Threads eintragen.
5. Unter Audio & Bild das richtige Aufnahmegerät auswählen. Nur das Streamer-Mikrofon als Rolle Streamer zuordnen; Spiel und Discord bei Bedarf getrennt konfigurieren.

Namensvarianten wie eine wiederholt falsch erkannte Schreibweise des Charakternamens können ausdrücklich hinterlegt werden. Es erfolgt keine biometrische Sprechererkennung.

## Betrieb und Fehlersuche

- **Begleiter starten** öffnet die Verarbeitung. Beim Laden des Gesprächsmodells wird die Vorbereitung angezeigt. Ein kalter Start benötigte auf dem Testrechner einmal rund 93 Sekunden.
- **Pause** hält auch die Audioverarbeitung an. **Weiter zuhören** setzt sie fort. Die Anwendung startet nach einem Programmneustart den Begleiter nicht selbstständig.
- **Offline-Test** erlaubt ausschließlich bei lokalem Eingang auch ohne Livestream einen Mikrofontest. Automatisch kann dann trotzdem in den echten Twitch-Chat schreiben; Vorschau erzeugt zunächst Entwürfe.
- Bei erreichtem Stundenlimit entstehen keine weiteren Beiträge. Das bedeutet keinen Absturz.
- Ein dauerhaft wachsender Audiopuffer weist auf Rückstand hin. Threadzahlen von Modell und Spracherkennung gemeinsam prüfen.
- Text und Sprache bleiben nur bei entsprechend gewählten lokalen Anbietern lokal. Twitch benötigt weiterhin Internet.

Die Messergebnisse stehen in [VALIDIERUNG.md](VALIDIERUNG.md). Eine gleichwertige Qualität oder Reaktionszeit gegenüber OpenAI ist nicht nachgewiesen.
