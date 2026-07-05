"""API route modules plus shared error-mapping helper."""

from __future__ import annotations

from fastapi import HTTPException

#: Substrings that mark a service ValueError as an unknown-id error (-> 404).
_NOT_FOUND_MARKERS = ("not found", "Unknown household id", "Unknown ids")


def value_error_to_http(exc: ValueError) -> HTTPException:
    """Map service ValueErrors: unknown-id errors -> 404, others -> 400."""
    detail = str(exc)
    status_code = 404 if any(marker in detail for marker in _NOT_FOUND_MARKERS) else 400
    return HTTPException(status_code=status_code, detail=detail)
