#!/usr/bin/env python3
"""
MKV2MP4-Wizard – CLI entry point.

Usage
-----
    python main.py --input /mnt/c/Videos/movie.mkv --output /mnt/c/Videos/out/
    python main.py --input /mnt/c/Videos/ --force --verbose
    python main.py --input movie.mkv --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.rule import Rule

# ---------------------------------------------------------------------------
# Module-level console (stderr so progress doesn't corrupt piped output)
# ---------------------------------------------------------------------------
console = Console(stderr=False)


# ---------------------------------------------------------------------------
# WSL path helpers
# ---------------------------------------------------------------------------

_WIN_PATH_RE = re.compile(
    r"""
    ^
    (?P<drive>[A-Za-z])   # drive letter
    :\\                    # colon + backslash
    """,
    re.VERBOSE,
)


def _is_wsl() -> bool:
    """Return True when running inside a WSL instance."""
    try:
        with open("/proc/version", "r") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def _win_to_wsl(path_str: str) -> str:
    """
    Convert a Windows-style path to its WSL mount equivalent.

    Example: ``C:\\Users\\Alice\\video.mkv`` → ``/mnt/c/Users/Alice/video.mkv``
    """
    m = _WIN_PATH_RE.match(path_str)
    if m:
        drive = m.group("drive").lower()
        rest = path_str[3:].replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    # Already a POSIX path – normalise backslashes just in case.
    return path_str.replace("\\", "/")


def _resolve_input_path(raw: str) -> Path:
    """Resolve and normalise the input path, handling WSL conversions."""
    if _is_wsl():
        raw = _win_to_wsl(raw)
    p = Path(raw).expanduser().resolve()
    if not p.exists():
        console.print(f"[bold red]✗  Input path not found:[/bold red] {p}")
        sys.exit(2)
    return p


def _collect_mkv_files(input_path: Path) -> List[Path]:
    """Return a list of .mkv files from a file or directory path."""
    if input_path.is_file():
        if input_path.suffix.lower() != ".mkv":
            console.print(
                f"[bold red]✗  File is not an MKV:[/bold red] {input_path}"
            )
            sys.exit(2)
        return [input_path]
    # Directory – glob recursively.
    files = sorted(input_path.rglob("*.mkv"))
    if not files:
        console.print(
            f"[bold red]✗  No .mkv files found in:[/bold red] {input_path}"
        )
        sys.exit(2)
    return files


def _output_path_for(
    mkv: Path, output_dir: Optional[Path], input_root: Path
) -> Path:
    """
    Derive the output .mp4 path.

    * If *output_dir* is given, preserve the relative sub-path of *mkv*
      under *input_root*.
    * Otherwise, write the .mp4 beside the source .mkv.
    """
    if output_dir is None:
        return mkv.with_suffix(".mp4")
    rel = mkv.relative_to(input_root) if mkv.is_relative_to(input_root) else Path(mkv.name)
    return output_dir / rel.with_suffix(".mp4")


# ---------------------------------------------------------------------------
# CLI definition
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mkv2mp4-wizard",
        description="Intelligently convert MKV files to MP4 (remux or re-encode).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --input movie.mkv
  python main.py --input /mnt/c/Videos/ --output /mnt/c/Out/ --verbose
  python main.py --input movie.mkv --force --dry-run
  python main.py --input movie.mkv --prefer-system
""",
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        metavar="PATH",
        help="Path to a single .mkv file or a directory containing .mkv files.",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="DIR",
        default=None,
        help="Output directory (default: same directory as source file).",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force re-encoding even when remux would suffice.",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Print the planned ffmpeg command without executing it.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed inspection output (stream table, codec info).",
    )
    parser.add_argument(
        "--prefer-system",
        action="store_true",
        help=(
            "Only use the system-installed ffmpeg/ffprobe. "
            "Fail rather than downloading static binaries."
        ),
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=23,
        metavar="N",
        help="CRF value for libx264 re-encode (default: 23, range 0–51).",
    )
    parser.add_argument(
        "--preset",
        default="medium",
        choices=[
            "ultrafast", "superfast", "veryfast", "faster",
            "fast", "medium", "slow", "slower", "veryslow",
        ],
        help="libx264 speed/quality preset (default: medium).",
    )
    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Orchestrate the full conversion pipeline. Returns an exit code."""
    parser = _build_parser()
    args = parser.parse_args()

    console.print(Rule("[bold cyan]MKV2MP4-Wizard[/bold cyan]"))

    # --- Resolve binaries ---
    from src.bin_manager import get_ffmpeg_path, get_ffprobe_path

    try:
        ffprobe = get_ffprobe_path(prefer_system=args.prefer_system)
        ffmpeg = get_ffmpeg_path(prefer_system=args.prefer_system)
    except RuntimeError as exc:
        console.print(f"[bold red]✗  Binary error:[/bold red] {exc}")
        return 1

    # --- Resolve input ---
    input_path = _resolve_input_path(args.input)
    mkv_files = _collect_mkv_files(input_path)
    input_root = input_path if input_path.is_dir() else input_path.parent
    output_dir = Path(args.output).expanduser().resolve() if args.output else None

    console.print(
        f"\n  Files to process: [bold]{len(mkv_files)}[/bold]\n"
    )

    # --- Process each file ---
    from src.inspector import inspect
    from src.converter import convert

    exit_code = 0

    for idx, mkv in enumerate(mkv_files, 1):
        console.print(
            Rule(
                f"[bold]({idx}/{len(mkv_files)}) {mkv.name}[/bold]",
                style="blue",
            )
        )

        # Inspect
        try:
            result = inspect(ffprobe, mkv, verbose=args.verbose)
        except RuntimeError as exc:
            console.print(f"[bold red]✗  Inspection failed:[/bold red] {exc}")
            exit_code = 1
            continue

        # Always print a one-liner summary even without --verbose
        if not args.verbose:
            mode_color = "green" if result.mode == "remux" else "yellow"
            console.print(
                f"  Strategy: [{mode_color}]{result.mode.upper()}[/{mode_color}]  "
                f"Video={result.video_streams[0].codec_name if result.video_streams else '—'}  "
                f"Audio={', '.join(s.codec_name for s in result.audio_streams) or '—'}"
            )

        # Output path
        out = _output_path_for(mkv, output_dir, input_root)

        # Convert
        rc = convert(
            ffmpeg_path=ffmpeg,
            input_path=mkv,
            output_path=out,
            result=result,
            force_reencode=args.force,
            dry_run=args.dry_run,
            crf=args.crf,
            preset=args.preset,
        )
        if rc != 0:
            exit_code = rc

    console.print(Rule("[bold cyan]Done[/bold cyan]"))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
