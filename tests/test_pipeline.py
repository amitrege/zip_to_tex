from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from io import BytesIO

from zip_to_tex.errors import SafeExtractionError, ZipToTexError
from zip_to_tex.pipeline import process_archive, process_zip, safe_extract_archive, safe_extract_zip


def _make_zip(root: Path, name: str, files: dict[str, str | bytes]) -> Path:
    archive_path = root / f"{name}.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for relative_path, contents in files.items():
            data = contents.encode("utf-8") if isinstance(contents, str) else contents
            archive.writestr(relative_path, data)
    return archive_path


def _make_tar_gz(root: Path, name: str, files: dict[str, str | bytes]) -> Path:
    archive_path = root / f"{name}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for relative_path, contents in files.items():
            data = contents.encode("utf-8") if isinstance(contents, str) else contents
            info = tarfile.TarInfo(relative_path)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
    return archive_path


def _tiny_pdf_bytes() -> bytes:
    stream = b"0.5 w\n0 0 m\n20 20 l\nS\n"
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n"
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 20 20] /Contents 4 0 R >>\n"
            b"endobj\n"
        ),
        (
            b"4 0 obj\n"
            + f"<< /Length {len(stream)} >>\n".encode("ascii")
            + b"stream\n"
            + stream
            + b"endstream\n"
            b"endobj\n"
        ),
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(payload))
        payload.extend(obj)
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


def _has_tex_file(name: str) -> bool:
    return subprocess.run(
        ["kpsewhich", name],
        capture_output=True,
        text=True,
        check=False,
    ).returncode == 0


class PipelineTests(unittest.TestCase):
    def test_safe_extract_archive_rejects_tar_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            archive = _make_tar_gz(
                tmp_path,
                "unsafe_tar",
                {
                    "../outside.tex": "bad",
                    "main.tex": r"\documentclass{article}\begin{document}x\end{document}",
                },
            )

            with self.assertRaises(SafeExtractionError):
                safe_extract_archive(archive, tmp_path / "extract")

    def test_safe_extract_zip_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            archive = _make_zip(
                tmp_path,
                "unsafe",
                {
                    "../outside.tex": "bad",
                    "main.tex": r"\documentclass{article}\begin{document}x\end{document}",
                },
            )

            with self.assertRaises(SafeExtractionError):
                safe_extract_zip(archive, tmp_path / "extract")

    def test_process_zip_basic_paper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            archive = _make_zip(
                tmp_path,
                "paper",
                {
                    "paper/main.tex": r"""
\documentclass{article}
\usepackage{graphicx}
\begin{document}
\input{sections/intro}
\end{document}
""".strip(),
                    "paper/sections/intro.tex": r"""
Intro.
\includegraphics[width=1cm]{../figs/pixel.pdf}
""".strip(),
                    "paper/figs/pixel.pdf": _tiny_pdf_bytes(),
                },
            )

            result = process_zip(archive)

            self.assertFalse(archive.exists())
            self.assertTrue(result.output_dir.exists())
            self.assertTrue(result.tex_file.exists())
            self.assertTrue(result.pdf_file.exists())
            self.assertTrue((result.output_dir / "figs" / "pixel.pdf").exists())
            flattened = result.tex_file.read_text(encoding="utf-8")
            self.assertIn(r"\includegraphics[width=1cm]{figs/pixel.pdf}", flattened)

    def test_process_tar_gz_basic_paper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            archive = _make_tar_gz(
                tmp_path,
                "paper",
                {
                    "paper/main.tex": r"""
\documentclass{article}
\usepackage{graphicx}
\begin{document}
\input{sections/intro}
\end{document}
""".strip(),
                    "paper/sections/intro.tex": r"""
Intro.
\includegraphics[width=1cm]{../figs/pixel.pdf}
""".strip(),
                    "paper/figs/pixel.pdf": _tiny_pdf_bytes(),
                },
            )

            result = process_archive(archive)

            self.assertFalse(archive.exists())
            self.assertEqual(result.output_dir.name, "paper_flat")
            self.assertEqual(result.tex_file.name, "paper_flat.tex")
            self.assertEqual(result.pdf_file.name, "paper_flat.pdf")
            self.assertTrue(result.pdf_file.exists())
            self.assertTrue((result.output_dir / "figs" / "pixel.pdf").exists())

    def test_process_archive_without_compile_still_emits_tex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            archive = _make_tar_gz(
                tmp_path,
                "paper_no_compile",
                {
                    "main.tex": r"""
\documentclass{article}
\usepackage[ruled,vlined]{algorithm2e}
\begin{document}
No compile required.
\end{document}
""".strip(),
                },
            )

            result = process_archive(archive, compile_pdf=False)

            self.assertFalse(archive.exists())
            self.assertTrue(result.tex_file.exists())
            self.assertIsNone(result.pdf_file)
            self.assertIn("algorithm2e", result.tex_file.read_text(encoding="utf-8"))

    @unittest.skipIf(shutil.which("xelatex") is None, "xelatex not installed")
    def test_process_zip_xelatex_paper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            archive = _make_zip(
                tmp_path,
                "xelatex_paper",
                {
                    "main.tex": r"""
% !TEX program = xelatex
\documentclass{article}
\begin{document}
XeLaTeX works.
\end{document}
""".strip(),
                },
            )

            result = process_zip(archive)

            self.assertEqual(result.engine, "xelatex")
            self.assertTrue(result.pdf_file.exists())

    @unittest.skipIf(shutil.which("bibtex") is None, "bibtex not installed")
    def test_process_zip_bibtex_paper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            archive = _make_zip(
                tmp_path,
                "bibtex_paper",
                {
                    "main.tex": r"""
\documentclass{article}
\begin{document}
See~\cite{knuth1984}.
\bibliographystyle{plain}
\bibliography{refs}
\end{document}
""".strip(),
                    "refs.bib": r"""
@book{knuth1984,
  author = {Donald E. Knuth},
  title = {The TeXbook},
  year = {1984},
  publisher = {Addison-Wesley}
}
""".strip(),
                },
            )

            result = process_zip(archive)

            self.assertTrue(result.pdf_file.exists())
            self.assertTrue((result.output_dir / "refs.bib").exists())

    @unittest.skipIf(
        shutil.which("bibtex") is None or not _has_tex_file("biblatex.sty"),
        "bibtex or biblatex not installed",
    )
    def test_process_zip_biblatex_backend_bibtex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            archive = _make_zip(
                tmp_path,
                "biblatex_paper",
                {
                    "main.tex": r"""
\documentclass{article}
\usepackage[backend=bibtex,style=numeric]{biblatex}
\addbibresource{refs.bib}
\begin{document}
See~\cite{knuth1984}.
\printbibliography
\end{document}
""".strip(),
                    "refs.bib": r"""
@book{knuth1984,
  author = {Donald E. Knuth},
  title = {The TeXbook},
  year = {1984},
  publisher = {Addison-Wesley}
}
""".strip(),
                },
            )

            result = process_zip(archive)

            self.assertTrue(result.pdf_file.exists())

    def test_process_zip_keeps_archive_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            archive = _make_zip(
                tmp_path,
                "broken",
                {
                    "main.tex": r"""
\documentclass{article}
\begin{document}
\input{missing}
\end{document}
""".strip(),
                },
            )

            with self.assertRaises(ZipToTexError):
                process_zip(archive)

            self.assertTrue(archive.exists())


if __name__ == "__main__":
    unittest.main()
