#!/usr/bin/env python3
"""Pull mainDevicesOnTimeData / mainDevicesFaultData / mainCCTVFaultData for every
device in a ThingsBoard customer, tagged with each device's hierarchy path.

Standard library only — no pip install. Python 3.9+.

    # Log in with your ThingsBoard account (no token juggling):
    export TB_USERNAME=you@bank.example
    export TB_PASSWORD=...
    python pull_uptime_keys.py > uptime-dump.json
    python pull_uptime_keys.py --csv uptime.csv --only-with-data

    # Or reuse a token you already have:
    python pull_uptime_keys.py --token "<BEARER>" > uptime-dump.json

Credentials come from --username/--password, the TB_USERNAME/TB_PASSWORD environment
variables, or the CONFIG block below, in that order. The script logs in itself and
re-logs in on a 401, so a pull that outlives the ~2.5h token does not silently turn
every remaining device into "no data".

THE ONE THING TO KNOW: these three keys are TIMESERIES, not attributes.

    /values/attributes?keys=mainDevicesOnTimeData   -> returns nothing
    /values/timeseries?keys=mainDevicesOnTimeData   -> returns the data

`full_path` IS an attribute, so the two come from different endpoints. Reading all
three from the attributes endpoint returns an empty result on every device and looks
exactly like "no device publishes this".

Shapes differ between the three keys:

    mainDevicesOnTimeData  {SUBSYS: {metric: {lastTs, monthly: {YYYY-MM: {...}}}}}
    mainDevicesFaultData   {SUBSYS: {fault:  {YYYY-MM: {...}}}}     <- no `monthly`
    mainCCTVFaultData      {type:   {chNN:   {lastTs, monthly: {YYYY-MM: {...}}}}}

A parser written against the first returns nothing for the second, silently.

month_duration + downtime_minutes sums to the length of the month (44640 for a
31-day month), so month_duration is the UPTIME minutes. The *_score fields are 0-10
values whose scale is undocumented — passed through, never interpreted.

Coverage is partial by design: measured 2026-08-04, 23 of 104 BOI devices publish
mainDevicesOnTimeData, 5 publish mainCCTVFaultData, 1 publishes mainDevicesFaultData.
Devices publishing none are still emitted, with nulls, so the gap is visible.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# CONFIG — fill these in, or pass them as flags / environment variables.
#
# Do NOT commit real credentials in this file, and do not email it with them
# filled in. Prefer the environment variables:
#
#     set TB_USERNAME=you@bank.example        (Windows)
#     export TB_USERNAME=you@bank.example     (macOS/Linux)
#     export TB_PASSWORD=...
#
# The script logs in itself and refreshes on expiry, so you never handle a token
# by hand. A token lasts ~2.5 hours; a full pull of 100 devices takes ~1 minute.
# ---------------------------------------------------------------------------
DEFAULT_TB = "https://app.swatch360.seple.in"
DEFAULT_CUSTOMER = "fb98a600-2778-11f1-9cdc-43ca8fc8dcc9"

TB_USERNAME = ""  # e.g. "headoffice.security@bankofindia.bank.in"
TB_PASSWORD = ""  # leave blank and use TB_PASSWORD in the environment

ONTIME = "mainDevicesOnTimeData"
DEVICE_FAULT = "mainDevicesFaultData"
CCTV_FAULT = "mainCCTVFaultData"
TS_KEYS = (ONTIME, DEVICE_FAULT, CCTV_FAULT)

# This deployment serves HTTPS with a certificate the default trust store rejects.
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


class Session:
    """Holds the bearer token and renews it when ThingsBoard expires it.

    Tokens last ~2.5 hours. A long pull that straddles expiry otherwise turns every
    remaining device into a 401 that looks exactly like "this device has no data",
    which is the failure this whole script exists to avoid.
    """

    def __init__(self, tb: str, username: str = "", password: str = "", token: str = ""):
        self.tb = tb.rstrip("/")
        self.username = username
        self.password = password
        self.token = token
        if not self.token:
            self.login()

    def login(self) -> None:
        if not (self.username and self.password):
            raise SystemExit(
                "No token and no credentials. Set TB_USERNAME and TB_PASSWORD in the "
                "environment, edit the CONFIG block, or pass --token."
            )
        body = json.dumps({"username": self.username, "password": self.password}).encode()
        req = urllib.request.Request(
            f"{self.tb}/api/auth/login",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=_CTX, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise SystemExit(f"login failed (HTTP {exc.code}): {detail}") from None
        self.token = payload.get("token") or ""
        if not self.token:
            raise SystemExit(f"login returned no token: {str(payload)[:200]}")
        print(f"# logged in as {self.username}", file=sys.stderr)

    def get(self, url: str, _retried: bool = False):
        req = urllib.request.Request(url, headers={"X-Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, context=_CTX, timeout=60) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and not _retried and self.username and self.password:
                self.login()
                return self.get(url, _retried=True)
            return {"_error": f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:120]}"}
        except Exception as exc:  # noqa: BLE001 - one device must not stop the dump
            return {"_error": f"{type(exc).__name__}: {exc}"}
        try:
            return json.loads(body)
        except ValueError:
            # ThingsBoard returns a bare sentence for permission errors, not JSON.
            return {"_error": body.strip()[:160]}


def maybe_json(value):
    """Timeseries values arrive as JSON *strings*; emit them as real objects."""
    if isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def split_path(full_path):
    """Segments of full_path. Production uses the unicode arrow; others are fallbacks."""
    if not full_path:
        return []
    for sep in ("→", "->", "/"):
        if sep in full_path:
            return [p.strip() for p in full_path.split(sep) if p.strip()]
    return [full_path.strip()]


def fetch_device(session, device_id):
    """(full_path, {key: value}) for one device. Two calls: attributes, then timeseries."""
    attrs = session.get(
        f"{session.tb}/api/plugins/telemetry/DEVICE/{device_id}/values/attributes?keys=full_path"
    )
    full_path, error = None, None
    if isinstance(attrs, list):
        for item in attrs:
            if isinstance(item, dict) and item.get("key") == "full_path":
                full_path = item.get("value")
    elif isinstance(attrs, dict):
        error = attrs.get("_error")

    series = session.get(
        f"{session.tb}/api/plugins/telemetry/DEVICE/{device_id}/values/timeseries"
        f"?keys={','.join(TS_KEYS)}"
    )
    values = {}
    if isinstance(series, dict) and "_error" not in series:
        for key, points in series.items():
            if isinstance(points, list) and points:
                values[key] = maybe_json(points[0].get("value"))
    elif isinstance(series, dict):
        error = error or series.get("_error")
    return full_path, values, error


def main() -> int:
    # full_path contains "→" (U+2192). On Windows the console defaults to cp1252 and
    # printing it raises UnicodeEncodeError halfway through the dump.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", default=os.environ.get("TB_TOKEN", ""),
                    help="existing bearer token; omit to log in with username/password")
    ap.add_argument("--username", default=os.environ.get("TB_USERNAME", TB_USERNAME))
    ap.add_argument("--password", default=os.environ.get("TB_PASSWORD", TB_PASSWORD))
    ap.add_argument("--customer", default=DEFAULT_CUSTOMER, help="customer UUID")
    ap.add_argument("--tb", default=DEFAULT_TB, help="ThingsBoard base URL")
    ap.add_argument("--csv", help="also write a flattened per-month CSV here")
    ap.add_argument("--only-with-data", action="store_true",
                    help="omit devices publishing none of the three keys")
    args = ap.parse_args()

    session = Session(args.tb, args.username, args.password, args.token)

    page = session.get(
        f"{args.tb}/api/customer/{args.customer}/devices?pageSize=1000&page=0"
    )
    if not isinstance(page, dict) or "data" not in page:
        print(f"could not list devices: {page}", file=sys.stderr)
        return 1
    devices = page["data"]

    out = []
    for index, row in enumerate(devices, 1):
        device_id = str((row.get("id") or {}).get("id") or "")
        full_path, values, error = fetch_device(session, device_id)
        path = split_path(full_path)
        record = {
            "name": row.get("name"),
            "device_id": device_id,
            "created_time": row.get("createdTime"),
            "full_path": full_path,
            # Positional. Level depth differs per bank AND within one bank, so nothing
            # here assumes four levels: root is the tenant, leaf is the branch.
            "region": path[1] if len(path) > 2 else None,
            "zone": path[-2] if len(path) > 3 else None,
            "branch": path[-1] if path else None,
            ONTIME: values.get(ONTIME),
            DEVICE_FAULT: values.get(DEVICE_FAULT),
            CCTV_FAULT: values.get(CCTV_FAULT),
            "error": error,
        }
        if args.only_with_data and not any(record[k] for k in TS_KEYS):
            continue
        out.append(record)
        print(f"\r{index}/{len(devices)}", end="", file=sys.stderr)
    print(file=sys.stderr)

    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")

    if args.csv:
        write_csv(out, args.csv)

    print(
        "# devices={} onTime={} deviceFault={} cctvFault={} unreadable={}".format(
            len(out),
            sum(1 for r in out if r[ONTIME]),
            sum(1 for r in out if r[DEVICE_FAULT]),
            sum(1 for r in out if r[CCTV_FAULT]),
            sum(1 for r in out if r["error"]),
        ),
        file=sys.stderr,
    )
    return 0


def _months(node):
    """The month map, whether or not it sits under a `monthly` wrapper.

    mainDevicesFaultData omits the wrapper. Assuming it is always present drops that
    key entirely and without complaint.
    """
    if not isinstance(node, dict):
        return {}
    inner = node.get("monthly")
    if isinstance(inner, dict):
        return inner
    return {k: v for k, v in node.items() if isinstance(k, str) and len(k) == 7 and k[4] == "-"}


def write_csv(records, path):
    """One row per branch / key / subsystem / metric / month."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "region", "zone", "branch", "device_id", "source_key", "subsystem",
            "metric", "channel", "month", "month_duration_min", "downtime_min",
            "uptime_pct", "score",
        ])
        for rec in records:
            base = [rec["region"], rec["zone"], rec["branch"], rec["device_id"]]
            for key in TS_KEYS:
                top = rec.get(key)
                if not isinstance(top, dict):
                    continue
                for group, children in top.items():
                    if not isinstance(children, dict):
                        continue
                    for leaf, node in children.items():
                        for month, vals in _months(node).items():
                            if not isinstance(vals, dict):
                                continue
                            up = vals.get("month_duration")
                            down = vals.get("downtime_minutes")
                            pct = None
                            if isinstance(up, (int, float)) and isinstance(down, (int, float)):
                                total = up + down
                                pct = round(up / total * 100, 2) if total else None
                            score = (
                                vals.get("uptime_score")
                                if vals.get("uptime_score") is not None
                                else vals.get("fault_score", vals.get("fit_score"))
                            )
                            channel = leaf if key == CCTV_FAULT else None
                            writer.writerow(base + [
                                key, group, leaf, channel, month, up, down, pct, score,
                            ])


if __name__ == "__main__":
    raise SystemExit(main())
