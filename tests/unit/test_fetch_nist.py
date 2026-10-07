"""Offline tests: fetching is faked, page counting and failure reporting are real."""
import fetch_nist
import pytest
from pypdf import PdfWriter


def make_pdf(path, pages):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=72, height=72)
    with path.open("wb") as fh:
        w.write(fh)


def test_counts_pages_itself(tmp_path):
    def fake_fetch(url, dest):
        make_pdf(dest, {"a": 3, "b": 5}[url])

    results, failures = fetch_nist.run({"doc-a": "a", "doc-b": "b"}, tmp_path, fetch=fake_fetch)
    assert failures == []
    assert sum(r["pages"] for r in results) == 8
    assert len(results) == 2


def test_failed_url_is_reported_not_counted(tmp_path):
    def fake_fetch(url, dest):
        if url == "bad":
            raise OSError("HTTP Error 404")
        make_pdf(dest, 2)

    results, failures = fetch_nist.run({"ok": "good", "gone": "bad"}, tmp_path, fetch=fake_fetch)
    assert [r["name"] for r in results] == ["ok"]
    assert failures[0]["name"] == "gone" and "404" in failures[0]["error"]


def test_html_error_page_is_rejected(tmp_path):
    dest = tmp_path / "x.pdf"
    dest.write_bytes(b"<html>blocked</html>")
    assert not fetch_nist.is_pdf(dest)


def test_existing_pdf_is_not_downloaded_again(tmp_path):
    make_pdf(tmp_path / "doc.pdf", 4)

    def explode(url, dest):
        pytest.fail("should not download")

    results, failures = fetch_nist.run({"doc": "u"}, tmp_path, fetch=explode)
    assert failures == [] and results[0]["pages"] == 4