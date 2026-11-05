"""Run with PYTHONPATH=src python tests/benchmark_verification.py.

Scripted responses only. Simulated inference is exactly one second per call;
wall_seconds measures the actual offline orchestration, not Qwen inference.
Can run unchanged against an earlier checkout through PYTHONPATH.
"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from gi_coder.config import CoderError
from gi_coder.pipeline import process_request
from gi_coder.project import PLAN_SCHEMA, REVIEW_SCHEMA
from clock_project_fixture import plan, SOURCES, PASS, REQUEST
from test_pipeline import assessment
from test_reliability_v2 import FIBONACCI, CALCULATOR, review, USAGE


class ScriptedRuntime:
    config = {'project_timeout': 60, 'max_components': 16, 'max_requirements': 16}

    def __init__(self, case):
        self.case, self.calls, self.clock = case, 0, 0.0

    def answer(self, response):
        self.calls += 1
        self.clock += 1
        return response, {**USAGE, 'seconds': 1}

    def generate(self, *args, **kwargs):
        return self.answer(self.case['source'])

    def complete(self, system, prompt, schema=None, **kwargs):
        context = json.loads(prompt)
        if schema == PLAN_SCHEMA:
            return self.answer(json.dumps(plan()))
        if schema == REVIEW_SCHEMA:
            return self.answer(json.dumps(self.case['review']))
        if schema is None:
            return self.answer(SOURCES[context['component']['name']] if self.case.get('clock') else self.case['source'])
        data = assessment(language='python', filename='program.py', extension='.py')
        data['complexity'] = 'complex' if self.case.get('clock') else 'moderate'
        return self.answer(json.dumps(data))


def benchmark():
    rows = []
    cases = [
        {'name': 'fibonacci_valid_review', 'prompt': 'Write Fibonacci with zero, one and negative inputs',
         'source': FIBONACCI, 'review': review(), 'expected_delivery': True},
        {'name': 'fibonacci_bad_citation', 'prompt': 'Write Fibonacci with zero, one and negative inputs',
         'source': FIBONACCI, 'review': review('invented()'), 'expected_delivery': True},
        {'name': 'calculator_bad_citation', 'prompt': 'Build a CLI calculator without external libraries',
         'source': CALCULATOR, 'review': review('invented()'), 'expected_delivery': True},
        {'name': 'clock_bad_citation', 'prompt': REQUEST, 'clock': True,
         'review': {**PASS, 'evidence_snippets': ['invented()']}, 'expected_delivery': True},
        {'name': 'undefined_global', 'prompt': 'Write Fibonacci',
         'source': 'def fibonacci(n):\n    return missing_name\n', 'review': review(), 'expected_delivery': False},
        {'name': 'genuine_requirement_failure', 'prompt': 'Write Fibonacci',
         'source': 'def fibonacci(n):\n    return 42\n',
         'review': review('return 42', verdict='fail', issues=['Always returns 42']), 'expected_delivery': False},
    ]
    for case in cases:
        runtime = ScriptedRuntime(case)
        started = time.perf_counter()
        row = {'case': case['name'], 'expected_delivery': case['expected_delivery'], 'delivered': False}
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with patch('time.monotonic', side_effect=lambda: runtime.clock), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    result = process_request(runtime, case['prompt'], root=root, output_dir=root/'out')
                except CoderError as error:
                    row['error'] = str(error)
                else:
                    row.update(delivered=result['path'].is_file(),
                               layers=result.get('verification', {}).get('layers', {}))
        row.update(model_calls=runtime.calls, simulated_inference_seconds=runtime.clock,
                   wall_seconds=time.perf_counter()-started,
                   expected_outcome_met=row['delivered'] == row['expected_delivery'])
        rows.append(row)
    positive = [row for row in rows if row['expected_delivery']]
    return {'kind': 'scripted_offline_verification', 'cases': rows,
            'valid_task_delivery_rate': sum(row['delivered'] for row in positive)/len(positive),
            'expected_outcome_rate': sum(row['expected_outcome_met'] for row in rows)/len(rows)}


if __name__ == '__main__':
    print(json.dumps(benchmark(), indent=2))
