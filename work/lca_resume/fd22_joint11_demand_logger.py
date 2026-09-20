#!/usr/bin/env python3
"""Log FD22 format-7 PSCM demand-chain telemetry to CSV (joint11).

Reports the demand chain that feeds the torque function:

    demand X:$2D49 = (control_law X:$2D54 * ramp X:$2D47) >> 10

The headline word is `ramp` (X:$2D47). Before joint11 the code-5 arm set the
ramp increment to a literal 0, so the ramp could never grow and the demand
stayed near zero. If `ramp` now rises during sustained LCA, the fix worked.

  python3 work/lca_resume/fd22_joint11_demand_logger.py --selftest
  python3 work/lca_resume/fd22_joint11_demand_logger.py -o fd22_joint11.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import time
from pathlib import Path

REQUEST = "22FD22"
FORMAT = 7

FIELD_NAMES = (
    "per_state_code",
    "ramp",
    "control_law",
    "demand",
    "rate_limit",
    "integrator",
    "torque_accumulator",
)
SOURCES = ("X:$2DB9", "X:$2D47", "X:$2D54", "X:$2D49", "X:$2D46", "X:$2D4B", "X:$2D53")

# Live code mapping established by joint3..joint8.
CODE_LABELS = {0: "idle", 1: "LKA-sustained", 2: "LKA-trans",
               3: "idle", 4: "LCA-entry", 5: "LCA-sustained"}

CSV_FIELDS = ["epoch_ns", "utc", "label", "status", "raw_response", "format", "code_label"]
for _n in FIELD_NAMES:
    CSV_FIELDS += [_n, _n + "_raw", _n + "_signed"]


def decode_response(response):
    if response is None:
        raise ValueError("timeout")
    if len(response) != 18 or response[:3] != bytes.fromhex("62FD22"):
        raise ValueError("expected 18-byte 62 FD 22 response")
    payload = response[3:]
    if payload[0] != FORMAT:
        raise ValueError(f"expected format {FORMAT}, got {payload[0]}")
    out = {"format": FORMAT}
    for i, name in enumerate(FIELD_NAMES):
        raw = int.from_bytes(payload[1 + 2 * i:3 + 2 * i], "big")
        out[name] = out[name + "_raw"] = raw
        out[name + "_signed"] = raw if raw < 0x8000 else raw - 0x10000
    out["code_label"] = CODE_LABELS.get(out["per_state_code"], "?")
    return out


def utc(ns):
    return dt.datetime.fromtimestamp(ns / 1e9, dt.timezone.utc).isoformat(timespec="microseconds")


def collect(request, output, rate, count, label, timeout=0.5, progress=None):
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    output.flush()
    period = 1 / rate
    n = 0
    while count is None or n < count:
        start = time.monotonic()
        stamp = time.time_ns()
        row = {"epoch_ns": stamp, "utc": utc(stamp), "label": label,
               "status": "", "raw_response": ""}
        response = request(REQUEST, timeout)
        if response is None:
            row["status"] = "timeout"
        else:
            row["raw_response"] = response.hex()
            try:
                row.update(decode_response(response))
                row["status"] = "ok"
            except ValueError:
                row["status"] = "error"
        writer.writerow(row)
        output.flush()
        n += 1
        if progress:
            progress(row)
        delay = period - (time.monotonic() - start)
        if delay > 0 and (count is None or n < count):
            time.sleep(delay)
    return n


def load_ecu(directory):
    path = directory / "vbf.py"
    spec = importlib.util.spec_from_file_location("vbflasher_vbf", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Ecu


def selftest():
    payload = bytes.fromhex("07 0005 0190 0200 0032 0400 0064 FFEC".replace(" ", ""))
    decoded = decode_response(bytes.fromhex("62FD22") + payload)
    assert [decoded[n + "_signed"] for n in FIELD_NAMES] == [5, 400, 512, 50, 1024, 100, -20], decoded
    assert decoded["code_label"] == "LCA-sustained"

    # A format-6 (joint9/joint10) response must be rejected, not misread.
    try:
        decode_response(bytes.fromhex("62FD22") + bytes([6]) + payload[1:])
    except ValueError:
        pass
    else:
        raise AssertionError("format 6 accepted by the format-7 decoder")

    # Short frame rejected.
    try:
        decode_response(bytes.fromhex("62FD22") + payload[:-1])
    except ValueError:
        pass
    else:
        raise AssertionError("short response accepted")

    print("SELFTEST PASS: format 7; " + ", ".join(
        f"{n}={s}" for n, s in zip(FIELD_NAMES, SOURCES)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--rate", type=float, default=10)
    ap.add_argument("--timeout", type=float, default=0.5)
    ap.add_argument("--count", type=int)
    ap.add_argument("--label", default="")
    ap.add_argument("--output", "-o")
    ap.add_argument("--vbflasher-dir", type=Path, default=Path("/home/gl/Projects/ford/VBFlasher"))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.rate <= 0 or args.timeout <= 0 or (args.count is not None and args.count <= 0):
        ap.error("rate, timeout and count must be positive")

    name = args.output or ("fd22_joint11_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
    out = Path(name).resolve()
    Ecu = load_ecu(args.vbflasher_dir)
    ecu = Ecu(args.iface, 0x730, 0x738, execute=True)

    def request(payload, timeout):
        return ecu.req(payload, timeout=timeout, pending_timeout=timeout,
                       what="FD22 demand-chain trace")

    def progress(row):
        if row["status"] == "ok":
            print(f"\r{row['utc']} code={row['per_state_code']:>2}"
                  f" {row['code_label']:<14}"
                  f" ramp={row['ramp_signed']:>7}"
                  f" ctrl={row['control_law_signed']:>7}"
                  f" dmd={row['demand_signed']:>7}"
                  f" acc={row['torque_accumulator_signed']:>7}", end="", flush=True)
        else:
            print(f"\r{row['utc']} {str(row['status']).upper()}", end="", flush=True)

    print(f"FD22 format-7 demand logger -> {out}\nPress Ctrl-C to stop safely.")
    total = "interrupted"
    try:
        with out.open("w", newline="", encoding="utf-8") as handle:
            total = collect(request, handle, args.rate, args.count, args.label,
                            args.timeout, progress)
    except KeyboardInterrupt:
        pass
    finally:
        sock = getattr(ecu, "s", None)
        if sock is not None:
            sock.close()
    print(f"\nStopped ({total}); CSV preserved at {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())