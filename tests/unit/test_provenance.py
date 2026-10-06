import json

import pytest

from scripts import provenance


def test_record_source_initializes_empty_file(tmp_path, monkeypatch):
    path = tmp_path / "provenance.json"
    path.write_text("")
    monkeypatch.setattr(provenance, "PATH", path)

    provenance.record_source("nist_sp_800", "Public domain", "https://example.com")

    data = json.loads(path.read_text())
    assert data["nist_sp_800"]["licence"] == "Public domain"
    assert data["nist_sp_800"]["licence_url"] == "https://example.com"
    assert data["nist_sp_800"]["retrieved"]


def test_record_source_rejects_malformed_json(tmp_path, monkeypatch):
    path = tmp_path / "provenance.json"
    path.write_text("{")
    monkeypatch.setattr(provenance, "PATH", path)

    with pytest.raises(json.JSONDecodeError):
        provenance.record_source("nist_sp_800", "Public domain", "https://example.com")
