#!/usr/bin/env python3
"""Log FD22 format-1 LCA consumer-state telemetry to CSV (read-only)."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
from pathlib import Path
import sys
import time
from typing import Callable, TextIO

REQUEST = "22FD22"
FORMAT = 1
FIELD_NAMES = (
    "lane_state", "per_state_code", "torque_accumulator",
    "lca_consumer_gate", "state_precondition", "state_machine_phase",
    "state_machine_value",
)
SOURCES = ("X:$2DDE", "X:$2DB9", "X:$2D53", "X:$2DC1", "X:$2DC3", "X:$2DAF", "X:$2DB8")
CSV_FIELDS = ["epoch_ns", "utc", "label", "status", "raw_response", "format"]
for name in FIELD_NAMES:
    CSV_FIELDS.extend((name, name + "_raw", name + "_signed"))


def decode_response(response: bytes | None) -> dict[str, int]:
    if response is None:
        raise ValueError("timeout")
    if len(response) != 18 or response[:3] != bytes.fromhex("62FD22"):
        raise ValueError("expected an 18-byte positive 62 FD 22 response")
    payload = response[3:]
    if payload[0] != FORMAT:
        raise ValueError(f"expected format {FORMAT}, got {payload[0]}")
    result = {"format": payload[0]}
    for index, name in enumerate(FIELD_NAMES):
        value = int.from_bytes(payload[1 + 2*index:3 + 2*index], "big")
        result[name] = result[name + "_raw"] = value
        result[name + "_signed"] = value if value < 0x8000 else value - 0x10000
    return result


def utc(epoch_ns: int) -> str:
    return dt.datetime.fromtimestamp(epoch_ns / 1e9, dt.timezone.utc).isoformat(timespec="microseconds")


def collect(request: Callable[[str, float], bytes | None], output: TextIO,
            rate: float, count: int | None, label: str, timeout: float = 0.5,
            progress: Callable[[dict[str, object]], None] | None = None) -> int:
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader(); output.flush()
    period = 1.0 / rate
    completed = 0
    while count is None or completed < count:
        started = time.monotonic(); stamp = time.time_ns()
        row: dict[str, object] = {"epoch_ns": stamp, "utc": utc(stamp), "label": label,
                                 "status": "", "raw_response": ""}
        response = request(REQUEST, timeout)
        if response is None:
            row["status"] = "timeout"
        else:
            row["raw_response"] = response.hex()
            try:
                row.update(decode_response(response)); row["status"] = "ok"
            except ValueError:
                row["status"] = "error"
        writer.writerow(row); output.flush(); completed += 1
        if progress: progress(row)
        delay = period - (time.monotonic() - started)
        if delay > 0 and (count is None or completed < count): time.sleep(delay)
    return completed


def load_ecu(directory: Path):
    path = directory / "vbf.py"
    spec = importlib.util.spec_from_file_location("vbflasher_vbf", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.Ecu


def selftest() -> int:
    payload = bytes.fromhex("01 0004 0000 FFFE 0001 0000 0006 0003")
    decoded = decode_response(bytes.fromhex("62FD22") + payload)
    assert [decoded[n + "_signed"] for n in FIELD_NAMES] == [4, 0, -2, 1, 0, 6, 3]
    for bad in (bytes.fromhex("62FD22") + bytes([0]) + payload[1:], None):
        try: decode_response(bad)
        except ValueError: pass
        else: raise AssertionError("invalid response accepted")
    print("SELFTEST PASS: format 1; " + ", ".join(f"{n}={s}" for n, s in zip(FIELD_NAMES, SOURCES)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--rate", type=float, default=10.0)
    ap.add_argument("--timeout", type=float, default=0.5)
    ap.add_argument("--count", type=int)
    ap.add_argument("--label", default="")
    ap.add_argument("--output", "-o")
    ap.add_argument("--vbflasher-dir", type=Path, default=Path("/home/gl/Projects/ford/VBFlasher"))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest: return selftest()
    if args.rate <= 0 or args.timeout <= 0 or (args.count is not None and args.count <= 0):
        ap.error("rate, timeout, and count must be positive")
    output = Path(args.output or ("fd22_trace_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")).resolve()
    Ecu = load_ecu(args.vbflasher_dir)
    ecu = Ecu(args.iface, 0x730, 0x738, execute=True)
    def request(payload: str, timeout: float) -> bytes | None:
        return ecu.req(payload, timeout=timeout, pending_timeout=timeout, what="FD22 gate trace")
    def progress(row: dict[str, object]) -> None:
        if row["status"] == "ok":
            print(f"\r{row['utc']} state={row['lane_state']:>2} code={row['per_state_code']:>2} "
                  f"acc={row['torque_accumulator_signed']:>6} gate={row['lca_consumer_gate']:>2} "
                  f"pre={row['state_precondition']:>2} phase={row['state_machine_phase']:>2} "
                  f"value={row['state_machine_value']:>2}", end="", flush=True)
        else:
            print(f"\r{row['utc']} {str(row['status']).upper()}", end="", flush=True)
    print(f"FD22 format-1 logger -> {output}\nPress Ctrl-C to stop safely.")
    try:
        with output.open("w", newline="", encoding="utf-8") as handle:
            total = collect(request, handle, args.rate, args.count, args.label,
                            timeout=args.timeout, progress=progress)
    except KeyboardInterrupt:
        total = "interrupted"
    finally:
        sock = getattr(ecu, "s", None)
        if sock is not None: sock.close()
    print(f"\nStopped ({total}); CSV preserved at {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
