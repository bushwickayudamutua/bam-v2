"""Service-layer exceptions.

``NotFoundError`` marks unknown-id lookups so the API layer can map them to
HTTP 404 without inspecting user-controlled message text (everything else a
service raises as plain ``ValueError`` maps to 400).
"""


class NotFoundError(ValueError):
    """An id passed to a service did not resolve to a row."""
