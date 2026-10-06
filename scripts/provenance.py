import json
from datetime import UTC, datetime
from pathlib import Path

PATH = Path("benchmarks/provenance.json")


def _load():
    if not PATH.exists():
        return {}
    text = PATH.read_text().strip()
    if not text:
        return {}  # an empty file counts as "no records yet"
    return json.loads(text)


def record_source(name, licence, licence_url, **extra):
    data = _load()
    data[name] = {
        "licence": licence,
        "licence_url": licence_url,
        "retrieved": datetime.now(tz=UTC).date().isoformat(),
        **extra,
    }
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, indent=2))