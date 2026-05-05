"""
inspector.py – Run ffprobe on an MKV file and decide the conversion strategy.

Decisions
---------
* **remux**   – All streams can be container-copied without quality loss.
* **reencode** – At least one stream needs transcoding to be MP4-compatible.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.table import Table

console = Console()

# Codecs that MP4 can carry as-is (copy mode).
_VIDEO_COPY_CODECS = {"h264", "hevc", "h265", "avc"}
_AUDIO_COPY_CODECS = {"aac", "mp3", "mp4a"}

# Codecs that require re-encoding.
_VIDEO_REENCODE_CODECS = {"vp9", "vp8", "av1", "theora"}
_AUDIO_REENCODE_CODECS = {"ac3", "dts", "eac3", "flac", "vorbis", "opus"}

# Subtitle formats incompatible with plain MP4 containers.
_INCOMPATIBLE_SUBTITLE_CODECS = {"ass", "ssa", "dvd_subtitle", "hdmv_pgs_subtitle"}


@dataclass
class StreamInfo:
    """Holds parsed data for a single media stream."""

    index: int
    codec_type: str          # "video", "audio", "subtitle", "data", …
    codec_name: str
    codec_long_name: str
    language: Optional[str] = None
    title: Optional[str] = None
    # Video-specific
    width: Optional[int] = None
    height: Optional[int] = None
    frame_rate: Optional[str] = None
    # Audio-specific
    channels: Optional[int] = None
    sample_rate: Optional[str] = None
    # Raw dict from ffprobe (for advanced use)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InspectionResult:
    """Aggregated result of inspecting an MKV file."""

    path: Path
    format_name: str
    duration_seconds: Optional[float]
    bit_rate: Optional[int]
    global_tags: Dict[str, str]

    video_streams: List[StreamInfo]
    audio_streams: List[StreamInfo]
    subtitle_streams: List[StreamInfo]
    other_streams: List[StreamInfo]

    # Determined by analyse():
    mode: str = "remux"               # "remux" | "reencode"
    reencode_reasons: List[str] = field(default_factory=list)
    subtitle_warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_stream(raw: Dict[str, Any]) -> StreamInfo:
    tags = raw.get("tags") or {}
    disposition = raw.get("disposition") or {}
    return StreamInfo(
        index=raw.get("index", 0),
        codec_type=raw.get("codec_type", "unknown"),
        codec_name=raw.get("codec_name", "unknown").lower(),
        codec_long_name=raw.get("codec_long_name", ""),
        language=tags.get("language"),
        title=tags.get("title"),
        width=_safe_int(raw.get("width")),
        height=_safe_int(raw.get("height")),
        frame_rate=raw.get("avg_frame_rate"),
        channels=_safe_int(raw.get("channels")),
        sample_rate=raw.get("sample_rate"),
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def inspect(ffprobe_path: str, input_path: Path, verbose: bool = False) -> InspectionResult:
    """
    Run *ffprobe* against *input_path* and return an :class:`InspectionResult`.

    Parameters
    ----------
    ffprobe_path:
        Absolute path to the ffprobe binary.
    input_path:
        The MKV file to inspect.
    verbose:
        When True, print detailed stream tables to the console.
    """
    cmd = [
        ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(input_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffprobe timed out inspecting {input_path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to run ffprobe: {exc}") from exc

    if proc.returncode != 0:
        stderr_msg = proc.stderr.decode(errors="replace").strip()
        raise RuntimeError(
            f"ffprobe exited with code {proc.returncode} for '{input_path}':\n{stderr_msg}"
        )

    data: Dict[str, Any] = json.loads(proc.stdout.decode())

    fmt = data.get("format", {})
    fmt_tags: Dict[str, str] = {
        k.lower(): v for k, v in (fmt.get("tags") or {}).items()
    }

    video_streams: List[StreamInfo] = []
    audio_streams: List[StreamInfo] = []
    subtitle_streams: List[StreamInfo] = []
    other_streams: List[StreamInfo] = []

    for raw_stream in data.get("streams", []):
        si = _parse_stream(raw_stream)
        if si.codec_type == "video":
            video_streams.append(si)
        elif si.codec_type == "audio":
            audio_streams.append(si)
        elif si.codec_type == "subtitle":
            subtitle_streams.append(si)
        else:
            other_streams.append(si)

    bit_rate_raw = fmt.get("bit_rate")
    result = InspectionResult(
        path=input_path,
        format_name=fmt.get("format_name", ""),
        duration_seconds=_safe_float(fmt.get("duration")),
        bit_rate=_safe_int(bit_rate_raw) if bit_rate_raw else None,
        global_tags=fmt_tags,
        video_streams=video_streams,
        audio_streams=audio_streams,
        subtitle_streams=subtitle_streams,
        other_streams=other_streams,
    )

    _analyse(result)

    if verbose:
        _print_report(result)

    return result


def _analyse(result: InspectionResult) -> None:
    """
    Populate ``result.mode``, ``result.reencode_reasons``, and
    ``result.subtitle_warnings`` in-place based on stream codecs.
    """
    reasons: List[str] = []

    for vs in result.video_streams:
        codec = vs.codec_name
        if codec in _VIDEO_REENCODE_CODECS:
            reasons.append(
                f"Video stream #{vs.index} codec '{codec}' is not MP4-compatible "
                f"(will re-encode with libx264)"
            )
        elif codec not in _VIDEO_COPY_CODECS:
            reasons.append(
                f"Video stream #{vs.index} codec '{codec}' is unknown; "
                "re-encoding as a precaution"
            )

    for aus in result.audio_streams:
        codec = aus.codec_name
        if codec in _AUDIO_REENCODE_CODECS:
            reasons.append(
                f"Audio stream #{aus.index} codec '{codec}' is not MP4-compatible "
                "(will re-encode with aac)"
            )
        elif codec not in _AUDIO_COPY_CODECS:
            reasons.append(
                f"Audio stream #{aus.index} codec '{codec}' is unknown; "
                "re-encoding as a precaution"
            )

    warnings: List[str] = []
    for ss in result.subtitle_streams:
        codec = ss.codec_name
        if codec in _INCOMPATIBLE_SUBTITLE_CODECS:
            warnings.append(
                f"Subtitle stream #{ss.index} uses '{codec}', which is not "
                "supported in MP4 containers and will be dropped."
            )

    result.reencode_reasons = reasons
    result.subtitle_warnings = warnings
    result.mode = "reencode" if reasons else "remux"


def _print_report(result: InspectionResult) -> None:
    """Render a rich table summarising the inspection result."""
    console.rule(f"[bold]Inspection: {result.path.name}[/bold]")

    # --- Format info ---
    console.print(
        f"  Format : [cyan]{result.format_name}[/cyan]\n"
        f"  Duration: [cyan]{result.duration_seconds:.1f}s[/cyan]"
        if result.duration_seconds
        else f"  Format : [cyan]{result.format_name}[/cyan]"
    )

    # --- Stream table ---
    tbl = Table(show_header=True, header_style="bold magenta")
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("Type", width=8)
    tbl.add_column("Codec", width=12)
    tbl.add_column("Details")
    tbl.add_column("Lang", width=6)

    for streams in (
        result.video_streams,
        result.audio_streams,
        result.subtitle_streams,
        result.other_streams,
    ):
        for s in streams:
            if s.codec_type == "video":
                details = f"{s.width or '?'}x{s.height or '?'} @ {s.frame_rate or '?'} fps"
            elif s.codec_type == "audio":
                details = f"{s.channels or '?'}ch  {s.sample_rate or '?'} Hz"
            else:
                details = s.title or ""
            tbl.add_row(
                str(s.index),
                s.codec_type,
                s.codec_name,
                details,
                s.language or "—",
            )

    console.print(tbl)

    # --- Decision ---
    mode_color = "green" if result.mode == "remux" else "yellow"
    console.print(
        f"\n  Decision: [{mode_color}]{result.mode.upper()}[/{mode_color}]"
    )
    for reason in result.reencode_reasons:
        console.print(f"    [yellow]• {reason}[/yellow]")
    for warning in result.subtitle_warnings:
        console.print(f"    [red]⚠  {warning}[/red]")
    console.print()
