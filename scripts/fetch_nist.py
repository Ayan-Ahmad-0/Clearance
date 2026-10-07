"""Download the NIST SP 800 PDFs used as the real-text part of the corpus.

Run:  python scripts/fetch_nist.py --out data/nist

The document and page counts are measured here, from the files themselves, and
written into benchmarks/provenance.json. Nothing is copied from a web page or
from the brief. Any URL that fails is reported by name; without --allow-partial
a failure stops the run before provenance is touched, so the recorded counts
always describe a complete download.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

from pypdf import PdfReader
from pypdf.errors import PyPdfError

LICENCE = (
    "Not subject to US copyright (17 U.S.C. 105, US government work); "
    "may be subject to foreign copyright"
)
LICENCE_URL = (
    "https://www.nist.gov/open/copyright-fair-use-and-licensing-statements-"
    "srd-data-software-and-technical-series-publications"
)
USER_AGENT = "clearance-benchmark/0.1 (research; fetch_nist.py)"

# Starter list. Replace it with the documents named in the brief, then compare
# the counts this script reports against the brief's figures.
DOCS = {
    "sp800-53r5": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53r5.pdf",
    "sp800-37r2": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-37r2.pdf",
    "sp800-171r2": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-171r2.pdf",
    "sp800-63-3": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-63-3.pdf",
    "sp800-61r2": "https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-61r2.pdf",
    "sp800-30r1": "https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-30r1.pdf",
    "sp800-137": "https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-137.pdf",
}


def is_pdf(path):
    with path.open("rb") as fh:
        return fh.read(5) == b"%PDF-"


def count_pages(path):
    pages = len(PdfReader(str(path)).pages)
    if pages == 0:
        raise ValueError("PDF has zero pages")
    return pages


def download(url, dest, retries=3, timeout=60):
    """Fetch `url` to `dest`. Raises OSError or ValueError on failure."""
    last = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            break
        except OSError as exc:  # URLError and HTTPError are OSError subclasses
            last = exc
            if attempt < retries - 1:
                time.sleep(2**attempt)
    else:
        raise OSError(f"{last}") from last
    if not data.startswith(b"%PDF-"):
        raise ValueError("response is not a PDF (an error page, perhaps)")
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(dest)


def run(docs, out, fetch=download):
    """Fetch and measure every document. Returns (results, failures)."""
    out.mkdir(parents=True, exist_ok=True)
    results, failures = [], []
    for name, url in docs.items():
        dest = out / f"{name}.pdf"
        try:
            if not (dest.exists() and is_pdf(dest)):
                fetch(url, dest)
            pages = count_pages(dest)
        except (OSError, ValueError, PyPdfError) as exc:
            failures.append({"name": name, "url": url, "error": str(exc)})
            continue
        results.append(
            {
                "name": name,
                "url": url,
                "bytes": dest.stat().st_size,
                "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                "pages": pages,
            }
        )
    return results, failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/nist")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    out = Path(args.out)

    results, failures = run(DOCS, out)

    for r in results:
        print(f"ok    {r['name']:<14} {r['pages']:>5} pages  {r['bytes']:>10,} bytes")
    for f in failures:
        print(f"FAIL  {f['name']:<14} {f['url']}\n      {f['error']}", file=sys.stderr)

    if failures and not args.allow_partial:
        print(
            f"\n{len(failures)} of {len(DOCS)} downloads failed; provenance not updated.",
            file=sys.stderr,
        )
        return 1

    (out / "manifest.json").write_text(json.dumps(results, indent=1))

    from provenance import record_source

    record_source(
        "nist_sp800",
        LICENCE,
        LICENCE_URL,
        documents=len(results),
        pages=sum(r["pages"] for r in results),
        failed_urls=[f["url"] for f in failures],
    )
    print(f"\n{len(results)} documents, {sum(r['pages'] for r in results)} pages recorded.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())