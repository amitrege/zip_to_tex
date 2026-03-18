# zip_to_tex

`zip_to_tex` takes an arXiv-style source archive, safely extracts it, finds the most likely main LaTeX document, flattens the include tree into one generated `.tex` file, compiles it, and keeps only the flattened output directory on success.

## What it does

- Accepts a single source `.zip`, `.tar.gz`, or `.tgz` archive at a time.
- Rejects unsafe archive entries such as path traversal members or symlinks.
- Detects the most likely root document and falls back to other viable roots if the first candidate does not compile.
- Inlines common TeX include commands into one generated file.
- Copies figures, bibliography files, class/style files, and other non-TeX support assets needed to recompile.
- Compiles with `pdflatex`, `xelatex`, or `lualatex`, with automatic engine detection by default.
- Deletes the original archive and temporary extraction directory only after a successful build.

## Install

```bash
python3 -m pip install -e .
```

## Usage

```bash
zip-to-tex path/to/paper.zip
zip-to-tex path/to/paper.tar.gz
```

Repo-local launcher:

```bash
./launch.sh /path/to/paper.zip
./launch.sh /path/to/paper.tar.gz
```

That script moves the archive into this repo directory with `mv`, runs the pipeline here, and leaves the final `<archive_stem>_flat/` output folder in this directory. By default it skips PDF compilation and just emits the flattened `.tex` plus support assets. Pass `--compile` if you want it to try building the PDF too.

Optional flags:

```bash
zip-to-tex paper.zip --output-root out --engine auto --max-runs 5
zip-to-tex paper.tar.gz --no-compile
```

Successful runs create a folder named `<zip_stem>_flat/` under the output root. That folder contains:

- `<zip_stem>_flat.tex`
- `<zip_stem>_flat.pdf`
- supporting assets such as figures, `.bib`, `.bbl`, `.bst`, `.cls`, and `.sty`

The tool fails safely when it cannot determine a usable root, flatten a dynamic include path, or complete compilation.

## Test

```bash
env PYTHONPATH=src python3 -m unittest discover -s tests -v
```
