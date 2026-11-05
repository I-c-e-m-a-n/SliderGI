# Reliability changes and regression benchmarks

The source pipeline retains one resident inference process and fresh, stateless
request contexts. Generated programs are parsed and compiled, never imported or
executed. Dependencies reported in manifests are never installed automatically.
Output collision checks still run before generation; atomic replacement and
symlink protections are unchanged.

## Routing and planning

Python requests use direct generation unless assessed complex, describing at
least five functions, or assessed moderate with a GUI/event-loop request.
A moderate single-function Fibonacci request and a three-function CLI calculator
therefore avoid decomposition. Assessment still runs so explicit language,
filename, text/code routing, and session behavior remain compatible.

Plans normalize trailing signature colons and bare dotted import names before
validation. Empty imports, dependencies, variables and classes may be omitted.
Unknown fields, duplicate JSON keys, executable default arguments, nonliteral
shared initializers, relative/wildcard imports, invalid dependency links and
unsafe package names remain rejected. No arbitrary text-to-code evaluation is
used during normalization.

## Bounds and verification

- Planning: initial attempt plus at most two corrections; repeated normalized
  plans or repeated validation diagnostics stop earlier.
- Components and direct Python source: initial implementation plus at most two
  corrections. An identical failed response stops after the first correction.
  Correction context includes the rejected source and specific validation error.
- Final integration: at most two corrections of one component while retaining
  all plan contracts. Disconnected functionality targets the entrypoint.
- Review responses: at most one evidence correction. AST-equivalent whitespace
  and quote variations normalize locally. Invented/comment-only evidence remains
  invalid. A reviewer contract error never triggers source regeneration.
- Inference, cancellation, context-capacity and transport failures propagate;
  they do not become source-repair retries.
- Projects retain a wall-time deadline, source-size limits and an aggregate
  model-call bound. Direct generation passes the remaining deadline to inference.

Python checks run before model review: parsing/compilation, duplicate definitions,
undefined globals and placeholder detection. Incremental projects additionally
check exact signatures, imports, shared variables, declared dependencies,
reachability and requirement-component links. Review operates on integrated
reachable slices and evaluates actual requirements. Identical slices share one
review response, with reuse recorded per requirement. A genuine review finding
can trigger direct-source repair. Complex-project semantic findings fail closed;
automatic semantic repair across multiple components is deliberately not attempted.

Manifests state static/model-review coverage explicitly. Neither a static pass
nor a model review proves runtime correctness. New private workspaces retain raw
direct-generation attempts, corrections, call usage, failure reasons and measured
inference durations. Delivered manifests include assessment statistics and
request duration before delivery. `metrics` includes model calls, token totals
(null when the engine did not report a count), inference time and completion
status. Failed workspace metrics cover calls recorded in that workspace; delivered
metrics also include assessment. Delivery timing printed by the CLI includes the
full request.

## Reproduce the offline regressions

Install the project in a virtual environment, then run from the checkout:

```sh
python -m pip install -e .
GI_CODER_BENCHMARK_REPORT=/tmp/slidergi-benchmarks.json \
  python -m unittest discover -s tests -v
```

`test_reliability_v2.py` exercises Fibonacci and CLI calculators, including syntax
repair, undefined-name repair, valid semantic rejection, repeated failures,
forbidden dependencies, faulty review evidence and transport failures. Trusted
handwritten fixtures test Fibonacci boundary/large inputs, all four arithmetic
operations, invalid numbers, unsupported operations and division by zero.
`test_incremental_project.py` and `test_plan_correction.py` cover the Pygame clock,
including accurate hand math, controls/colours, missing functionality, invalid
plans, bounded retries, component repair and final integration repair.

Only explicitly handwritten fixtures are executed by the tests. Scripted model
responses exercise orchestration without executing generated artifacts. GUI
behavior and actual Qwen inference require separate opt-in validation.

The offline JSON report records measured orchestration latency and scripted token
usage. Representative deterministic call budgets (including assessment):

| Scenario | Before | After | Acceptance |
| --- | ---: | ---: | --- |
| Moderate Fibonacci | plan plus components/reviews; formatting failures could spend 3 planning calls | 3 | Source validated and reviewed |
| Small three-function calculator | plan plus components/reviews | 3 | Direct generation, no planning |
| Clock fixture | 11 | 8 | Five components, one integrated review |
| Fibonacci syntax/name correction | previously aborts | 4 | One targeted repair, then review |
| Invented evidence, corrected | previously aborts | 4 | Review repaired; source generated once |
| Repeated invalid source | previously aborts | 3 | Fails closed after one correction |

Token counts in the synthetic fixture are 30 per call. These are protocol
regressions, not measured model token consumption or end-to-end inference speed.
The report's millisecond latencies measure the offline test harness only. Real
hardware/model latency, token use and completion rates must be measured from
manifests collected with real inference. On supported hardware, existing opt-in
checks run with `GI_CODER_INFERENCE=1` after installing the pinned engine and
verified official weights. macOS Metal validation requires a Mac.

Validation on Linux: the full suite ran 114 tests, with 107 passing and seven
opt-in/platform-specific tests skipped. This includes real subprocess/local HTTP
session lifecycle checks using the synthetic engine, not real Qwen inference.
The pre-change baseline ran 92 tests with the same seven skips.
