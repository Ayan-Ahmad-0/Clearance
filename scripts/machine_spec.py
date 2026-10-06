import json
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import psutil

today = datetime.now(tz=UTC).date().isoformat()
out_dir = Path("benchmarks/results") / today
out_dir.mkdir(parents=True, exist_ok=True)

git_sha = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    capture_output=True,
    text=True,
    check=False,
).stdout.strip()

spec = {
    "platform": platform.platform(),
    "cpu": platform.processor() or platform.machine(),
    "cpu_cores_physical": psutil.cpu_count(logical=False),
    "cpu_cores_logical": psutil.cpu_count(logical=True),
    "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
    "disk_free_gb": round(shutil.disk_usage(".").free / 2**30, 1),
    "python": sys.version.split()[0],
    "git_sha": git_sha,
}
(out_dir / "machine.json").write_text(json.dumps(spec, indent=2))
print(json.dumps(spec, indent=2))