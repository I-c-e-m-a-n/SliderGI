# Bounded single-file implementation

This document describes the source/tests on `feature/macos-assessment-session`.

## Capacity

`assessment_max_tokens` now defaults to `null`: schema responses may use the
remaining configured context instead of stopping at 384 tokens. An explicit
integer still caps them. Default context is 4096, generation is 768 tokens per
component, and no context expansion happens automatically. Before inference,
the resident llama.cpp applies its actual chat template and tokenizes the
prompt. The wrapper reserves 64 context tokens and rejects an oversized request.
The server-level `--n-predict` ceiling also follows context size; it no longer
silently clamps detailed plans to the generation limit.

Example overrides in `config/local.json`:

```json
{
  "context_size": 8192,
  "assessment_max_tokens": null,
  "max_tokens": 1024,
  "max_components": 16,
  "max_requirements": 16,
  "project_timeout": 1800
}
```

More context costs memory. A large plan or review can still exceed context;
GI-Coder fails rather than silently dropping requirements or loading another
model. Use a larger context or smaller request deliberately. Individual model
calls retain their configured timeout. The project time budget is checked
around each bounded call; transport timeouts are not a hard real-time deadline.

## Paths through generation

All requests still receive assessment and rewriting on the same resident model.
Text keeps the existing assessment/answer path. Python assessed as moderate or
complex, or summarized with multiple functions, follows:

1. One schema-constrained implementation plan. No recursive task planning.
2. Validate requirements, imports/dependencies, literal shared initializers,
   class fields, function/method signatures and dependency graph.
3. Generate each function/method once in topological dependency order. Each
   prompt contains its specification, linked requirements, needed imports/shared
   values/classes and direct dependency signatures—not the accumulated source.
4. Assemble and compile-check the partial source; update the central manifest
   after each accepted step. No generated code is executed.
5. Verify the final source against the manifest, then review every requirement
   against relevant source and caller slices. Missing requirements or failed
   reviews block publication. Reviews must cite exact executable AST snippets.

Simple Python requests keep one-shot generation plus one bounded semantic
review, avoiding a separate planning call. Their manifests record the actual
post-generation structure. Non-Python generation remains single-pass with
framing checks; its manifest explicitly marks semantic coverage unverified.
Incremental cross-language parsing is outside this stage.

## Manifest and checkpoints

Each incremental project has a private, retained workspace:

```text
SliderGI/.gi-coder/projects/project-<id>/
    manifest.json
    source.py
```

The structured manifest records the original request and assessment,
requirements, architecture, exact signatures, classes/fields, shared variables,
imports/package dependencies, direct references, component states, source hash,
per-call timing/usage and review evidence. Its source inventory is recomputed
from the AST after every integration step. Failed projects retain their last
accepted checkpoint and error; they do not resume or retry automatically.

Only accepted final code is delivered to the normal `SliderGI_Output` directory
(or explicit output override). Every generated source has an associated
`<filename>.gi-coder.json` manifest. Filename validation, independent source/
manifest overwrite consent and original metadata compatibility remain. If an
existing manifest was replaced and source publication fails, the old manifest
is restored. Filesystem publication is not a multi-process transaction.

## Verification and limits

Static checks detect signature drift, missing/extra definitions, undeclared
imports/shared-state use, undefined global names, placeholders, cyclic plans,
unreachable functions/methods, unused planned globals/classes and unimplemented
requirement links. Imports must match package declarations; installed package
versions are recorded without importing or installing dependencies. Missing
packages are reported, not silently installed. Directly forbidden imports such
as `Do not use tkinter` are checked against the original request.

This is conservative static integration plus model-based requirement review,
not a proof of behavior. Review evidence must exist in source, but Qwen can
still misjudge it. Imported APIs, dependency versions, GUI event handling,
rendering and runtime behavior require human testing. No program or GUI is
launched by GI-Coder. Conventional top-level functions and `self` methods are
supported. Nested definitions, decorators, dynamic imports and dynamic dispatch
are rejected or not fully analyzable. Inheritance-heavy frameworks can exceed
the current resolver. Function defaults and shared initializers must be literals;
initialize live resources in `main`/`__init__`.

The flat plan is limited by `max_components` and `max_requirements` (1–32), one
generation per component, one review per requirement, project time budget and
128 KiB assembled-source bound. There are no autonomous execution tools, repair
loops, background agents or multi-file program generation. Assessment timings
remain separate; the displayed Generation duration now includes planning,
integration and review. Per-call measurements also appear in project manifests.

## Clock regression and genuine validation

The regression request asks for a Pygame clock with continuous hour/minute/second
hands, twelve hour notches, Space analog/digital switching, C colour cycling,
and no tkinter. Deterministic tests assemble handwritten function fixtures,
check dependency order/checkpoint hashes and reject disconnected rendering,
wrong signatures, missing imports, invented review evidence and omitted
controls. Handwritten time/control unit fixtures are tested; no model-generated
program is executed. Synthetic reviews do not establish Qwen's actual quality.

On a Mac with official weights already installed:

```sh
GI_CODER_LAUNCHERS=1 .venv/bin/python -m unittest discover -s tests -v
GI_CODER_INFERENCE=1 GI_CODER_TEST_MODEL=7b .venv/bin/python -m unittest discover -s tests -p test_real_clock.py -v
```

The opt-in real test generates and reviews source with Qwen and runs
`py_compile`; it does not start Pygame. Test the resulting UI yourself after
reviewing the source and installing its declared dependencies. Repeat with 3B
if desired. This cloud has no official weights or macOS host; the real test
currently stops before inference. No working Qwen-generated clock or macOS
performance benchmark is claimed here.
