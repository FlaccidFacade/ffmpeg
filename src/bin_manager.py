"""
bin_manager.py – Locate or download ffmpeg/ffprobe static binaries.

Priority chain:
  1. System PATH (shutil.which) – verified with --version.
  2. Local bin/ directory next to this package.
  3. Download latest static build from BtbN/FFmpeg-Builds, extract, cache.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

import requests
from rich.console import Console
from tqdm import tqdm

console = Console()

# Directory that holds cached static binaries (relative to this file's parent).
_BIN_DIR: Path = Path(__file__).resolve().parent.parent / "bin"

# BtbN nightly release URL pattern.
_BTBN_RELEASE_API = (
    "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
)
_BTBN_ASSET_PATTERN = "ffmpeg-master-latest-linux{arch}-gpl.tar.xz"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _arch_suffix() -> str:
    """Return the architecture suffix used by BtbN release asset names."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    raise RuntimeError(f"Unsupported CPU architecture: {machine}")


def _verify_binary(path: str) -> Optional[str]:
    """
    Run *path* with ``--version`` and return the version string on success,
    or ``None`` if the binary cannot be executed.
    """
    try:
        result = subprocess.run(
            [path, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        if result.returncode == 0:
            first_line = result.stdout.decode(errors="replace").splitlines()[0]
            return first_line
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _download_file(url: str, dest: Path) -> None:
    """Stream-download *url* to *dest* with a tqdm progress bar."""
    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()
    total = int(response.headers.get("content-length", 0))
    with open(dest, "wb") as fh, tqdm(
        desc=dest.name,
        total=total,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        leave=False,
    ) as bar:
        for chunk in response.iter_content(chunk_size=65536):
            fh.write(chunk)
            bar.update(len(chunk))


def _download_static_binaries() -> None:
    """
    Fetch the latest BtbN GPL static build for the current architecture,
    extract ``ffmpeg`` and ``ffprobe`` into *_BIN_DIR*, and make them
    executable.
    """
    _BIN_DIR.mkdir(parents=True, exist_ok=True)

    arch = _arch_suffix()
    asset_name = _BTBN_ASSET_PATTERN.format(arch=arch)

    console.print(
        f"[bold yellow]⬇  Fetching latest FFmpeg release metadata …[/bold yellow]"
    )
    api_resp = requests.get(_BTBN_RELEASE_API, timeout=15)
    api_resp.raise_for_status()
    release_data = api_resp.json()

    download_url: Optional[str] = None
    for asset in release_data.get("assets", []):
        if asset["name"] == asset_name:
            download_url = asset["browser_download_url"]
            break

    if download_url is None:
        raise RuntimeError(
            f"Could not find asset '{asset_name}' in the latest BtbN release. "
            "Check https://github.com/BtbN/FFmpeg-Builds/releases manually."
        )

    console.print(
        f"[bold yellow]⬇  Downloading static FFmpeg build …[/bold yellow]\n"
        f"   [dim]{download_url}[/dim]"
    )

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        tarball = tmp_dir / asset_name
        _download_file(download_url, tarball)

        console.print("[bold yellow]📦 Extracting binaries …[/bold yellow]")
        with tarfile.open(tarball, "r:xz") as tf:
            for member in tf.getmembers():
                basename = Path(member.name).name
                if basename in ("ffmpeg", "ffprobe") and member.isfile():
                    member.name = basename  # flatten directory structure
                    tf.extract(member, path=_BIN_DIR)

    # Ensure executables have +x
    for name in ("ffmpeg", "ffprobe"):
        binary = _BIN_DIR / name
        if binary.exists():
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    console.print(
        f"[bold green]✓  Static binaries cached in:[/bold green] {_BIN_DIR}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _resolve_binary(name: str, prefer_system: bool) -> str:
    """
    Return an absolute path to *name* (``ffmpeg`` or ``ffprobe``).

    Resolution order
    ----------------
    1. System PATH (unless *prefer_system* is False and a local copy exists).
    2. ``bin/<name>`` next to the project root.
    3. Download static build into ``bin/``.

    Raises
    ------
    RuntimeError
        When no usable binary can be found and *prefer_system* is True
        (download is disabled).
    """
    # --- Priority 1: system PATH ---
    system_path = shutil.which(name)
    if system_path:
        version = _verify_binary(system_path)
        if version:
            console.print(
                f"[green]✓  Using system [bold]{name}[/bold]:[/green] "
                f"{version.split(',')[0]}"
            )
            return system_path
        console.print(
            f"[yellow]⚠  System {name} found at {system_path} "
            "but failed version check.[/yellow]"
        )

    if prefer_system:
        raise RuntimeError(
            f"System '{name}' is unavailable and --prefer-system was set. "
            "Install ffmpeg via your package manager and retry."
        )

    # --- Priority 2: local bin/ ---
    local = _BIN_DIR / name
    if local.exists():
        version = _verify_binary(str(local))
        if version:
            console.print(
                f"[cyan]✓  Using cached local [bold]{name}[/bold]:[/cyan] "
                f"{version.split(',')[0]}"
            )
            return str(local)
        console.print(
            f"[yellow]⚠  Local {name} at {local} failed version check. "
            "Re-downloading …[/yellow]"
        )

    # --- Priority 3: download ---
    console.print(
        f"[bold yellow]⚠  '{name}' not found. "
        "Downloading and caching static build …[/bold yellow]"
    )
    _download_static_binaries()

    local = _BIN_DIR / name
    if not local.exists():
        raise RuntimeError(
            f"Download completed but '{name}' was not found in {_BIN_DIR}. "
            "The archive layout may have changed."
        )
    return str(local)


def get_ffmpeg_path(prefer_system: bool = False) -> str:
    """Return a verified path to ``ffmpeg``."""
    return _resolve_binary("ffmpeg", prefer_system)


def get_ffprobe_path(prefer_system: bool = False) -> str:
    """Return a verified path to ``ffprobe``."""
    return _resolve_binary("ffprobe", prefer_system)
