"""Publish a permanent, compact weather archive as static GitHub Pages files."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import dwd


def encode_month(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["source", "timestamp", "temperature_c"])
    for (source, stamp), value in sorted(rows.items()):
        writer.writerow([source, stamp, "" if value is None else format(value, ".1f")])
    return output.getvalue().encode()


def decode_month(body):
    result = {}
    reader = csv.DictReader(io.StringIO(body.decode()))
    if reader.fieldnames != ["source", "timestamp", "temperature_c"]:
        raise ValueError("Invalid archive columns")
    for row in reader:
        source, stamp = row["source"], int(row["timestamp"])
        if source not in {"10min", "hourly"}:
            raise ValueError("Invalid archive source")
        raw = row["temperature_c"]
        value = float(raw) if raw else None
        if value is not None and (not math.isfinite(value) or value == -999):
            raise ValueError("Invalid archive temperature")
        result[(source, stamp)] = value
    return result


def write_atomic(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    os.replace(temporary, path)


def collect(output, start, now=None):
    now = now or datetime.now(dwd.UTC)
    output = Path(output)
    if start.year < 2000 or start >= now:
        raise ValueError("Archive start must be between 2000 and now")
    old = json.loads((output / "manifest.json").read_text()) if (output / "manifest.json").exists() else {"months": {}}
    if old.get("archive_start"):
        start = min(start, dwd.moment(old["archive_start"]))
    history_due = not old.get("historical_checked_at") or now - dwd.moment(old["historical_checked_at"]) >= timedelta(days=7)
    history_ok = True
    incoming = {"10min": {}, "hourly": {}}
    errors = []
    for source, directory, code in [("10min", "10_minutes", "10minutenwerte"), ("hourly", "hourly", "stundenwerte")]:
        root = dwd.BASE + directory + "/air_temperature/"
        files = []
        if history_due:
            try:
                listing = dwd.fetch(root + "historical/").decode()
                names = set(re.findall(rf'{code}_TU_01639_(\d{{8}})_(\d{{8}})_hist\.zip', listing))
                if not names:
                    raise ValueError("No historical station archives found")
                for first, last in sorted(names):
                    if datetime.strptime(last, "%Y%m%d").replace(tzinfo=dwd.UTC) + timedelta(days=1) >= start:
                        files.append((f"historical/{code}_TU_01639_{first}_{last}_hist.zip", True))
            except Exception as exc:
                history_ok = False
                errors.append({"source": source, "file": "historical/", "error": str(exc)})
        files.append((f"recent/{code}_TU_01639_akt.zip", False))
        if source == "10min":
            files.append((f"now/{code}_TU_01639_now.zip", False))
        for path, historical in files:
            try:
                incoming[source].update(dwd.parse_archive(dwd.fetch(root + path), source))
            except Exception as exc:
                if historical:
                    history_ok = False
                errors.append({"source": source, "file": path, "error": str(exc)})
    if not any(incoming.values()):
        raise RuntimeError("All DWD downloads failed; previous archive is unchanged")

    months = {}
    for month, info in old["months"].items():
        if re.fullmatch(r"\d{4}-\d{2}", month) is None or info["file"] != f"months/{month}.csv":
            raise ValueError("Invalid archive path")
        body = (output / info["file"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != info["sha256"]:
            raise ValueError(f"Archive checksum mismatch: {month}")
        months[month] = decode_month(body)
    changed = set()
    for source, samples in incoming.items():
        for stamp, value in samples.items():
            if start.timestamp() <= stamp <= now.timestamp():
                month = datetime.fromtimestamp(stamp, dwd.UTC).strftime("%Y-%m")
                rows = months.setdefault(month, {})
                if (source, stamp) not in rows or rows[source, stamp] != value:
                    rows[source, stamp] = value
                    changed.add(month)
    descriptors = dict(old["months"])
    for month in sorted(changed):
        body = encode_month(months[month])
        path = f"months/{month}.csv"
        write_atomic(output / path, body)
        descriptors[month] = {"file": path, "sha256": hashlib.sha256(body).hexdigest(), "rows": len(months[month])}
    ten = {stamp: value for rows in months.values() for (source, stamp), value in rows.items() if source == "10min"}
    hourly = {stamp: value for rows in months.values() for (source, stamp), value in rows.items() if source == "hourly"}
    cutoff = int((now - timedelta(hours=1)).timestamp()) // 600 * 600
    expected = range(math.ceil(start.timestamp() / 600) * 600, cutoff + 1, 600)
    missing = [stamp for stamp in expected if ten.get(stamp) is None]
    yesterday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    yesterday_result = dwd.evaluate(ten, hourly, yesterday, yesterday + timedelta(days=1))
    status = {"schema_version": 1, "station_id": "01639", "station_name": "Gießen/Wettenberg",
              "checked_at": dwd.iso(now), "archive_start": dwd.iso(start), "schedule": "0,30 * * * *",
              "publication_grace_seconds": 3600, "expected_10min_through": dwd.iso(datetime.fromtimestamp(cutoff, dwd.UTC)),
              "expected_10min_count": len(expected), "missing_10min_count": len(missing),
              "missing_10min_first_100": [dwd.iso(datetime.fromtimestamp(t, dwd.UTC)) for t in missing[:100]],
              "ten_minute_valid_count": sum(v is not None for v in ten.values()),
              "hourly_valid_count": sum(v is not None for v in hourly.values()),
              "latest_10min": dwd.iso(datetime.fromtimestamp(max((t for t, v in ten.items() if v is not None), default=int(start.timestamp())), dwd.UTC)),
              "historical_checked_at": dwd.iso(now) if history_due and history_ok else old.get("historical_checked_at"),
              "download_errors": errors, "yesterday": yesterday_result,
              "attribution": "Deutscher Wetterdienst (DWD), CC BY 4.0; current data provisional"}
    # Raw observations are retained; no TTL, no Actions cache/artifacts as permanent storage.
    manifest = dict(status, months=dict(sorted(descriptors.items())))
    write_atomic(output / "manifest.json", json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
    write_atomic(output / "status.json", json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False).encode())
    index = io.StringIO(newline="")
    writer = csv.writer(index, lineterminator="\n")
    writer.writerow(["station_id", "checked_at", "archive_start", "month", "sha256"])
    for month, info in sorted(descriptors.items()):
        writer.writerow(["01639", dwd.iso(now), dwd.iso(start), month, info["sha256"]])
    write_atomic(output / "manifest.csv", index.getvalue().encode())
    write_atomic(output / ".nojekyll", b"")
    page = f'''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Wetterarchiv Gießen</title>
<style>body{{font:18px/1.6 system-ui;max-width:760px;margin:60px auto;padding:0 24px;color:#183c38;background:#f7faf8}}a{{color:#165c51}}small{{color:#536960}}</style>
<h1>Wetterarchiv Gießen/Wettenberg</h1><p>DWD-Station 01639 · 10-Minuten-Temperaturen mit Stundenwerten als Ersatz bei Lücken.</p>
<p>Letzter abgeschlossener Abruf: <strong>{html.escape(dwd.iso(now))}</strong> (UTC). Geplant um :00 und :30; GitHub kann Läufe verzögern.</p>
<p>{status['ten_minute_valid_count']:,} gültige 10-Minuten-Messungen und {status['hourly_valid_count']:,} Stundenmessungen seit {start.date()}.</p>
<p>Gestern: {yesterday_result['kelvin_days']:.2f} K·Tage (Basis 20 °C), {100*yesterday_result['covered_seconds']/86400:.1f} % Abdeckung, davon {yesterday_result['hourly_fallback_seconds']/3600:.2f} Stunden Ersatzdaten.</p>
<p>{len(errors)} Downloadfehler im letzten Lauf; {len(missing):,} fehlende 10-Minuten-Zeitpunkte im Archiv (letzte Stunde noch ausstehend).</p>
<p><a href="status.json">Abrufstatus</a> · <a href="manifest.json">Dateiindex (JSON)</a> · <a href="manifest.csv">Dateiindex (CSV)</a></p>
<small>Quelle: <a href="https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/">Deutscher Wetterdienst</a>, CC BY 4.0. Aktuelle Messungen sind vorläufig. Wetterdaten und Collector sind öffentlich; Verbrauchsdaten werden nicht erfasst.</small></html>'''
    write_atomic(output / "index.html", page.encode())
    print(json.dumps({k: v for k, v in status.items() if k not in {"missing_10min_first_100", "yesterday"}}, ensure_ascii=False))
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("site"))
    parser.add_argument("--start", default="2020-01-01")
    args = parser.parse_args()
    collect(args.output, datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=dwd.UTC))


if __name__ == "__main__":
    main()
