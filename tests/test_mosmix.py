import csv
import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dwd
import mosmix

NOW = datetime(2026, 11, 25, 15, tzinfo=dwd.UTC)


def fixture(issued=NOW, values=('273.15', '275.15', '-'), station='10532', steps=None):
    steps = steps or [NOW+timedelta(hours=n+1) for n in range(3)]
    xml = f'''<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:d="https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd"><Document>
<d:IssueTime>{dwd.iso(issued)}</d:IssueTime><d:ForecastTimeSteps>{''.join('<d:TimeStep>'+dwd.iso(t)+'</d:TimeStep>' for t in steps)}</d:ForecastTimeSteps>
<Placemark><name>{station}</name><ExtendedData><d:Forecast d:elementName="TTT"><d:value>{' '.join(values)}</d:value></d:Forecast></ExtendedData></Placemark></Document></kml>'''
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z: z.writestr('forecast.kml',xml)
    return out.getvalue()


class MosmixChecks(unittest.TestCase):
    def test_kelvin_to_celsius_and_missing(self):
        issued, rows=mosmix.parse(fixture())
        self.assertEqual(issued,NOW)
        self.assertAlmostEqual(rows[0][1],0)
        self.assertAlmostEqual(rows[1][1],2)
        self.assertIsNone(rows[2][1])
        self.assertEqual(rows[0][0],int((NOW+timedelta(hours=1)).timestamp()))

    def test_validation(self):
        for body in (fixture(station='10637'),fixture(values=('273.15',)),fixture(values=('NaN','273','273')),
                     fixture(steps=[NOW,NOW,NOW+timedelta(hours=1)]),b'not a zip'):
            with self.assertRaises(Exception): mosmix.parse(body)

    def test_publish_and_failure_retains_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(dwd,'fetch',return_value=fixture()): status=mosmix.collect(folder,NOW)
            self.assertTrue(status['download_ok'])
            self.assertEqual(hashlib.sha256((Path(folder)/status['file']).read_bytes()).hexdigest(),status['sha256'])
            original=(Path(folder)/'forecast/latest.csv').read_bytes()
            with patch.object(dwd,'fetch',side_effect=OSError('offline')): failed=mosmix.collect(folder,NOW+timedelta(hours=1))
            self.assertFalse(failed['download_ok'])
            self.assertTrue(failed['available'])
            self.assertEqual((Path(folder)/'forecast/latest.csv').read_bytes(),original)
            with patch.object(dwd,'fetch',return_value=fixture(issued=NOW-timedelta(hours=6))): older=mosmix.collect(folder,NOW)
            self.assertFalse(older['download_ok'])
            self.assertEqual((Path(folder)/'forecast/latest.csv').read_bytes(),original)

    def test_expired_and_future_runs_not_published(self):
        for issued in [NOW-timedelta(hours=25),NOW+timedelta(hours=2)]:
            with tempfile.TemporaryDirectory() as folder,patch.object(dwd,'fetch',return_value=fixture(issued=issued)):
                self.assertFalse(mosmix.collect(folder,NOW)['available'])
                self.assertFalse((Path(folder)/'forecast/latest.csv').exists())
