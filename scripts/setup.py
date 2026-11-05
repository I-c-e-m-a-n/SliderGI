"""Standard-library bootstrap. No global installs; preserve caller's working directory."""
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.version_info < (3, 10):
        raise SystemExit("GI-Coder requires Python 3.10 or later. Install Python from python.org.")
    env = ROOT / ".venv"
    bindir = env / ("Scripts" if os.name == "nt" else "bin")
    python = bindir / ("python.exe" if os.name == "nt" else "python")
    if not python.exists():
        print("Creating local Python environment...", flush=True)
        venv.EnvBuilder(with_pip=True).create(env)
    # Reinstall only if declared package requirements or packaging metadata changed.
    import hashlib
    stamp = env / ".gi-coder-installed"
    digest = hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()
    if not stamp.exists() or stamp.read_text() != digest:
        subprocess.run([str(python), "-m", "pip", "install", "-e", str(ROOT)], check=True)
        stamp.write_text(digest)
    child_env = dict(os.environ)
    child_env["PATH"] = str(bindir) + os.pathsep + child_env.get("PATH", "")
    child_env["GI_CODER_HOME"] = str(ROOT)
    args = sys.argv[1:]
    if args[:1] == ["--launch"]:
        args = args[1:]
    elif not args:
        args = ["setup"]
    command = [str(python), "-m", "gi_coder.cli", *args]
    if os.name == 'posix':
        # On macOS the CLI replaces bootstrap, so terminal signals reach the
        # process that owns the model instead of leaving an extra waiting parent.
        os.execve(str(python), command, child_env)
    return subprocess.call(command, env=child_env)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Setup failed: {exc}. Check Python/venv availability, disk space and network access.", file=sys.stderr)
        sys.exit(1)
