# zip_to_tex

This repo is for a pretty specific job: you grab the source archive for a paper from arXiv, drop it into this workflow, and it gives you one flattened `.tex` file instead of a pile of nested `\input{}` files.

I mostly built it for the annoying cases where a paper has `main.tex`, `macros.tex`, section files, figures, random style files, and you just want one top-level tex file you can inspect or pass around without digging through the whole tree.

## What it does

- takes an arXiv source archive: `.zip`, `.tar.gz`, or `.tgz`
- unpacks it safely
- tries to figure out which `.tex` file is the real entry point
- pulls the included tex files into one flattened output file
- keeps the assets that still matter, like figures, `.bib`, `.sty`, and friends
- by default, skips PDF compilation and just gives you the flattened tex
- if you want, you can still tell it to try a PDF build

## The normal way to use it

From this repo folder:

```bash
./launch.sh /path/to/paper.tar.gz
```

or

```bash
./launch.sh /path/to/paper.zip
```

That script does a few things for you:

- moves the archive into this repo folder with `mv` instead of copying it
- runs the flattening pipeline here
- removes the original archive after a successful run
- leaves you with a folder like `papername_flat/`

Inside that output folder you’ll usually see:

- `papername_flat.tex`
- figures and other support files the flattened tex still needs
- optionally a PDF, if you asked it to compile

## If you want PDF compilation too

The launcher skips compilation by default, because real-world paper sources are messy and missing TeX packages gets old fast.

If you still want it to take a shot at the PDF:

```bash
./launch.sh /path/to/paper.tar.gz --compile
```

If the compile step blows up, the tool keeps the temp folder around so you can inspect the LaTeX log and the generated flat file.

## You can also run the Python entrypoint directly

```bash
env PYTHONPATH=src python3 -m zip_to_tex /path/to/paper.tar.gz --no-compile
```

Some useful flags:

- `--no-compile` keeps it tex-only
- `--compile` tries to build a PDF
- `--engine xelatex` or `--engine lualatex` if you want to force an engine
- `--output-root /some/folder` if you want the result somewhere else
- `--max-runs 6` if you want more LaTeX passes during compilation

## What this repo is good for

- reading a paper source tree without chasing imports all over the place
- handing someone one main tex file instead of a whole archive
- making it easier to inspect macros, sections, and figure references in one place
- getting a cleaner starting point before doing your own edits

## What it does not magically solve

- weird custom TeX macros that build file paths dynamically
- every possible LaTeX package setup on earth
- source trees that rely on shell escape or very custom build steps
- papers that are technically compilable but only in a very particular local setup

So the safe expectation is: this should do a good job flattening normal arXiv paper sources, but PDF compilation is still best-effort.

## Install

If you want the Python entrypoint available directly:

```bash
python3 -m pip install -e .
```

## Tests

```bash
env PYTHONPATH=src python3 -m unittest discover -s tests -v
```
