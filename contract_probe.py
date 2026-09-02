from __future__ import annotations
import json, pathlib

spec = json.loads(pathlib.Path("contract/openapi.json").read_text(encoding="utf-8-sig"))
for path, ops in spec.get("paths", {}).items():
    for method, op in ops.items():
        if method not in ("get", "post", "put", "delete", "patch"):
            continue
        body = op.get("requestBody")
        ref = ""
        if body:
            try:
                ref = list(body["content"].values())[0]["schema"].get("$ref", "inline")
            except Exception:
                ref = "?"
        params = ",".join(p.get("name", "?") for p in op.get("parameters", []))
        print("%-6s %-16s body=%-28s params=%s" % (method.upper(), path, ref or "-", params or "-"))

print()
for name, sch in sorted(spec.get("components", {}).get("schemas", {}).items()):
    props = list((sch.get("properties") or {}).keys())
    req = sch.get("required") or []
    print("%s: %d field(s), %d required" % (name, len(props), len(req)))
    if props:
        print("   " + ", ".join(props[:24]) + (" ..." if len(props) > 24 else ""))
        print("   required: " + (", ".join(req) if req else "(none)"))
