"""Custom exceptions for zip_to_tex."""


class ZipToTexError(RuntimeError):
    """Base error raised for processing failures."""


class SafeExtractionError(ZipToTexError):
    """Raised when a zip archive contains unsafe entries."""


class TexProcessingError(ZipToTexError):
    """Raised when TeX flattening fails."""


class CompileError(ZipToTexError):
    """Raised when LaTeX compilation fails."""
