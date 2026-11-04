# GI-Coder 1.1 — local coding and answers on macOS

GI-Coder uses Qwen2.5-Coder GGUF and a pinned official llama.cpp runtime on your
Mac. It assesses English requests, answers ordinary questions in the terminal,
or writes one source file in the installation's sibling `SliderGI_Output` folder. No Ollama, hosted inference,
subscription, user API key, agent orchestration or generated-code execution.

**Validation:** regression, protocol and POSIX process/signal tests run on Linux.
macOS execution and real Qwen assessment/generation are not verified in this
cloud: official weights cannot currently be downloaded through its network
policy. Synthetic test responses are not evidence of Qwen's code quality. See
[VALIDATION.md](VALIDATION.md) for exact outcomes and remaining gates.

## Install on macOS

This upgrade targets macOS only, on Intel or Apple Silicon. Install Python
3.10+ (3.11/3.12 recommended), Git, CMake >=3.20 and Apple's command-line build
tools. Obtain Python from [python.org](https://www.python.org/downloads/macos/),
CMake from [cmake.org](https://cmake.org/download/), and use Apple's installer:

```sh
xcode-select --install
python3 --version
git --version
cmake --version
clang++ --version
```

Run Apple Silicon Python/compiler tools natively rather than mixing Rosetta and
ARM architectures. Prerequisites are installed by you; GI-Coder does not modify
system settings or install a daemon. Legacy Windows launchers remain in the
repository for compatibility, but are outside this upgrade's supported target.

```sh
git clone https://github.com/I-c-e-m-a-n/SliderGI.git
cd SliderGI
# Until the feature PR is merged:
git switch feature/macos-assessment-session
./start
```

Updating an existing installation:

```sh
git fetch origin
git switch feature/macos-assessment-session
git pull --ff-only
./start
```

First launch creates repository-local `.venv`, installs dependencies, fetches
official llama.cpp commit `74d4f5b041ad837153b0e90fc864b8290e01d8d5` (`b5046`),
builds `llama-server` with CMake, downloads the selected official Qwen model,
checks its SHA-256 and loads it. CMake uses two build jobs. On macOS, Metal
support and embedded shaders are enabled by default; CUDA remains disabled.
A mismatched/interrupted upstream checkout is
preserved in `third_party/llama.cpp.backup-<unique-id>` before fetching the pin.
Models are unaffected. Inspect backups before removing them manually.

Subsequent launches reuse dependencies, builds and valid weights. There is no
automatic recompilation/redownload on each launch. An existing CPU-only macOS
build is upgraded once to enable Metal. Failed Metal builds fall back to CPU
and record that attempt, avoiding a rebuild on every launch. Explicit engine
overrides and offline runs reuse the existing engine. The model loads once for
each interactive session and stays resident until that session ends, the
runtime fails or you explicitly switch models. There is no idle-unload policy,
cross-session worker or background daemon.

Allow roughly 15 GB free disk for one model and setup headroom. 7B Q4_K_M weights
are roughly 4.7 GB; 3B roughly 2 GB. RAM requirements exceed weight size and vary
with context. Start with 3B on memory-constrained Macs. CPU speed/RAM estimates
are not performance measurements. Defaults: four threads, 2,048 context tokens,
768 generation tokens, a separate 384-token assessment budget and one inference sequence.

## Metal acceleration and fallback

`backend` accepts `auto` (default), `metal` or `cpu`. `gpu_layers` is a
nonnegative integer: the default 99 requests all layers of the supported Qwen
models, a smaller positive number requests partial offload, and 0 forces CPU.
`backend=metal` expresses a preference and still allows graceful CPU fallback.
Use `backend=cpu` to disable acceleration explicitly.

On macOS GI-Coder probes the pinned executable with `--list-devices` before
loading weights. It selects a reported Metal device, not an assumed GPU based
on the Mac's name. If unsupported or probing fails, CPU is used. If accelerated
model initialization fails, that child is stopped and startup retries once on
CPU. There are no automatic reloads after the session becomes ready. Subsequent
requests reuse the resident process; runtime failures end the session.

Startup displays `Inference: Local Metal (N layers offloaded)` only when the
load log confirms actual layer offloading; otherwise it displays `Local CPU`.
Detection probes do not load weights. GPU memory pressure, unsupported hardware,
missing Metal tools or custom CPU-only executables can prevent acceleration.
If a failed build was cached, remove only
`third_party/llama.cpp/build/.gi-coder-build.json` after fixing build tools to
retry the Metal upgrade. Missing rebuild prerequisites reuse a working CPU
engine when available. No Mac/Metal benchmark has been run in this cloud.

## Interactive use

```text
GI-Coder> Explain how a hash table works.
<English answer printed here; no source file>
GI-Coder> Write a Rust program that greets the user.
File created: .../greeting.rs
Language: rust
Status: Complete
```

This is an illustrative interaction, not a captured genuine Qwen result. Code
filenames and languages are selected by Qwen, respecting explicit instructions.
There is no filename prompt for each request. Existing files require typing
`yes` before replacement. A second request starts fresh; this is not a chat
history or memory system.

Commands: `/help`, `/config`, `/model 3b`, `/model 7b`, `/quit`, `/exit`.
For multiline input enter `/multi`, then finish with a line containing only `.`.
EOF and Ctrl+C end the session. SIGHUP (terminal closure) and SIGTERM unwind the
session and reap the inference process. Uncatchable OS termination such as
SIGKILL cannot run Python cleanup. No inference process is intentionally retained.

## One-shot requests and explicit options

```sh
./start generate "Explain recursion in three sentences"
./start generate "Create a calculator" --language python --filename calculator.py
./start generate "Write a Swift greeting program" --output-dir "$HOME/My Code"
./start generate "Write an add function" -l python -f add.py --overwrite --offline
./start generate "Explain closures" --model 3b
./start generate --help
./start config
./start model 3b
./start model 7b
```

Existing options remain: `--language/-l`, `--filename/-f`, `--output-dir/-o`,
`--overwrite`, `--model`, `--offline`. CLI language/filename options explicitly
request code and override inferred preferences. Conflicting explicit language
and extension are rejected. `--filename` accepts a basename, not a path; use
`--output-dir` for directories. Hidden names, traversal, separators and control
characters are rejected. Automatically generated source and metadata default to
`ROOT.parent / SliderGI_Output`, where ROOT is the resolved installation path:

```text
ParentDirectory/
├── SliderGI/
└── SliderGI_Output/
    ├── calculator.py
    └── greeting.rs
```

The folder is created when writing code and reused. Calling `start` from another
terminal directory does not change this default. Explicit `--output-dir` paths
still override it; relative overrides remain relative to the caller. Existing
files retain overwrite protection. Text responses do not create the output folder.

Python is syntax-checked before writing; JSON is parsed when generated as JSON.
Other languages receive framing and filename checks but no automatic compiler
execution. No generated program or dependency installer is executed.

## Assessment pipeline

`User input → Qwen assessment → validated revised prompt → Qwen generation → output`

Both calls use the same loaded runtime. Each call contains only its own system
and user messages with `cache_prompt=false` and one fixed inference slot. On
the pinned llama.cpp implementation this clears prior KV tokens before the new
prompt. Context shifting is disabled so an oversized request fails rather than
silently discarding constraints. Generation receives only the current original
request and validated assessment; no prior request, response or conversation
history is appended.

Assessment is constrained by llama.cpp JSON-schema grammar and independently
validated in Python. Fields: `output_type`, `objective`, `constraints`,
`output_format`, `language`, `filename`, `extension`, `complexity`,
`revised_prompt`, `functions`. Unknown/duplicate fields, wrong types, oversized
values, inconsistent routing and unsafe paths fail without source output.
Function summaries contain `name`, `inputs`, `behaviour`, `variables`, `outputs`;
a module summary covers files without functions. Text assessments emit only
`output_type`, `objective`, `constraints`, `complexity`, `revised_prompt` over
the inference protocol, reducing JSON output overhead. Validation restores
the code-only null fields and empty functions internally. Every request,
including `Hi`, still receives a Qwen assessment and a real revised prompt;
there is no greeting shortcut. Assessments are advisory: original
user instructions remain in the generation prompt. Mechanical validation cannot
prove that the model understood every semantic constraint correctly.

Each successful response, including text, ends with actual monotonic-clock
measurements: `Assessment`, `Generation`, and `Total` in seconds. Assessment
includes inference and validation; generation measures its separate inference
call. Total runs from request entry through output and temporary-file cleanup,
including file I/O or overwrite consent, but excluding initial model setup/load.
Measurements are unrounded internally and displayed to two decimal places.
They are local wall-clock durations, never values supplied by Qwen. Total can
exceed the sum of the two stages. A zero display can reflect sub-0.005s mocked
calls; it is not an inference performance claim.

Latency changes reduce assessment instructions and text-schema output fields,
use compact JSON prompts, deterministic assessment sampling, and a separate
`assessment_max_tokens` limit. No assessment or rewrite is skipped. Complex
assessments that reach their budget fail without writing files; increase that
limit and context as needed rather than silently losing constraints.

Request-private `assessment.json` and `revised-prompt.json` files live under
`.gi-coder/tmp/request-*` with owner-only permissions, and are removed on
success, errors or cancellation. A hard crash can leave temporary files; they
are ignored by Git and can be removed manually. The empty temp parent may remain.

Code gets a concise function-skeleton comment header generated from the validated
assessment. It states the generation contract, not a static-analysis guarantee
of behavior. Shebangs and Python encoding declarations retain their positions.
Where suitable comment syntax is unavailable (including JSON), summaries go in
`<filename>.gi-coder.json`. This is metadata, not another generated program.
Source and existing sidecar each require overwrite authorization. No model
content is interpolated into shell commands or executed.

## Languages

There is no five-language whitelist. `config/languages.json` maps common
languages to default/alternate extensions and safe line-comment syntax, including
Rust, Swift, Go, Java, Ruby, Kotlin, Lua, SQL and many others. Extend that file
to add mappings. For an unmapped language Qwen must provide a safe extension;
metadata uses a sidecar rather than guessing comment syntax. Known language
extensions must match the mapping. Filename validation and Python syntax checks
are retained. The legacy `choose_language` library helper remains for V1 callers
and regression tests; it is not used for request routing or CLI assessment.

## Models, configuration and offline operation

Official sources only:

* Default [Qwen2.5-Coder-7B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF),
  `qwen2.5-coder-7b-instruct-q4_k_m.gguf`.
* Fallback [Qwen2.5-Coder-3B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF),
  `qwen2.5-coder-3b-instruct-q4_k_m.gguf`.

The official Hugging Face downloader pins the metadata's repository revision,
resumes partial downloads and compares full GGUF SHA-256 against published LFS
metadata. Receipt files record hash/revision/size. Unchanged file size/mtime lets
startup reuse the cache offline; full `verify` rehashes it. Local adversaries
able to edit both weights and receipts are outside that cache trust model.

```sh
python3 scripts/setup.py
python3 scripts/download_model.py 3b
python3 scripts/verify_installation.py --offline
```

Once setup succeeds, normal startup and assessment/generation require no network.
`generate --offline` explicitly forbids downloads/builds. Keep `.venv`, weights
and receipts. No remote inference fallback or telemetry is introduced.

Defaults are in `config/settings.json`; overrides go in ignored `config/local.json`:

```json
{"model": "3b", "context_size": 2048, "threads": 4, "max_tokens": 768,
 "assessment_max_tokens": 384, "backend": "auto", "gpu_layers": 99,
 "temperature": 0.2, "model_path": null, "engine_path": null,
 "load_timeout": 300, "generation_timeout": 1800}
```

Paths are installation-relative unless absolute. Model overrides must match
the selected official model/hash. Engine overrides must match the pinned local
runtime. Model switching clears custom model paths and unloads the previous
model before loading another. Context includes instructions, assessment and
output. If assessment truncates or generation hits its limit, no source is saved;
simplify the request or increase limits within available RAM.

## Testing on your Mac

After successful setup, from the repository root:

```sh
GI_CODER_LAUNCHERS=1 .venv/bin/python -m unittest discover -s tests -v
GI_CODER_INFERENCE=1 GI_CODER_TEST_MODEL=7b .venv/bin/python -m unittest discover -s tests -p test_real_session.py -v
GI_CODER_INFERENCE=1 GI_CODER_TEST_MODEL=7b .venv/bin/python -m unittest discover -s tests -p test_inference.py -v
```

Repeat genuine inference tests with `GI_CODER_TEST_MODEL=3b` after downloading it.
The real session test loads Qwen once, assesses/generates Python with automatic
filenames, runs `py_compile`, asks an independent text question, checks that
its payload excludes the prior request marker, verifies unchanged process PID
and asserts cleanup. It never executes generated code. Both genuine inference
tests are opt-in; skipped tests do not prove inference works.

Synthetic subprocess tests exercise four calls sharing one child PID and actual
SIGINT/SIGTERM/SIGHUP cleanup. Protocol tests inspect fresh message payloads and
disabled cache reuse. V1 tests are retained. See the validation record for what
was actually executed and what remains blocked.

For real performance measurements, start `./start` once, note the actual backend
and model, and submit `Hi` several times. Every iteration assesses and revises
afresh on that resident model. Record each stage/total duration, Mac model,
macOS version, model quantization, context and offloaded layers. Compare with
`backend=cpu` in a new session using the same settings. Initial model loading
is excluded from request timings. GPU availability, memory bandwidth, thermal
throttling and generated-token count affect latency; no numeric macOS speedup
is claimed from Linux or mocked tests.

## Architecture, troubleshooting and licences

`cli.py` owns sessions; `session.py` handles terminal signals; `assessment.py`
validates schema-constrained decisions; `pipeline.py` routes text/code and manages
temporary files/headers; `runtime.py` owns one local llama.cpp child; `models.py`
verifies official downloads; `generation.py` protects source delivery;
`languages.py` loads extensible mappings. The runtime binds loopback only, uses
internally generated local authentication, and applies Qwen's embedded GGUF
chat template with `--jinja`. No user API key or externally hosted server.

* Missing compiler/CMake: install prerequisites and reopen the shell.
* Wrong-architecture build: preserve/move the generated build directory and
  rebuild with tools matching the native Mac architecture.
* Model download 403: allow official HF/CDN hosts in the network policy and retry.
* SHA mismatch: do not use the file; remove the corrupt GGUF/receipt and retry
  verified online setup. No TLS/checksum bypass is supported.
* Bad assessment: no file is written; rephrase or simplify the request.
* Existing source/metadata: choose another filename or authorize replacement.
* Out of memory: close applications, use 3B and reduce context.
* Runtime connection/load failure: the owned process is stopped; restart the CLI.

Generated code may still contain logical errors. Review it before execution.
This stage intentionally has no multi-file program generation, task splitting,
tool execution, MCP integration, agents or scheduling.

Original wrapper: MIT. See [LICENSES.md](LICENSES.md) for upstream attribution
and model-specific licence locations. Official references:
[llama.cpp pinned build docs](https://github.com/ggml-org/llama.cpp/blob/74d4f5b041ad837153b0e90fc864b8290e01d8d5/docs/build.md),
[HF downloading/caching](https://huggingface.co/docs/huggingface_hub/guides/download),
[Qwen](https://qwen.readthedocs.io/), [Python](https://docs.python.org/3/library/venv.html).
