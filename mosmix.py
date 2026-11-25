"""Gießen MOSMIX point temperatures, kept separate from observed weather."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import dwd

STATION = "10532"
URL = f"https://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_L/single_stations/{STATION}/kml/MOSMIX_L_LATEST_{STATION}.kmz"
KML = "{http://www.opengis.net/kml/2.2}"
DWD = "{https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd}"


def parse(body):
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        files = [n for n in archive.infolist() if n.filename.endswith('.kml')]
        if len(files) != 1 or files[0].file_size > 5_000_000:
            raise ValueError("Expected one bounded point forecast KML")
        root = ET.fromstring(archive.read(files[0]))
    issued = dwd.moment(root.findtext(f'.//{DWD}IssueTime'))
    steps = [int(dwd.moment(e.text).timestamp()) for e in root.findall(f'.//{DWD}TimeStep')]
    if len(steps) < 2 or any(t % 3600 for t in steps) or any(b <= a for a, b in zip(steps, steps[1:])):
        raise ValueError("Invalid MOSMIX time axis")
    points = root.findall(f'.//{KML}Placemark')
    if len(points) != 1 or points[0].findtext(f'{KML}name') != STATION:
        raise ValueError("Unexpected forecast point")
    temperatures = points[0].find(f'.//{DWD}Forecast[@{DWD}elementName="TTT"]/{DWD}value')
    if temperatures is None:
        raise ValueError("MOSMIX 2m temperatures missing")
    values = temperatures.text.split()
    if len(values) != len(steps):
        raise ValueError("MOSMIX temperature/time lengths differ")
    rows = []
    for stamp, token in zip(steps, values):
        value = None if token == '-' else float(token) - 273.15
        if value is not None and (not math.isfinite(value) or not -150 <= value <= 100):
            raise ValueError("Invalid MOSMIX temperature")
        rows.append((stamp, value))
    if sum(v is not None for _, v in rows) < 2:
        raise ValueError("No usable forecast temperatures")
    return issued, rows


def atomic(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_bytes(body)
    os.replace(temp, path)


def collect(output, now):
    """A failed/older download cannot replace the last usable forecast snapshot."""
    output = Path(output)
    index = output / 'forecast/latest.csv'
    previous = None
    if index.exists():
        records = list(csv.DictReader(io.StringIO(index.read_text())))
        if len(records) != 1:
            raise ValueError("Invalid previous forecast index")
        previous = records[0]
    try:
        issued, rows = parse(dwd.fetch(URL))
        if issued > now + timedelta(hours=1) or issued < now - timedelta(hours=24):
            raise ValueError("MOSMIX issue time is stale or in the future")
        if previous and issued < dwd.moment(previous['issued_at']):
            raise ValueError("MOSMIX run older than published forecast")
        buffer = io.StringIO(newline='')
        writer = csv.writer(buffer, lineterminator='\n')
        writer.writerow(['timestamp', 'temperature_c'])
        writer.writerows((stamp, '' if value is None else format(value, '.8g')) for stamp, value in rows)
        body = buffer.getvalue().encode()
        digest = hashlib.sha256(body).hexdigest()
        file = f"forecast/runs/{int(issued.timestamp())}-{digest[:16]}.csv"
        atomic(output / file, body)
        descriptor = {'station_id': STATION, 'issued_at': dwd.iso(issued), 'checked_at': dwd.iso(now),
                      'file': file, 'sha256': digest}
        buffer = io.StringIO(newline='')
        writer = csv.DictWriter(buffer, fieldnames=list(descriptor), lineterminator='\n')
        writer.writeheader()
        writer.writerow(descriptor)
        atomic(index, buffer.getvalue().encode())
        return dict(descriptor, available=True, download_ok=True, points=len(rows),
                    first_timestamp=rows[0][0], last_timestamp=rows[-1][0])
    except Exception as exc:
        return dict(previous or {}, available=previous is not None, download_ok=False, error=str(exc))
