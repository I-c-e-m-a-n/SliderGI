"""Independent outcomes and diagnostics; no generated program execution."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from gi_coder.config import CoderError
from gi_coder.project import review_source, REVIEW_SCHEMA
from gi_coder.verification import verify_source
import test_reliability_v2 as fixtures
from test_reliability_v2 import review, FIBONACCI, CALCULATOR


class VerificationLayerTest(unittest.TestCase):
    def test_syntax_failure_does_not_claim_static_or_runtime_analysis(self):
        with self.assertRaises(CoderError) as caught:
            verify_source('def broken(:\n')
        layers = caught.exception.verification['layers']
        self.assertEqual(layers['syntax']['status'], 'failed')
        for name in ('static_analysis', 'semantic_review', 'runtime_testing'):
            self.assertEqual(layers[name]['status'], 'not_run')

    def test_syntax_pass_with_independent_static_failure(self):
        with self.assertRaises(CoderError) as caught:
            verify_source('def fibonacci(n):\n    return nonexistent\n')
        layers = caught.exception.verification['layers']
        self.assertEqual(layers['syntax']['status'], 'passed')
        self.assertEqual(layers['static_analysis']['status'], 'failed')
        self.assertEqual(layers['semantic_review']['status'], 'not_run')

    def test_malformed_review_remains_inconclusive_without_retry(self):
        calls = []
        def complete(*args):
            calls.append(args)
            return '{not valid JSON'
        result = review_source(complete, {}, FIBONACCI)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['semantic_status'], 'inconclusive')
        self.assertTrue(result['validation_errors'])
        self.assertEqual(result['raw_response'], '{not valid JSON')

    def test_substantive_findings_survive_citation_and_schema_errors(self):
        for response in (review('nonexistent()', verdict='fail', issues=['Incorrect result']),
                         {'verdict': 'fail', 'issues': ['Missing negative input handling']},
                         review('nonexistent()', missing_requirements=['No division-zero handling'])):
            with self.subTest(response=response):
                result = review_source(lambda *args: json.dumps(response), {}, CALCULATOR)
                self.assertEqual(result['semantic_status'], 'failed')
                self.assertTrue(result['validation_errors'])

    def test_comment_evidence_and_empty_evidence_are_not_passes(self):
        for response in (review('# a comment'), review(evidence_snippets=[])):
            result = review_source(lambda *args: json.dumps(response), {}, FIBONACCI)
            self.assertEqual(result['semantic_status'], 'inconclusive')

    def test_fenced_json_is_normalized_without_losing_negative_findings(self):
        response = review('not_in_source()', verdict='fail', issues=['No negative input handling'])
        raw = '```json\n' + json.dumps(response) + '\n```'
        result = review_source(lambda *args: raw, {}, FIBONACCI)
        self.assertEqual(result['semantic_status'], 'failed')
        self.assertEqual(result['raw_response'], raw)

    def test_partially_malformed_issue_lists_keep_actionable_strings(self):
        raw = json.dumps({'verdict': 'pass', 'issues': ['Always returns 42', 123]})
        result = review_source(lambda *args: raw, {}, FIBONACCI)
        self.assertEqual(result['semantic_status'], 'failed')
        self.assertEqual(result['issues'], ['Always returns 42'])


class DeliveryDiagnosticsTest(unittest.TestCase):
    # Reuse the existing scripted runtime setup, without inheriting its test suite.
    setUp = fixtures.ReliabilityBenchmarks.setUp
    run_request = fixtures.ReliabilityBenchmarks.run_request
    rows = []

    def manifest(self):
        path = next((self.root/'.gi-coder/projects').glob('*/manifest.json'))
        return path.parent, json.loads(path.read_text())

    def test_invalid_calculator_evidence_delivers_source_and_raw_diagnostics(self):
        self.sources = [CALCULATOR]
        self.review_responses = [review('nonexistent()')]
        result = self.run_request('Build a CLI calculator without external libraries.')
        workspace, manifest = self.manifest()
        self.assertEqual(result['metrics']['model_calls'], 3)
        self.assertEqual(manifest['status'], 'statically_valid')
        layers = manifest['verification']['layers']
        self.assertEqual({key: value['status'] for key, value in layers.items()},
                         {'syntax': 'passed', 'static_analysis': 'passed',
                          'semantic_review': 'inconclusive', 'runtime_testing': 'not_run'})
        self.assertIn('calculate', (workspace/'source.py').read_text())
        self.assertIn('nonexistent', manifest['reviews'][0]['raw_response'])
        self.assertTrue(manifest['reviews'][0]['validation_errors'])
        for call in manifest['calls']:
            self.assertTrue((workspace/call['raw_response']).is_file())
            self.assertEqual((workspace/call['raw_response']).stat().st_mode & 0o777, 0o600)

    def test_bad_citations_cannot_suppress_genuine_failure_or_repeat_review(self):
        self.sources = ['def fibonacci(n):\n    return 42\n']
        self.review_responses = [review('not_in_source()', verdict='fail', issues=['always returns 42'])]
        with self.assertRaisesRegex(CoderError, 'Source correction exhausted'):
            self.run_request()
        workspace, manifest = self.manifest()
        self.assertFalse((self.root/'out/program.py').exists())
        self.assertEqual(manifest['verification']['layers']['semantic_review']['status'], 'failed')
        self.assertEqual(sum(schema == REVIEW_SCHEMA for _, _, schema in self.responses), 1)
        self.assertEqual(manifest['attempts'][0]['review']['issues'], ['always returns 42'])
        self.assertTrue((workspace/'source.py').is_file())

    def test_syntax_error_and_static_error_retained_when_repair_exhausts(self):
        self.sources = ['def fibonacci(n)\n    return n']
        with self.assertRaises(CoderError):
            self.run_request()
        workspace, manifest = self.manifest()
        self.assertEqual(manifest['verification']['layers']['syntax']['status'], 'failed')
        self.assertTrue(manifest['attempts'][0]['validation_error'])
        self.assertTrue((workspace/'source-attempt-1.raw.txt').is_file())

    def test_inconclusive_delivery_prints_explicit_untested_status(self):
        self.review_responses = [review('not_in_source()')]
        from gi_coder.pipeline import process_request
        with contextlib.redirect_stdout(io.StringIO()) as output:
            process_request(self.runtime, 'Write Fibonacci', root=self.root, output_dir=self.root/'out')
        self.assertIn('semantic_review=inconclusive', output.getvalue())
        self.assertIn('runtime_testing=not_run', output.getvalue())
        self.assertIn('Runtime behavior is untested', output.getvalue())
