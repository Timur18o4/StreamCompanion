# Validierung · 1. November 2026

## Durchgeführt

- **39 automatisierte Tests bestanden** in einer neu angelegten Windows-Python-Umgebung, installiert aus den mitgelieferten Anforderungen. Eine Warnung betraf die künftig geänderte TestClient-Anbindung von Starlette; keine fehlgeschlagenen Tests.
- Python-Dateien kompiliert und beide JavaScript-Dateien auf Syntax geprüft.
- Lokale Oberfläche im Browser geöffnet; Demo, Speichern der Einrichtung, zusätzliche Audioquelle, Rollen und responsive Darstellung geprüft. Keine automatisierte Twitch-Anmeldung und keine Mikrofonfreigabe im Browser durchgeführt.
- Geräteauflistung auf dem Windows-PC geprüft, ohne eine Aufnahme zu starten.
- Frischer Erststart über den mitgelieferten Installer/Launcher geprüft. Wiederverwendung einer eigenen Instanz und Abweisung eines anderweitig belegten Ports sind vorgesehen; für den parallelen Test wurde ein anderer Port verwendet.
- PC-Sender separat gestartet und über einen einmaligen Code mit einem lokalen Workspace gekoppelt. Der Senderzugang wird nicht als allgemeiner Workspace-Zugang akzeptiert.
- Portables Windows-Programm gebaut und als EXE gestartet. HTTP-Start, authentifizierter Status, Oberfläche, Geräteauflistung und Demo geprüft.
- Der im Windows-Paket enthaltene Streamlink-Worker meldete seine Version erfolgreich. Das ist kein Nachweis eines erfolgreichen Twitch-Streamempfangs.

Die automatischen Tests prüfen insbesondere Device-Code-Anmeldung, rotierende Tokens, Kanalbesitzerprüfung, EventSub-Wiederverbindung und Duplikate, das Echtzeit-Audioformat, Quellenzuordnung, getrennte Übertragung von Audio und optionalen Bildern, Vorschau, bestätigten Versand, Stop, Kontextwechsel, veraltete Vorschläge, Opt-in und Löschung.

## Bewusst noch nicht als live bestätigt

- Freigaben mit den tatsächlichen Twitch-Konten und einer registrierten Twitch-App.
- Tatsächliche KI-Antworten und Live-Spracherkennung über einen bezahlten API-Zugang.
- Mikrofon-, Voicemeeter- oder Gesprächsaufnahmen; es wurden für Tests keine privaten Audiodaten aufgenommen.
- Tatsächliche Veröffentlichung in einem Twitch-Kanal.
- OBS-Verbindung und Bildinhaltserkennung im echten Stream.
- Empfangen eines echten Twitch-Livestreams über Streamlink/FFmpeg.
- Laufzeit, Installation und Leistung auf einem physischen Raspberry Pi.
- Inhaltliche Zuverlässigkeit des gewählten KI-Modells, Ironie und Personenidentität in gemischtem Audio.

Anmelde- und Modellantworten in den automatischen Tests sind simuliert. Die integrierte Demo ist klar als Beispielsituation markiert und führt keine KI-Anfrage oder Twitch-Veröffentlichung aus.
