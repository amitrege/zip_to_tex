from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from zip_to_tex.errors import TexProcessingError
from zip_to_tex.tex import detect_bibliography_mode, detect_engine, detect_root_candidates, flatten_tex_tree


def _write(root: Path, relative_path: str, contents: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


class TexTests(unittest.TestCase):
    def test_detect_root_candidates_prefers_main_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write(
                tmp_path,
                "main.tex",
                r"""
\documentclass{article}
\begin{document}
\input{sections/intro}
\end{document}
""".strip(),
            )
            _write(tmp_path, "sections/intro.tex", "Intro.\n")
            _write(
                tmp_path,
                "appendix.tex",
                r"""
\documentclass{article}
\begin{document}
Appendix.
\end{document}
""".strip(),
            )

            candidates = detect_root_candidates(tmp_path)

            self.assertEqual(candidates[0].path, (tmp_path / "main.tex").resolve())

    def test_flatten_respects_includeonly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            root = _write(
                tmp_path,
                "main.tex",
                r"""
\documentclass{article}
\includeonly{chapters/one}
\begin{document}
\include{chapters/one}
\include{chapters/two}
\end{document}
""".strip(),
            )
            _write(tmp_path, "chapters/one.tex", "One.\n")
            _write(tmp_path, "chapters/two.tex", "Two.\n")

            flattened = flatten_tex_tree(root, tmp_path)

            self.assertIn("One.", flattened)
            self.assertNotIn("Two.", flattened)
            self.assertIn("Skipped \\include{chapters/two}", flattened)

    def test_flatten_detects_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            root = _write(
                tmp_path,
                "main.tex",
                r"""
\documentclass{article}
\begin{document}
\input{a}
\end{document}
""".strip(),
            )
            _write(tmp_path, "a.tex", r"\input{b}")
            _write(tmp_path, "b.tex", r"\input{a}")

            with self.assertRaises(TexProcessingError):
                flatten_tex_tree(root, tmp_path)

    def test_flatten_rebases_asset_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            root = _write(
                tmp_path,
                "main.tex",
                r"""
\documentclass{article}
\begin{document}
\input{sections/part}
\end{document}
""".strip(),
            )
            _write(
                tmp_path,
                "sections/part.tex",
                r"""
\includegraphics{figs/plot.pdf}
\bibliography{refs,more}
""".strip(),
            )

            flattened = flatten_tex_tree(root, tmp_path)

            self.assertIn(r"\includegraphics{sections/figs/plot.pdf}", flattened)
            self.assertIn(r"\bibliography{sections/refs,sections/more}", flattened)

    def test_scanner_ignores_verbatim_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            root = _write(
                tmp_path,
                "main.tex",
                r"""
\documentclass{article}
\begin{document}
% \input{ignored}
\begin{verbatim}
\input{ignored-again}
\end{verbatim}
\input{real}
\end{document}
""".strip(),
            )
            _write(tmp_path, "real.tex", "Real.\n")
            _write(tmp_path, "ignored.tex", "Ignored.\n")
            _write(tmp_path, "ignored-again.tex", "Ignored again.\n")

            flattened = flatten_tex_tree(root, tmp_path)

            self.assertIn("Real.", flattened)
            self.assertNotIn("Ignored.", flattened)
            self.assertIn(r"\input{ignored-again}", flattened)

    def test_detect_engine_and_bibliography_mode(self) -> None:
        xelatex_source = r"""
% !TEX program = xelatex
\documentclass{article}
\usepackage{fontspec}
\begin{document}
Hi
\end{document}
""".strip()
        biblatex_source = r"""
\documentclass{article}
\usepackage[backend=bibtex]{biblatex}
\addbibresource{refs.bib}
\begin{document}
\printbibliography
\end{document}
""".strip()

        self.assertEqual(detect_engine(xelatex_source), "xelatex")
        self.assertEqual(detect_bibliography_mode(biblatex_source), "bibtex")


if __name__ == "__main__":
    unittest.main()
