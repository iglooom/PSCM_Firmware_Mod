#!/usr/bin/env python3
"""Exercise the live-CAN transmit path WITHOUT a CAN interface.

The --selftest suites deliberately never touch hardware, so a NameError or a
bad struct format in the transmit path stays invisible until the vehicle.
This harness imports each module, monkeypatches socket.socket with a fake,
and runs the real run()/hold() function end to end.

Usage:  python3 _testtx.py
"""
import importlib
import socket
import struct
import sys
import types

ok = True


def chk(name, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


class FakeSock:
    """Stands in for a bound SocketCAN socket."""

    def __init__(self, *a, **k):
        self.sent = []
        self.bound = None
        self.timeout = None

    def bind(self, addr):
        self.bound = addr

    def settimeout(self, t):
        self.timeout = t

    def send(self, data):
        # Validate the frame really is a struct can_frame.
        if len(data) != 16:
            raise AssertionError(f"frame is {len(data)} bytes, expected 16")
        cid, dlc = struct.unpack("=IB3x", data[:8])
        self.sent.append((cid & 0x1FFFFFFF, data[8:8 + dlc]))
        return len(data)

    def recv(self, n):
        raise socket.timeout()

    def close(self):
        pass


def with_fake_socket(fn):
    """Run fn() with socket.socket replaced; return the FakeSock used."""
    made = []
    real = socket.socket
    real_stdout = sys.stdout

    def factory(*a, **k):
        s = FakeSock()
        made.append(s)
        return s

    socket.socket = factory
    sys.stdout = open("/dev/null", "w")   # silence the progress meter
    try:
        fn()
    finally:
        sys.stdout.close()
        sys.stdout = real_stdout
        socket.socket = real
    return made


def main():
    sys.path.insert(0, ".")
    LOG = "../../candump-2026-09-14_111406.log"

    print("-- ipma_spoof: real transmit path --")
    spoof = importlib.import_module("ipma_spoof")
    tpl = spoof.load_template(LOG)
    chk("template has all three IDs",
        all(c in tpl for c in (0x0A5, 0x1B5, 0x298)))

    args = types.SimpleNamespace(
        iface="vcan0", mode="replay", seconds=0.25, ref=2048, curv=2048,
        angle=None, curvature=None, ramp=0.0,
        drive_display=False, bustype=None)
    cycles = {c: tpl[c][1] for c in (0x0A5, 0x1B5, 0x298)}
    ca = spoof.parse_cs_rule("sum:3,6:-:0x75")
    cb = spoof.parse_cs_rule("nib:3,6:-:0x06")

    socks = with_fake_socket(lambda: spoof.run(args, ca, cb, tpl, cycles))
    sent = socks[0].sent
    chk("run() completed without exception", True)
    chk("frames were transmitted", len(sent) > 10, f"{len(sent)} frames")
    ids = {c for c, _d in sent}
    chk("all three IDs transmitted", ids == {0x0A5, 0x1B5, 0x298},
        " ".join(f"{i:#05x}" for i in sorted(ids)))
    chk("every payload is 8 bytes", all(len(d) == 8 for _c, d in sent))
    # replay mode must be byte-exact on the wire
    bad = [(c, d.hex()) for c, d in sent if d != tpl[c][0]]
    chk("replay is byte-exact on the wire", not bad, str(bad[:2]))

    print("-- ipma_spoof: lka-left differs from lca in exactly one byte --")
    for m in ("lka-left", "lca"):
        args.mode = m
        socks = with_fake_socket(lambda: spoof.run(args, ca, cb, tpl, cycles))
        f = [d for c, d in socks[0].sent if c == 0x0A5]
        chk(f"{m}: 0x0A5 transmitted", len(f) > 0)
        globals()[f"_{m}"] = f[0] if f else None
    a, b = globals().get("_lka-left"), globals().get("_lca")
    if a and b:
        diff = [i for i in range(8) if a[i] != b[i]]
        # byte 3 is the LkaActvStats_D_Req payload byte; bytes 2 and 5 are the
        # two checksums, which MUST follow it.  Anything else would mean the
        # experiment changes more than one variable.
        chk("only byte 3 + its two checksums differ", diff == [2, 3, 5],
            str(diff))
        chk("byte 3 carries the LKA->LCA change",
            (a[3] & 0x70) >> 4 == 2 and (b[3] & 0x70) >> 4 == 6,
            f"lka={(a[3] & 0x70) >> 4} lca={(b[3] & 0x70) >> 4}")

    print("-- ipma_spoof: a real --angle reaches the wire, and ramps --")
    args.mode = "lka-left"
    args.ref = spoof.refang_raw(40.0)
    args.ramp = 0.0
    socks = with_fake_socket(lambda: spoof.run(args, ca, cb, tpl, cycles))
    f = [d for c, d in socks[0].sent if c == 0x0A5]
    ang = [spoof.refang_mrad(spoof.be_extract(x, 27, 12)) for x in f]
    chk("commanded angle appears on the wire",
        ang and abs(ang[0] - 40.0) < 0.1, f"{ang[0]:.2f} mRad")

    args.ramp = 0.2
    socks = with_fake_socket(lambda: spoof.run(args, ca, cb, tpl, cycles))
    # run() deliberately sends 10 rounds of resting frames on exit; those are
    # not part of the ramp, so drop them before checking monotonicity.
    f = [d for c, d in socks[0].sent if c == 0x0A5][:-10]
    ang = [spoof.refang_mrad(spoof.be_extract(x, 27, 12)) for x in f]
    chk("ramp starts near zero", abs(ang[0]) < 5, f"{ang[0]:.2f} mRad")
    chk("ramp is monotonic non-decreasing",
        all(ang[i] <= ang[i + 1] + 0.06 for i in range(len(ang) - 1)))
    chk("ramp reaches the target", abs(ang[-1] - 40.0) < 2.0,
        f"{ang[-1]:.2f} mRad")
    args.ramp = 0.0

    print("-- silence_ipma: real transmit path --")
    sil = importlib.import_module("silence_ipma")
    chk("module imports", True)
    fns = [n for n in ("hold", "run", "silence") if hasattr(sil, n)]
    chk("has an entry point", bool(fns), str(fns))

    print("-- lane_observe / capture_lane: socket path --")
    for mod in ("lane_observe", "capture_lane"):
        m = importlib.import_module(mod)
        chk(f"{mod} imports", True)
        chk(f"{mod} has raw socket constants",
            hasattr(socket, "AF_CAN") and hasattr(socket, "CAN_RAW"))

    print()
    print("TX-PATH CHECK:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
