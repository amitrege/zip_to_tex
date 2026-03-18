"""End-to-end archive processing pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile

from .errors import CompileError, SafeExtractionError, TexProcessingError, ZipToTexError
from .tex import RootCandidate, detect_bibliography_mode, detect_engine, detect_root_candidates, flatten_tex_tree

LATEX_FLAGS = [
    "-interaction=nonstopmode",
    "-halt-on-error",
    "-file-line-error",
]
TRANSIENT_SUFFIXES = {
    ".aux",
    ".bcf",
    ".blg",
    ".fdb_latexmk",
    ".fls",
    ".log",
    ".out",
    ".run.xml",
    ".synctex.gz",
    ".toc",
}
SKIP_DIRECTORIES = {"__MACOSX", ".git"}
SUPPORTED_ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz")


@dataclass(slots=True)
class ProcessingResult:
    output_dir: Path
    tex_file: Path
    pdf_file: Path | None
    root_file: Path
    engine: str


def process_archive(
    archive_path: str | Path,
    *,
    output_root: str | Path | None = None,
    engine: str = "auto",
    max_runs: int = 5,
    compile_pdf: bool = True,
) -> ProcessingResult:
    """Process a single source archive into a flattened output."""

    archive = Path(archive_path).expanduser().resolve()
    if not archive.is_file():
        raise ZipToTexError(f"Archive file not found: {archive}")
    if not _is_supported_archive(archive):
        supported = ", ".join(SUPPORTED_ARCHIVE_SUFFIXES)
        raise ZipToTexError(f"Unsupported archive type: {archive}. Supported: {supported}")

    if max_runs < 1:
        raise ZipToTexError("--max-runs must be at least 1.")

    archive_stem = _archive_stem(archive)
    final_parent = Path(output_root).expanduser().resolve() if output_root else archive.parent
    final_output_dir = final_parent / f"{archive_stem}_flat"
    _validate_output_path(final_output_dir)

    temp_dir = Path(tempfile.mkdtemp(prefix=f"{archive_stem}_", dir=str(archive.parent)))
    extract_dir = temp_dir / "extracted"
    build_root = temp_dir / "builds"
    extract_dir.mkdir()
    build_root.mkdir()

    try:
        source_root = safe_extract_archive(archive, extract_dir)
        candidates = detect_root_candidates(source_root)
        failures: list[str] = []

        for index, candidate in enumerate(candidates, start=1):
            build_dir = build_root / f"candidate_{index}"
            build_dir.mkdir()
            try:
                result = _attempt_candidate(
                    archive=archive,
                    archive_stem=archive_stem,
                    source_root=source_root,
                    candidate=candidate,
                    build_dir=build_dir,
                    engine_override=engine,
                    max_runs=max_runs,
                    compile_pdf=compile_pdf,
                )
            except ZipToTexError as exc:
                failures.append(
                    f"- {candidate.path.relative_to(source_root).as_posix()}: {exc}"
                )
                continue

            _prepare_successful_output(build_dir)
            final_output_dir.parent.mkdir(parents=True, exist_ok=True)
            if final_output_dir.exists():
                final_output_dir.rmdir()
            shutil.copytree(build_dir, final_output_dir)

            archive.unlink()
            shutil.rmtree(temp_dir)
            return ProcessingResult(
                output_dir=final_output_dir,
                tex_file=final_output_dir / result.tex_file.name,
                pdf_file=(final_output_dir / result.pdf_file.name) if result.pdf_file else None,
                root_file=result.root_file.relative_to(source_root),
                engine=result.engine,
            )

        details = "\n".join(failures) if failures else "- no viable root candidates found"
        raise ZipToTexError(
            "Failed to process archive. Temporary files were kept for debugging.\n"
            f"Archive: {archive}\n"
            f"Temp dir: {temp_dir}\n"
            f"Attempts:\n{details}"
        )
    except Exception:
        raise


def process_zip(
    zip_path: str | Path,
    *,
    output_root: str | Path | None = None,
    engine: str = "auto",
    max_runs: int = 5,
    compile_pdf: bool = True,
) -> ProcessingResult:
    """Backward-compatible wrapper for the archive processor."""

    return process_archive(
        zip_path,
        output_root=output_root,
        engine=engine,
        max_runs=max_runs,
        compile_pdf=compile_pdf,
    )


def safe_extract_archive(archive_path: Path, extract_dir: Path) -> Path:
    """Safely extract a supported source archive."""

    archive = archive_path.resolve()
    if archive.name.endswith(".zip"):
        return safe_extract_zip(archive, extract_dir)
    if archive.name.endswith(".tar.gz") or archive.name.endswith(".tgz"):
        return safe_extract_tar(archive, extract_dir)

    supported = ", ".join(SUPPORTED_ARCHIVE_SUFFIXES)
    raise SafeExtractionError(f"Unsupported archive type: {archive}. Supported: {supported}")


def safe_extract_zip(zip_path: Path, extract_dir: Path) -> Path:
    """Safely extract a zip archive."""

    extracted_any = False
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            raw_name = info.filename.replace("\\", "/")
            if not raw_name or raw_name.endswith("/"):
                continue

            parts = PurePosixPath(raw_name).parts
            if PurePosixPath(raw_name).is_absolute() or any(part == ".." for part in parts):
                raise SafeExtractionError(f"Unsafe zip entry: {raw_name}")
            if any(part in SKIP_DIRECTORIES for part in parts):
                continue

            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise SafeExtractionError(f"Zip archive contains a symlink: {raw_name}")

            destination = extract_dir.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, destination.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            extracted_any = True

    if not extracted_any:
        raise SafeExtractionError(f"No extractable files found in {zip_path}.")

    source_root = _collapse_single_wrapper_dir(extract_dir).resolve()
    if not any(source_root.rglob("*.tex")):
        raise SafeExtractionError(f"No .tex files found in extracted archive {zip_path}.")
    return source_root


def safe_extract_tar(tar_path: Path, extract_dir: Path) -> Path:
    """Safely extract a gzip-compressed tar archive."""

    extracted_any = False
    with tarfile.open(tar_path, "r:*") as archive:
        for member in archive.getmembers():
            raw_name = member.name.replace("\\", "/")
            if not raw_name or raw_name == ".":
                continue

            parts = PurePosixPath(raw_name).parts
            if PurePosixPath(raw_name).is_absolute() or any(part == ".." for part in parts):
                raise SafeExtractionError(f"Unsafe tar entry: {raw_name}")
            if any(part in SKIP_DIRECTORIES for part in parts):
                continue
            if member.issym() or member.islnk():
                raise SafeExtractionError(f"Tar archive contains a link: {raw_name}")
            if not (member.isfile() or member.isdir()):
                raise SafeExtractionError(f"Tar archive contains an unsupported entry: {raw_name}")

            destination = extract_dir.joinpath(*parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue

            source = archive.extractfile(member)
            if source is None:
                raise SafeExtractionError(f"Could not read tar entry: {raw_name}")

            destination.parent.mkdir(parents=True, exist_ok=True)
            with source, destination.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            extracted_any = True

    if not extracted_any:
        raise SafeExtractionError(f"No extractable files found in {tar_path}.")

    source_root = _collapse_single_wrapper_dir(extract_dir).resolve()
    if not any(source_root.rglob("*.tex")):
        raise SafeExtractionError(f"No .tex files found in extracted archive {tar_path}.")
    return source_root


def _attempt_candidate(
    *,
    archive: Path,
    archive_stem: str,
    source_root: Path,
    candidate: RootCandidate,
    build_dir: Path,
    engine_override: str,
    max_runs: int,
    compile_pdf: bool,
) -> ProcessingResult:
    flattened_name = f"{archive_stem}_flat.tex"
    flattened_path = build_dir / flattened_name
    pdf_path = build_dir / f"{archive_stem}_flat.pdf"

    _copy_support_files(source_root, build_dir)
    flattened_text = flatten_tex_tree(candidate.path, source_root)
    flattened_path.write_text(flattened_text, encoding="utf-8")

    selected_engine = detect_engine(flattened_text, override=engine_override)
    if compile_pdf:
        _compile_flat_tex(
            tex_path=flattened_path,
            engine=selected_engine,
            max_runs=max_runs,
            bibliography_mode=detect_bibliography_mode(flattened_text),
        )

        if not pdf_path.is_file():
            raise CompileError(f"Compilation reported success but did not produce {pdf_path.name}.")
    else:
        pdf_path = None

    return ProcessingResult(
        output_dir=build_dir,
        tex_file=flattened_path,
        pdf_file=pdf_path,
        root_file=candidate.path,
        engine=selected_engine,
    )


def _copy_support_files(source_root: Path, build_dir: Path) -> None:
    for path in sorted(source_root.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(source_root)
        if any(part in SKIP_DIRECTORIES for part in relative.parts):
            continue
        if path.suffix.lower() == ".tex":
            continue
        if _is_transient_file(path):
            continue

        destination = build_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def _compile_flat_tex(
    *,
    tex_path: Path,
    engine: str,
    max_runs: int,
    bibliography_mode: str,
) -> None:
    stem = tex_path.stem
    log_path = tex_path.with_name("build.log")
    rerun_seen = False
    bibliography_done = bibliography_mode == "none"

    for run_number in range(1, max_runs + 1):
        result = subprocess.run(
            [engine, *LATEX_FLAGS, tex_path.name],
            cwd=tex_path.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        _append_command_log(
            log_path,
            f"$ {engine} {' '.join(LATEX_FLAGS)} {tex_path.name}\n",
            result.stdout,
            result.stderr,
        )
        if result.returncode != 0:
            raise CompileError(
                f"{engine} failed on pass {run_number}. See {log_path} for details."
            )

        if bibliography_mode == "bibtex" and not bibliography_done:
            if _should_run_bibtex(tex_path.parent, stem):
                bibtex = shutil.which("bibtex")
                if not bibtex:
                    raise CompileError("This paper needs bibtex, but bibtex is not installed.")
                bib_result = subprocess.run(
                    [bibtex, stem],
                    cwd=tex_path.parent,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                _append_command_log(log_path, f"$ {bibtex} {stem}\n", bib_result.stdout, bib_result.stderr)
                if bib_result.returncode != 0:
                    raise CompileError(f"bibtex failed. See {log_path} for details.")
                bibliography_done = True
                continue
            bibliography_done = True

        if bibliography_mode == "biber" and not bibliography_done:
            if _should_run_biber(tex_path.parent, stem):
                biber = shutil.which("biber")
                if biber:
                    biber_result = subprocess.run(
                        [biber, stem],
                        cwd=tex_path.parent,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    _append_command_log(log_path, f"$ {biber} {stem}\n", biber_result.stdout, biber_result.stderr)
                    if biber_result.returncode != 0:
                        raise CompileError(f"biber failed. See {log_path} for details.")
                elif not _has_usable_bbl(tex_path.parent, stem):
                    raise CompileError(
                        "This paper uses biblatex with biber, but biber is not installed and no .bbl was provided."
                    )
                bibliography_done = True
                continue
            bibliography_done = True

        rerun_seen = _needs_rerun(tex_path.parent, stem)
        if not rerun_seen:
            return

    if rerun_seen:
        raise CompileError(
            f"Compilation still requested another LaTeX pass after {max_runs} runs."
        )


def _append_command_log(log_path: Path, command: str, stdout: str, stderr: str) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(command)
        if stdout:
            handle.write(stdout)
        if stderr:
            handle.write(stderr)
        if not stdout.endswith("\n") and stdout:
            handle.write("\n")
        if not stderr.endswith("\n") and stderr:
            handle.write("\n")


def _should_run_bibtex(work_dir: Path, stem: str) -> bool:
    aux_path = work_dir / f"{stem}.aux"
    if not aux_path.is_file():
        return False

    aux_text = aux_path.read_text(encoding="utf-8", errors="ignore")
    bibdata_lines = [line for line in aux_text.splitlines() if line.startswith("\\bibdata{")]
    if not bibdata_lines:
        return False

    bbl_path = work_dir / f"{stem}.bbl"
    if bbl_path.is_file() and bbl_path.stat().st_size > 0 and not _referenced_bib_files_exist(work_dir, bibdata_lines):
        return False
    return True


def _should_run_biber(work_dir: Path, stem: str) -> bool:
    bcf_path = work_dir / f"{stem}.bcf"
    if not bcf_path.is_file():
        return False

    bbl_path = work_dir / f"{stem}.bbl"
    if bbl_path.is_file() and bbl_path.stat().st_size > 0:
        return False
    return True


def _has_usable_bbl(work_dir: Path, stem: str) -> bool:
    bbl_path = work_dir / f"{stem}.bbl"
    return bbl_path.is_file() and bbl_path.stat().st_size > 0


def _needs_rerun(work_dir: Path, stem: str) -> bool:
    log_path = work_dir / f"{stem}.log"
    if not log_path.is_file():
        return False

    log_text = log_path.read_text(encoding="utf-8", errors="ignore")
    rerun_signals = (
        "Rerun to get cross-references right",
        "LaTeX Warning: Label(s) may have changed.",
        "Package rerunfilecheck Warning",
        "LaTeX Warning: There were undefined references.",
        "LaTeX Warning: There were undefined citations.",
    )
    return any(signal in log_text for signal in rerun_signals)


def _referenced_bib_files_exist(work_dir: Path, bibdata_lines: list[str]) -> bool:
    for line in bibdata_lines:
        contents = line.removeprefix("\\bibdata{").removesuffix("}")
        for entry in contents.split(","):
            candidate = work_dir / f"{entry.strip()}.bib"
            if candidate.is_file():
                return True
    return False


def _prepare_successful_output(build_dir: Path) -> None:
    for path in sorted(build_dir.rglob("*"), reverse=True):
        if path.is_dir():
            if not any(path.iterdir()):
                path.rmdir()
            continue
        if _is_transient_file(path):
            path.unlink()


def _validate_output_path(output_dir: Path) -> None:
    if output_dir.exists():
        if output_dir.is_dir() and not any(output_dir.iterdir()):
            return
        raise ZipToTexError(
            f"Refusing to overwrite existing output directory: {output_dir}"
        )


def _collapse_single_wrapper_dir(root: Path) -> Path:
    current = root
    while True:
        children = [child for child in current.iterdir() if child.name not in SKIP_DIRECTORIES]
        if len(children) == 1 and children[0].is_dir():
            current = children[0]
            continue
        return current


def _is_transient_file(path: Path) -> bool:
    name = path.name
    if name.endswith(".synctex.gz"):
        return True
    return path.suffix.lower() in TRANSIENT_SUFFIXES


def _is_supported_archive(path: Path) -> bool:
    return any(path.name.endswith(suffix) for suffix in SUPPORTED_ARCHIVE_SUFFIXES)


def _archive_stem(path: Path) -> str:
    name = path.name
    for suffix in SUPPORTED_ARCHIVE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem
