"""Verify the portable offline dataset and its single analysis pipeline."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'data').mkdir()
        for name in ['experiment.py', 'requirements.txt']:
            shutil.copy2(BASE / name, self.root / name)
        shutil.copy2(BASE / 'data' / 'dataset.json', self.root / 'data' / 'dataset.json')

    def execute(self, expression, succeeds=True):
        code = '''import socket

def deny(*args, **kwargs):
    raise AssertionError('Network access is forbidden')
socket.socket.connect = deny
socket.create_connection = deny
''' + expression
        result = subprocess.run([sys.executable, '-c', code], cwd=self.root,
                                env=dict(os.environ, MPLCONFIGDIR=str(self.root / '.cache' / 'matplotlib')),
                                text=True, capture_output=True, timeout=60)
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def dataset(self):
        return json.loads((self.root / 'data' / 'dataset.json').read_text())

    def save_dataset(self, data):
        (self.root / 'data' / 'dataset.json').write_text(json.dumps(data, ensure_ascii=False))

    def test_clean_checkout_reproduces_baseline_and_charts_without_archive_or_network(self):
        self.execute('from experiment import run; run()')
        report = json.loads((self.root / 'artifacts' / 'result.json').read_text())
        expected = json.loads((BASE / 'tests' / 'baseline.json').read_text())
        self.assertEqual(report['source_rows'], 1025)
        self.assertEqual(report['selected_targets'], 194)
        self.assertEqual(report['checkable_targets'], 180)
        self.assertEqual(len(report['evaluated_ids']), 180)
        comparisons = report['comparison_group_members']
        self.assertEqual(sum(g.startswith('agency:') for g in comparisons), 6)
        self.assertEqual(sum(g.startswith('company:') for g in comparisons), 4)
        self.assertEqual(len(comparisons['agency:easy_hunt']), 1)
        self.assertEqual(len(comparisons['company:wild_wild_group']), 1)
        for detector, groups in expected.items():
            self.assertEqual(report['detectors'][detector]['usable_results'], 180)
            self.assertEqual(report['detectors'][detector]['groups'].keys(), groups.keys())
            for name, stats in groups.items():
                got = report['detectors'][detector]['groups'][name]
                self.assertEqual(got['n'], stats['n'])
                self.assertEqual(got['detected'], stats['detected'])
                self.assertAlmostEqual(got['mean'], stats['mean'], places=14)
        self.assertEqual({p.name for p in (self.root / 'artifacts').iterdir()},
                         {'result.json', 'aiornot_chart.png', 'pangram_chart.png',
                          'gptzero_chart.png', 'agencies_chart.png', 'companies_chart.png',
                          'ai_and_aggregates_chart.png'})
        for path in (self.root / 'artifacts').glob('*.png'):
            self.assertGreater(path.stat().st_size, 1000)
            if path.name in {'agencies_chart.png', 'companies_chart.png', 'ai_and_aggregates_chart.png'}:
                from PIL import Image
                with Image.open(path) as image:
                    self.assertEqual(image.size, (2000, 2000))

    def test_repeated_runs_and_input_reordering_preserve_results(self):
        self.execute('from experiment import run; run(charts=False)')
        path = self.root / 'artifacts' / 'result.json'
        before = path.read_bytes()
        original = json.loads(before)
        self.execute('from experiment import run; run(charts=False)')
        self.assertEqual(path.read_bytes(), before)
        data = self.dataset()
        data['vacancies'].reverse()
        self.save_dataset(data)
        self.execute('from experiment import run; run(charts=False)')
        reordered = json.loads(path.read_text())
        for key in ['detectors', 'selected_ids', 'evaluated_ids', 'group_members']:
            self.assertEqual(reordered[key], original[key])
        self.assertNotEqual(reordered['dataset_sha256'], original['dataset_sha256'])

    def test_incomplete_or_outside_cohort_results_fail_before_overwriting_output(self):
        self.execute('from experiment import run; run(charts=False)')
        path = self.root / 'artifacts' / 'result.json'
        before = path.read_bytes()
        original = self.dataset()
        measured = next(row for row in original['vacancies'] if row['results'])
        measured['results'].pop('pangram')
        self.save_dataset(original)
        failure = self.execute('from experiment import run; run(charts=False)', succeeds=False)
        self.assertIn('Incomplete detector coverage', failure.stderr)
        self.assertEqual(path.read_bytes(), before)
        original = json.loads((BASE / 'data' / 'dataset.json').read_text())
        unselected = next(row for row in original['vacancies'] if not row['results'] and row['owner'] == 'unknown')
        unselected['results'] = {'pangram': {'score': 1.0, 'detected': True}}
        self.save_dataset(original)
        failure = self.execute('from experiment import run; run(charts=False)', succeeds=False)
        self.assertIn('Results outside the evaluated cohort', failure.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_duplicate_changed_text_and_invalid_results_are_rejected(self):
        original = self.dataset()
        duplicate = self.dataset()
        duplicate['vacancies'].append(duplicate['vacancies'][0])
        self.save_dataset(duplicate)
        failure = self.execute('from experiment import analyze; analyze()', succeeds=False)
        self.assertIn('Duplicate vacancy', failure.stderr)
        changed = json.loads(json.dumps(original))
        changed['vacancies'][0]['text'] += ' changed'
        self.save_dataset(changed)
        failure = self.execute('from experiment import analyze; analyze()', succeeds=False)
        self.assertIn('Content ID mismatch', failure.stderr)
        for result, message in [({'score': float('nan'), 'detected': False}, 'Invalid pangram score'),
                                ({'score': 0.1, 'detected': 'false'}, 'Invalid pangram verdict')]:
            invalid = json.loads(json.dumps(original))
            row = next(r for r in invalid['vacancies'] if r['results'])
            row['results']['pangram'] = result
            self.save_dataset(invalid)
            failure = self.execute('from experiment import analyze; analyze()', succeeds=False)
            self.assertIn(message, failure.stderr)

    def test_collected_data_is_read_only(self):
        path = self.root / 'data' / 'dataset.json'
        before = path.read_bytes()
        self.execute('from experiment import run; run(charts=False)')
        self.assertEqual(path.read_bytes(), before)
        # Old collection commands are no longer accepted by the sole entry point.
        result = subprocess.run([sys.executable, 'experiment.py', 'collect', 'aiornot'],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('unrecognized arguments', result.stderr)


if __name__ == '__main__':
    unittest.main()
