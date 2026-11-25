# Stündliches Wetterarchiv Gießen/Wettenberg

Öffentliche Wetterdaten für eine persönliche Heizverbrauchs-App: **DWD-Station 01639**. Aus je sechs gültigen 10-Minuten-Messungen entsteht ein Stundenmittel. Bei unvollständiger Stunde dient die DWD-Stundentemperatur derselben Station als gekennzeichneter Ersatz. Fehlt auch sie, bleibt die Stunde unbedeckt. Aktuelle Stunden werden erst nach ihrem Abschluss veröffentlicht.

Es werden keine Verbrauchsdaten, Zählerstände oder Zugangsschlüssel veröffentlicht. Das Archiv beginnt 2020 und enthält in der aktuellen Fassung Stundenwerte sowie separat archivierte MOSMIX-Vorhersageläufe; die 10-Minuten-Eingangsdaten werden temporär verarbeitet.

## Dateien auf GitHub Pages

- `status.json`: letzter abgeschlossener Abruf, Vollständigkeit und Fehler sowie Heizgradtage des Vortags.
- `manifest.json` und `manifest.csv`: verfügbare Monate, Aktualisierungszeitpunkt und SHA-256-Prüfsummen.
- `hours/YYYY-MM.csv`: `timestamp,temperature_c,source,samples`. Unixzeit bezeichnet den UTC-Stundenbeginn; die Temperatur gilt für `[timestamp,timestamp+3600)`. `source=10min`: Mittel aus genau sechs Messungen um :00, :10, :20, :30, :40 und :50. `source=hourly`: DWD-Stundenmessung als gröbere Näherung. `source=missing`: kein verwendbarer Wert. Leeres Temperaturfeld bedeutet fehlend, niemals 0 °C. `samples` nennt die Zahl gültiger 10-Minuten-Messungen, höchstens sechs.

Die App lädt nur die benötigten Monatsdateien und prüft deren SHA-256-Prüfsummen. **HDD20** wird aus den Stundenwerten berechnet: `max(20 − T_Stunde, 0) × abgedeckte Sekunden / 86400`. Teilstunden werden am tatsächlichen Ablesezeitpunkt abgeschnitten. Die Stundenauflösung ist eine Näherung und kann gegenüber einer Integration der einzelnen 10-Minuten-Messungen abweichen. Aus fehlenden Stunden werden keine Werte interpoliert. Der Anteil ersetzter Stunden wird getrennt ausgewiesen.

## MOSMIX-Punktvorhersage

Bei jedem erfolgreichen Collector-Lauf wird außerdem **MOSMIX_L für Gießen, Punkt 10532** abgerufen. Die Quelle ist `https://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_L/single_stations/10532/kml/MOSMIX_L_LATEST_10532.kmz`. MOSMIX_L erscheint viermal täglich und reicht ungefähr zehn Tage voraus. Ein KMZ enthält KML/XML mit UTC-Zeitpunkten und dem Temperaturparameter `TTT` in Kelvin. Der Collector prüft Punkt-ID, Ausgabezeit, Zeitachse und Reihenlängen, wandelt nach Celsius um und erhält Fehlwerte als leere Felder.

- `forecast/latest.csv`: genau eine Zeile `station_id,issued_at,checked_at,file,sha256`. Sie verweist auf den aktuellen archivierten Lauf.
- `forecast/runs/ISSUED_EPOCH-HASH.csv`: `timestamp,temperature_c`, UTC-Unixsekunden und Celsius. Diese Werte sind Temperaturen **zu Zeitpunkten**, keine beobachteten Stundenmittel. Jeder neue bzw. korrigierte Lauf wird separat und mit SHA-256 veröffentlicht.
- `status.json` und `manifest.json` enthalten unter `forecast` Abrufstatus und Metadaten. Fehler oder ältere Läufe überschreiben den letzten gültigen Snapshot nicht. Vorhersagen werden niemals in die beobachteten `hours/`-Dateien gemischt.

Der Android-Client akzeptiert nur zur aktuellen Zeit passende Läufe (höchstens 24 Stunden alt) und prüft die Dateiprüfsumme. Er integriert `max(20 − T, 0)` linear zwischen direkt benachbarten stündlichen Prognosepunkten, einschließlich Durchgängen durch 20 °C. Lücken werden nicht überbrückt. Beobachtungen haben Vorrang. Zukünftige Heizgradtage und daraus mit dem letzten Ableseintervall kalibrierte Liter werden im Diagramm mit **50 % Deckkraft** dargestellt. Vorhersagen ändern weder Tankbestand noch erfassten Verbrauch.

## Automatischer Betrieb

GitHub Actions startet planmäßig um **:00 und :30 UTC**, übernimmt neue Werte und Korrekturen und prüft das erwartete Zeitraster. Die letzte Stunde erhält eine Stunde Spielraum für Veröffentlichungsverzögerungen. Spätere 10-Minuten-Nachlieferungen ersetzen den Stundenfallback. Historische Archive werden beim Erstimport und danach alle sieben Tage erneut geprüft.

Der Bestand liegt dauerhaft in normalen Git-Dateien, nicht im Actions-Cache oder in kurzlebigen Artefakten. Alte Monate werden nicht gelöscht. Bei vollständigem Downloadausfall wird keine neue Fassung veröffentlicht. Bei Teilfehlern bleiben ältere Daten erhalten und Fehler erscheinen im Status. Integrität und Aktualität lassen sich über Prüfsummen und `checked_at` kontrollieren.

GitHub kann Zeitpläne verzögern oder Läufe auslassen. Bei 60 Tagen ohne Repository-Aktivität können Zeitpläne deaktiviert werden; reguläre Archivaktualisierungen erzeugen hier Commits. Im Actions-Tab kann ein Lauf manuell gestartet werden. Das öffentliche Repository nutzt kostenlose Standard-Linux-Runner und GitHub Pages innerhalb deren Nutzungsgrenzen; kein Google-Cloud-Konto oder eigener Cloud-Schlüssel ist nötig.

## Lokal prüfen

```sh
python3 -m unittest discover -s tests -v
python3 collector.py --output site --start 2020-01-01
```

## Quellen und Lizenz

Messdaten: **Deutscher Wetterdienst (DWD), CC BY 4.0**. Die CSV-Dateien sind normalisierte und aggregierte Auszüge; aktuelle Werte sind vorläufig. Heizgradtage sind eigene Ableitungen.

- https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/
- https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly/air_temperature/
- https://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_L/single_stations/10532/kml/
- https://docs.github.com/en/actions/concepts/billing-and-usage
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
