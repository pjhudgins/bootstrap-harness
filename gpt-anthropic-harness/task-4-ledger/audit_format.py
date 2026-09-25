"""Read-only format review; prints metadata, never message bodies or lease contents.

Bar: identify format defects in surviving records without changing them.
Shared-scribe findings plus independent byte/header/shape/hash checks. This is
not a complete validator for the standard's deferred link/export semantics.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from ledger import NIMOI, scribe


def reject_constant(value):
    raise ValueError("Non-finite JSON constant")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def audit():
    standard = NIMOI / "bootstrap-ledger/standard/wiki_ledger_v0.4.md"
    readme = next(line[2:] for line in standard.read_text(encoding="utf-8").splitlines()
                  if line.startswith("> One JSON object per line."))
    root = Path(__file__).resolve().parent / "ledgers"
    required = {
        "ledger_version": {"ledger_version", "ts", "author", "name", "scribe", "prior", "readme"},
        "body": {"body", "ts", "author", "name"},
        "tag": {"tag", "ts", "author", "name"},
        "untag": {"untag", "ts", "author", "name"},
        "closed": {"closed", "ts", "author"},
    }
    optional = {"body": {"prev"}, "closed": {"hash"}}
    output = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        view = scribe.load(root, directory.name)
        session_reports = []
        for path in sorted(directory.glob("*.ledger")):
            raw = path.read_bytes()
            lines = raw.split(b"\n")
            tail = lines.pop()
            problems, shapes, kinds, body_types = [], Counter(), Counter(), Counter()
            links, nested_link_markers, revisions = [], 0, []
            header = None
            latest = {}
            byte_offset = 0
            hash_ok = None
            sizes = []
            for number, line in enumerate(lines, 1):
                obj = json.loads(line.decode("utf-8"), parse_constant=reject_constant,
                                 object_pairs_hook=unique_keys)
                discriminants = set(obj) & set(required)
                if len(discriminants) != 1:
                    problems.append(f"line {number}: shape")
                    continue
                shape = next(iter(discriminants))
                shapes[shape] += 1
                if not required[shape] <= set(obj) or set(obj) - required[shape] - optional.get(shape, set()):
                    problems.append(f"line {number}: keys")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", obj["ts"]):
                    problems.append(f"line {number}: timestamp")
                if shape == "ledger_version":
                    header = obj
                    expected_stamp = obj["ts"][:19].replace("-", "").replace(":", "") + "Z"
                    if number != 1 or obj["ledger_version"] != "wiki_ledger_v0.4" or obj["name"] != directory.name or obj["readme"] != readme or path.stem != expected_stamp:
                        problems.append(f"line {number}: header")
                elif shape == "body":
                    name = obj["name"]
                    if obj.get("prev") != latest.get(name):
                        problems.append(f"line {number}: prev")
                    if "prev" in obj:
                        revisions.append({"line": number, "prev": obj["prev"]})
                    latest[name] = f"{path.stem}:{number}"
                    body_types[type(obj["body"]).__name__] += 1
                    sizes.append((len(line), number))
                    if isinstance(obj["body"], dict):
                        kinds[obj["body"].get("kind", "other")] += 1
                        nested_link_markers += json.dumps(obj["body"], ensure_ascii=False).count("[[")
                    elif isinstance(obj["body"], str) and "[[" in obj["body"]:
                        links.append(number)
                elif shape == "closed":
                    hash_ok = obj.get("hash") == "sha256:" + hashlib.sha256(raw[:byte_offset]).hexdigest()
                    if obj["closed"] != number or number != len(lines) or not hash_ok:
                        problems.append(f"line {number}: trailer")
                byte_offset += len(line) + 1
            if b"\r" in raw or raw.startswith(b"\xef\xbb\xbf"):
                problems.append("CR or BOM")
            session_reports.append({"file": path.name, "bytes": len(raw),
                "snapshot_sha256": hashlib.sha256(raw).hexdigest(), "lines": len(lines),
                "tail_bytes": len(tail), "shapes": dict(shapes), "body_types": dict(body_types),
                "header_prior": header["prior"], "scribe": header["scribe"],
                "independent_problems": problems, "trailer_hash_ok": hash_ok,
                "revisions": revisions, "string_bodies_with_link_markers": links,
                "nested_link_markers": nested_link_markers,
                "largest_body_lines": sorted(sizes, reverse=True)[:3], "event_kinds": dict(kinds)})
        output.append({"ledger": directory.name, "position": view.position,
            "well_formed": view.well_formed, "closed": view.head_closed,
            "lease_present": view.is_leased(), "scribe_findings": [str(f) for f in view.findings],
            "sessions": session_reports})
    return {"checked_at": datetime.now(timezone.utc).isoformat(),
            "standard": str(standard), "ledgers": output}


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
