#!/usr/bin/env python3
"""Continuously log the instrumented CV6T PSCM FD22 snapshot to CSV.

The logger sends only UDS ReadDataByIdentifier request 22 FD22 to PSCM
(0x730/0x738). Stop with Ctrl-C. No diagnostic session or security access is
requested and no ECU state is written.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import os
from pathlib import Path
import sys
import time
from typing import BinaryIO, Callable, TextIO

REQUEST = "22FD22"
FIELD_NAMES = (
    "lane_state", "per_state_code", "torque_accumulator",
    "torque_output_result", "fd0e_raw_source", "fd0c_raw_source",
    "upstream_intermediate",
)
CSV_FIELDS = ["epoch_ns", "utc", "label", "status", "raw_response", "format"]
for _name in FIELD_NAMES:
    CSV_FIELDS.extend((_name, _name + "_raw", _name + "_signed"))


def decode_response(response: bytes | None) -> dict[str, int]:
    if response is None:
        raise ValueError("timeout")
    if len(response) != 18:
        raise ValueError(f"expected 18-byte response, got {len(response)}")
    if response[:3] != bytes.fromhex("62FD22"):
        raise ValueError("not a positive 62 FD 22 response")
    payload = response[3:]
    if payload[0] != 0:
        raise ValueError(f"unsupported format byte 0x{payload[0]:02X}")
    result: dict[str, int] = {"format": payload[0]}
    for index, name in enumerate(FIELD_NAMES):
        value = int.from_bytes(payload[1 + 2 * index:3 + 2 * index], "big")
        result[name] = value
        result[name + "_raw"] = value
        result[name + "_signed"] = value if value < 0x8000 else value - 0x10000
    return result


def _utc(epoch_ns: int) -> str:
    return dt.datetime.fromtimestamp(epoch_ns / 1_000_000_000, dt.timezone.utc).isoformat(timespec="microseconds")


def collect(
    request: Callable[[str, float], bytes | None],
    output: TextIO,
    rate_hz: float,
    count: int | None,
    label: str,
    *,
    timeout: float = 0.5,
    clock_ns: Callable[[], int] = time.time_ns,
    sleeper: Callable[[float], None] = time.sleep,
    progress: Callable[[dict[str, object]], None] | None = None,
) -> int:
    if rate_hz <= 0:
        raise ValueError("rate_hz must be positive")
    if count is not None and count <= 0:
        raise ValueError("count must be positive")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    output.flush()
    period = 1.0 / rate_hz
    completed = 0
    while count is None or completed < count:
        started = time.monotonic()
        epoch_ns = clock_ns()
        row: dict[str, object] = {
            "epoch_ns": epoch_ns,
            "utc": _utc(epoch_ns),
            "label": label,
            "status": "",
            "raw_response": "",
        }
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
        completed += 1
        if progress is not None:
            progress(row)
        remaining = period - (time.monotonic() - started)
        if remaining > 0 and (count is None or completed < count):
            sleeper(remaining)
    return completed


def _load_ecu(vbflasher_dir: Path):
    module_path = vbflasher_dir / "vbf.py"
    if not module_path.is_file():
        raise RuntimeError(f"VBFlasher transport not found: {module_path}")
    spec = importlib.util.spec_from_file_location("vbflasher_vbf", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Ecu


def _default_output() -> str:
    return "fd22_snapshot_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iface", default="can0")
    parser.add_argument("--rate", type=float, default=10.0, help="requests per second (default: 10)")
    parser.add_argument("--timeout", type=float, default=0.5, help="response timeout in seconds")
    parser.add_argument("--count", type=int, help="stop after N requests; default runs until Ctrl-C")
    parser.add_argument("--label", default="", help="constant test-phase label written to every row")
    parser.add_argument("--output", "-o", default=None, help="CSV path; default is timestamped")
    parser.add_argument("--vbflasher-dir", type=Path, default=Path("/home/gl/Projects/ford/VBFlasher"))
    args = parser.parse_args()
    if args.rate <= 0 or args.timeout <= 0 or (args.count is not None and args.count <= 0):
        parser.error("rate, timeout, and count (when supplied) must be positive")

    output_path = Path(args.output or _default_output()).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Ecu = _load_ecu(args.vbflasher_dir)

    print("FD22 logger: read-only UDS service 0x22; PSCM 0x730 -> 0x738")
    print(f"Writing: {output_path}")
    print("Press Ctrl-C to stop safely.")
    ecu = Ecu(args.iface, 0x730, 0x738, execute=True)

    def request(payload: str, timeout: float) -> bytes | None:
        return ecu.req(payload, timeout=timeout, pending_timeout=timeout, what="FD22 snapshot")

    def progress(row: dict[str, object]) -> None:
        if row["status"] == "ok":
            print(f"\r{row['utc']}  state={row['lane_state']:>3} code={row['per_state_code']:>3} "
                  f"acc={row['torque_accumulator_signed']:>6} out={row['torque_output_result_signed']:>6}",
                  end="", flush=True)
        else:
            print(f"\r{row['utc']}  {str(row['status']).upper():<12}", end="", flush=True)

    try:
        with output_path.open("w", newline="", encoding="utf-8") as output:
            total = collect(request, output, args.rate, args.count, args.label,
                            timeout=args.timeout, progress=progress)
    except KeyboardInterrupt:
        total = "interrupted"
    finally:
        sock = getattr(ecu, "s", None)
        if sock is not None:
            sock.close()
    print(f"\nStopped ({total}); CSV preserved at {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
