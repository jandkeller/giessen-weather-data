# Wetterarchiv Gießen/Wettenberg

Öffentliche Wetterdaten für eine persönliche Heizverbrauchs-App: DWD-Station **01639**, 10-Minuten-Temperaturen und Stundenwerte als Ersatz bei Lücken. Die Referenztemperatur für Heizgradtage ist **20 °C**. Es werden keine Verbrauchsdaten, Zählerstände oder Zugangsschlüssel veröffentlicht.

## Nutzung

GitHub Pages veröffentlicht eine Statusseite sowie folgende Dateien:

- `status.json`: Zeitpunkt des letzten abgeschlossenen Abrufs, Datenabdeckung, Downloadfehler und Heizgradtage des Vortags.
- `manifest.json` und `manifest.csv`: Monatsdateien mit SHA-256-Prüfsummen und Aktualisierungszeitpunkt.
- `months/YYYY-MM.csv`: `source,timestamp,temperature_c`. Quelle `10min` oder `hourly`; Unixzeit in UTC-Sekunden, Temperatur in °C. Ein leeres Temperaturfeld ist ungültig/fehlend, nicht 0 °C.

Clients laden den Index und nur die für ihren Zeitraum benötigten Monate einschließlich einer Stunde Randbereich. Monatsdateien werden anhand der Prüfsummen validiert; bei CDN-Versatz muss der Index neu geladen werden. Messreihen bleiben getrennt. Bei der Berechnung werden positive Differenzen zu 20 °C zwischen direkt benachbarten gültigen Messpunkten linear integriert. Stundenwerte füllen ausschließlich unbedeckte Abschnitte. Fehlen geeignete Stundenwerte, bleibt die Lücke offen. Bei nachgelieferten 10-Minuten-Werten entfällt der Ersatz automatisch.

## Betrieb

Der Workflow ist für **:00 und :30 UTC** geplant. GitHub kann geplante Läufe verzögern oder ausfallen lassen; es besteht keine Garantie für minutengenaue Ausführung. Im Actions-Tab lässt sich der Workflow auch manuell starten. Bei 60 Tagen ohne Repository-Aktivität kann GitHub Zeitpläne deaktivieren. Datenaktualisierungen werden hier mit einem regulären Commit archiviert.

Der Erstlauf lädt die verfügbaren Daten ab 2020. Anschließend werden bei jedem Lauf die rollierenden DWD-Archive `recent` und `now` erneut gelesen, um neue Werte, Lücken und Korrekturen zu übernehmen. Historische Archive werden alle sieben Tage erneut geprüft. Die Vollständigkeitsprüfung berücksichtigt alle erwarteten 10-Minuten-Zeitpunkte; die letzte Stunde wird für mögliche Veröffentlichungsverzögerungen ausgenommen. Die Auswertung selbst weist jede unbedeckte Zeit aus, auch innerhalb dieser Stunde.

Das dauerhafte Archiv liegt als normale Dateien im Git-Repository. Actions-Artefakte dienen ausschließlich dem Pages-Deployment. Ältere Monatsdateien werden nicht gelöscht. Bei vollständigem Downloadausfall wird keine neue Fassung veröffentlicht. Teilfehler werden sichtbar im Status protokolliert; alte Daten bleiben erhalten. Bei längeren Ausfällen deshalb immer `checked_at` prüfen.

Keine Zusatzbibliotheken, kostenpflichtigen Runner, Google-Cloud-Ressourcen oder eigenen Cloud-Schlüssel erforderlich. Das öffentliche Repository verwendet Standard-Linux-Runner und GitHub Pages im kostenlosen GitHub-Angebot, innerhalb der jeweiligen Nutzungsgrenzen.

## Lokale Ausführung

```sh
python3 -m unittest discover -s tests -v
python3 collector.py --output site --start 2020-01-01
```

## Quellen und Lizenz

Messdaten: **Deutscher Wetterdienst (DWD), CC BY 4.0**. Die veröffentlichten CSV-Dateien sind normalisierte Auszüge; aktuelle Daten sind vorläufig. Heizgradtage sind eigene Ableitungen.

- https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/
- https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly/air_temperature/
- https://docs.github.com/en/actions/concepts/billing-and-usage
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
