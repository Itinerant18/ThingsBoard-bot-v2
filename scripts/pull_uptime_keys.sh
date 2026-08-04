#!/usr/bin/env bash
# Pull mainDevicesOnTimeData / mainDevicesFaultData / mainCCTVFaultData for every
# device in a customer, tagged with each device's hierarchy path.
#
# Two calls per device is deliberate: ThingsBoard has no endpoint returning
# attributes for many devices at once, and asking for all of them returns ~239 keys
# per device. Naming the four keys keeps each response small.
#
#   ./scripts/pull_uptime_keys.sh <BEARER_TOKEN> [CUSTOMER_ID] > uptime-dump.json
#
# Output: a JSON array of {name, device_id, full_path, region, zone, branch, + the
# three keys}. Devices publishing NONE of the three are included with nulls, so the
# dump shows the coverage gap instead of hiding it. A per-key summary goes to stderr.
#
# PYTHON: defaults to `python`, override for a venv:
#   PYTHON=.venv/Scripts/python.exe ./scripts/pull_uptime_keys.sh "$TOKEN"
set -euo pipefail

TB="${TB_URL:-https://app.swatch360.seple.in}"
PY_BIN="${PYTHON:-python}"
command -v "$PY_BIN" >/dev/null 2>&1 || PY_BIN=python3
command -v "$PY_BIN" >/dev/null 2>&1 || { echo "no python on PATH; set PYTHON=..." >&2; exit 2; }

TOKEN="${1:?usage: pull_uptime_keys.sh <BEARER_TOKEN> [CUSTOMER_ID]}"
CUSTOMER="${2:-fb98a600-2778-11f1-9cdc-43ca8fc8dcc9}"
KEYS="full_path,mainDevicesOnTimeData,mainDevicesFaultData,mainCCTVFaultData"

devices_file="$(mktemp)"
script_file="$(mktemp)"
trap 'rm -f "$devices_file" "$script_file"' EXIT

curl -sk "${TB}/api/customer/${CUSTOMER}/devices?pageSize=1000&page=0" \
     -H "X-Authorization: Bearer ${TOKEN}" > "$devices_file"

# The assembly happens in python: bash cannot safely splice JSON, and these attribute
# values are themselves nested JSON documents.
cat > "$script_file" <<'PY'
import json, subprocess, sys

tb, token, keys, devices_path = sys.argv[1:5]
with open(devices_path, encoding="utf-8") as fh:
    page = json.load(fh)
rows = page.get("data", []) if isinstance(page, dict) else []


def attributes(device_id):
    url = f"{tb}/api/plugins/telemetry/DEVICE/{device_id}/values/attributes?keys={keys}"
    out = subprocess.run(
        ["curl", "-sk", url, "-H", f"X-Authorization: Bearer {token}"],
        capture_output=True, text=True,
    ).stdout
    try:
        parsed = json.loads(out)
    except ValueError:
        # Not JSON — usually "You don't have permission to perform 'READ_ATTRIBUTES'".
        return {"_error": out.strip()[:120]}
    if not isinstance(parsed, list):
        return {"_error": str(parsed)[:120]}
    return {i["key"]: i.get("value") for i in parsed if isinstance(i, dict) and "key" in i}


def maybe_json(value):
    """These attributes arrive as JSON *strings*; emit them as real objects."""
    if isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def split_path(full_path):
    if not full_path:
        return []
    for sep in ("→", "->", "/"):
        if sep in full_path:
            return [p.strip() for p in full_path.split(sep) if p.strip()]
    return [full_path.strip()]


out = []
for row in rows:
    device_id = str((row.get("id") or {}).get("id") or "")
    attrs = attributes(device_id)
    path = split_path(attrs.get("full_path"))
    out.append({
        "name": row.get("name"),
        "device_id": device_id,
        "created_time": row.get("createdTime"),
        "full_path": attrs.get("full_path"),
        # Positional, because depth differs per bank AND within one bank: the root is
        # the tenant, the leaf is the branch, whatever sits between is region/zone for
        # THIS path. Do not assume four levels everywhere.
        "region": path[1] if len(path) > 2 else None,
        "zone": path[-2] if len(path) > 3 else None,
        "branch": path[-1] if path else None,
        "mainDevicesOnTimeData": maybe_json(attrs.get("mainDevicesOnTimeData")),
        "mainDevicesFaultData": maybe_json(attrs.get("mainDevicesFaultData")),
        "mainCCTVFaultData": maybe_json(attrs.get("mainCCTVFaultData")),
        "error": attrs.get("_error"),
    })

json.dump(out, sys.stdout, indent=1, ensure_ascii=False)
sys.stdout.write("\n")

print(
    "\n# devices={} onTime={} deviceFault={} cctvFault={} unreadable={}".format(
        len(out),
        sum(1 for r in out if r["mainDevicesOnTimeData"]),
        sum(1 for r in out if r["mainDevicesFaultData"]),
        sum(1 for r in out if r["mainCCTVFaultData"]),
        sum(1 for r in out if r["error"]),
    ),
    file=sys.stderr,
)
PY

"$PY_BIN" "$script_file" "$TB" "$TOKEN" "$KEYS" "$devices_file"
