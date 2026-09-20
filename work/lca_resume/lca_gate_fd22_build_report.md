# CV6T-AR LCA gate-open + FD22 test image

## Artifact

```text
/home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_GATE_FD22.VBF
SHA-256 12c78350a88bc3003fa1caf1176fa5448527c0d51eb49535946dd58f3c6c5598
```

Source application and paired calibration:

```text
CV6T-14C217-AR.VBF  cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5
CV6T-14C218-AX.VBF  6fdc0ad81727fd39db98a486f2ec2b4de928100138fc93b8c3e5c2a8e307c5f7
```

Required live identities before flashing:

```text
F188 = CV6T-14C217-AR
F124 = CV6T-14C218-AX
```

Do not flash on any mismatch.

## Behavioral edit

The OEM request-6 dispatcher arm is:

```text
P:$2A77E  4C06       compare request 6
P:$2A77F  A207       branch for requests other than 6
P:$2A780  F07C 0904  load LCA-specific gate X:$0904
P:$2A782  4C01       compare gate with 1
P:$2A783  A203       reject if gate is not 1
P:$2A784  E684 2DDE  store LCA internal state 4
P:$2A786  A902       branch over idle store
P:$2A787  E680 2DDE  store state 0
```

The combined image changes exactly the four guard words at `P:$2A780..$2A783`:

```text
F07C 0904 4C01 A203
    ->
E700 E700 E700 E700
```

The outer request-6 compare/branch, OEM state-4 store, idle store, and all other dispatcher arms remain unchanged. Therefore only OEM request value 6 is allowed through this guard; no lane state or torque value is forced directly.

## Observation retained

The existing FD22 snapshot is retained byte-for-byte:

```text
request:  22 FD 22
response: 62 FD 22 00 + seven 16-bit words
handler:  P:$33800, 60 words
```

Fields remain:

```text
X:$2DDE lane state
X:$2DB9 per-state code
X:$2D53 torque accumulator
X:$2D52 unclassified result cell
X:$1CB1 FD0E source
X:$171B FD0C source
X:$2D54 upstream intermediate
```

The same `fd22_snapshot_logger.py` works without modification.

## Integrity

All integrity layers were rebuilt in order and independently verified:

```text
internal checksum A: D110 -> 3DD1
internal checksum B: 3216 -> 0880
blk1 CRC-16:         1B6C -> 7337
VBF file checksum:   5BE7CF1E -> BBB3821B
```

Independent payload audit found 133 differing bytes confined to five contiguous runs:

```text
0x1DD48..0x1DD4B  FD22 pointer differing octets
0x54F00..0x54F08  four gate words
0x67000..0x67077  FD22 handler differing octets
0x7FFEA..0x7FFEE  internal checksums A/B
```

The apparent split in the handler range is caused by one octet that coincidentally retains its OEM value. No unexplained payload or header difference exists.

Verification results:

```text
combined builder self-test: ALL PASS
independent verifier:       READY FOR CONTROLLED TEST
vbftool verify:              OK (3 blocks)
VBFlasher verify:            ALL CRCs OK
VBFlasher dry-run:           valid PSCM 3-block plan
```

## Reproduction

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/build_lca_gate_fd22_vbf.py --selftest
python3 work/lca_resume/build_lca_gate_fd22_vbf.py
python3 work/lca_resume/verify_lca_gate_fd22_vbf_independent.py
python3 /home/gl/Projects/ford/VBFlasher/vbflasher.py verify \
  CV6T-14C217-AR_LCA_GATE_FD22.VBF
sha256sum CV6T-14C217-AR_LCA_GATE_FD22.VBF
```

## Controlled test

Before flashing, save live identity and actual DTC state. Use stationary vehicle and stable external power. Retain the interactive flash confirmation and save the complete trace:

```bash
cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py ident PSCM --iface can0
python3 vbflasher.py dtc PSCM --iface can0
python3 vbflasher.py flash --iface can0 \
  --logfile /home/gl/Projects/ford/PSCM/Research/lca_gate_fd22_flash.log \
  /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_GATE_FD22.VBF
```

After restart, first collect a parked FD22 sample and reread actual DTCs. Do not drive if identity changes unexpectedly, FD22 fails, a new actual steering DTC appears, or parked steering behavior is abnormal.

For the road test, start both loggers before moving and leave them unattended. Include physically effective ordinary LKA as the positive control and displayed LCA as the test condition. Use a passenger or unattended logging; do not operate a computer while driving.

Primary acceptance criterion during raw `0x0A5` request 6:

```text
X:$2DDE changes from 0 to 4
X:$2DB9 advances into the LCA consumer code(s)
```

The test must then determine whether `X:$2D53` becomes nonzero and whether steering action appears. State 4 without accumulator/steering action proves a second downstream blocker; state 4 with a nonzero accumulator but no steering action moves the next target further downstream.

## Recovery

This is a complete application image and replaces the currently installed FD22-only application. It retains FD22, but it is mutually exclusive with the stock and gate-only application images. Keep verified stock `CV6T-14C217-AR.VBF` immediately available for restoration after the capture or on any unexpected behavior.

No ECU communication or flashing was performed while building this artifact.
