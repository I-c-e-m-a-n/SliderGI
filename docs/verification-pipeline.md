# Verification outcomes and delivery

Python delivery now reports four independent outcomes in `verification.layers`
and the CLI. A successful code-delivery manifest uses `status: statically_valid`;
its per-component progress uses the same term. This replaces the ambiguous
`verified` label. Existing inventory, source hash, plan, coverage and metrics
fields remain available. Non-Python framing behavior is unchanged.

| Layer | What runs | Meaning |
| --- | --- | --- |
| `syntax` | Python AST parsing and compilation without execution | The source compiles; no behavioral claim |
| `static_analysis` | Symbol/placeholder checks; planned projects also check exact signatures, imports, dependencies, reachability and requirement links | The supported deterministic checks passed |
| `semantic_review` | One model review per distinct integrated source slice | `passed`, `failed`, or `inconclusive`; a model opinion, never runtime proof |
| `runtime_testing` | No generated execution | `not_run`, with an explicit reason |

Syntax and static failures block delivery and can trigger the existing bounded
repair. Explicit import restrictions still block delivery. Model-reported
requirement failures also block delivery; direct generation may repair actionable
findings within its existing two-correction limit. An unchanged failed response
stops immediately, before another review call. Complex-project semantic failures
continue to fail closed without attempting unrestricted multi-component repair.

Citation or review-format errors do not establish a source defect. Whitespace and
quote variations compare through ASTs, and a complete JSON fence is normalized
locally. Other malformed, invented or missing citations become diagnostics with
`semantic_review: inconclusive`. There is no citation correction call and no
source regeneration for those errors. Review inference failures likewise retain
diagnostics and allow statically valid output; generation failures and user
cancellation still propagate. Explicit negative findings are retained even when
ancillary review fields or citations are malformed.

Example delivery status:

```text
Verification: syntax=passed; static_analysis=passed; semantic_review=inconclusive; runtime_testing=not_run
Delivered statically valid code; semantic review is inconclusive. Runtime behavior is untested.
```

The workspace retains parsed source before semantic review, every raw model
response (including review responses and available partial responses), rejected
source attempts, validation errors and per-attempt verification outcomes. Files
remain private. Sidecar staging, overwrite confirmation, symlink protection,
resident inference sessions, Metal selection and automatic-dependency-install
restrictions are preserved. Successful-completion metrics mean delivery completed,
not that the program is behaviorally correct.

## Regression benchmark

Run from the checkout with dependencies installed:

```sh
python -m unittest discover -s tests -v
PYTHONPATH=src python tests/benchmark_verification.py
```

The benchmark uses scripted responses and trusted handwritten source fixtures.
It never executes generated artifacts. The same script was run against baseline
`d471793` through a separate `PYTHONPATH`, and against the refactored implementation.
The complete results, including measured offline wall latency, are in
[verification-benchmark.json](verification-benchmark.json).

| Scenario | Baseline delivery / calls | New delivery / calls |
| --- | --- | --- |
| Fibonacci, valid review | yes / 3 | yes / 3 |
| Fibonacci, invalid citation | no / 4 | yes / 3 |
| CLI calculator, invalid citation | no / 4 | yes / 3 |
| Pygame clock, invalid citation | no / 9 | yes / 8 |
| Undefined global | no / 3 | no / 3 |
| Genuine requirement failure | no / 5 | no / 4 |

Valid-task delivery improves from 25% to 100% across the four positive scripted
cases; expected outcomes improve from 3/6 to 6/6. Both negative cases continue to
block delivery. Simulated inference costs exactly one second per call, so removing
one call saves one simulated second in each affected scenario. Real offline wall
latency is also recorded, but millisecond differences are noisy harness costs,
not measurements of model inference speed.

Regression tests independently cover syntax and static failures, Fibonacci
boundary cases, CLI arithmetic/input validation/division by zero, clock hand math,
controls and integration, malformed/fenced reviews, missing/invented evidence,
retained negative findings, exhausted repairs, raw diagnostics and explicit
untested delivery. Actual Qwen inference, GUI behavior and macOS Metal performance
remain separate opt-in checks requiring the corresponding model and hardware.

Final Linux validation: 127 tests ran, with 120 passing and seven opt-in or
platform-specific skips. Local HTTP, resident-session subprocess lifecycle,
output-safety and Metal fallback unit tests passed. Native Metal hardware and
real-weight inference were not exercised.
