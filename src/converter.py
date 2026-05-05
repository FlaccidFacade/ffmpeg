"""
converter.py – Build and execute ffmpeg conversion commands.

Supports two modes:
* **remux**    – Container-copy all streams; near-instant.
* **reencode** – Transcode video to libx264 and audio to aac.

A lightweight progress bar is driven by parsing ffmpeg's ``time=`` output on
stderr.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from .inspector import InspectionResult

console = Console()

# Regex to parse "time=HH:MM:SS.xx" from ffmpeg stderr.
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------

def _build_remux_cmd(
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    result: InspectionResult,
    extra_metadata: Optional[dict] = None,
) -> List[str]:
    """Return the ffmpeg command list for a stream-copy remux."""
    cmd = [
        ffmpeg_path,
        "-y",                     # overwrite output without asking
        "-i", str(input_path),
        "-c", "copy",             # copy all streams
        "-map_metadata", "0",     # preserve global metadata
    ]

    # Drop subtitle streams that are incompatible with MP4.
    if result.subtitle_warnings:
        cmd += ["-sn"]            # no subtitle output

    # Inject / override specific metadata tags if provided.
    if extra_metadata:
        for key, value in extra_metadata.items():
            cmd += ["-metadata", f"{key}={value}"]

    cmd.append(str(output_path))
    return cmd


def _build_reencode_cmd(
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    result: InspectionResult,
    crf: int = 23,
    preset: str = "medium",
    extra_metadata: Optional[dict] = None,
) -> List[str]:
    """Return the ffmpeg command list for a full re-encode."""
    cmd = [
        ffmpeg_path,
        "-y",
        "-i", str(input_path),
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        "-c:a", "aac",
        "-b:a", "192k",
        "-map_metadata", "0",
    ]

    if result.subtitle_warnings:
        cmd += ["-sn"]

    if extra_metadata:
        for key, value in extra_metadata.items():
            cmd += ["-metadata", f"{key}={value}"]

    cmd.append(str(output_path))
    return cmd


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def _parse_time_seconds(line: str) -> Optional[float]:
    """Extract the current playback time in seconds from an ffmpeg status line."""
    match = _TIME_RE.search(line)
    if not match:
        return None
    h, m, s, cs = (int(x) for x in match.groups())
    return h * 3600 + m * 60 + s + cs / 100


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert(
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    result: InspectionResult,
    force_reencode: bool = False,
    dry_run: bool = False,
    crf: int = 23,
    preset: str = "medium",
) -> int:
    """
    Run ffmpeg to convert *input_path* → *output_path*.

    Parameters
    ----------
    ffmpeg_path:
        Absolute path to the ffmpeg binary.
    input_path:
        Source MKV file.
    output_path:
        Destination MP4 file.
    result:
        :class:`~src.inspector.InspectionResult` from the inspection phase.
    force_reencode:
        Override the smart decision and always re-encode.
    dry_run:
        Print the planned command and return 0 without executing.
    crf:
        Constant-Rate-Factor for libx264 (default 23).
    preset:
        libx264 speed/quality preset (default "medium").

    Returns
    -------
    int
        Exit code (0 = success).
    """
    # Collect any global metadata to forward
    extra_meta = {}
    for tag in ("title", "artist", "date", "comment", "album", "genre"):
        if tag in result.global_tags:
            extra_meta[tag] = result.global_tags[tag]

    mode = "reencode" if (force_reencode or result.mode == "reencode") else "remux"

    if mode == "remux":
        cmd = _build_remux_cmd(
            ffmpeg_path, input_path, output_path, result, extra_meta
        )
        mode_label = "[green]REMUX[/green] (stream copy)"
    else:
        cmd = _build_reencode_cmd(
            ffmpeg_path, input_path, output_path, result, crf, preset, extra_meta
        )
        mode_label = f"[yellow]RE-ENCODE[/yellow] (libx264 CRF={crf}, preset={preset})"

    console.print(f"\n  Mode   : {mode_label}")
    console.print(f"  Output : [cyan]{output_path}[/cyan]")

    if dry_run:
        console.print("\n[bold magenta]── Dry-run: planned command ──[/bold magenta]")
        console.print("  " + " ".join(cmd))
        return 0

    # Ensure output directory exists.
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Print subtitle warnings before starting.
    for warning in result.subtitle_warnings:
        console.print(f"  [red]⚠  {warning}[/red]")

    duration = result.duration_seconds or 0.0

    return _run_ffmpeg(cmd, duration, input_path.name)


def _run_ffmpeg(cmd: List[str], duration: float, label: str) -> int:
    """
    Execute *cmd* (an ffmpeg invocation) and display a progress bar driven by
    parsing the ``time=`` tokens on stderr.

    Returns the process exit code.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    total = max(duration, 1.0)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task(f"[cyan]{label}[/cyan]", total=total)

        stderr_lines: List[str] = []
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)
            elapsed = _parse_time_seconds(line)
            if elapsed is not None:
                progress.update(task, completed=min(elapsed, total))

        progress.update(task, completed=total)

    proc.wait()

    if proc.returncode != 0:
        console.print(
            f"\n[bold red]✗  ffmpeg failed (exit {proc.returncode}):[/bold red]"
        )
        # Print last 20 lines of stderr for context.
        for line in stderr_lines[-20:]:
            console.print(f"  [dim]{line.rstrip()}[/dim]")
        return proc.returncode

    console.print(f"\n[bold green]✓  Conversion complete →[/bold green] {cmd[-1]}")
    return 0
