"""Open Location Code (plus code) encoder — port of bam-automation's
``core/bam_core/lib/olc.py``.

A pure-Python, network-free encoder deliberately truncated to
``BWK_CODE_LENGTH_ = 8`` code characters (vs Google's default 10) so the
stored plus code OBSCURES the exact location for privacy. ``encode`` returns
a 9-character string (8 code chars + the ``+`` separator), e.g.
``encode(40.7128, -74.0060) == "87G7PX7V+"``, or ``None`` if either
coordinate is falsy.
"""

from __future__ import annotations

import math

SEPARATOR_ = "+"
SEPARATOR_POSITION_ = 8
CODE_ALPHABET_ = "23456789CFGHJMPQRVWX"
ENCODING_BASE_ = len(CODE_ALPHABET_)  # 20
LATITUDE_MAX_ = 90
LONGITUDE_MAX_ = 180
MAX_DIGIT_COUNT_ = 15
PAIR_CODE_LENGTH_ = 10
PAIR_PRECISION_ = ENCODING_BASE_**3
GRID_CODE_LENGTH_ = MAX_DIGIT_COUNT_ - PAIR_CODE_LENGTH_  # 5
GRID_COLUMNS_ = 4
GRID_ROWS_ = 5
FINAL_LAT_PRECISION_ = PAIR_PRECISION_ * GRID_ROWS_ ** (MAX_DIGIT_COUNT_ - PAIR_CODE_LENGTH_)
FINAL_LNG_PRECISION_ = PAIR_PRECISION_ * GRID_COLUMNS_ ** (MAX_DIGIT_COUNT_ - PAIR_CODE_LENGTH_)
BWK_CODE_LENGTH_ = 8


def clip_latitude(latitude: float) -> float:
    return min(90, max(-90, latitude))


def normalize_longitude(longitude: float) -> float:
    while longitude < -180:
        longitude += 360
    while longitude >= 180:
        longitude -= 360
    return longitude


def _compute_latitude_precision(code_length: int) -> float:
    if code_length <= 10:
        return pow(ENCODING_BASE_, math.floor(code_length / -2 + 2))
    return pow(ENCODING_BASE_, -3) / pow(GRID_ROWS_, code_length - 10)


def encode(latitude: float | None, longitude: float | None) -> str | None:
    """Encode a lat/lng to the privacy-truncated 8-char plus code (+ ``+``)."""
    if not latitude or not longitude:
        return None
    latitude = clip_latitude(latitude)
    longitude = normalize_longitude(longitude)
    if latitude == 90:
        latitude = latitude - _compute_latitude_precision(MAX_DIGIT_COUNT_)

    code = ""
    lat_val = int(round((latitude + LATITUDE_MAX_) * FINAL_LAT_PRECISION_, 6))
    lng_val = int(round((longitude + LONGITUDE_MAX_) * FINAL_LNG_PRECISION_, 6))

    # Grid refinement digits (least significant).
    for _ in range(GRID_CODE_LENGTH_):
        ndx = (lat_val % GRID_ROWS_) * GRID_COLUMNS_ + (lng_val % GRID_COLUMNS_)
        code = CODE_ALPHABET_[ndx] + code
        lat_val //= GRID_ROWS_
        lng_val //= GRID_COLUMNS_

    # Pair-encoded digits (most significant).
    for _ in range(PAIR_CODE_LENGTH_ // 2):
        code = CODE_ALPHABET_[lng_val % ENCODING_BASE_] + code
        code = CODE_ALPHABET_[lat_val % ENCODING_BASE_] + code
        lat_val //= ENCODING_BASE_
        lng_val //= ENCODING_BASE_

    code = code[:SEPARATOR_POSITION_] + SEPARATOR_ + code[SEPARATOR_POSITION_:]
    # Truncate to the reduced BWK length: 8 code chars + the separator.
    return code[: BWK_CODE_LENGTH_ + 1]
