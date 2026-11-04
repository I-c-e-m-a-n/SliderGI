"""Official HF downloads pinned to metadata revision and checked against LFS SHA-256."""
import hashlib
import json
import shutil
from pathlib import Path

from .config import ROOT, MODELS, CoderError, installation_path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_file(path, expected_hash, expected_size):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != expected_size:
        raise CoderError(f"Missing or incomplete model: {path} (expected {expected_size:,} bytes)")
    if sha256(path) != expected_hash:
        raise CoderError(f"SHA-256 mismatch: {path}. Remove the corrupt file and run setup again.")
    with path.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise CoderError(f"Not a GGUF model: {path}")


def model_path(config, root=ROOT):
    return (installation_path(config["model_path"], root) if config.get("model_path")
            else root / "models" / MODELS[config["model"]]["filename"])


def ensure_model(config, root=ROOT, offline=False, full_verify=False):
    path = model_path(config, root)
    receipt = path.with_name(path.name + ".verified.json")
    try:
        metadata = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else None
        spec = MODELS[config["model"]]
        if metadata and (metadata.get("repo") != spec["repo"] or
                         metadata.get("filename") != spec["filename"]):
            raise CoderError(f"Verification receipt does not match configured model: {receipt}")
        if path.exists() and metadata:
            stat = path.stat()
            if full_verify or [stat.st_size, stat.st_mtime_ns] != metadata.get("stat"):
                verify_file(path, metadata["sha256"], metadata["size"])
                metadata["stat"] = [stat.st_size, stat.st_mtime_ns]
                _save(receipt, metadata)
            return path
        if offline:
            raise CoderError(f"Model or verification receipt missing: {path}. Run online setup first.")
        from huggingface_hub import HfApi, hf_hub_download
        if metadata is None:
            info = HfApi().model_info(spec["repo"], files_metadata=True)
            entry = next((f for f in info.siblings if f.rfilename == spec["filename"]), None)
            if entry is None or entry.lfs is None:
                raise CoderError(f"Official repository lacks {spec['filename']} with LFS SHA-256 metadata")
            metadata = {"repo": spec["repo"], "filename": spec["filename"],
                        "revision": info.sha, "sha256": entry.lfs.sha256, "size": entry.size}
        if path.exists():
            verify_file(path, metadata["sha256"], metadata["size"])
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Account for HF cache plus destination copy and headroom.
            if shutil.disk_usage(path.parent).free < metadata["size"] * 2 + 512 * 1024**2:
                raise CoderError(f"Insufficient disk space for {path}; need approximately "
                                 f"{metadata['size'] * 2 / 1024**3 + .5:.1f} GiB free")
            print(f"Downloading {spec['repo']} / {spec['filename']} (resumable HF cache)...", flush=True)
            # local_dir uses resumable .cache/huggingface/download/*.incomplete files.
            cached = Path(hf_hub_download(repo_id=spec["repo"], filename=spec["filename"],
                                         revision=metadata["revision"], local_dir=str(path.parent)))
            verify_file(cached, metadata["sha256"], metadata["size"])
            if cached != path:
                shutil.copyfile(cached, path)
        stat = path.stat()
        metadata["stat"] = [stat.st_size, stat.st_mtime_ns]
        _save(receipt, metadata)
        return path
    except CoderError:
        raise
    except Exception as exc:
        raise CoderError(f"Model setup failed: {exc}. Check disk space, network access to "
                         "huggingface.co and its download CDN, then rerun setup; partial downloads resume.") from exc


def _save(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
