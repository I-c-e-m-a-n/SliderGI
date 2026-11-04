"""Probe the pinned engine's local Metal devices; no model is loaded by probing."""
import platform
import re
import subprocess


def wants_metal(config):
    return (platform.system() == 'Darwin' and config.get('backend', 'auto') != 'cpu'
            and config.get('gpu_layers', 99) > 0)


def metal_device(engine):
    try:
        result = subprocess.run([str(engine), '--list-devices'], capture_output=True,
                                text=True, timeout=15, check=True)
        match = re.search(r'^\s*(Metal\d*):', result.stdout, re.M)
        return match.group(1) if match else None
    except (OSError, subprocess.SubprocessError):
        return None
