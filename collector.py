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
    writer.writerow(["timestamp", "temperature_c", "source", "samples"])
    for stamp, (value, source, count) in sorted(rows.items()):
        writer.writerow([stamp, "" if value is None else format(value, ".8g"), source, count])
    return output.getvalue().encode()


def decode_month(body):
    reader = csv.DictReader(io.StringIO(body.decode()))
    if reader.fieldnames != ["timestamp", "temperature_c", "source", "samples"]:
        raise ValueError("Invalid hourly archive columns")
    result = {}
    for row in reader:
        stamp, source, count = int(row["timestamp"]), row["source"], int(row["samples"])
        value = float(row["temperature_c"]) if row["temperature_c"] else None
        if stamp % 3600 or stamp in result or source not in {"10min", "hourly", "missing"} or not 0 <= count <= 6:
            raise ValueError("Invalid hourly archive record")
        if (source == "missing") != (value is None) or source == "10min" and count != 6:
            raise ValueError("Invalid hourly archive coverage")
        if value is not None and (not math.isfinite(value) or value == -999):
            raise ValueError("Invalid temperature")
        result[stamp] = (value, source, count)
    return result


def aggregate(ten, hourly, previous, now):
    rows = dict(previous)
    first, last = min(ten, default=0), max(ten, default=0)
    for stamp in sorted({t // 3600 * 3600 for t in ten} | set(hourly)):
        if stamp + 3600 > now.timestamp():
            continue  # only completed hours; current hour is still pending
        values = [ten.get(t) for t in range(stamp, stamp + 3600, 600)]
        count = sum(value is not None for value in values)
        observed_range = first <= stamp and last >= stamp + 3000
        old = rows.get(stamp)
        if count == 6:
            rows[stamp] = (sum(values) / 6, "10min", 6)
        elif old and old[1] == "10min" and not observed_range:
            continue  # partial rolling archive edge or failed download cannot erase known data
        elif hourly.get(stamp) is not None:
            rows[stamp] = (hourly[stamp], "hourly", count if observed_range or old is None else old[2])
        elif observed_range or old is None:
            rows[stamp] = (None, "missing", count)
    return rows


def evaluate_hours(rows, start, end):
    total, ten_seconds, fallback_seconds = 0.0, 0.0, 0.0
    for stamp, (value, source, count) in rows.items():
        seconds = max(0, min(end.timestamp(), stamp + 3600) - max(start.timestamp(), stamp))
        if seconds <= 0 or value is None:
            continue
        total += max(20 - value, 0) * seconds / 86400
        if source == "10min":
            ten_seconds += seconds
        else:
            fallback_seconds += seconds
    requested = max(0, (end - start).total_seconds())
    return {"kelvin_days": total, "covered_seconds": ten_seconds + fallback_seconds,
            "hourly_fallback_seconds": fallback_seconds, "ten_minute_seconds": ten_seconds,
            "requested_seconds": requested, "complete": requested > 0 and requested - ten_seconds - fallback_seconds < 1}


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

    previous = {}
    for month, info in old["months"].items():
        if re.fullmatch(r"\d{4}-\d{2}", month) is None or info["file"] not in {f"months/{month}.csv", f"hours/{month}.csv"}:
            raise ValueError("Invalid archive path")
        body = (output / info["file"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != info["sha256"]:
            raise ValueError(f"Archive checksum mismatch: {month}")
        if old.get("schema_version") == 2:
            previous.update(decode_month(body))
        else:
            # One-time migration of the initial raw archive to hourly aggregates.
            reader = csv.DictReader(io.StringIO(body.decode()))
            for row in reader:
                source, stamp = row["source"], int(row["timestamp"])
                incoming[source].setdefault(stamp, float(row["temperature_c"]) if row["temperature_c"] else None)
    ten = {t: v for t, v in incoming["10min"].items() if start.timestamp() <= t <= now.timestamp()}
    hourly = {t: v for t, v in incoming["hourly"].items() if start.timestamp() <= t <= now.timestamp()}
    rows = aggregate(ten, hourly, previous, now)
    months = {}
    for stamp, value in rows.items():
        month = datetime.fromtimestamp(stamp, dwd.UTC).strftime("%Y-%m")
        months.setdefault(month, {})[stamp] = value
    descriptors = {}
    for month, values in sorted(months.items()):
        body = encode_month(values)
        path = f"hours/{month}.csv"
        write_atomic(output / path, body)
        descriptors[month] = {"file": path, "sha256": hashlib.sha256(body).hexdigest(), "rows": len(values)}
    due_end = int((now - timedelta(hours=1)).timestamp()) // 3600 * 3600
    expected = range(math.ceil(start.timestamp() / 3600) * 3600, due_end, 3600)
    missing = [stamp for stamp in expected if rows.get(stamp, (None, "missing", 0))[0] is None]
    incomplete = [stamp for stamp in expected if rows.get(stamp, (None, "missing", 0))[2] != 6]
    yesterday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    yesterday_result = evaluate_hours(rows, yesterday, yesterday + timedelta(days=1))
    status = {"schema_version": 2, "station_id": "01639", "station_name": "Gießen/Wettenberg",
              "checked_at": dwd.iso(now), "archive_start": dwd.iso(start), "schedule": "0,30 * * * *",
              "resolution_seconds": 3600, "aggregation": "mean of six instantaneous 10-minute observations per completed UTC hour",
              "publication_grace_seconds": 3600, "expected_hours_before": dwd.iso(datetime.fromtimestamp(due_end, dwd.UTC)),
              "expected_hour_count": len(expected), "missing_hour_count": len(missing),
              "incomplete_10min_hour_count": len(incomplete),
              "missing_hour_first_100": [dwd.iso(datetime.fromtimestamp(t, dwd.UTC)) for t in missing[:100]],
              "aggregate_hour_count": sum(v[1] == "10min" for v in rows.values()),
              "fallback_hour_count": sum(v[1] == "hourly" for v in rows.values()),
              "historical_checked_at": dwd.iso(now) if history_due and history_ok else old.get("historical_checked_at"),
              "download_errors": errors, "yesterday": yesterday_result,
              "attribution": "Deutscher Wetterdienst (DWD), CC BY 4.0; current data provisional"}
    # Hourly aggregates are retained permanently; raw 10-minute observations are transient.
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
<h1>Wetterarchiv Gießen/Wettenberg</h1><p>DWD-Station 01639 · Stundenmittel aus 10-Minuten-Messungen mit DWD-Stundenwerten als Ersatz bei Lücken.</p>
<p>Letzter abgeschlossener Abruf: <strong>{html.escape(dwd.iso(now))}</strong> (UTC). Geplant um :00 und :30; GitHub kann Läufe verzögern.</p>
<p>{status['aggregate_hour_count']:,} Stundenmittel aus je sechs 10-Minuten-Messungen und {status['fallback_hour_count']:,} Ersatz-Stundenwerte seit {start.date()}.</p>
<p>Gestern: {yesterday_result['kelvin_days']:.2f} K·Tage (Basis 20 °C), {100*yesterday_result['covered_seconds']/86400:.1f} % Abdeckung, davon {yesterday_result['hourly_fallback_seconds']/3600:.2f} Stunden Ersatzdaten.</p>
<p>{len(errors)} Downloadfehler im letzten Lauf; {len(missing):,} fehlende Stunden im Archiv (letzte Stunde noch ausstehend).</p>
<p><a href="status.json">Abrufstatus</a> · <a href="manifest.json">Dateiindex (JSON)</a> · <a href="manifest.csv">Dateiindex (CSV)</a></p>
<small>Quelle: <a href="https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/">Deutscher Wetterdienst</a>, CC BY 4.0. Aktuelle Messungen sind vorläufig. Wetterdaten und Collector sind öffentlich; Verbrauchsdaten werden nicht erfasst.</small></html>'''
    write_atomic(output / "index.html", page.encode())
    print(json.dumps({k: v for k, v in status.items() if k not in {"missing_hour_first_100", "yesterday"}}, ensure_ascii=False))
    for raw in (output / "months").glob("*.csv"):
        raw.unlink()
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("site"))
    parser.add_argument("--start", default="2020-01-01")
    args = parser.parse_args()
    collect(args.output, datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=dwd.UTC))


if __name__ == "__main__":
    main()
