"""Offline regression benchmarks with scripted completions, never real model output.

GI_CODER_BENCHMARK_REPORT=/tmp/report.json writes measured harness latency and
synthetic token/call counts. This measures orchestration, not Metal/Qwen speed.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

from gi_coder.config import CoderError
from gi_coder.pipeline import process_request
from gi_coder.project import (PLAN_SCHEMA, REVIEW_SCHEMA, ReviewEvidenceError,
                              decode, validate_plan, verify_evidence)
from gi_coder.runtime import RuntimeFailure
from gi_coder.verification import verify_source
from clock_project_fixture import plan
from test_pipeline import assessment

FIBONACCI = '''def fibonacci(n):
    if not isinstance(n, int) or n < 0:
        raise ValueError("n must be a nonnegative integer")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
'''
CALCULATOR = '''def calculate(operation, a, b):
    if operation == "+":
        return a + b
    if operation == "-":
        return a - b
    if operation == "*":
        return a * b
    if operation == "/":
        if b == 0:
            raise ValueError("division by zero")
        return a / b
    raise ValueError("unknown operation")

def main():
    try:
        a = float(input())
        operation = input().strip()
        b = float(input())
        print(calculate(operation, a, b))
    except ValueError as error:
        print("Error:", error)

if __name__ == "__main__":
    main()
'''
USAGE = {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}


def review(snippet='return a', **updates):
    return {'verdict': 'pass', 'evidence': 'Source implements the requirement',
            'evidence_snippets': [snippet], 'issues': [], 'missing_requirements': [], **updates}


class ReliabilityBenchmarks(unittest.TestCase):
    rows = []

    @classmethod
    def tearDownClass(cls):
        report = os.environ.get('GI_CODER_BENCHMARK_REPORT')
        if report:
            Path(report).write_text(json.dumps({'kind': 'scripted_offline_orchestration',
                'cases': cls.rows}, indent=2) + '\n')

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runtime = Mock()
        self.runtime.config = {'project_timeout': 60}
        self.data = assessment(language='python', filename='program.py', extension='.py')
        self.data['complexity'] = 'moderate'  # Historical Fibonacci failure.
        self.responses = []
        self.review_responses = [review()]
        self.sources = [FIBONACCI]
        self.runtime.generate.side_effect = lambda *args, **kwargs: (self.sources[0], USAGE)
        def complete(system, prompt, schema=None, **kwargs):
            self.responses.append((system, json.loads(prompt), schema))
            if schema == REVIEW_SCHEMA:
                value = self.review_responses.pop(0) if len(self.review_responses) > 1 else self.review_responses[0]
                if isinstance(value, BaseException):
                    raise value
                return json.dumps(value), USAGE
            if schema is None:
                value = self.sources.pop(0) if len(self.sources) > 1 else self.sources[0]
                if isinstance(value, BaseException):
                    raise value
                return value, USAGE
            self.assertNotEqual(schema, PLAN_SCHEMA, 'Small requests must never plan')
            return json.dumps(self.data), USAGE
        self.runtime.complete.side_effect = complete

    def run_request(self, prompt='Write a Python function to calculate Fibonacci numbers.'):
        started = time.monotonic()
        success = False
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                result = process_request(self.runtime, prompt, root=self.root, output_dir=self.root/'out')
            success = True
            return result
        finally:
            work = list((self.root/'.gi-coder/projects').glob('*/manifest.json'))
            record = json.loads(work[0].read_text()) if work else {}
            self.rows.append({'case': self._testMethodName, 'seconds': time.monotonic()-started,
                              'completion': success, 'metrics': record.get('metrics', {})})

    def test_fibonacci_direct_success_metrics_and_manifest_hash(self):
        result = self.run_request()
        self.assertEqual(result['metrics']['model_calls'], 3)
        self.assertEqual(result['metrics']['total_tokens'], 90)
        self.assertTrue(result['metrics']['successful_completion'])
        manifest = json.loads(result['path'].with_name('program.py.gi-coder.json').read_text())
        self.assertEqual(manifest['verification']['source_sha256'], verify_source(result['path'].read_text())['source_sha256'])
        self.assertEqual(manifest['verification']['coverage'], 'model_reviewed_not_runtime_proof')

    def test_calculator_multiple_functions_stays_direct(self):
        self.data['functions'] *= 3
        self.sources = [CALCULATOR]
        self.review_responses = [review('return a + b')]
        self.run_request('Build a CLI calculator in Python without external libraries.')
        self.runtime.generate.assert_called_once()
        self.assertEqual(len(self.responses), 2)

    def test_syntax_defect_corrected_with_targeted_feedback(self):
        self.sources = ['def fibonacci(n)\n    return n', FIBONACCI]
        # generate consumes the first source; corrections pop the next.
        self.runtime.generate.side_effect = lambda *args, **kwargs: (self.sources.pop(0), USAGE)
        result = self.run_request()
        correction = next(ctx for _, ctx, schema in self.responses if schema is None)
        self.assertIn('syntactically valid', correction['validation_error'])
        self.assertIn('rejected_source', correction)
        self.assertEqual(result['metrics']['model_calls'], 4)

    def test_undefined_global_corrected_before_review(self):
        self.sources = ['def fibonacci(n):\n    return missing_value\n', FIBONACCI]
        self.runtime.generate.side_effect = lambda *args, **kwargs: (self.sources.pop(0), USAGE)
        self.run_request()
        self.assertIn('Undefined global/import', self.responses[1][1]['validation_error'])
        self.assertEqual(sum(schema == REVIEW_SCHEMA for _, _, schema in self.responses), 1)

    def test_repeated_source_failure_stops_after_one_correction(self):
        self.sources = ['def fibonacci(n):\n    return missing_value\n']
        with self.assertRaisesRegex(CoderError, 'Source correction exhausted'):
            self.run_request()
        self.assertEqual(len(self.responses), 2)  # assessment, one repair; no review
        self.assertFalse((self.root/'out/program.py').exists())
        record = json.loads(next((self.root/'.gi-coder/projects').glob('*/manifest.json')).read_text())
        self.assertFalse(record['metrics']['successful_completion'])
        self.assertEqual(len(record['attempts']), 2)

    def test_distinct_failures_still_stop_at_two_source_corrections(self):
        self.sources = [f'def fibonacci(n):\n    return missing_{n}\n' for n in range(3)]
        self.runtime.generate.side_effect = lambda *args, **kwargs: (self.sources.pop(0), USAGE)
        with self.assertRaisesRegex(CoderError, 'Source correction exhausted'):
            self.run_request()
        self.assertEqual(sum(schema is None for _, _, schema in self.responses), 2)
        self.assertFalse(any(schema == REVIEW_SCHEMA for _, _, schema in self.responses))

    def test_bad_evidence_delivers_inconclusive_without_citation_retry(self):
        self.review_responses = [review('made_up()'), review()]
        result = self.run_request()
        self.runtime.generate.assert_called_once()
        self.assertEqual(result['metrics']['model_calls'], 3)
        self.assertEqual(result['verification']['layers']['semantic_review']['status'], 'inconclusive')
        self.assertFalse(any(schema is None for _, _, schema in self.responses))

    def test_persistent_bad_evidence_is_reported_as_reviewer_error(self):
        self.review_responses = [review('made_up()')]
        result = self.run_request()
        self.assertEqual(result['verification']['layers']['semantic_review']['status'], 'inconclusive')
        self.runtime.generate.assert_called_once()
        self.assertFalse(any(schema is None for _, _, schema in self.responses))

    def test_valid_review_finding_triggers_code_repair(self):
        self.sources = ['def fibonacci(n):\n    return 42\n', FIBONACCI]
        self.runtime.generate.side_effect = lambda *args, **kwargs: (self.sources.pop(0), USAGE)
        self.review_responses = [review('return 42', verdict='fail', issues=['always returns 42']), review()]
        result = self.run_request()
        self.assertEqual(result['metrics']['model_calls'], 5)
        self.assertIn('Source requirement review failed', self.responses[2][1]['validation_error'])

    def test_review_transport_failure_delivers_untested_without_code_repair(self):
        self.review_responses = [RuntimeFailure('engine exited')]
        result = self.run_request()
        self.assertEqual(result['verification']['layers']['semantic_review']['status'], 'inconclusive')
        self.assertEqual(result['verification']['layers']['runtime_testing']['status'], 'not_run')
        self.assertFalse(any(schema is None for _, _, schema in self.responses))

    def test_forbidden_external_library_fails_before_review(self):
        self.sources = ['import numpy\n' + CALCULATOR]
        with self.assertRaisesRegex(CoderError, 'External imports forbidden'):
            self.run_request('Build a calculator without external libraries.')
        self.assertFalse(any(schema == REVIEW_SCHEMA for _, _, schema in self.responses))

    def test_handwritten_fibonacci_acceptance_cases(self):
        # Only trusted, handwritten fixture is executed. Production never executes responses.
        namespace = {}
        exec(compile(FIBONACCI, '<handwritten-fibonacci-fixture>', 'exec'), namespace)
        function = namespace['fibonacci']
        self.assertEqual([function(n) for n in (0, 1, 2, 10, 100)],
                         [0, 1, 1, 55, 354224848179261915075])
        for invalid in (-1, '3', 2.5):
            with self.assertRaises(ValueError):
                function(invalid)

    def test_handwritten_cli_acceptance_cases(self):
        path = self.root/'trusted_calculator_fixture.py'
        path.write_text(CALCULATOR)
        for inputs, expected in [('2\n+\n3\n', '5.0'), ('9\n-\n2\n', '7.0'),
                                 ('3\n*\n4\n', '12.0'), ('6\n/\n2\n', '3.0'),
                                 ('6\n/\n0\n', 'Error: division by zero'),
                                 ('x\n', 'Error:'), ('1\n?\n2\n', 'Error: unknown operation')]:
            with self.subTest(inputs=inputs):
                result = subprocess.run([sys.executable, str(path)], input=inputs, text=True,
                                        capture_output=True, timeout=5, check=True)
                self.assertIn(expected, result.stdout)


class PlanNormalizationTest(unittest.TestCase):
    def test_colons_and_bare_modules_normalize_before_validation(self):
        data = plan()
        data['imports'] = ['math', 'time', 'pygame']
        for component in data['components']:
            component['signature'] += ':'
        normalized = decode(json.dumps(data), PLAN_SCHEMA, 'plan')
        result = validate_plan(normalized, {})
        self.assertEqual(result['imports'], ['import math', 'import time', 'import pygame'])
        self.assertTrue(all(not c['signature'].endswith(':') for c in result['components']))
        self.assertEqual(data['imports'][0], 'math')  # no caller mutation

    def test_normalization_cannot_admit_injected_executable_syntax(self):
        for line in ('sys; dangerous()', 'import sys; dangerous()', 'from . import secret', 'from sys import *'):
            data = plan()
            data['imports'] = [line]
            with self.subTest(line=line), self.assertRaises(CoderError):
                validate_plan(data, {})
        data = plan()
        data['components'][0]['signature'] = 'def main(value=dangerous()):'
        with self.assertRaises(CoderError):
            validate_plan(data, {})

    def test_evidence_whitespace_and_quote_variants_match_ast(self):
        verify_evidence(review("print('hi')"), 'print( "hi" )\n')
        with self.assertRaises(ReviewEvidenceError):
            verify_evidence(review('print("hi")'), '# print("hi")\nprint("different")\n')

    def test_duplicate_json_keys_remain_rejected(self):
        with self.assertRaisesRegex(CoderError, 'Duplicate'):
            decode('{"architecture":"a","architecture":"b"}', PLAN_SCHEMA)

    def test_empty_plan_sections_can_be_omitted_without_relaxing_validation(self):
        data = {'architecture': 'Greeting', 'requirements': [{'id': 'R1', 'text': 'Greet', 'components': ['main']}],
                'entrypoint': 'main', 'components': [{'name': 'main', 'signature': 'def main():',
                'description': 'Print greeting', 'depends_on': [], 'uses': [], 'requirements': ['R1']}]}
        normalized = validate_plan(decode(json.dumps(data), PLAN_SCHEMA), {})
        self.assertEqual(normalized['imports'], [])
        data['unexpected_code'] = 'dangerous()'
        with self.assertRaises(CoderError):
            decode(json.dumps(data), PLAN_SCHEMA)

    def test_routing_small_helpers_vs_complex_requests(self):
        from gi_coder.pipeline import needs_decomposition
        data = assessment(language='python', filename='calculator.py', extension='.py')
        data['functions'] *= 3
        self.assertFalse(needs_decomposition(data, 'Create a basic command-line calculator'))
        data['complexity'] = 'moderate'
        self.assertFalse(needs_decomposition(data, 'Write a Python Fibonacci function'))
        self.assertTrue(needs_decomposition(data, 'Build a Pygame clock'))
        data['complexity'] = 'complex'
        self.assertTrue(needs_decomposition(data, 'Build a complex subsystem'))
