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
    def test_roundtrip_and_nulls(self):
        rows = {('10min', STAMP): None, ('hourly', STAMP): 2.1}
        self.assertEqual(collector.decode_month(collector.encode_month(rows)), rows)
        with self.assertRaises(ValueError):
            collector.decode_month(b'source,timestamp,temperature_c\n10min,1,-999\n')

    def test_publication_repair_and_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with patch.object(dwd, 'fetch', downloads([(0, 10), (600, -999), (1200, 10)], [(0, 0), (3600, 0)])):
                first = collector.collect(output, START, START + timedelta(hours=2))
            self.assertEqual(first['expected_10min_count'], 7)
            self.assertEqual(first['missing_10min_count'], 5)
            rows = collector.decode_month((output / 'months/2026-11.csv').read_bytes())
            ten = {t: v for (s, t), v in rows.items() if s == '10min'}
            hour = {t: v for (s, t), v in rows.items() if s == 'hourly'}
            fallback = dwd.evaluate(ten, hour, START, START + timedelta(hours=1))
            self.assertTrue(fallback['complete'])
            self.assertEqual(fallback['hourly_fallback_seconds'], 3600)
            with patch.object(dwd, 'fetch', downloads([(n * 600, 10) for n in range(7)], [(0, 0), (3600, 0)])):
                second = collector.collect(output, START, START + timedelta(hours=2))
            self.assertEqual(second['missing_10min_count'], 0)
            digest = second['months']['2026-11']['sha256']
            import hashlib
            self.assertEqual(hashlib.sha256((output / 'months/2026-11.csv').read_bytes()).hexdigest(), digest)
            # A later refresh with a partial file keeps all older observations.
            with patch.object(dwd, 'fetch', downloads([(86400, 5)], [])):
                collector.collect(output, START, START + timedelta(days=1, hours=2))
            rows = collector.decode_month((output / 'months/2026-11.csv').read_bytes())
            self.assertEqual(rows['10min', STAMP + 600], 10)
            self.assertEqual(rows['10min', STAMP + 86400], 5)
            self.assertTrue((output / 'index.html').exists())

    def test_failed_download_retains_published_state(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with patch.object(dwd, 'fetch', downloads([(0, 10)], [])):
                collector.collect(output, START, START + timedelta(hours=2))
            original = {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()}
            with patch.object(dwd, 'fetch', side_effect=OSError('unavailable')):
                with self.assertRaises(RuntimeError):
                    collector.collect(output, START, START + timedelta(hours=3))
            self.assertEqual(original, {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()})

    def test_hash_mismatch_stops_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with patch.object(dwd, 'fetch', downloads([(0, 10)], [])):
                collector.collect(output, START, START + timedelta(hours=2))
                (output / 'months/2026-11.csv').write_text('corrupt')
                with self.assertRaises(ValueError):
                    collector.collect(output, START, START + timedelta(hours=3))

    def test_no_double_count_and_partial_intervals(self):
        ten = {STAMP: 10, STAMP + 600: 10, STAMP + 1200: None, STAMP + 1800: 10, STAMP + 2400: 10, STAMP + 3000: 10, STAMP + 3600: 10}
        result = dwd.evaluate(ten, {STAMP: 0, STAMP + 3600: 0}, START, START + timedelta(hours=1))
        self.assertEqual(result['ten_minute_seconds'], 2400)
        self.assertEqual(result['hourly_fallback_seconds'], 1200)
        self.assertAlmostEqual(result['kelvin_days'], (2400 * 10 + 1200 * 20) / 86400)
        crossing = dwd.evaluate({}, {STAMP: 10, STAMP + 3600: 30}, START + timedelta(minutes=15), START + timedelta(minutes=45))
        self.assertAlmostEqual(crossing['kelvin_days'], 2250 / 86400)
        missing = dwd.evaluate({}, {STAMP: 10, STAMP + 7200: 10}, START, START + timedelta(hours=2))
        self.assertEqual(missing['covered_seconds'], 0)

    def test_missing_whole_day_is_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(dwd, 'fetch', downloads([], [(0, 10), (3600, 10)])):
                result = collector.collect(Path(temp), START, START + timedelta(days=2, hours=1))
            self.assertEqual(result['missing_10min_count'], 289)


if __name__ == '__main__':
    unittest.main()
