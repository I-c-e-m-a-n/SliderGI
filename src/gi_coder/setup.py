"""Repeatable, CPU-only official llama.cpp build."""
import shutil
import subprocess
import uuid
from pathlib import Path

from .config import ROOT, CoderError
from .runtime import LLAMA_COMMIT, discover_engine


def run(command, **kwargs):
    try:
        subprocess.run([str(x) for x in command], check=True, **kwargs)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CoderError(f"Setup command failed: {command[0]}: {exc}. Install Git, CMake >=3.20 "
                         "and a C++17 compiler. Windows: Visual Studio 2022 Build Tools, "
                         "Desktop development with C++; run from Developer PowerShell.") from exc


def prepare_source(source):
    """Fetch the exact pin; preserve mismatched or interrupted checkouts in full."""
    source.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        # Never mistake the containing project's HEAD for an upstream checkout.
        actual = None
        if (source / ".git").exists():
            try:
                actual = subprocess.check_output(
                    ["git", "-C", str(source), "rev-parse", "--verify", "HEAD"],
                    text=True, stderr=subprocess.DEVNULL).strip()
            except subprocess.CalledProcessError:
                pass  # An interrupted fetch may leave an unborn repository.
        if actual == LLAMA_COMMIT:
            return
        backup = source.with_name("llama.cpp.backup-" + uuid.uuid4().hex)
        source.rename(backup)
        print(f"Preserved previous llama.cpp checkout/build at {backup}", flush=True)
    # Do not clone the default branch and rely on a later checkout: a failed
    # fetch used to strand installations at the moving upstream HEAD.
    run(["git", "init", source])
    run(["git", "-C", source, "remote", "add", "origin", "https://github.com/ggml-org/llama.cpp.git"])
    run(["git", "-C", source, "fetch", "--depth", "1", "origin", LLAMA_COMMIT])
    run(["git", "-C", source, "checkout", "--detach", LLAMA_COMMIT])


def ensure_engine(config, root=ROOT, offline=False):
    try:
        engine = discover_engine(config, root)
        version = subprocess.run([str(engine), "--version"], capture_output=True, text=True, check=True)
        if LLAMA_COMMIT[:7] not in version.stdout + version.stderr:
            raise CoderError("Runtime version does not match the pinned llama.cpp commit. "
                             "Move the existing build aside and rerun setup.")
        return engine
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CoderError(f"Cannot run llama.cpp: {exc}. Rebuild the runtime; see README.md.") from exc
    except CoderError:
        if offline or config.get("engine_path"):
            raise
    for tool in ("git", "cmake"):
        if not shutil.which(tool):
            raise CoderError(f"Missing {tool}. Install Git and CMake >=3.20; "
                             "Windows also needs VS 2022 C++ Build Tools. See README.md.")
    source = root / "third_party/llama.cpp"
    prepare_source(source)
    try:
        actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CoderError(f"Cannot inspect llama.cpp checkout: {exc}") from exc
    if actual != LLAMA_COMMIT:
        raise CoderError(f"Expected llama.cpp {LLAMA_COMMIT}, found {actual}. "
                         "Move your custom checkout aside before running setup.")
    build = source / "build"
    run(["cmake", "-S", source, "-B", build, "-DCMAKE_BUILD_TYPE=Release",
         "-DGGML_CUDA=OFF", "-DGGML_METAL=OFF", "-DGGML_NATIVE=OFF", "-DLLAMA_CURL=OFF",
         "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_SERVER=ON", "-DBUILD_SHARED_LIBS=OFF"])
    run(["cmake", "--build", build, "--config", "Release", "--target", "llama-server", "--parallel", "2"])
    engine = discover_engine(config, root)
    run([engine, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return engine
