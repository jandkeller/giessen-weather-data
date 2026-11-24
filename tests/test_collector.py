import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector
import dwd

START = datetime(2026, 11, 20, tzinfo=dwd.UTC)
STAMP = int(START.timestamp())


def archive(source, rows):
    field = 'TT_10' if source == '10min' else 'TT_TU'
    pattern = '%Y%m%d%H%M' if source == '10min' else '%Y%m%d%H'
    lines = [f'STATIONS_ID;MESS_DATUM;{field}']
    for seconds, value in rows:
        lines.append(f'1639;{(START + timedelta(seconds=seconds)).strftime(pattern)};{value}')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('produkt_test.txt', '\n'.join(lines))
    return output.getvalue()


def downloads(ten, hourly):
    def fetch(url):
        if url.endswith('historical/'):
            code = '10minutenwerte' if '10_minutes' in url else 'stundenwerte'
            return f'{code}_TU_01639_20200101_20251231_hist.zip'.encode()
        if '_hist.zip' in url:
            return archive('10min' if '10_minutes' in url else 'hourly', [])
        return archive('10min', ten) if '10_minutes' in url else archive('hourly', hourly)
    return fetch


class ArchiveChecks(unittest.TestCase):
    def test_mean_requires_six_valid_points(self):
        ten = {STAMP+n*600: float(n) for n in range(6)}
        result = collector.aggregate(ten, {STAMP: 99}, {}, START+timedelta(hours=1))
        self.assertEqual(result[STAMP], (2.5, '10min', 6))
        ten[STAMP+600] = None
        result = collector.aggregate(ten, {STAMP: 9}, {}, START+timedelta(hours=1))
        self.assertEqual(result[STAMP], (9, 'hourly', 5))
        result = collector.aggregate(ten, {}, {}, START+timedelta(hours=1))
        self.assertEqual(result[STAMP], (None, 'missing', 5))
        self.assertFalse(collector.aggregate(ten, {}, {}, START+timedelta(minutes=50)))

    def test_roundtrip_and_partial_hour_integral(self):
        rows = {STAMP: (10, '10min', 6), STAMP+3600: (0, 'hourly', 5), STAMP+7200: (None, 'missing', 0)}
        self.assertEqual(collector.decode_month(collector.encode_month(rows)), rows)
        result = collector.evaluate_hours(rows, START+timedelta(minutes=30), START+timedelta(hours=1, minutes=30))
        self.assertAlmostEqual(result['kelvin_days'], (10*1800+20*1800)/86400)
        self.assertEqual(result['hourly_fallback_seconds'],1800)
        self.assertTrue(result['complete'])
        with self.assertRaises(ValueError):
            collector.decode_month(b'timestamp,temperature_c,source,samples\n0,10,10min,5\n')

    def test_publication_repair_and_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)
            with patch.object(dwd,'fetch',downloads([(0,10),(600,-999),(3000,10)],[(0,0)])):
                first=collector.collect(output,START,START+timedelta(hours=2))
            self.assertEqual(first['expected_hour_count'],1)
            self.assertEqual(first['incomplete_10min_hour_count'],1)
            self.assertEqual(first['fallback_hour_count'],1)
            with patch.object(dwd,'fetch',downloads([(n*600,10) for n in range(6)],[(0,0)])):
                second=collector.collect(output,START,START+timedelta(hours=2))
            self.assertEqual(second['incomplete_10min_hour_count'],0)
            self.assertEqual(second['fallback_hour_count'],0)
            self.assertEqual(hashlib.sha256((output/'hours/2026-11.csv').read_bytes()).hexdigest(),second['months']['2026-11']['sha256'])
            # The next file's left edge is partial; keep the known complete hour.
            with patch.object(dwd,'fetch',downloads([(3000,10),(86400,5)],[(0,0)])):
                collector.collect(output,START,START+timedelta(days=1,hours=2))
            rows=collector.decode_month((output/'hours/2026-11.csv').read_bytes())
            self.assertEqual(rows[STAMP],(10,'10min',6))
            self.assertTrue((output/'index.html').exists())

    def test_total_failure_does_not_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)
            with patch.object(dwd,'fetch',downloads([(0,10)],[])):
                collector.collect(output,START,START+timedelta(hours=2))
            original={str(p):p.read_bytes() for p in output.rglob('*') if p.is_file()}
            with patch.object(dwd,'fetch',side_effect=OSError('unavailable')):
                with self.assertRaises(RuntimeError):
                    collector.collect(output,START,START+timedelta(hours=3))
            self.assertEqual(original,{str(p):p.read_bytes() for p in output.rglob('*') if p.is_file()})

    def test_corruption_blocks_update(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)
            with patch.object(dwd,'fetch',downloads([(0,10)],[])):
                collector.collect(output,START,START+timedelta(hours=2))
                (output/'hours/2026-11.csv').write_text('corrupt')
                with self.assertRaises(ValueError):
                    collector.collect(output,START,START+timedelta(hours=3))

    def test_empty_day_and_raw_archive_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)
            (output/'months').mkdir()
            raw='source,timestamp,temperature_c\n'+''.join(f'10min,{STAMP+n*600},10.0\n' for n in range(6))
            (output/'months/2026-11.csv').write_text(raw)
            manifest={'schema_version':1,'months':{'2026-11':{'file':'months/2026-11.csv','sha256':hashlib.sha256(raw.encode()).hexdigest()}}}
            (output/'manifest.json').write_text(json.dumps(manifest))
            with patch.object(dwd,'fetch',downloads([],[(86400,5)])):
                result=collector.collect(output,START,START+timedelta(days=2,hours=1))
            rows=collector.decode_month((output/'hours/2026-11.csv').read_bytes())
            self.assertEqual(rows[STAMP],(10,'10min',6))
            self.assertEqual(result['expected_hour_count'],48)
            self.assertEqual(result['missing_hour_count'],46)
            self.assertFalse((output/'months/2026-11.csv').exists())


if __name__=='__main__':
    unittest.main()
