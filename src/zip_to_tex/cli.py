"""Command-line interface for zip_to_tex."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .errors import ZipToTexError
from .pipeline import process_archive


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zip-to-tex",
        description="Flatten an arXiv source archive into one compilable .tex file.",
    )
    parser.add_argument("zip_path", help="Path to a source .zip, .tar.gz, or .tgz file.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Directory where the <zip_stem>_flat output folder should be created.",
    )
    parser.add_argument(
        "--engine",
        choices=("auto", "pdflatex", "xelatex", "lualatex"),
        default="auto",
        help="Compiler engine to use. Defaults to automatic detection.",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=5,
        help="Maximum number of LaTeX passes.",
    )
    parser.set_defaults(compile_pdf=True)
    parser.add_argument(
        "--compile",
        dest="compile_pdf",
        action="store_true",
        help="Compile the flattened TeX into a PDF.",
    )
    parser.add_argument(
        "--no-compile",
        dest="compile_pdf",
        action="store_false",
        help="Skip compilation and emit only the flattened TeX plus support files.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = process_archive(
            args.zip_path,
            output_root=args.output_root,
            engine=args.engine,
            max_runs=args.max_runs,
            compile_pdf=args.compile_pdf,
        )
    except ZipToTexError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Output: {result.output_dir}")
    print(f"Root: {result.root_file}")
    print(f"Engine: {result.engine}")
    print(f"TeX: {result.tex_file}")
    if result.pdf_file:
        print(f"PDF: {result.pdf_file}")
    else:
        print("PDF: skipped")
    return 0
