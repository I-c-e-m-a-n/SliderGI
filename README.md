# GI-Coder v1

A local coding CLI in the SliderGI repository. GI-Coder asks Qwen2.5-Coder for
one complete source file, removes Markdown framing, checks Python syntax and
writes UTF-8 source to your working directory. Generated programs are never
executed. No Ollama, hosted inference, account, subscription or user API key.
Internet is needed for initial setup only.

**Validation status:** Linux CPU runtime compilation and local tests pass.
Real Qwen generation is **not yet verified**: this development cloud denies
Hugging Face downloads. Windows 10 integration and Lenovo performance are also
unverified. This is not yet a completed, hardware-validated release. See
[VALIDATION.md](VALIDATION.md) and the real smoke-test instructions below.

## Hardware and prerequisites

Target: Windows 10 x64, Ryzen 5 3500U (AVX2), about 10 GB usable RAM. Default
7B Q4_K_M weights are roughly 4.7 GB; 3B roughly 2 GB. These are planning
estimates, not measurements on your laptop. Allow at least 15 GB free disk for
one model, source, build and download headroom; more for both models and Visual
Studio. CPU inference can be slow. Close memory-heavy applications and switch
to 3B if Windows has insufficient free memory. Defaults: four threads, 2,048
context tokens, 768 output tokens, batch size 128, one sequence and no GPU.

Install these prerequisites yourself; setup never changes system-wide settings:

1. [Python 3.10+ x64](https://www.python.org/downloads/windows/) with pip and the
   Python launcher. Python 3.11/3.12 is recommended.
2. [Git for Windows](https://git-scm.com/download/win).
3. [CMake >=3.20](https://cmake.org/download/), on PATH.
4. [Visual Studio 2022 Build Tools](https://visualstudio.microsoft.com/downloads/):
   choose **Desktop development with C++**, MSVC v143 x64/x86 tools and a Windows SDK.

Use **Developer PowerShell for VS 2022** or **x64 Native Tools Command Prompt**.
Confirm `py -3 --version`, `git --version`, `cmake --version` and `cl` work there.
On Linux: Python 3.10+ with venv/pip, Git, CMake >=3.20, make and GCC/Clang with
C++17 support. Debian/Ubuntu packages: `python3-venv python3-pip git cmake
build-essential`. macOS requires Xcode command-line tools and CMake; it is untested.

## Installation and startup

In Developer PowerShell:

```powershell
git clone https://github.com/I-c-e-m-a-n/SliderGI.git
cd SliderGI
.\start.ps1
```

In Developer Command Prompt, use `start.cmd`. If PowerShell's execution policy
blocks scripts, use `start.cmd`; no policy change is required. On Unix use `./start`.

First launch creates `.venv`, installs the wrapper and official HF downloader,
fetches pinned official llama.cpp, builds `llama-server` using CMake (two build
jobs), automatically downloads and verifies the configured model and loads it.
Missing tools and download/build/loading failures produce errors and nonzero
exit codes. Initial compilation/download may take substantial time. Later
launches reuse completed dependencies, builds and models. Model loading still
happens for each new CLI process. Startup scripts preserve your invocation directory.

Prepare without interactive mode, or verify the complete installation:

```powershell
py -3 scripts/setup.py
py -3 scripts/verify_installation.py --offline
```

Verification hashes the entire model and actually loads it. It does not generate
code; the separate smoke test below does that.

## CLI usage

Interactive example (illustrative, not a captured inference result):

```text
GI-Coder v1.0.0
Model: Qwen2.5-Coder-7B
Inference: Local CPU
Status: Ready
GI-Coder> Create a Python calculator with addition, subtraction, multiplication and division.
Filename (blank = generated): calculator.py
Generating...
File created: .../calculator.py
Language: python
Status: Complete
Statistics: { ... }
```

Enter one-line instructions, or `/multi` followed by multiple lines and a line
containing only `.`. Commands: `/help`, `/config`, `/model 7b`, `/model 3b`,
`/exit` (also `/quit`). Requests are independent; v1 does not retain conversation
history. Existing files require typing `yes` to overwrite.

One-shot commands in CMD (use `.\start.ps1` in PowerShell or `./start` on Unix):

```bat
start.cmd generate "Create a Python calculator" --filename calculator.py
start.cmd generate "Write a JavaScript debounce function" -l javascript -f debounce.js -o "C:\My Projects\output"
start.cmd generate "Write a Python hello program" -f hello.py --model 3b --offline
start.cmd generate "Create a Python calculator" -f calculator.py --overwrite
start.cmd config
start.cmd model 3b
start.cmd model 7b
start.cmd generate --help
```

Languages: Python (`.py`), JavaScript (`.js`), TypeScript (`.ts`), C++ (`.cpp`),
HTML (`.html`). Select using `--language`/`-l` or filename extension; otherwise
the request must name one recognized language. Ambiguous requests fail clearly.
Default filename: `generated.<extension>`. A missing extension is appended.
Use a basename for `--filename`/`-f`, and `--output-dir`/`-o` for directories.
Existing files are refused unless `--overwrite` is explicit. Indentation and
Unicode are preserved; output directories are created as needed.

**Output defaults to the caller's working directory**, including when invoking
`C:\Tools\SliderGI\start.cmd` from elsewhere. Relative output directories are
caller-relative. Installation/model paths are repository-relative.

## Model management and offline operation

Only official Qwen HF repositories are used:

| Selection | Repository | File |
| --- | --- | --- |
| `7b` (default) | [Qwen/Qwen2.5-Coder-7B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF) | `qwen2.5-coder-7b-instruct-q4_k_m.gguf` |
| `3b` | [Qwen/Qwen2.5-Coder-3B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF) | `qwen2.5-coder-3b-instruct-q4_k_m.gguf` |

`py -3 scripts/download_model.py 3b` downloads/selects 3B; use `7b` for 7B.
`generate --model 3b` overrides one request. Switching keeps other weights cached
and clears custom model-path overrides.

The official `huggingface_hub` API obtains the file size, published LFS SHA-256
and repository commit. Downloads pin that exact commit and use resumable HF
cache files. The complete GGUF is checked against the official SHA-256 before
use. A `.verified.json` receipt records revision/hash/size, not credentials.
Unchanged size/mtime allows offline cache reuse; changed files are rehashed.
`verify` always rehashes. Someone able to change both receipts and weights is
outside this cache trust model. No third-party mirrors or invented hashes.
Actual HF metadata and downloads remain unverified here until network access is enabled.

Weights, `.venv`, upstream source/build, caches and `config/local.json` are
ignored by Git. Preserve receipts when copying an installation. After successful
setup, disconnect the network and use normal commands. `generate --offline`
explicitly forbids downloads/builds. Missing model/receipt/runtime is an error;
there is no remote inference fallback. Keep `.venv` and dependency metadata
unchanged for offline startup. No telemetry or generated-program execution.

## Configuration

Defaults: `config/settings.json`. Put private overrides in ignored
`config/local.json`, for example:

```json
{
  "model": "3b",
  "context_size": 2048,
  "threads": 4,
  "max_tokens": 768,
  "temperature": 0.2,
  "model_path": null,
  "engine_path": null,
  "load_timeout": 300,
  "generation_timeout": 1800
}
```

Paths may be absolute or installation-relative. A custom model path must hold
the selected official model and is still checked against its HF hash on first
use. An engine override must identify local `llama-server` from the pinned
commit, not an external server. `GI_CODER_HOME` explicitly selects a config root.
Context includes system prompt, instruction and output. `max_tokens` must be
smaller than context. Larger context/output limits use more memory and time.
Optimal thread counts and performance must be measured on your Lenovo.

## Tests and laptop validation

After initial setup, from the repository root in PowerShell:

```powershell
$env:GI_CODER_LAUNCHERS = '1'
.venv\Scripts\python.exe -m unittest discover -s tests -v
$env:GI_CODER_INFERENCE = '1'
$env:GI_CODER_TEST_MODEL = '7b'
.venv\Scripts\python.exe -m unittest discover -s tests -p test_inference.py -v
```

Repeat with `GI_CODER_TEST_MODEL=3b` after downloading 3B. The genuine smoke test
invokes Qwen through llama.cpp, writes Python in a caller directory with spaces,
confirms the output location and runs `py_compile`. It never executes the
generated program. A passing, actually executed test is required before claiming
working inference. It is opt-in and is never replaced with a mock.

Windows launcher tests exercise CMD and Windows PowerShell separately. Also
test actual Windows launch/generation from another directory (substitute your
installation path):

```powershell
mkdir "$env:TEMP\GI Coder output" -Force
cd "$env:TEMP\GI Coder output"
& 'C:\Tools\SliderGI\start.cmd' generate 'Write a Python program that prints hello' -f hello.py --offline
py -3 -m py_compile .\hello.py
```

Linux test commands after setup:

```sh
GI_CODER_LAUNCHERS=1 .venv/bin/python -m unittest discover -s tests -v
GI_CODER_INFERENCE=1 GI_CODER_TEST_MODEL=7b .venv/bin/python -m unittest discover -s tests -p test_inference.py -v
```

Unit download/completion fixtures and HTTP protocol tests are synthetic. An
actual-engine invalid-model test checks compiled llama.cpp failure handling;
this is not real Qwen inference. See [VALIDATION.md](VALIDATION.md) for executed
versus blocked checks. Measure Windows Task Manager memory and generation time
on the Lenovo before making performance claims.

## Architecture

`scripts/setup.py` bootstraps a local venv and editable package.
`src/gi_coder/setup.py` fetches official llama.cpp commit
`74d4f5b041ad837153b0e90fc864b8290e01d8d5` (tag `b5046`), then uses official
CMake configure/build commands for `llama-server`, with CUDA/native tuning off.
The upstream checkout retains licences and attribution.

`runtime.py` owns a llama-server child bound only to `127.0.0.1`, on an ephemeral
port with internally generated, per-process local authentication. No user API
key is required. This is a bundled local subprocess, not an external inference
service. The wrapper bypasses proxies only for its loopback requests, waits for
model health and uses `/v1/chat/completions`. `--jinja` applies the GGUF's embedded
model-specific chat template to system/user messages inside llama.cpp. The
wrapper never substitutes arbitrary role-text concatenation. The child is
terminated on exit/error, stays loaded during interactive use and is unloaded
before switching models. There is no persistent server to manage.

`models.py` handles verified downloads, `generation.py` handles source/file
delivery, `config.py` handles configuration, and `cli.py` coordinates them.
These boundaries permit later C API integration and orchestration. v1 contains
no agents, tool execution, task database or background workers.

## Troubleshooting and limitations

* **Missing tools/build failure:** install prerequisites and reopen a developer
  shell. The failing command is shown; no TLS or integrity checks are disabled.
* **403/download failure:** allow GitHub, PyPI, huggingface.co and official HF
  CDN/Xet redirect hosts in your network policy. Rerun setup to resume. Public
  models need no HF login; never paste tokens into config.
* **Insufficient disk:** free the requested space; resumable partial files are
  in `models/.cache/huggingface`.
* **SHA mismatch:** do not use the file. Remove the corrupt GGUF and receipt
  (and corrupt HF cached artifact if necessary), then run online setup again.
* **Missing offline model:** successfully set up that model online first.
* **Loading failure/out of memory:** close applications, try 3B, reduce context
  or increase load timeout. The error includes llama.cpp's log tail.
* **Output truncated:** no file is written. Increase output/context limits within
  available RAM or request a smaller program.
* **Invalid Python:** no file is written; refine the request. Other supported
  languages have framing checks, but no automatic compiler/type checker in v1.
* **Existing file:** choose a new filename or authorize overwrite explicitly.
* **Engine pin mismatch:** move a custom build aside; setup refuses to reset an
  unrelated checkout. A matching successful build is reused.
* **Offline pip attempt:** finish initial installation first. Changing
  `pyproject.toml` intentionally refreshes dependencies at the next launch.

Generated code can have logical errors or unsafe operations; review it before
execution. Syntax validation does not prove correctness. v1 writes one file per
request and rejects multiple/truncated code blocks. Unfenced output is accepted
only for Python that parses as source; other languages require one fenced block.
There is no auto-repair loop or generated dependency installation.

## Official references and licences

* [llama.cpp pinned build instructions](https://github.com/ggml-org/llama.cpp/blob/74d4f5b041ad837153b0e90fc864b8290e01d8d5/docs/build.md)
* [Hugging Face downloads/cache](https://huggingface.co/docs/huggingface_hub/guides/download)
* [Qwen documentation](https://qwen.readthedocs.io/)
* [Python venv](https://docs.python.org/3/library/venv.html)
* [CMake](https://cmake.org/cmake/help/latest/manual/cmake.1.html)
* [LICENSES.md](LICENSES.md): upstream attribution and model licence locations.
