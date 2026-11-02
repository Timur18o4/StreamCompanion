# Validierung von Version 0.2.3

188 automatisierte Tests bestanden (317,24 Sekunden, zwei Hinweise aus Testbibliotheken). JavaScript-Syntax und die installierte EXE samt Oberfläche wurden geprüft. Vier zusätzliche Modellordner-Prüfungen unter Windows PowerShell 5.1 bestanden.

Neu: längere digitale Stille vermeidet unnötige ASR-Arbeit; konsistente lokale Zeitgrenzen und Abbruch; getrennte Statusphasen für Kontext und Antwort; gemeinsamer Charakteranfang sowie Vorrang fälliger Gespräche vor Gedächtnisprüfungen. Der Starthelfer erkennt einen eigenen Server mit abweichendem Modellbestand.

Ein getrennter Offline-Test mit echtem lokalem Modell lieferte einen Textentwurf nach 13,19 Sekunden und einen Entwurf zu einem roten Testbild (1280 × 720) nach 23,64 Sekunden. Die Phasen Kontext und Schreiben wurden tatsächlich beobachtet. Kalte Modellvorbereitung: 117,73 Sekunden, außerhalb dieser Werte. Twitch-Versand war gesperrt, Mikrofon und OBS waren aus. Diese Messungen garantieren keine gleichen Zeiten unter Streamlast.

Die zusätzliche Startkorrektur wurde mit dem tatsächlichen lokalen Server geprüft; das Modell wurde wieder gefunden und vorbereitet, beide Audiospuren liefen ohne Paketverluste.

## Vorherige Prüfungen von Version 0.2.1

# Validierung von Version 0.2.1

Stand: 2. November 2026. Geprüfter Windows-Rechner: zwei Xeon E5-2667 v2, 16 physische Kerne / 32 logische Prozessoren und rund 64 GB RAM. OBS blieb in seiner bestehenden Konfiguration aktiv.

## Software und Windows-Programm

**175 automatisierte Tests bestanden**, Laufzeit 292,91 Sekunden. Zwei Hinweise kamen aus Testbibliotheken; kein Test schlug fehl.

Geprüft wurden unter anderem direkte Ansprachen, Rückfragen ohne erneutes `@`, Vorrang für Chatantworten, gesprochene Nachträge, Pause und Stop während laufender Berechnungen, begrenzte Kontexte, Einwilligung und Vergessen, getrennte Personen-Zuordnung, unsichere Gedächtnisbelege und die Vorbereitung großer lokaler Modelle.

Die gebaute Windows-EXE wurde gestartet und meldete Version 0.2.1. Twitch-Chat und Mikrofon waren verbunden, das lokale Gesprächsmodell wurde erfolgreich vorbereitet. Der abschließende Vorschautest verarbeitete rund 17,4 MB Audiodaten ohne Paketverluste, protokollierte Warnungen oder Fehler. Am Prüfende bestand kein Audio-Rückstand. Dieser Durchlauf bestätigt Aufnahme und Verbindungen, keinen neuen tatsächlichen Twitch-Versand.

Die App ist ein Vorabstand. Der Download enthält keine persönlichen Einstellungen, Konten, Notizen oder Modellgewichte. Das individuelle Testprofil wird daher nicht automatisch auf andere Rechner übertragen.

## Lokales Modell und gleichzeitige Sprachverarbeitung

Ausgewählt wurde Gemma 4 26B mit CPU-Ausführung. Im geprüften Profil: acht Modell-Threads, acht Nemotron-ASR-Threads, 4.096 Token Modellkontext, höchstens 340 Zeichen pro Beitrag und deaktivierte spekulative Vorhersage (`draft_num_predict: 0`). Die GPU-Speicherbelegung des Gesprächsmodells wurde mit null bestätigt.

Ein 20-minütiger Vergleich mit fortlaufender Spracherkennung ergab:

| Modell-Threads | Spekulative Vorhersage | Modellantworten | Mittel je Modellantwort | Ausgabe in Tokens/s |
|---:|---:|---:|---:|---:|
| 8 | aus | 13 | 17,59 s | 6,12 |
| 8 | an | 11 | 22,63 s | 4,29 |
| 16 | aus | 13 | 16,80 s | 6,12 |
| 16 | an | 12 | 17,90 s | 5,22 |

Die Texte wurden frei erzeugt und waren unterschiedlich lang. Die Tabelle ist kein Vergleich identischer Antworten und garantiert keine bestimmte Antwortzeit. Einmalige Vorbereitungsanfragen wurden nicht als Modellantworten gezählt.

Insgesamt: **91 abgeschlossene Sprachaussagen, 49 Modellantworten, keine verworfenen Audiopakete und keine Laufzeitfehler**. Der höchste Audiopuffer über alle Einstellungen betrug 2,1 Sekunden, bei acht Threads ohne spekulative Vorhersage 0,7 Sekunden. Die Sprachdaten waren wiederholte, künstlich erzeugte deutsche Beispielsätze. Das prüft die Verarbeitung unter gleichzeitiger Last, nicht jede natürliche Stimme oder die Last jedes Spiels.

## Durchgängige Reaktionsproben

Diese Prüfungen verwendeten echte lokale Spracherkennung und das echte Gesprächsmodell mit einem getrennten Testdatenbestand. Der Twitch-Versand war ersetzt; es wurden keine erfundenen Testgespräche veröffentlicht.

| Probe | Erster vorläufiger Text | Entwurf nach Sprachende | Audiopuffer maximal | Paketverluste |
|---|---:|---:|---:|---:|
| Direkte Frage mit Charakternamen | 1,81 s | 22,77 s | 0,4 s | 0 |
| Spontaner Kommentar ohne Namen | 1,22 s | 18,81 s | 0,4 s | 0 |

Die Beispiele unterscheiden sich und sind kein kontrollierter Vorher/Nachher-Vergleich. Die einmalige Vorbereitung lag außerhalb dieser Reaktionszeiten. Ein kaltes Neuladen des großen Modells dauerte in einer separaten Messung rund 93 Sekunden.

Eine zusätzliche Gesprächsprobe führte sechs Beiträge von zwei Personen durch die tatsächliche Engine mit ersetztem Twitch-Transport. Rückfragen ohne erneute Erwähnung wurden erkannt, eine Brot-Sorte blieb der richtigen Person zugeordnet, und eine unbekannte persönliche Spielvorliebe wurde nicht als bekannter Fakt ausgegeben. Dies sind einzelne geprüfte Beispiele, keine Garantie gegen erfundene Modellantworten.

## Grenzen

- Die Prüfung belegt weder dieselbe Qualität noch dieselbe Reaktionszeit wie OpenAI.
- Bilder und Discord waren im abschließenden lokalen Profil ausgeschaltet. OBS-Bildkontext im echten Stream ist nicht abschließend geprüft.
- Alle Voicemeeter-Routings, ein physischer Raspberry Pi und der Empfang eines echten Twitch-Streams über Streamlink sind nicht abschließend geprüft.
- Das lokale Modell kann weiterhin sachlich falsch oder unpassend antworten. Eine zuverlässige Sprecheridentität, Ironieerkennung oder inhaltlich fehlerfreie Erinnerung ist nicht garantiert.
- Reaktionszeit, Startzeit und Speicherbedarf hängen von Modell, Hardware und paralleler Last ab.

Einrichtung: [README.md](README.md) und [LOKALE-KI.md](LOKALE-KI.md).
