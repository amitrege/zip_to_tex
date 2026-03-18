"""TeX discovery, flattening, and source analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import posixpath
import re
from typing import Iterable

from .errors import TexProcessingError

PROTECTED_ENVIRONMENTS = {
    "verbatim",
    "Verbatim",
    "lstlisting",
    "minted",
}

DOCUMENTCLASS_RE = re.compile(r"\\documentclass(?:\[[^\]]*\])?\{[^{}]+\}")
BEGIN_DOCUMENT_RE = re.compile(r"\\begin\{document\}")
END_DOCUMENT_RE = re.compile(r"\\end\{document\}")
MAGIC_ENGINE_RE = re.compile(
    r"^\s*%\s*!TEX\s+program\s*=\s*(pdflatex|xelatex|lualatex)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
INCLUDEONLY_RE = re.compile(r"\\includeonly\s*\{(?P<arg>[^{}]+)\}")
SIMPLE_INCLUDE_RE = re.compile(
    r"\\(?P<cmd>input|include|subfile)\s*(?P<opt>\[[^\]]*\]\s*)?\{(?P<arg>[^{}]+)\}"
)
IMPORT_INCLUDE_RE = re.compile(
    r"\\(?P<cmd>import|subimport)\s*\{(?P<dir>[^{}]+)\}\s*\{(?P<arg>[^{}]+)\}"
)
GRAPHICSPATH_RE = re.compile(r"\\graphicspath\s*\{(?P<body>(?:\{[^{}]+\}\s*)+)\}")
PATH_COMMAND_RE = re.compile(
    r"""
    \\(?P<cmd>includegraphics|bibliography|addbibresource|lstinputlisting|includepdf)
    (?P<opt>\s*\[[^\]]*\])?
    \s*\{(?P<arg>[^{}]+)\}
    """,
    re.VERBOSE,
)
INPUTMINTED_RE = re.compile(
    r"""
    \\inputminted
    (?P<opt>\s*\[[^\]]*\])?
    \s*\{(?P<lang>[^{}]+)\}
    \s*\{(?P<arg>[^{}]+)\}
    """,
    re.VERBOSE,
)
BIBLATEX_RE = re.compile(
    r"\\usepackage(?:\[(?P<opts>[^\]]*)\])?\{biblatex\}",
    re.IGNORECASE,
)
FONTSPEC_RE = re.compile(r"\\(?:usepackage(?:\[[^\]]*\])?\{fontspec\}|setmainfont\{)")
UNICODE_MATH_RE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{unicode-math\}")
LUALATEX_HINT_RE = re.compile(r"\\(?:directlua|usepackage(?:\[[^\]]*\])?\{luacode\})")

ROOT_FILENAME_HINTS = {
    "main": 40,
    "paper": 35,
    "ms": 30,
    "article": 25,
}


@dataclass(slots=True)
class RootCandidate:
    """A potential main TeX root file."""

    path: Path
    score: int
    reason: str


@dataclass(slots=True)
class _TexMetadata:
    path: Path
    has_documentclass: bool
    has_begin_document: bool
    includes: list[Path]
    magic_engine: str | None


@dataclass(slots=True)
class _Action:
    start: int
    end: int
    kind: str
    match: re.Match[str]


def split_protected_segments(text: str) -> list[tuple[bool, str]]:
    """Split text into normal and protected segments."""

    if not text:
        return []

    begin_pattern = re.compile(
        r"\\begin\{(" + "|".join(re.escape(name) for name in sorted(PROTECTED_ENVIRONMENTS)) + r")\}"
    )
    segments: list[tuple[bool, str]] = []
    cursor = 0

    while cursor < len(text):
        begin_match = begin_pattern.search(text, cursor)
        if not begin_match:
            segments.append((False, text[cursor:]))
            break
        if begin_match.start() > cursor:
            segments.append((False, text[cursor:begin_match.start()]))

        env = begin_match.group(1)
        end_pattern = re.compile(rf"\\end\{{{re.escape(env)}\}}")
        end_match = end_pattern.search(text, begin_match.end())
        if not end_match:
            segments.append((True, text[begin_match.start() :]))
            break

        segments.append((True, text[begin_match.start() : end_match.end()]))
        cursor = end_match.end()

    return segments


def mask_comments(text: str) -> str:
    """Replace commented characters with spaces while preserving offsets."""

    chars = list(text)
    index = 0

    while index < len(chars):
        if chars[index] == "%" and not _is_escaped(chars, index):
            while index < len(chars) and chars[index] != "\n":
                chars[index] = " "
                index += 1
            continue
        index += 1

    return "".join(chars)


def detect_engine(text: str, override: str = "auto") -> str:
    """Pick a LaTeX engine."""

    if override != "auto":
        return override

    magic = MAGIC_ENGINE_RE.search(text)
    if magic:
        return magic.group(1).lower()

    if FONTSPEC_RE.search(text) or UNICODE_MATH_RE.search(text):
        return "xelatex"
    if LUALATEX_HINT_RE.search(text):
        return "lualatex"
    return "pdflatex"


def detect_bibliography_mode(text: str) -> str:
    """Return none, bibtex, or biber."""

    match = BIBLATEX_RE.search(text)
    if match:
        opts = (match.group("opts") or "").replace(" ", "").lower()
        if "backend=bibtex" in opts or "backend=bibtex8" in opts:
            return "bibtex"
        return "biber"

    if "\\bibliography{" in text or "\\bibliographystyle{" in text:
        return "bibtex"
    return "none"


def detect_root_candidates(source_root: Path) -> list[RootCandidate]:
    """Rank potential root TeX files."""

    source_root = source_root.resolve()
    tex_files = sorted(path.resolve() for path in source_root.rglob("*.tex"))
    if not tex_files:
        raise TexProcessingError(f"No .tex files found under {source_root}.")

    metadata_by_path = {path: _inspect_tex_file(path, source_root) for path in tex_files}
    included_by: dict[Path, set[Path]] = {path: set() for path in tex_files}
    for path, metadata in metadata_by_path.items():
        for child in metadata.includes:
            if child in included_by:
                included_by[child].add(path)

    def transitive_size(path: Path, seen: set[Path] | None = None) -> int:
        active = seen or set()
        if path in active:
            return 0
        active.add(path)
        total = 0
        for child in metadata_by_path[path].includes:
            if child in metadata_by_path:
                total += 1 + transitive_size(child, active.copy())
        return total

    candidates: list[RootCandidate] = []
    for path, metadata in metadata_by_path.items():
        depth = len(path.relative_to(source_root).parts) - 1
        score = 0
        reasons: list[str] = []
        if metadata.has_documentclass:
            score += 100
            reasons.append("documentclass")
        if metadata.has_begin_document:
            score += 50
            reasons.append("begin-document")
        if not included_by[path]:
            score += 15
            reasons.append("not-included")
        if depth == 0:
            score += 10
            reasons.append("top-level")

        transitive = transitive_size(path)
        if transitive:
            score += transitive * 2
            reasons.append(f"{transitive}-includes")

        stem_hint = ROOT_FILENAME_HINTS.get(path.stem.lower())
        if stem_hint:
            score += stem_hint
            reasons.append(f"name:{path.stem.lower()}")

        size_hint = max(1, path.stat().st_size // 500)
        score += size_hint

        if metadata.magic_engine:
            score += 5
            reasons.append(f"magic:{metadata.magic_engine}")

        if metadata.has_documentclass or metadata.has_begin_document:
            candidates.append(RootCandidate(path=path, score=score, reason=", ".join(reasons)))

    if not candidates:
        for path, metadata in metadata_by_path.items():
            score = transitive_size(path) * 2 + max(1, path.stat().st_size // 500)
            candidates.append(RootCandidate(path=path, score=score, reason="fallback"))

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.score,
            len(candidate.path.relative_to(source_root).parts),
            candidate.path.name,
        ),
    )


def flatten_tex_tree(root_file: Path, source_root: Path) -> str:
    """Flatten a TeX include tree into one file."""

    resolved_root = source_root.resolve()
    return _flatten_file(root_file.resolve(), resolved_root, stack=[])


def _flatten_file(path: Path, source_root: Path, stack: list[Path]) -> str:
    if path in stack:
        cycle = " -> ".join(node.relative_to(source_root).as_posix() for node in stack + [path])
        raise TexProcessingError(f"Detected include cycle: {cycle}")

    text = path.read_text(encoding="utf-8", errors="ignore")
    includeonly = _collect_includeonly(text)
    pieces: list[str] = []
    current_stack = stack + [path]

    for protected, segment in split_protected_segments(text):
        if protected:
            pieces.append(segment)
            continue

        masked = mask_comments(segment)
        actions = _collect_actions(masked)
        cursor = 0
        for action in actions:
            if action.start < cursor:
                continue
            pieces.append(segment[cursor : action.start])
            pieces.append(
                _render_action(
                    action=action,
                    source_segment=segment,
                    current_file=path,
                    source_root=source_root,
                    includeonly=includeonly,
                    stack=current_stack,
                )
            )
            cursor = action.end
        pieces.append(segment[cursor:])

    combined = "".join(pieces)
    if stack:
        combined = _strip_document_wrapper(combined)
    return combined


def _inspect_tex_file(path: Path, source_root: Path) -> _TexMetadata:
    text = path.read_text(encoding="utf-8", errors="ignore")
    includes: list[Path] = []
    for protected, segment in split_protected_segments(text):
        if protected:
            continue
        masked = mask_comments(segment)
        for match in SIMPLE_INCLUDE_RE.finditer(masked):
            resolved = _resolve_include_target(path, source_root, match)
            if resolved:
                includes.append(resolved)
        for match in IMPORT_INCLUDE_RE.finditer(masked):
            resolved = _resolve_include_target(path, source_root, match)
            if resolved:
                includes.append(resolved)

    magic = MAGIC_ENGINE_RE.search(text)
    return _TexMetadata(
        path=path,
        has_documentclass=bool(DOCUMENTCLASS_RE.search(mask_comments(text))),
        has_begin_document=bool(BEGIN_DOCUMENT_RE.search(mask_comments(text))),
        includes=includes,
        magic_engine=magic.group(1).lower() if magic else None,
    )


def _collect_includeonly(text: str) -> set[str]:
    includeonly: set[str] = set()
    for protected, segment in split_protected_segments(text):
        if protected:
            continue
        masked = mask_comments(segment)
        for match in INCLUDEONLY_RE.finditer(masked):
            values = [value.strip() for value in match.group("arg").split(",") if value.strip()]
            includeonly.update(_normalize_include_token(value) for value in values)
    return includeonly


def _collect_actions(masked_segment: str) -> list[_Action]:
    actions: list[_Action] = []
    for pattern, kind in (
        (INCLUDEONLY_RE, "drop"),
        (IMPORT_INCLUDE_RE, "inline"),
        (SIMPLE_INCLUDE_RE, "inline"),
        (GRAPHICSPATH_RE, "rewrite-graphicspath"),
        (INPUTMINTED_RE, "rewrite-inputminted"),
        (PATH_COMMAND_RE, "rewrite-path"),
    ):
        for match in pattern.finditer(masked_segment):
            actions.append(_Action(start=match.start(), end=match.end(), kind=kind, match=match))

    actions.sort(key=lambda action: (action.start, -(action.end - action.start)))
    return actions


def _render_action(
    action: _Action,
    source_segment: str,
    current_file: Path,
    source_root: Path,
    includeonly: set[str],
    stack: list[Path],
) -> str:
    if action.kind == "drop":
        return ""

    if action.kind == "inline":
        match = action.match
        command = match.group("cmd")
        resolved = _resolve_include_target(current_file, source_root, match)
        source_text = source_segment[action.start : action.end]
        if not resolved:
            raise TexProcessingError(
                f"Unsupported or missing include target in {current_file}: {source_text.strip()}"
            )

        if command == "include" and includeonly:
            if _normalize_include_token(match.group("arg")) not in includeonly and _normalize_include_token(
                resolved.relative_to(source_root).with_suffix("").as_posix()
            ) not in includeonly:
                return f"\n% Skipped {source_text.strip()} due to \\includeonly\n"

        child = _flatten_file(resolved, source_root, stack=stack)
        child_rel = resolved.relative_to(source_root).as_posix()
        wrapped = f"\n% BEGIN inlined: {child_rel}\n{child.rstrip()}\n% END inlined: {child_rel}\n"
        if command == "include":
            return f"\n\\clearpage\n{wrapped}\\clearpage\n"
        return wrapped

    if action.kind == "rewrite-graphicspath":
        body = action.match.group("body")
        entries = re.findall(r"\{([^{}]+)\}", body)
        rebased = "".join(f"{{{_rebase_path(entry, current_file, source_root, ensure_trailing_slash=True)}}}" for entry in entries)
        return f"\\graphicspath{{{rebased}}}"

    if action.kind == "rewrite-inputminted":
        options = action.match.group("opt") or ""
        language = action.match.group("lang")
        arg = action.match.group("arg")
        rebased = _rebase_path(arg, current_file, source_root)
        return f"\\inputminted{options}{{{language}}}{{{rebased}}}"

    command = action.match.group("cmd")
    options = action.match.group("opt") or ""
    arg = action.match.group("arg")
    if command == "bibliography":
        rebased_arg = ",".join(
            _rebase_path(item.strip(), current_file, source_root)
            for item in arg.split(",")
            if item.strip()
        )
    else:
        rebased_arg = _rebase_path(arg, current_file, source_root)
    return f"\\{command}{options}{{{rebased_arg}}}"


def _strip_document_wrapper(text: str) -> str:
    begin_match = BEGIN_DOCUMENT_RE.search(text)
    end_match = END_DOCUMENT_RE.search(text)
    if begin_match and end_match and begin_match.end() <= end_match.start():
        body = text[begin_match.end() : end_match.start()]
        return body.strip() + "\n"

    without_documentclass = DOCUMENTCLASS_RE.sub("", text)
    without_end_document = END_DOCUMENT_RE.sub("", without_documentclass)
    without_begin_document = BEGIN_DOCUMENT_RE.sub("", without_end_document)
    return without_begin_document


def _resolve_include_target(current_file: Path, source_root: Path, match: re.Match[str]) -> Path | None:
    if match.groupdict().get("dir") is not None:
        raw_path = posixpath.join(match.group("dir").strip(), match.group("arg").strip())
    else:
        raw_path = match.group("arg").strip()

    return _resolve_tex_path(current_file, source_root, raw_path)


def _resolve_tex_path(current_file: Path, source_root: Path, raw_path: str) -> Path | None:
    if _is_dynamic_path(raw_path):
        return None

    posix_candidate = raw_path.replace("\\", "/")
    joined = PurePosixPath(posix_candidate)
    candidates = [joined]
    if not joined.suffix:
        candidates.append(PurePosixPath(f"{posix_candidate}.tex"))

    current_dir = current_file.parent.resolve()
    source_root_resolved = source_root.resolve()

    for candidate in candidates:
        path = (current_dir / Path(candidate.as_posix())).resolve()
        try:
            path.relative_to(source_root_resolved)
        except ValueError:
            continue
        if path.is_file():
            return path
    return None


def _rebase_path(
    raw_path: str,
    current_file: Path,
    source_root: Path,
    *,
    ensure_trailing_slash: bool = False,
) -> str:
    path_text = raw_path.strip()
    if not path_text:
        return path_text
    if _is_non_rebasable_resource(path_text):
        return path_text

    current_dir = current_file.parent.relative_to(source_root).as_posix()
    if current_dir == ".":
        rebased = posixpath.normpath(path_text)
    else:
        rebased = posixpath.normpath(posixpath.join(current_dir, path_text))

    if ensure_trailing_slash and not rebased.endswith("/"):
        rebased = f"{rebased}/"
    return rebased


def _normalize_include_token(value: str) -> str:
    stripped = value.strip().replace("\\", "/")
    pure = PurePosixPath(stripped)
    normalized = pure.with_suffix("").as_posix()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_dynamic_path(path_text: str) -> bool:
    return any(marker in path_text for marker in ("\\", "#", "~"))


def _is_non_rebasable_resource(path_text: str) -> bool:
    return (
        _is_dynamic_path(path_text)
        or "://" in path_text
        or path_text.startswith("/")
        or re.match(r"^[A-Za-z]:", path_text) is not None
    )


def _is_escaped(chars: Iterable[str], index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and chars[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1
