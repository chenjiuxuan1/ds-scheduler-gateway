"""Read-only country Git SQL search used by ownership matching."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def search_country_git_sql(country: str, payload: dict):
    root = Path(f"/data/git/starrocks/workflow/{country}").resolve()
    sql = str(payload.get("sql_query") or payload.get("sql") or "").strip()
    if not sql or not root.is_dir():
        return False, {"message": "sql_query and country Git root are required"}
    targets = re.findall(r"\b(?:table|into|update)\s+([\w.]+)", sql, re.I)
    names = {value.split(".")[-1].lower() for value in targets}
    files = []
    for name in sorted(names, key=len, reverse=True)[:4]:
        result = subprocess.run(["rg", "--files", str(root)], capture_output=True, text=True, timeout=8, check=False)
        files.extend(path for path in result.stdout.splitlines() if name in Path(path).stem.lower() or name in path.lower())
    unique = list(dict.fromkeys(files))[:20]
    return True, {"items": [{"path": path, "full_path": path} for path in unique], "target_terms": sorted(names), "git_root": str(root)}
