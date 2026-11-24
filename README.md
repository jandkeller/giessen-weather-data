# Stündliches Wetterarchiv Gießen/Wettenberg

Öffentliche Wetterdaten für eine persönliche Heizverbrauchs-App: **DWD-Station 01639**. Aus je sechs gültigen 10-Minuten-Messungen entsteht ein Stundenmittel. Bei unvollständiger Stunde dient die DWD-Stundentemperatur derselben Station als gekennzeichneter Ersatz. Fehlt auch sie, bleibt die Stunde unbedeckt. Aktuelle Stunden werden erst nach ihrem Abschluss veröffentlicht.

Es werden keine Verbrauchsdaten, Zählerstände oder Zugangsschlüssel veröffentlicht. Das Archiv beginnt 2020 und enthält in der aktuellen Fassung ausschließlich Stundenwerte; die 10-Minuten-Eingangsdaten werden temporär verarbeitet.

## Dateien auf GitHub Pages

- `status.json`: letzter abgeschlossener Abruf, Vollständigkeit und Fehler sowie Heizgradtage des Vortags.
- `manifest.json` und `manifest.csv`: verfügbare Monate, Aktualisierungszeitpunkt und SHA-256-Prüfsummen.
- `hours/YYYY-MM.csv`: `timestamp,temperature_c,source,samples`. Unixzeit bezeichnet den UTC-Stundenbeginn; die Temperatur gilt für `[timestamp,timestamp+3600)`. `source=10min`: Mittel aus genau sechs Messungen um :00, :10, :20, :30, :40 und :50. `source=hourly`: DWD-Stundenmessung als gröbere Näherung. `source=missing`: kein verwendbarer Wert. Leeres Temperaturfeld bedeutet fehlend, niemals 0 °C. `samples` nennt die Zahl gültiger 10-Minuten-Messungen, höchstens sechs.

Die App lädt nur die benötigten Monatsdateien und prüft deren SHA-256-Prüfsummen. **HDD20** wird aus den Stundenwerten berechnet: `max(20 − T_Stunde, 0) × abgedeckte Sekunden / 86400`. Teilstunden werden am tatsächlichen Ablesezeitpunkt abgeschnitten. Die Stundenauflösung ist eine Näherung und kann gegenüber einer Integration der einzelnen 10-Minuten-Messungen abweichen. Aus fehlenden Stunden werden keine Werte interpoliert. Der Anteil ersetzter Stunden wird getrennt ausgewiesen.

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
- https://docs.github.com/en/actions/concepts/billing-and-usage
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
