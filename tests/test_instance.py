"""White-label instance config (bam/instance.py, GET /config, catalog reconfig)."""

from __future__ import annotations

import pytest

import bam.instance as inst
import bam.request_types as rt
from bam.instance import InstanceConfig, apply_to_runtime, load_instance_config


@pytest.fixture(autouse=True)
def _restore_globals():
    """Instance config + catalog are process-global; restore BAM defaults so a
    test that reconfigures them never leaks into other tests."""
    yield
    rt.load_catalog()
    inst.instance = InstanceConfig()


def _write(tmp_path, text: str, name: str = "instance.toml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_defaults_are_bam_identity():
    cfg = InstanceConfig()
    assert cfg.org.name == "Bushwick Ayuda Mutua"
    assert cfg.branding.primary_color == "#ff6e2e"
    assert cfg.branding.logo == "hands"
    assert all(cfg.features.model_dump().values())  # everything on


def test_load_custom_toml(tmp_path):
    path = _write(
        tmp_path,
        """
[org]
name = "Anytown Mutual Aid"
short_name = "AMA"
timezone = "America/Chicago"

[branding]
primary_color = "#2b7a4b"
logo = "initials"

[features]
furniture = false
distros = false
""",
    )
    cfg = load_instance_config(path)
    assert cfg.org.name == "Anytown Mutual Aid"
    assert cfg.org.timezone == "America/Chicago"
    assert cfg.branding.primary_color == "#2b7a4b"
    assert cfg.features.furniture is False and cfg.features.distros is False
    assert cfg.features.checkin is True  # unspecified → default on


def test_load_json_config(tmp_path):
    path = _write(tmp_path, '{"org": {"name": "JSON Aid"}}', name="instance.json")
    assert load_instance_config(path).org.name == "JSON Aid"


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_instance_config(str(tmp_path / "nope.toml"))


def test_apply_catalog_override_reconfigures_backend(tmp_path):
    path = _write(
        tmp_path,
        """
[catalog]
languages = ["English", "Soomaali / Somali"]

[[catalog.goods]]
key = "blankets"
label = "Cobijas / Blankets"
category = "household"

[[catalog.social_services]]
key = "legal_aid"
label = "Legal Aid"
category = "social_service"
""",
    )
    cfg = load_instance_config(path)
    apply_to_runtime(cfg)
    assert rt.LANGUAGES == ["English", "Soomaali / Somali"]
    assert [t.key for t in rt.GOODS] == ["blankets"]
    assert rt.normalize_type("Blankets") == "blankets"  # custom type resolves
    assert rt.normalize_type("Sofa / Sofa / 沙發") is None  # default type gone
    assert rt.SPEC_COMPAT == []  # BAM-only extras dropped on override


def test_partial_catalog_keeps_defaults(tmp_path):
    # Only languages overridden → goods keep the BAM defaults.
    path = _write(tmp_path, '[catalog]\nlanguages = ["English"]\n')
    apply_to_runtime(load_instance_config(path))
    assert rt.LANGUAGES == ["English"]
    assert rt.normalize_type("Sofa / Sofa / 沙發") == "sofa"  # goods unchanged


def test_config_endpoint_serves_instance(tmp_path, client):
    # Point the live singleton at a custom config, rebuild the app.
    inst.instance = load_instance_config(
        _write(
            tmp_path,
            '[org]\nname = "Anytown Mutual Aid"\n[branding]\nprimary_color = "#2b7a4b"\n'
            "[features]\nfurniture = false\n",
        )
    )
    from bam.api.main import create_app
    from fastapi.testclient import TestClient

    resp = TestClient(create_app()).get("/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["org"]["name"] == "Anytown Mutual Aid"
    assert body["branding"]["primary_color"] == "#2b7a4b"
    assert body["features"]["furniture"] is False
    assert body["features"]["checkin"] is True
    assert len(body["catalog"]["languages"]) == 12  # no catalog override → defaults


def test_config_endpoint_defaults(client):
    body = client.get("/config").json()
    assert body["org"]["name"] == "Bushwick Ayuda Mutua"
    assert body["branding"]["logo"] == "hands"
