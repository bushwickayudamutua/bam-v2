"""Dialpad SMS provider unit tests (no network — HTTP is injected)."""

from __future__ import annotations

import pytest

from bam.config import Settings
from bam.sms.base import get_provider
from bam.sms.dialpad_provider import (
    DialpadSMSProvider,
    clean_phone_number,
    split_message,
)


class FakeResponse:
    def __init__(self, ok: bool = True, data: dict | None = None) -> None:
        self.is_success = ok
        self._data = data or {"id": "msg-1"}

    def json(self) -> dict:
        return self._data


class Recorder:
    """A fake http_post that records calls and returns queued responses."""

    def __init__(self, responses: list[FakeResponse] | None = None, raises: bool = False) -> None:
        self.calls: list[dict] = []
        self._responses = responses or []
        self._raises = raises

    def __call__(self, url: str, *, json: dict, headers: dict) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self._raises:
            raise RuntimeError("boom")
        return self._responses.pop(0) if self._responses else FakeResponse()


def _provider(rec: Recorder, interval: int = 0) -> DialpadSMSProvider:
    sleeps: list[float] = []
    p = DialpadSMSProvider(
        api_token="tok",
        user_id="user-9",
        send_interval_seconds=interval,
        sleeper=sleeps.append,
        http_post=rec,
    )
    p._sleeps = sleeps  # type: ignore[attr-defined]
    return p


def test_clean_phone_number():
    assert clean_phone_number("(718) 555-1234") == "+17185551234"  # 10 digits
    assert clean_phone_number("17185551234") == "+17185551234"  # 11 digits
    assert clean_phone_number("447911123456") == "447911123456"  # 12 → raw, no +
    assert clean_phone_number("") == ""
    assert clean_phone_number(None) == ""


def test_split_message_short_is_one_chunk():
    assert split_message("hola") == ["hola"]


def test_split_message_long_splits_on_word_boundaries():
    body = " ".join(["word"] * 60)  # 60*5-1 = 299 chars
    chunks = split_message(body, 160)
    assert len(chunks) > 1
    assert all(len(c) <= 160 for c in chunks)
    # re-joining reproduces the words
    assert " ".join(chunks).split(" ") == body.split(" ")


def test_split_message_single_oversize_word_not_hard_split():
    word = "x" * 200
    assert split_message(word, 160) == [word]  # one oversize chunk, faithful to source


def test_send_success_posts_payload_and_pauses_once():
    rec = Recorder([FakeResponse(ok=True, data={"id": "abc"})])
    p = _provider(rec, interval=2)
    result = p.send("(718) 555-0142", "hello")
    assert result.ok and result.provider_id == "abc"
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["url"] == "https://dialpad.com/api/v2/sms"
    assert call["json"] == {
        "infer_country_code": False,
        "to_numbers": "+17185550142",
        "text": "hello",
        "user_id": "user-9",
    }
    assert call["headers"]["authorization"] == "Bearer tok"
    assert call["headers"]["accept"] == "application/json"
    assert p._sleeps == [2]  # one pause per recipient


def test_send_http_error_stops_remaining_chunks():
    body = " ".join(["word"] * 60)  # multi-chunk
    rec = Recorder([FakeResponse(ok=False, data={"error": {"message": "bad number"}})])
    p = _provider(rec)
    result = p.send("+17185550142", body)
    assert not result.ok and result.error == "bad number"
    assert len(rec.calls) == 1  # stopped after the first failing chunk


def test_send_exception_becomes_error_result():
    rec = Recorder(raises=True)
    p = _provider(rec)
    result = p.send("+17185550142", "hi")
    assert not result.ok and "boom" in result.error


def test_construction_requires_credentials():
    with pytest.raises(RuntimeError, match="BAM_DIALPAD"):
        DialpadSMSProvider(api_token="", user_id="", http_post=lambda *a, **k: FakeResponse())


def test_factory_selects_dialpad_and_console_default():
    dialpad = get_provider(
        Settings(sms_provider="dialpad", dialpad_api_token="t", dialpad_user_id="u")
    )
    assert isinstance(dialpad, DialpadSMSProvider)
    from bam.sms.console import ConsoleSMSProvider

    assert isinstance(get_provider(Settings(sms_provider="console")), ConsoleSMSProvider)
