"""Outreach language fallback routing (bam/sms/base + send_text_blast).

Ports bam-automation send_mass_text.determine_message_language / build_twilio_message.
"""

from __future__ import annotations

from sqlmodel import Session

from bam.models import Household
from bam.sms.base import (
    assemble_all_message,
    resolve_send_language,
    select_template,
)
from bam.sms.console import ConsoleSMSProvider
from bam.services.outreach import send_text_blast

ES = "Español / Spanish / 西班牙语"
EN = "Inglés / English / 英文"
MANDARIN = "Chino Mandarín / Mandarin / 普通话"
CANTONESE = "Chino Cantonés / Cantonese / 广东话"
TOISHANESE = "Chino Toishanés / Toishanese / 台山话"
QUECHUA = "Quechua el dialecto / Quechua Dialect / 克丘亞語"
PORTUGUESE = "Portugués / Portuguese / 葡萄牙語"


def test_resolve_send_language_rules():
    assert resolve_send_language([ES]) == "Spanish"
    assert resolve_send_language([QUECHUA]) == "Spanish"  # Quechua → Spanish
    assert resolve_send_language([MANDARIN]) == "Cantonese"  # Mandarin → Cantonese
    assert resolve_send_language([CANTONESE]) == "Cantonese"
    assert resolve_send_language([EN]) == "English"  # sole routing signal
    assert resolve_send_language([EN, ES]) == "Spanish"  # Spanish precedence
    assert resolve_send_language([EN, MANDARIN]) == "Cantonese"
    assert resolve_send_language([EN, PORTUGUESE]) == "English"  # org quirk: PT never routes
    assert resolve_send_language([PORTUGUESE]) == "All"
    assert resolve_send_language([TOISHANESE]) == "All"  # 'Cantonese' not in 'Toishanese'
    assert resolve_send_language([]) == "All"


def test_assemble_all_message_order_and_separator():
    assert assemble_all_message({"Spanish": "ES", "Cantonese": "YUE", "English": "EN"}) == "ES\n\nYUE\n\nEN"
    assert assemble_all_message({"English": "EN", "Spanish": "ES"}) == "ES\n\nEN"  # order forced


def test_select_template_direct_and_all_fallback():
    tpls = {"Spanish": "ES", "Cantonese": "YUE", "English": "EN"}
    assert select_template(tpls, [QUECHUA]) == "ES"  # resolves Spanish, direct
    assert select_template(tpls, [TOISHANESE]) == "ES\n\nYUE\n\nEN"  # All → concat
    # resolves Cantonese but no Cantonese text supplied → All concatenation
    assert select_template({"Spanish": "ES", "English": "EN"}, [CANTONESE]) == "ES\n\nEN"


def _hh(session, name, langs, phone):
    hh = Household(name=name, phone_number=phone, languages=langs)
    session.add(hh)
    session.commit()
    session.refresh(hh)
    return hh


def test_blast_routes_each_household_to_its_language(session: Session):
    es = _hh(session, "Rosa", [ES], "+17185550001")
    yue = _hh(session, "Wei", [MANDARIN], "+17185550002")
    allh = _hh(session, "Toi", [TOISHANESE], "+17185550003")
    report = send_text_blast(
        session,
        [es.id, yue.id, allh.id],
        "",
        ConsoleSMSProvider(),
        templates={"Spanish": "Hola [FIRST_NAME]", "Cantonese": "YUE msg", "English": "EN msg"},
        sleeper=lambda s: None,
    )
    by_hh = {m.household_id: m for m in report.messages}
    assert by_hh[es.id].send_language == "Spanish" and by_hh[es.id].body == "Hola Rosa"
    assert by_hh[yue.id].send_language == "Cantonese" and by_hh[yue.id].body == "YUE msg"
    assert by_hh[allh.id].send_language == "All"
    assert by_hh[allh.id].body == "Hola Toi\n\nYUE msg\n\nEN msg"


def test_blast_scalar_template_backcompat(session: Session):
    hh = _hh(session, "Ana", [MANDARIN], "+17185550009")
    report = send_text_blast(session, [hh.id], "One template [FIRST_NAME]", ConsoleSMSProvider(), sleeper=lambda s: None)
    assert report.messages[0].body == "One template Ana"
    assert report.messages[0].send_language is None  # no routing engaged
