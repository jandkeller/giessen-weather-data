"""DWD download, normalization and HDD20 integration; standard library only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

UTC = timezone.utc
BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/"
STATION = "01639"


def iso(moment):
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def moment(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return parsed.astimezone(UTC)


def fetch(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "Heizoel-HDD20/1.0"}), timeout=90) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def parse_archive(body, source):
    """Keep invalid values as null, so a correction can invalidate an older value."""
    field = "TT_10" if source == "10min" else "TT_TU"
    pattern = "%Y%m%d%H%M" if source == "10min" else "%Y%m%d%H"
    result = {}
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = [n for n in archive.namelist() if n.startswith("produkt_") and n.endswith(".txt")]
        if len(names) != 1:
            raise ValueError("Expected one DWD product file")
        text = archive.read(names[0]).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if reader.fieldnames is None:
        raise ValueError("Missing DWD CSV header")
    reader.fieldnames = [n.strip() for n in reader.fieldnames]
    if not {field, "MESS_DATUM", "STATIONS_ID"} <= set(reader.fieldnames):
        raise ValueError("Unexpected DWD columns")
    for row in reader:
        if int(row["STATIONS_ID"]) != int(STATION):
            raise ValueError("Unexpected DWD station")
        date = datetime.strptime(row["MESS_DATUM"].strip(), pattern)
        # Historical 10-minute timestamps before 2000 use fixed CET, not DST.
        zone = timezone(timedelta(hours=1)) if source == "10min" and date.year < 2000 else UTC
        stamp = int(date.replace(tzinfo=zone).timestamp())
        value = float(row[field])
        result[stamp] = value if math.isfinite(value) and value != -999 else None
    return result


def positive_area(x, y, seconds):
    if x >= 0 and y >= 0:
        return (x + y) * seconds / 2
    if x <= 0 and y <= 0:
        return 0.0
    return max(x, y) ** 2 / abs(x - y) * seconds / 2


def segments(samples, step):
    # Invalid points break interpolation; removing them must not bridge a missing step.
    ordered = sorted(samples.items())
    return [(a, b, x, y) for (a, x), (b, y) in zip(ordered, ordered[1:])
            if b - a == step and x is not None and y is not None]


def evaluate(ten, hourly, start, end):
    if not start < end:
        raise ValueError("from must be before to")
    bounds = (start.timestamp(), end.timestamp())
    primary = segments(ten, 600)
    # Each hourly interval is at most 3600s, so only nearby primary intervals can overlap.
    primary_by_hour = {}
    total = 0.0
    covered = {"10min": 0.0, "hourly": 0.0}

    def add(a, b, x, y, left, right, source):
        nonlocal total
        left, right = max(left, bounds[0]), min(right, bounds[1])
        if right <= left:
            return
        slope = (y - x) / (b - a)
        total += positive_area(20 - x - slope * (left - a), 20 - x - slope * (right - a), right - left) / 86400
        covered[source] += right - left

    for a, b, x, y in primary:
        if b <= bounds[0] or a >= bounds[1]:
            continue
        add(a, b, x, y, a, b, "10min")
        for hour in range(a // 3600, (b - 1) // 3600 + 1):
            primary_by_hour.setdefault(hour, []).append((a, b))
    for a, b, x, y in segments(hourly, 3600):
        if b <= bounds[0] or a >= bounds[1]:
            continue
        cursor = a
        overlaps = sorted(set(primary_by_hour.get(a // 3600, []) + primary_by_hour.get((b - 1) // 3600, [])))
        for left, right in overlaps:
            if right <= cursor or left >= b:
                continue
            add(a, b, x, y, cursor, min(left, b), "hourly")
            cursor = max(cursor, right)
        add(a, b, x, y, cursor, b, "hourly")
    requested = bounds[1] - bounds[0]
    missing = max(0, requested - sum(covered.values()))
    return {"station_id": STATION, "station_name": "Gießen/Wettenberg", "base_temperature_c": 20,
            "from": iso(start), "to": iso(end), "kelvin_days": total,
            "requested_seconds": requested, "covered_seconds": sum(covered.values()),
            "ten_minute_seconds": covered["10min"], "hourly_fallback_seconds": covered["hourly"],
            "missing_seconds": missing, "complete": missing < 1,
            "method": "positive temperature deficit, linear interpolation, hourly fallback only in gaps",
            "attribution": "Deutscher Wetterdienst (DWD), CC BY 4.0; recent observations provisional"}


def day_keys(start, end):
    current = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= end:
        yield current.date().isoformat()
        current += timedelta(days=1)

