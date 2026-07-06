"""API route modules plus shared error-mapping helper."""

from __future__ import annotations

from fastapi import HTTPException

from bam.errors import NotFoundError


def value_error_to_http(exc: ValueError) -> HTTPException:
    """Map service errors by type: ``NotFoundError`` -> 404, others -> 400.

    Classification is by exception class, never by message text — messages
    embed user-supplied values, so substring matching would let a payload
    like ``outcome="number not found"`` masquerade as a missing household.
    """
    status_code = 404 if isinstance(exc, NotFoundError) else 400
    return HTTPException(status_code=status_code, detail=str(exc))
