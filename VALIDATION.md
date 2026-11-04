# Validation record

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
