# Stream Companion · Version 0.3.0

Ein Gesprächsbegleiter für einen eigenen Twitch-Kanal. Er verarbeitet laufend Audio, berücksichtigt Chat und Kategorie und erzeugt kurze Kommentare oder Fragen. Ein eigener Bot-Account schreibt in den Chat. Die Oberfläche läuft lokal auf einem Windows-PC oder auf einem Raspberry/Linux-Rechner und lässt sich im Browser bedienen.

**Dies ist eine funktionsfähige erste Testversion mit echten Integrationen.** Die Twitch-Anmeldung und KI-Verarbeitung benötigen deine eigenen Zugänge. Eine durchlaufene Demo ist kein Nachweis eines erfolgreichen Livebetriebs. Es wurde nichts aus dem früheren Kick-Projekt übernommen, insbesondere keine Zugangsdaten.

Neu in Version 0.2.4: optional automatische Zuschauer-Erinnerungen ohne vorheriges Opt-in, direkte Vormerkung geeigneter Chat-Aussagen und dauerhafte Speichersperren durch Zuschauer. Der Modellstart wechselt bei einem ungültigen ausdrücklich eingerichteten Pfad nicht mehr auf andere Modelle. Der Betrieb bleibt lokal; ein Mischbetrieb wurde nicht ergänzt.

Die bisherigen Gesprächsfunktionen bleiben enthalten: bestätigte Frage-Antwort-Verläufe bleiben im Kontext, Rückfragen desselben Chatteilnehmers können ohne erneute Erwähnung beantwortet werden, und kurze Nachträge ergänzen eine laufende Anfrage. Direkte Antworten und spontane Kommentare verwenden normale Dialogrollen und freien Chattext; Antwortlänge und CPU-Threads lassen sich einstellen. Fortlaufende lokale Spracherkennung mit Nemotron ist enthalten. Fragen und hypothetische Aussagen gelten nicht als Gedächtnisbelege. Ein identischer Qualitätsstand gegenüber einem Cloudmodell ist damit nicht nachgewiesen.

[Downloads für v0.3.0](https://github.com/Timur18o4/StreamCompanion/releases/tag/v0.3.0) · [Prüfbericht](VALIDIERUNG.md) · [Lokale KI einrichten](LOKALE-KI.md)

## Neu in Version 0.3.0: MultiChat und Sprachausgabe

Unter **Einrichtung → Chat-Eingang und Bot** kann statt des direkten Twitch-Eingangs der lokale MultiChat-Ereignisstrom ausgewählt werden. Standardadresse: `http://localhost:8765/events`. Es werden neue Chatnachrichten von Twitch, YouTube, Kick, TikTok und CNG verarbeitet; System-, Status- und Alert-Ereignisse werden ignoriert. Unterbrechungen werden automatisch neu verbunden. Die eingehenden Texte bleiben Daten und erteilen dem Begleiter keine zusätzlichen Rechte.

MultiChat benötigt keine Anmeldung des Kanalbesitzers in Companion. Für Textausgabe verwendet Companion ausschließlich den angemeldeten Twitch-Bot; die Kanalmetadaten werden ebenfalls mit dessen Zugang gelesen. Die Textausgabe geht weiterhin an den eingestellten Twitch-Kanal, auch wenn der Anlass von einer anderen Plattform stammt. Fremde Plattform-Nachrichten werden nicht als Twitch-Antwort-ID verwendet. Bestehende Kanalbesitzer-Anmeldungen bleiben gespeichert, werden in diesem Modus aber nicht benötigt. Der direkte Twitch-Eingang bleibt als Alternative erhalten.

Im MultiChat-Betrieb muss kein Livestream bestätigt sein. Lokales Audio, Chat und gegebenenfalls aktivierter OBS-Bildkontext können auch offline verarbeitet werden. Der Begleiter beginnt erst nach seinem Start; automatischer Modus kann echte Beiträge schreiben oder sprechen. Stundenlimit, Abstände, Vorschau, Pause und Stop gelten weiterhin.

Unter **Antworten schreiben oder sprechen** sind zwei Ausgabewege wählbar:

- **Text im Twitch-Chat:** bisheriger Versand über den Bot.
- **Lokal sprechen:** spielt den fertigen Beitrag auf dem ausgewählten Wiedergabegerät ab; es wird keine zusätzliche Chatnachricht geschrieben. Bei MultiChat-Eingang und lokalem Audio ist dafür weder eine Kanalbesitzer- noch eine Bot-Anmeldung erforderlich. Ohne Twitch-Zugang werden keine aktuellen Twitch-Kategorie- oder Livestatusdaten behauptet.

Beim Speichern eines Wechsels zwischen Text und Sprache sowie Änderungen an Stimme oder Wiedergabegerät wird der laufende Begleiter gestoppt; die Anwendung bleibt geöffnet. Anschließend „Begleiter starten“ wählen. Die Vorschau erzeugt keinen Ton, bevor „Vorlesen“ gewählt wird. Der separate Stimmtest ist nur bei gestopptem Begleiter verfügbar und spielt hörbar auf dem gewählten Gerät. Wiedergabegeräte werden nach Namen und Audiosystem wiedergefunden; fehlt das Gerät, wird kein anderes Gerät ungefragt verwendet. Während der Sprachausgabe und für einen kurzen Nachlauf wird Audioeingang verworfen, um Rückkopplungen mit der eigenen Stimme zu verhindern. Pause und Stop brechen laufende Sprachausgabe ab.

**Sprachsysteme:** Unter Windows stehen die auf dem Rechner installierten Windows-Stimmen zur Auswahl. Piper ergänzt lokale neuronale Stimmen; Thorsten (Deutsch), Eva (Deutsch) und Lessac (Englisch) können über die Oberfläche heruntergeladen werden. Stimme, Lautstärke und Sprechtempo sind einstellbar. Die Piper-Stimme bleibt für weitere Beiträge geladen; nach fünf Minuten ohne Auftrag endet der Sprachprozess. Er nutzt zwei CPU-Threads und keine GPU. [Piper-Projekt und Lizenz](https://github.com/OHF-Voice/piper1-gpl) · [Stimmen und Modellkarten](https://huggingface.co/rhasspy/piper-voices).

Für einen Quellstart mit Piper zusätzlich `python -m pip install -r requirements-tts.txt` ausführen. Die Windows-Testinstallation enthält die Laufzeit, aber Stimmgewichte werden separat lokal unter `data/tts-models` abgelegt. Die Stimmen haben eigene Modellkarten und Lizenzangaben. Piper-Lizenztext und Quellenhinweise stehen unter `third-party-licenses` und in `DRITTANBIETER-TTS.md`.

Persönliche MultiChat-Erinnerungen benötigen eine stabile Nutzer-ID aus der Bridge. Twitch-IDs bleiben mit vorhandenen Notizen kompatibel; andere Plattformen erhalten getrennte Kennungen. Gleiche Anzeigenamen auf verschiedenen Plattformen werden nicht zusammengeführt. Fehlt die Nutzer-ID, ist die Nachricht für das aktuelle Gespräch nutzbar, aber keine dauerhafte persönliche Erinnerung. Die bestehenden Befehle `!remember off`, `!forget` und `!remember on` gelten pro Person und Plattform.

## Windows: portables Paket

Im separaten Paket **StreamCompanion-Windows-v0.3.0.zip** ist die Python-Laufzeit einschließlich der lokalen Erkennungsbibliotheken bereits enthalten. Den gesamten Ordner entpacken und **StreamCompanion.exe** oder **Start.cmd** öffnen. `_internal` muss neben der EXE erhalten bleiben. Für den PC-Sender `PC-Sender.cmd` öffnen. Eine eigene Python-Installation ist bei diesem Paket nicht erforderlich. Ollama und die Gesprächs- und Sprachmodelle werden separat eingerichtet; persönliche Konten, Notizen und Modellgewichte sind nicht im Download enthalten.

Die folgenden Installationsbefehle und die `.venv`-Anweisungen beziehen sich auf das **Quellpaket**. Im portablen Paket werden keine zusätzlichen Python-Pakete über `Start.cmd` installiert.

## Windows: Quellpaket starten

1. Den gesamten Ordner an einen beschreibbaren Ort entpacken, zum Beispiel `E:\StreamCompanion`.
2. Python **3.11 oder neuer, 64 Bit**, muss installiert sein und als `python` erreichbar sein.
3. **`Start.cmd` doppelklicken.** Beim ersten Start werden benötigte Python-Pakete aus PyPI heruntergeladen und in `.venv` installiert. Dafür wird Internet benötigt. Danach öffnet sich die Oberfläche.
4. Zum Kennenlernen **„Demo ansehen“** anklicken. Sie benötigt keine Twitch- oder KI-Zugangsdaten und veröffentlicht nichts.
5. Den Livebetrieb unter **Einrichtung** und **Audio & Bild** konfigurieren.

`Stop.cmd` beendet die gesamte Anwendung. Der Stop-Knopf im Dashboard beendet nur den Begleiter; die Einrichtung bleibt erreichbar. `PC-Sender.cmd` startet den optionalen Sender, `PC-Sender-Stop.cmd` beendet ihn.

Beim ersten Start ist **Vorschau** aktiv. „Veröffentlichen“ sendet einen vorhandenen, noch gültigen Vorschlag. **Automatisch** muss ausdrücklich eingeschaltet werden. Der Begleiter startet nach einem Programmneustart nicht von selbst.

## Twitch einmal einrichten

### Eigene Twitch-App

In der [Twitch-Entwicklerkonsole](https://dev.twitch.tv/console/apps) eine Anwendung registrieren. Twitch kann dafür ein verifiziertes Konto und Zwei-Faktor-Anmeldung verlangen; diese Schritte führst du selbst aus.

- Für PC/Raspberry ist der **öffentliche Client-Typ / Public Client** für den Device-Code-Ablauf vorgesehen.
- Die **Client-ID** in der Oberfläche unter „Twitch-App-ID“ eintragen und speichern.
- Der Device-Code-Ablauf verwendet keinen lokalen OAuth-Callback. Falls die Registrierung dennoch eine Redirect-Adresse verlangt, eine von Twitch akzeptierte lokale Adresse wie `http://localhost:8787` hinterlegen. Sie wird von dieser Anwendung nicht als Callback verwendet.
- Falls du eine bestehende vertrauliche App verwendest, kann das Client-Secret im aufgeklappten Zusatzbereich hinterlegt werden. Für die Verteilung an andere Nutzer ist ein eingebautes vertrauliches Secret ungeeignet.
- Alternativ kann der Entwickler die öffentliche Client-ID vor dem Start über die Umgebungsvariable `STREAM_COMPANION_TWITCH_CLIENT_ID` bereitstellen. Endnutzer müssen dann keine eigene App-ID eintragen. Eine registrierte App-ID ist diesem Paket noch nicht beigefügt.

### Kanalbesitzer und Bot separat anmelden

1. **Kanalbesitzer → Mit Twitch verbinden** anklicken.
2. Twitch über die angezeigte Adresse öffnen und den Code/Freigabedialog bestätigen.
3. Für **Bot-Account** denselben Vorgang durchführen. Im Twitch-Browser vorher zum richtigen Bot-Konto wechseln, gegebenenfalls ein getrenntes Browserprofil benutzen.
4. Den zu beobachtenden Kanalnamen eingeben und **Prüfen** anklicken.

Das Tool prüft, ob der angemeldete Besitzer zu diesem Kanal gehört. Es erstellt keine Twitch-Accounts und fragt keine Twitch-Passwörter ab. Die Berechtigungen sind auf die Anbindung beschränkt: Besitzer `channel:bot`, Bot `user:read:chat` und `user:write:chat`. Moderationsrechte sind nicht erforderlich.

Tokens werden serverseitig geprüft und bei Bedarf erneuert. Öffentliche Refresh-Tokens werden nach jeder Verwendung ersetzt; abgelaufene oder widerrufene Freigaben erfordern eine neue Anmeldung. Der Chat nutzt EventSub-WebSockets mit Duplikaterkennung und Wiederverbindung. Ein Versand zählt erst, wenn Twitch `is_sent: true` und eine Nachrichten-ID zurückgibt.

Quellen: [Twitch Device Code Flow](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#device-code-grant-flow), [Chat-Anmeldung](https://dev.twitch.tv/docs/chat/authenticating/), [Nachrichtenversand](https://dev.twitch.tv/docs/chat/send-receive-messages/).

## KI und kontinuierliche Spracherkennung

### Gesprächsmodell

Die Oberfläche unterstützt:

- einen lokalen OpenAI-kompatiblen Server, beispielsweise Ollama unter `http://127.0.0.1:11434/v1`;
- OpenAI mit eigenem API-Schlüssel;
- einen anderen OpenAI-kompatiblen Server über HTTPS.

Modellnamen eintragen oder **Modelle laden** verwenden. Für Bildkontext ist ein tatsächlich bildfähiges Modell erforderlich. Der Server muss Chat-Completions und JSON-Antworten unterstützen. Die Modellliste enthält unter Umständen auch Modelle, die dafür nicht geeignet sind. **KI-Verbindung testen** führt eine kleine Testanfrage ohne Streaminhalte aus; das kann bei externen Anbietern kostenpflichtig sein.

### Audio

Standard ist OpenAI-Echtzeit-Transkription über eine laufende Verbindung. Die Anwendung liefert PCM-Audio mit 24 kHz und erkennt kurze Sprechabschnitte, statt erst eine Minute aufzunehmen. Das voreingestellte Sprachmodell ist `gpt-live-transcribe`; Verfügbarkeit und Abrechnung hängen vom Anbieterzugang ab. Für Audio kann ein eigener OpenAI-Schlüssel eingetragen werden. Der normale KI-Schlüssel wird sonst auch für Audio verwendet; bei einem anderen Textanbieter muss deshalb ein gesonderter OpenAI-Audioschlüssel gesetzt werden.

Alternativ ist **lokales Whisper** vorbereitet. Zusatzpaket installieren:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-whisper.txt
```

Unter Linux:

```sh
.venv/bin/python -m pip install -r requirements-whisper.txt
```

Beim ersten lokalen Einsatz wird das ausgewählte Whisper-Modell heruntergeladen. Die Auswertung erfolgt abschnittsweise mit CPU/int8. Reaktionszeit und Echtzeitfähigkeit auf einem bestimmten Raspberry sind **nicht gemessen**. Auf einem schwachen Gerät können Puffer verworfen werden; dies wird bei der Quelle angezeigt. Lokale KI-Server sind nicht im Paket enthalten.

Quellen: [OpenAI Echtzeit-Transkription](https://developers.openai.com/api/docs/guides/realtime-transcription), [Ollama OpenAI-Kompatibilität](https://docs.ollama.com/api/openai-compatibility).

## Audioquellen: drei Betriebsarten

### Fortlaufende lokale Spracherkennung

Unter Einrichtung lässt sich **Lokal · fortlaufend (Nemotron)** auswählen. Zusatzpaket: `pip install -r requirements-streaming.txt`. Benötigt wird der entpackte Ordner des [mehrsprachigen Nemotron-Streaming-Modells für sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/nemo/nemotron-streaming.html) mit `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx` und `tokens.txt`. Dieser Pfad und die CPU-Threads werden in der Einrichtung gespeichert. Das Modell läuft mit CPU-Ausführung; im laufenden Betrieb werden dafür weder API-Schlüssel noch ein Cloud-Dienst verwendet. [NVIDIA-Modellkarte und Lizenz](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b).

Die Erkennung behält ihren Zustand während des Sprechens. Vorläufiger Text wird in derselben Beobachtung ergänzt und kann sich noch ändern. Erst abgeschlossene Aussagen dürfen Gedächtnisnotizen belegen. Aufnahme und Auswertung müssen nicht auf feste 8–12-Sekunden-Blöcke warten. Eine ausdrückliche Ansprache des Charakters im Streamer-Mikrofon wird nach Abschluss bevorzugt beantwortet; Gesprächs- und Spielspuren werden dafür nicht als Streamer ausgegeben. Pause verwirft ausstehende vorläufige Daten. Bei wachsendem Rückstand wird die Pufferdauer angezeigt. Die Text- und Bild-KI bleibt ein getrenntes lokales Modell; dies ist kein vollständiger Ersatz für ein Audio-Gesprächsmodell und kein Nachweis derselben Qualität wie ein Cloud-Dienst. Whisper bleibt als alternative Erkennung auswählbar.

### Direkt auf dem Rechner

„Direkt auf diesem Rechner“ auswählen und die verfügbaren Aufnahmegeräte zuordnen. Voicemeeter-Ausgänge können als eigene Geräte ausgewählt werden, wenn dein Routing sie getrennt bereitstellt. Ein gemischter Ausgang wird nicht nachträglich zuverlässig in Mikrofon, Discord und Spiel zerlegt.

Rollen:

- **Streamer / Mikrofon:** für Streameraussagen; optional für Erinnerungen.
- **Gespräch / Discord:** zusätzlicher Gesprächskontext; zunächst keine Identifizierung einzelner Sprecher.
- **Gameplay:** erkannte Spielsprache als Kontext. Eine gezielte Erkennung aller Spielgeräusche oder ein verlässlicher Spielzustandsdetektor sind nicht enthalten.
- **Nicht auswerten:** diese Quelle bleibt ausgeschlossen.

Wähle für den Streamer möglichst ein Signal **nach** deiner Stream-Stummschaltung. Eine direkt aufgenommene physische Mikrofonquelle erkennt den Mute-Zustand in OBS nicht. Private Anrufe nicht auswählen. **Pause** stoppt das Weiterreichen von Audio in die Verarbeitung; **Stop** schließt die laufenden Audioverbindungen und Erfassungsgeräte.

Bei einem geänderten Gerätenamen am gespeicherten Geräteindex wird eine erneute Auswahl verlangt. Mehrere parallele Quellen sind in dieser Testversion freigeschaltet. Es gibt noch keine Premium-Abrechnung oder Abo-Sperre.

### Vom PC zum Raspberry / anderen Rechner

1. Auf dem Zielrechner den Workspace mit LAN-Bindung starten.
2. Unter „Audio & Bild → Streamer-PC verbinden“ einen Verbindungscode erzeugen.
3. Auf dem Streamer-PC **`PC-Sender.cmd`** öffnen.
4. Adresse des Workspace eingeben, z. B. `http://raspberrypi.local:8787`, und den achtstelligen Code eingeben.
5. Quellen im PC-Sender auswählen und „Sender bereitstellen“ anklicken. Der Begleiter im Workspace muss für die erste Quellenübernahme gestoppt sein.
6. Im Workspace die übernommenen Quellen prüfen, dann den Begleiter starten.

Der Sender prüft alle zwei Sekunden den Betriebszustand. Audio wird nur bei laufendem, nicht pausiertem Begleiter und bestätigtem Livestatus weitergereicht. Fällt die Rückmeldung aus, wird die Übertragung eingestellt. Am Empfänger wird der Zustand nochmals geprüft. Livestatus wird regelmäßig abgefragt; eine Offline-Erkennung kann deshalb verzögert eintreffen. **Für sofortige Privatsphäre immer Pause/Stop verwenden.**

Der Verbindungscode gilt fünf Minuten und nur einmal. Der daraus entstehende Zugang ist auf den Sender beschränkt und kann unter „PC-Verbindung trennen“ widerrufen werden. Der Sender erhält keine Twitch- oder KI-Schlüssel. Dieser Übertragungsweg verwendet eigene WebSockets; VBAN ist nicht integriert.

### Aus dem Twitch-Stream

Hier empfängt Streamlink das Audio des eigenen Kanals; FFmpeg wandelt es um. Streamlink wird als optionales Audio-Paket installiert. **FFmpeg muss zusätzlich im PATH verfügbar sein.** Unter Raspberry OS erledigt das Vorbereitungsskript die Installation.

Die Zuordnung des empfangenen Mixes bleibt „Gespräch / unbekannte Stimme“, auch wenn die ausgewählte Zeile als Mikrofon beschriftet war. Aus diesem Mix werden keine personenbezogenen Audio-Erinnerungen erzeugt. Streamverzögerung, Werbung, Plattformänderungen und mögliche Zugangsbeschränkungen können den Empfang beeinträchtigen. Dieser Empfangspfad wurde nicht gegen einen echten Twitch-Livestream getestet.

## Bildkontext

Unter „Audio & Bild“ Bildkontext einschalten und eine Bildquelle wählen:

- **OBS-Programmszene:** OBS-WebSocket aktivieren, Adresse und gegebenenfalls Passwort hinterlegen. Die Anwendung verwendet nur `GetCurrentProgramScene` und `GetSourceScreenshot`. Szenenwechsel und Änderungen an der Show sind nicht vorgesehen.
- **PC-Sender:** im PC-Sender optional einen Stream-/Spielmonitor auswählen. Der **gesamte** gewählte Monitor wird alle 30 Sekunden als Bild übertragen, solange die Bildfreigabe des Workspace und die Laufbedingungen aktiv sind. Private Desktopinhalte daher ausschließen.
- **Manuelles Testbild:** eine Bilddatei auswählen.

Es wird höchstens ein aktuelles, verkleinertes Bild an eine Gesprächsanfrage angehängt. Veraltete Bilder werden ausgelassen. Eine vollständige Videoanalyse und eine schnelle Mehrbild-Sequenzanalyse sind noch nicht enthalten. Ein KI-Modell kann Bilddetails falsch verstehen.

## Ohne Livestream testen

Unter **Audio & Bild → Offline-Test mit lokalem Mikrofon aktivieren** kann der Begleiter bei einem offline gemeldeten Kanal zuhören und den echten Twitch-Chat verwenden. Das gilt ausschließlich für **Direkt auf diesem Rechner**. Der echte Livestatus bleibt in der Oberfläche erkennbar; die letzte Streamkategorie wird in diesem Test nicht als aktueller Spielkontext verwendet. Standardmäßig ist die Option ausgeschaltet.

Nach dem Speichern den Begleiter starten. Im Automatikmodus können reguläre Nachrichten auch im Offline-Chat erscheinen; in der Vorschau ist weiter eine manuelle Freigabe nötig. Pause, Stop, Stundenlimit, Nachrichtenabstände und die Prüfung der Twitch-Verbindung gelten weiterhin. Eine Änderung dieser Option beendet den laufenden Durchgang. Der PC-Sender und der Empfang aus Twitch benötigen weiterhin einen Livestream.

Für falsch erkannte Charakternamen lassen sich unter **Einrichtung → 4 → Erkannte Namensvarianten am Mikrofon** ausdrückliche Varianten durch Kommas getrennt hinterlegen. Sie gelten nur für abgeschlossene Aussagen der als Streamer konfigurierten Spur. Originaltext, Chat-Namensprüfung und die Identität des Charakters bleiben erhalten. Es wird keine Stimme biometrisch erkannt.

## Gesprächssteuerung

Einstellbar sind Mindestabstand, höchstens veröffentlichte Beiträge pro Stunde, eine ruhige Phase als Prüf-Anlass und eine zusätzliche Versandverzögerung. Die KI darf bewusst schweigen. Sie bekommt die letzten Beobachtungen sowie eine begrenzte Auswahl relevanter Erinnerungen. Neue Chatnachrichten und Transkripte laufen während der Berechnung weiter und verwerfen einen Beitrag nicht pauschal. Stop, Pause, Datenlöschungen sowie verifizierte Kanal-, Kategorie- oder Livestatuswechsel können einen Beitrag weiterhin ungültig machen. Lokale Gesprächsanfragen haben höchstens 150 Sekunden Zeit, andere Anfragen höchstens 90 Sekunden. Abgelaufene Anfragen werden beendet; derselbe fehlgeschlagene Kontext wird nicht sofort erneut berechnet. Die Anzeige unterscheidet Kontextverarbeitung und das Schreiben der Antwort. Fällige Gesprächsbeiträge haben Vorrang vor Gedächtnisprüfungen. Ein fertiger automatischer Beitrag wartet bei Bedarf auf eine Sprechpause und wird dann erneut zum Versand geprüft.

Die Anwendung erkennt Antworten auf offene Fragen grob über neue menschliche Chat- oder Streameraussagen. Sie identifiziert keine Sprecher biometrisch und garantiert keine zuverlässige Ironie-, Konzentrations- oder Abwesenheitserkennung. Das Gesprächsgefühl muss mit realen Streams kalibriert werden. Lokale spontane Kommentare erhalten höchstens vier aktuelle Beobachtungen mit insgesamt bis zu 1.200 Textzeichen und eine kleine Auswahl relevanter Erinnerungen. Direkte Antworten erhalten einen größeren, auf die angesprochene Person begrenzten Dialogausschnitt. Der übrige Verlauf und die gespeicherten Erinnerungen werden dadurch nicht gelöscht. Während der Berechnung zeigt die Oberfläche die verstrichene Zeit an.

**Ansprachen und Erwähnungen:** `@Botaccount`, der vollständige Accountname, der eingestellte Charaktername und Twitch-Antworten auf Bot-Nachrichten werden bevorzugt geprüft. Die betreffende Nachricht bleibt für diese Antwort im Modellkontext, auch wenn inzwischen neue Audioabschnitte eintreffen. Der Bot antwortet über die Twitch-Antwortfunktion auf die ursprüngliche Nachricht. Ein allgemeiner Vorschlag macht dafür Platz. Eine offene Bot-Frage oder laufende Streamersprache blockiert diese gezielte Antwort nicht; mindestens 20 Sekunden Abstand, Stundenlimit, Pause und bestätigter Livestatus gelten weiter; für lokale Tests ist der ausdrücklich aktivierte Offline-Test vorgesehen. Vorschau veröffentlicht auch solche Antworten erst nach Freigabe. Ohne Freigabe für externe Chatverarbeitung werden sie nicht an externe Modelle gegeben. Ansprachen werden höchstens drei Minuten berücksichtigt; jede wird nur einmal beantwortet oder bewusst ausgelassen. Reine Pronomen wie „er“ ohne Namensbezug werden nicht gezielt zugeordnet. Die Rechenzeit des gewählten Modells bleibt bestehen.

Gezielte lokale Antworten verwenden die angesprochene Nachricht und einen begrenzten Verlauf mit bisherigen bestätigten Bot-Antworten. Persönliche Erinnerungen werden der fragenden Person und ausdrücklich genannten anderen Personen zugeordnet. Eine allgemeine KI-Anfrage oder eine nachgelagerte Gedächtnisprüfung macht für eine neue Ansprache Platz. Der Server kann dabei noch seinen laufenden Rechenblock beenden. Gedächtnisprüfungen erfolgen getrennt mit niedrigerer Priorität; dieselbe Aussage wird nicht wiederholt eingereiht. Stop, Datenlöschung und Opt-out entfernen ausstehende Notizen; vor dem Speichern werden Freigabe und Beleg nochmals geprüft. Die Vorbereitung eines großen lokalen Gesprächsmodells wird gesondert angezeigt und beim Start oder Fortsetzen ausgeführt.

Das Stundenlimit begrenzt **veröffentlichte Nachrichten**, nicht die Zahl oder Kosten der KI-Anfragen. Die Audioverarbeitung kann auch in längeren ruhigen Phasen Kosten verursachen. Alle genauen Reaktionszeiten und Kosten hängen von Modell, Hardware, Quellen und Nutzung ab.

## Erinnerungen und Zuschauer

Erinnerungen sind auf den konfigurierten Kanal begrenzt. Vorschläge aus der KI benötigen eine passende aktuelle Aussage, eine erlaubte Person und eine ausreichende vom Modell angegebene Sicherheit. Audio mit unbekannter Person wird nicht als persönliche Erinnerung gespeichert. Die Zuordnung einer Quelle zum Streamer ist eine Einstellung des Betreibers, keine bewiesene Sprecheridentität.

**Diese Prüfungen garantieren keine inhaltlich richtigen Erinnerungen.** Herkunft und Datum werden angezeigt, damit du Notizen prüfen und löschen kannst. Es findet kein Nachtraining eines KI-Modells statt.

Bei aktiviertem Zuschauer-Gedächtnis:

In der Einrichtung kann zwischen Zustimmung mit `!remember on` und automatischer Prüfung gewählt werden. Der automatische Modus prüft klare eigene Aussagen zu Spielen, Hobbys, Vorlieben, Projekten und Streamzielen, auch ohne Ansprache oder öffentliche Antwort des Bots. Der bisherige Zustimmungsmodus bleibt bei vorhandenen Installationen voreingestellt. Explizite Ablehnungen gelten in beiden Modi und bleiben nach Neustarts erhalten.

Lokale Gedächtnisprüfungen werden direkt beim Eintreffen geeigneter Chatnachrichten vorgemerkt. Antworten haben Vorrang. Die Warteschlange ist auf acht Prüfungen begrenzt; bei hoher Auslastung werden nicht alle Aussagen gespeichert. Ungeprüfte Aufträge werden nicht über einen Programmneustart hinweg aufbewahrt. Gespeicherte Notizen bleiben in der Datenbank, mit Person, Herkunft und Datum. Fragen, Vermutungen und Zitate werden bereits vor der Modellanfrage ausgeschlossen; die weitere inhaltliche Auswahl übernimmt das Modell.

- `!remember on`: der Zuschauer erlaubt Erinnerungen für seinen Twitch-Account in diesem Kanal.
- `!remember off`: sperrt weiteres Speichern und entfernt die eigenen gespeicherten Erinnerungen, auch im automatischen Modus.
- `!forget` oder `!forget me`: dieselbe Löschung und Sperre; zusätzlich wird der eigene noch vorhandene temporäre Chatkontext entfernt. `!remember on` hebt die Sperre wieder auf, sofern das Zuschauer-Gedächtnis aktiviert ist.

Das aktuelle Chatfenster ist unabhängig davon für die laufende Unterhaltung nutzbar. Ein Opt-out des dauerhaften Gedächtnisses bedeutet nicht, dass eine neue öffentliche Chatnachricht für den aktuellen Gesprächskontext unsichtbar wird. Die Befehle sind keine Moderationsbefehle.

Beim Einsatz externer Gesprächsmodelle sind Chattexte und Zuschauer-Erinnerungen standardmäßig von der Übertragung ausgeschlossen. Nur bei vorhandener entsprechender Berechtigung die externe Chatverarbeitung freigeben. Eingeblendeter Chat in selbst ausgewählten Bildern ist gesondert zu beachten. Ein Zuschauer-Opt-in allein klärt nicht alle Twitch-Vertrags- oder Datenschutzfragen. [Twitch Developer Agreement](https://legal.twitch.com/en/legal/developer-agreement/)

## Raspberry Pi / Linux

Vorbereitet für aktuelles **64-Bit-Linux mit Python 3.11+**. Eine echte Installation auf deinem Raspberry ist in dieser Lieferung noch nicht geprüft.

```sh
cd /pfad/zu/stream-companion
sh setup-raspberry.sh
sh start-linux.sh
```

Das erste Skript installiert über `apt` Python/venv, PortAudio und FFmpeg und richtet die Python-Umgebung ein. Dafür wird `sudo` benötigt. Der Start bleibt im Vordergrund. Nach Start im Browser eines anderen Rechners öffnen:

```text
http://IP-DES-RASPBERRY:8787
```

Den lokalen Zugangscode aus `data/access.key` eingeben. Das ist **kein Twitch-Token**. Für Zugriff im eigenen LAN muss Port 8787 erreichbar sein. Es wird keine Firewall automatisch umgestellt. Die Oberfläche nicht direkt ins öffentliche Internet freigeben. Für einen späteren externen Betrieb sind HTTPS, geeignete Zugriffsverwaltung und ein gesonderter Bereitstellungsprozess nötig.

Manueller Start mit anderem Port oder Datenordner:

```sh
.venv/bin/python run.py --host 0.0.0.0 --port 8788 --data-dir ./meine-daten --no-browser
```

Ein optionales Beispiel für einen Benutzer-Dienst liegt unter `deployment/stream-companion.service.example`. Pfade selbst ersetzen; es wird kein Autostart ungefragt installiert.

## Daten, Sicherung und Updates

- `data/companion.sqlite3`: Einstellungen, Kontoverbindungen, Erinnerungen und Opt-ins.
- `data/encryption.key`: lokaler Schlüssel für die verschlüsselten Einstellungs-/Tokenwerte.
- `data/access.key`: Zugang zur lokalen Oberfläche.
- `sender-data/`: entsprechende getrennte Daten des optionalen PC-Senders.

**Den gesamten Datenordner gemeinsam sichern.** Tokens und API-Schlüssel sind in der Datenbank verschlüsselt; die Schlüsseldatei liegt auf demselben Rechner. Das ersetzt keinen geschützten Betriebssystemaccount und keine Laufwerksverschlüsselung. Erinnerungsnotizen liegen als lesbare Datensätze in der lokalen Datenbank. Laufender Gesprächskontext und Diagnosen werden im Arbeitsspeicher gehalten und nicht als vollständiges Chat-/Audioarchiv gespeichert. Rohaudio wird nicht dauerhaft als Audiodatei archiviert.

Beim Aktualisieren nur Programmdateien ersetzen, `data/` und `sender-data/` erhalten. Die ZIP-Lieferung enthält weder diese Datenordner noch eine Python-Umgebung noch Zugangsdaten. `runtime.log` enthält Startfehler; beim Weitergeben trotzdem auf private Angaben prüfen.

## Tests und Grenzen dieser Lieferung

Automatische Tests:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Es gibt Tests für Oberfläche/API, Zugangsschutz, verschlüsselte Tokenablage, Kanalbesitzerprüfung, Device-Code-Anmeldung mit simulierten Twitch-Antworten, rotierende Refresh-Tokens, Chat-Wiederverbindung und Duplikate, Audioformat/Transkriptzuordnung, Sender-Kopplung, Vorschau, bestätigten Versand, Stop, veraltete Beiträge, Bildprüfung, Quellenrollen und Gedächtnis-Opt-in.

**Geprüft:** 175 automatisierte Tests, lokale Spracherkennung und Gesprächsmodelle auf einem Windows-PC, ein 20-minütiger Belastungstest ohne verworfene Audiopakete sowie die gestartete Windows-EXE mit Mikrofon und Twitch-Verbindung. Die abschließenden Gesprächsproben verwendeten einen ersetzten Twitch-Versand; sie bestätigen keinen neuen echten Chatbeitrag. Details und gemessene Zeiten stehen in [VALIDIERUNG.md](VALIDIERUNG.md).

**Nicht abschließend geprüft:** OBS-Bildkontext im echten Stream, sämtliche Voicemeeter-Routings, Raspberry-Leistung und Empfang eines echten Twitch-Streams. Eigene Konten und Modelle müssen eingerichtet werden. Es gibt keine automatische Account-Erstellung, Viewer-Boosts, Abo-Abrechnung oder Produktionsfreigabe als öffentlicher Dienst.

Der Charakter ist als KI-Begleiter erkennbar. Er muss sich auf Nachfrage wahrheitsgemäß erklären und erhält keine erfundene menschliche Biografie.
