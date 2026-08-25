from __future__ import annotations

from pathlib import Path

from scripts.publication.audit_public_repo import audit


def test_publication_audit_passes_clean_placeholder(tmp_path):
    path = tmp_path / "README.md"
    path.write_text("placeholder <query> and <candidate_id>\n", encoding="utf-8")
    assert audit(tmp_path, files=[path])["publication_audit_status"] == "PASS"


def test_publication_audit_catches_artifact_secret_path_and_tunnel(tmp_path):
    artifact = tmp_path / "result.jsonl"
    artifact.write_text("{}\n", encoding="utf-8")
    text = tmp_path / "bad.md"
    domain = "trycloudflare" + ".com"
    authorization = "Author" + "ization: " + "Bear" + "er " + "abcdefghijklmnopqrstuvwxyz"
    private_path = "C:" + "\\Users\\private\\x"
    text.write_text(authorization + "\n" + private_path + "\nhttps://active." + domain + "/v1\n", encoding="utf-8")
    result = audit(tmp_path, files=[artifact, text])
    assert result["publication_audit_status"] == "FAIL"
    assert result["counts"]["forbidden_files"] == 1
    assert result["counts"]["secret_findings"] == 1
    assert result["counts"]["absolute_private_paths"] == 1
    assert result["counts"]["live_tunnel_urls"] == 1
