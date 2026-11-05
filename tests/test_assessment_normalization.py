"""Regression fixtures reproduce malformed assessments, not genuine Qwen output."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from gi_coder.assessment import assess
from gi_coder.config import CoderError
from gi_coder.languages import normalize, extension_for, requested_language
from gi_coder.pipeline import process_request
from test_pipeline import assessment

CLOCK = ('Generate a python program that creates an accurate clock with an hour hand, '
         'minute hand and second hand. It should have notches at every hour mark '
         'and should be animated. Do not use tkinter')


class AssessmentNormalizationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'SliderGI'
        self.root.mkdir()
        self.runtime = Mock()
        self.runtime.config = {'debug_assessment': False}

    def respond(self, **changes):
        data = assessment(language='Python 3', filename='animated_clock.py', extension=None)
        data['constraints'] = ['Do not use tkinter', 'Animate all three hands', 'Hourly notches']
        data['revised_prompt'] = 'Create an accurate animated Python clock with three hands and hourly notches; no tkinter.'
        data.update(changes)
        self.runtime.complete.return_value = (json.dumps(data), {})
        return data

    def test_clock_request_recovers_missing_language_or_bad_model_guess(self):
        for language in ('Python 3', 'Python3.12', ' Python ', 'Python (Pygame)', None, 'unknown'):
            self.respond(language=language, extension=None)
            with self.subTest(language=language):
                result, _ = assess(self.runtime, CLOCK, root=self.root)
                self.assertEqual(result['language'], 'python')
                self.assertEqual(result['extension'], '.py')
                self.assertEqual(result['filename'], 'animated_clock.py')
                sent = json.loads(self.runtime.complete.call_args.args[1])
                self.assertEqual(sent['explicit_language'], 'python')
                self.assertEqual(sent['request'], CLOCK)
                self.assertIn('Do not use tkinter', result['constraints'])

    def test_original_request_reaches_generation_with_constraints(self):
        self.respond(language='Python (Pygame)', extension='PY')
        self.runtime.generate.return_value = ('```python\n# clock fixture\nprint("clock")\n```', {})
        with contextlib.redirect_stdout(io.StringIO()):
            result = process_request(self.runtime, CLOCK, root=self.root)
        self.assertEqual(result['path'].name, 'animated_clock.py')
        compile(result['path'].read_text(), str(result['path']), 'exec')
        revised = json.loads(self.runtime.generate.call_args.args[0])
        self.assertEqual(revised['original_request'], CLOCK)
        self.assertIn('no tkinter', revised['assessment']['revised_prompt'])

    def test_normalize_language_and_extension_variants(self):
        for value in ('PYTHON', ' Python 3 ', 'python3.12', 'Python-3', '.PY', 'py'):
            self.assertEqual(normalize(value), 'python')
        for value in (None, '', 'py', 'PY', ' .PY ', '.py'):
            self.assertEqual(extension_for(' Python 3 ', value), '.py')
        self.assertEqual(extension_for('typescript', 'D.TS'), '.d.ts')

    def test_explicit_cli_overrides_prompt_and_filename_conflicts_still_fail(self):
        self.respond(filename='clock.js')
        result, _ = assess(self.runtime, CLOCK, language='JS', root=self.root)
        self.assertEqual(result['language'], 'javascript')
        self.assertEqual(result['extension'], '.js')
        self.respond(filename='clock.js')
        with self.assertRaisesRegex(CoderError, 'conflicts'):
            assess(self.runtime, CLOCK, root=self.root)
        with self.assertRaisesRegex(CoderError, 'conflicts'):
            assess(self.runtime, CLOCK, filename='clock.js', root=self.root)

    def test_unknown_language_safety_and_conflicting_extensions(self):
        self.assertEqual(extension_for('zig', ' ZIG '), '.zig')
        for language, extension in [('zig', None), ('zig', '../zig'), ('zig', 'zig/evil'),
                                    ('python', '.js'), ('python', '.py/evil'), ('python', 0)]:
            with self.subTest(language=language, extension=extension), self.assertRaises(CoderError):
                extension_for(language, extension)

    def test_incidental_ambiguous_and_negated_language_mentions_are_not_overrides(self):
        self.assertIsNone(requested_language('Explain Python versus JavaScript'))
        self.assertIsNone(requested_language('Do not generate a Python program; explain clocks.'))
        self.assertIsNone(requested_language('Generate a Python program and create a JavaScript program.'))
        self.assertEqual(requested_language('Write an animated clock in Python 3.12'), 'python')
        self.assertEqual(requested_language('Write a C++ program'), 'cpp')

    def test_debug_retains_invalid_raw_json_before_normal_temp_files(self):
        self.runtime.complete.return_value = ('{"broken":', {})
        with patch.dict(os.environ, {'GI_CODER_DEBUG_ASSESSMENT': '1'}), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(CoderError, 'not valid JSON'):
                process_request(self.runtime, CLOCK, root=self.root)
        paths = list((self.root/'.gi-coder/debug').glob('assessment-*.json'))
        self.assertEqual(len(paths), 1)
        record = json.loads(paths[0].read_text())
        self.assertEqual(record['raw_assessment'], '{"broken":')
        self.assertIn('not valid JSON', record['validation_error'])
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(paths[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(paths[0].parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(list((self.root/'.gi-coder/tmp').iterdir()), [])
        self.runtime.generate.assert_not_called()

    def test_debug_retains_unknown_extension_validation_error(self):
        self.runtime.config['debug_assessment'] = True
        raw = self.respond(language='zig', filename='clock.zig', extension=None)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(CoderError, 'safe source extension'):
            assess(self.runtime, 'Make a clock', root=self.root)
        path = next((self.root/'.gi-coder/debug').glob('*.json'))
        record = json.loads(path.read_text())
        self.assertEqual(json.loads(record['raw_assessment']), raw)
        self.assertIn('safe source extension', record['validation_error'])

    def test_debug_off_by_default_and_retains_success_when_enabled(self):
        self.respond()
        with patch.dict(os.environ, {'GI_CODER_DEBUG_ASSESSMENT': '0'}):
            assess(self.runtime, CLOCK, root=self.root)
        self.assertFalse((self.root/'.gi-coder/debug').exists())
        self.runtime.config['debug_assessment'] = True
        with contextlib.redirect_stderr(io.StringIO()):
            assess(self.runtime, CLOCK, root=self.root)
        record = json.loads(next((self.root/'.gi-coder/debug').glob('*.json')).read_text())
        self.assertEqual(record['status'], 'validated')
        self.assertIsNone(record['validation_error'])
        self.assertEqual(record['normalized_assessment']['extension'], '.py')
