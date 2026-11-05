# Validation record

## Assessment language/extension fix — 2026-11-05 (Singapore)

Full suite: **68 discovered, 64 passed, 4 skipped**. Existing session, backend,
timing, path and security regressions remain passing. Skips are still the two
genuine-Qwen opt-in tests and two legacy Windows launcher tests. Python syntax
compilation and Git whitespace checks passed.

New deterministic regressions use the exact animated-clock request, including
the prohibition on tkinter. They cover Python version/case/whitespace labels,
missing language/extension metadata, bare and dotted extension forms, explicit
CLI precedence, ambiguous/negated/incidental language mentions, unsafe unknown
extensions and filename conflicts. A synthetic completion is written and syntax
checked to verify the request and constraints reach generation; this does not
prove Qwen generates a working animated clock.

Opt-in diagnostics are tested on malformed JSON and extension-validation failure
before normal assessment/revised-prompt files exist. Raw JSON and the original
validation error remain in private retained debug files; normal temporary files
are still cleaned. Logging defaults off and successful normalization can also
be inspected when enabled. No actual failing raw Qwen assessment was available
from the user's Mac, so the tests reproduce compatible failure shapes rather
than claiming an observed exact model response.

macOS execution and genuine Qwen inference remain unverified in this Linux
environment without official weights. No new hardware/latency claims are made.

## Metal, timings and output-directory fixes — 2026-11-05 (Singapore)

Current full suite: **59 discovered, 55 passed, 4 skipped**. Skips are the two
opt-in genuine Qwen tests and two legacy Windows launcher tests. Existing tests
remain passing; the original real smoke test now supplies its temporary output
directory explicitly to retain that check under the new default.

Executed on Linux/Python 3.12: backend preference/config validation, simulated
Metal device availability and confirmed layer offload, startup/build CPU
fallback, one-time migration/reuse of a CPU build, separate assessment token
budget, deterministic monotonic-clock accuracy, compact text assessment with
real revision stage, default sibling output creation/reuse, metadata placement,
explicit overrides, all prior process-residency/context/signal regressions.
Metal availability and timing values in deterministic tests are mocked, not
macOS benchmarks. The actual local CPU llama-server device probe also ran.

The text schema was converted successfully using the pinned llama.cpp C++
JSON-schema-to-grammar implementation linked against the already-built runtime
libraries (4,810 bytes of generated grammar). Python compilation, shell syntax
and Git whitespace checks passed. This validates schema conversion, not Qwen's
ability to produce correct assessments or source code.

Measured structural changes: assessment instruction size decreased from 897 to
638 characters; text assessments emit five fields rather than ten; JSON prompts
are compact; the assessment output ceiling defaults to 384 instead of sharing
generation's 768-token limit. These are input/output-budget reductions, not a
measured inference speedup. Every request, including greetings, is still assessed
and revised by Qwen before generation. Complex requests may need a higher
assessment budget; truncation is reported rather than accepted silently.

Unexecuted: actual macOS Metal build/offload, real Apple/Intel GPU compatibility,
Mac CPU-versus-Metal benchmarks, and genuine Qwen inference. This environment
has no Mac hardware or official Qwen weights; HF access remains blocked by the
previously observed proxy policy. No Mac latency or throughput is claimed.
README documents on-Mac benchmark and genuine inference commands. GitHub API
access was checked again and returns Forbidden; feature-branch Git publishing
works, while API-based PR creation/update remains blocked.

Prior validation records below describe earlier commits and defaults.

## macOS session and assessment upgrade — 2026-11-05 (Singapore)

Target: macOS only. Runner: Linux x86-64, Python 3.12. No macOS host is
available in this task. V1 tests are retained unchanged.

New coverage includes Qwen-assessment JSON schema/semantic validation, explicit
options taking precedence, automatic Rust filenames, unmapped-language fallback,
JSON metadata sidecars, independent overwrite consent, private temporary files
and cleanup, safe headers, text routing, context-isolated HTTP payloads,
interactive reuse/model switching and runtime failure behavior. Actual POSIX
subprocess tests use a clearly marked synthetic HTTP engine to exercise four
calls sharing one PID and cleanup on SIGINT, SIGTERM and SIGHUP. These are not
genuine Qwen completions. Signal tests exposed an intermittent shutdown issue
while changing OS signal dispositions inside a handler; shutdown now uses an
idempotent Python handler. The isolated subprocess/signal suite passed five
independent runs after that correction.

Final full suite: **46 discovered, 42 passed, 4 skipped** (two opt-in genuine
Qwen tests and two legacy Windows launchers). Syntax compilation, shell syntax
checking and Git whitespace checks passed.

The genuine resident-session test was explicitly enabled and **blocked before
inference** by missing official 7B weights/verification receipt. Online setup
was retried and still receives proxy **403 Forbidden** from huggingface.co.
No actual Qwen assessment, generated source or model-residency performance is
claimed verified. The new opt-in test covers real assessment + Python source
generation + `py_compile`, an independent text question, unchanged runtime PID
and request isolation once weights are available. Run README's commands on Mac
for both models. The original genuine smoke test also remains available.

macOS Intel/Apple Silicon compilation, real terminal closure on macOS and the
real-model tests remain unexecuted. Legacy Windows integration tests are skipped
on Linux and outside this upgrade's target. GitHub API access returns Forbidden;
the feature branch can be published with Git, but API-based PR creation is blocked.

## Original validation — 2026-11-04

## Checkout recovery fix

After a reported macOS pin mismatch, setup now fetches the exact pinned commit
directly rather than cloning the moving default branch first. Mismatched and
interrupted checkouts are preserved in uniquely named, Git-ignored backup
directories before fetching anew; no model files are moved. Metal is explicitly
disabled for CPU-only macOS builds.

Verified a fresh official GitHub fetch with the corrected helper and confirmed
the exact expected HEAD. Added three local-Git regression tests for exact pin
selection/reuse, backup preservation of local edits/builds/history, and retry
after an interrupted fetch. Full Linux suite: **26 discovered, 23 passed,
3 skipped** (genuine inference, Windows CMD, Windows PowerShell). macOS execution
has not been tested. The Qwen and hardware-validation limitations below remain.

Test environment: Linux x86-64, Python 3.12, GCC 14.2, CMake 3.31.6.
This is not the target Lenovo or Windows 10.

## Executed successfully

* Built official llama.cpp `74d4f5b041ad837153b0e90fc864b8290e01d8d5`
  (`b5046`) with CMake Release, CPU-only, native tuning off, static libraries,
  CUDA/curl/tests disabled. Built and executed `llama-server --version`.
* Actual first-launch `scripts/setup.py --launch --help`: created `.venv`,
  installed the editable package and dependencies, printed CLI help.
* Repeated `./start config`: reused dependencies without reinstalling.
* Removed only the generated runtime executable and invoked the implemented
  `ensure_engine()` setup path: CMake reconfigured/relinked `llama-server`
  successfully, reusing valid object files. `pip check` found no broken dependencies.
* `GI_CODER_LAUNCHERS=1 .venv/bin/python -m unittest discover -s tests -v`:
  **23 tests discovered, 20 passed, 3 skipped** (real Qwen opt-in, Windows CMD,
  Windows PowerShell). Includes actual compiled-engine rejection of an invalid
  model and child cleanup, localhost HTTP protocol, Unix launcher from a caller
  directory with spaces, config, language selection, output paths/extensions,
  UTF-8 file creation, overwrite refusal, extraction, truncation rejection,
  simulated official SHA metadata/download/cache/disk errors and syntax checks.
* `sh -n start`, Python compileall, and `git diff --check` passed.

Local socket tests need network/socket permission in sandboxed runners. Their
initial sandbox denial was resolved by running through the approved execution
permission flow; the tests were not disabled or altered to hide the denial.

## Blocked / unsuccessful

* Actual `.venv/bin/python -m gi_coder.cli setup` reached official HF metadata
  retrieval, but the proxy returned **403 Forbidden** for `huggingface.co`.
  No real model was downloaded or hash-verified. Official metadata availability,
  exact filenames and successful multi-gigabyte downloads still need live validation.
* Explicitly enabled real smoke test:
  `GI_CODER_INFERENCE=1 .venv/bin/python -m unittest discover -s tests -p test_inference.py -v`
  **failed its prerequisite**, reporting missing official 7B weights/receipt.
  Inference did not run and no genuine Qwen-generated source file was validated.
  Mocked generation tests are not a substitute for this requirement.
* HF model-specific licence inspection was blocked by the same network policy.

Official HF/CDN domains were saved in the environment draft. A draft save does
not activate runtime network policy. Review/save the new network settings and,
if required by the environment UI, publish that configuration before retrying.
No network restriction was bypassed and no environment publication was performed.

## Not executed here

* Windows 10/MSVC build and Windows CMD/PowerShell launcher integration.
* Genuine 7B and 3B model generation and fully offline inference.
* Lenovo RAM use, generation throughput, thermals and optimal thread count.
* macOS build/startup and non-Python generated-language compiler checks.

## Required completion gate

Enable official HF downloads, rerun setup, and run the opt-in genuine smoke
test for 7B and 3B. It must create a real `.py` file in the expected caller
directory and pass `py_compile`. Then run the Windows suite and manual launcher
generation checks documented in README on the Lenovo. Until those checks run,
do not describe this implementation as a completed or hardware-verified release.
