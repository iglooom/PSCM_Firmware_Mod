# CV6T PSCM: minimal in-firmware instrumentation design

**Goal:** distinguish LKA (`CAN state 2/4`) and LCA (`CAN state 6`) using data
captured inside the PSCM at the lane-control runnable frequency, without relying
on slow UDS polling. The target version is `CV6T-14C217-AR` only.

**Status:** the hook, code, DID, and `FD0C/FD0E` source addresses have been
confirmed in the OEM image. The P-space cave has been confirmed. The RAM
candidate and the ABI of the new DID handler have not yet been sufficiently
proven for building/flashing. Therefore, no VBF/bin **was created or modified**,
and live CAN was not started.

## 1. Baseline and OEM integrity

```text
CV6T-14C217-AR.VBF
sha256 cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5

blk0 @ 0x00000000, 0x09800 bytes
sha256 21d095f6ce8496695951a6ad2f012d428da87ea21c781b4f4a4d0992994e5074
blk1 @ 0x0001C000, 0x64000 bytes
sha256 6e60963a3583993d1b872ebd88bb9b2f1acdeb397a7954a1ae75be3ce7d933b0
blk2 @ 0x04008C00, 0x07400 bytes
sha256 4e65a6493cf770d02a125c115a50c7b6b8d58969405bbd0292fafd896750d6c0
```

`vbftool verify` for the OEM file: 3/3 block CRC OK, file CRC-32 OK, trailing 0.

## 2. Actual hook and cadence

### Primary hook: after the torque/output stage, once per lane runnable

The lane component is registered in the 28-word lifecycle object `X:$612C`:

```text
X:$6132  A756 0002   -> P:$2A756
X:$6134  A743 0002   -> P:$2A743   cyclic callback
X:$6136  A75E 0002   -> P:$2A75E
X:$6138  A75B 0002   -> P:$2A75B
```

`P:$2A743` is a sequential lane runnable. Before the torque stage, it updates
the internal lane state/per-state state machines; it then unconditionally calls
`P:$2AE86`, after which it runs the remaining consumers:

```text
P:$2A743  JSR P:$2A7D7   ; contains dispatcher -> X:$2DDE
...
P:$2A749  JSR P:$2AD62   ; per-state processing
P:$2A74B  E256 AE86      JSR P:$2AE86   ; torque/output stage
P:$2A74D  JSR P:$2B8EA
...
P:$2A755  RTS
```

**Proposed single hook site:** replace the call at `P:$2A74B` with a call to a
trampoline. The trampoline first calls the unmodified `P:$2AE86`, then saves the
sample and returns the same register state that the OEM routine returned.

```text
P word address          P:$2A74B
flash byte address      0x054E96
OEM words               E256 AE86
OEM bytes               56 E2 86 AE        (little-endian words)
candidate cave call     E257 3800           (JSR P:$33800; DO NOT patch now)
candidate bytes         57 E2 00 38
```

Why this is better than a hook inside the torque math:

* runs exactly once on every invocation of the lane cyclic callback;
* the sample is captured after `P:$2AE86`, so `X:$2D53/$2D52` are already final;
* if the torque stage took the inhibit path, `P:$2AE86` itself zeros
  `X:$2D53/$2D52`, and the zero sample will still be recorded;
* does not insert code into the ISR or change the dispatcher/enable branch.

**Cadence:** this is not an estimate of "approximately 1 kHz," but an exact
structural relationship—one sample per invocation of cyclic callback
`P:$2A743`. The absolute period of this callback has not been found statically.
It must be measured with the first observation-only image using `hook_count`
over a known interval. Until such a measurement, the frequency must not be
stated in Hz. The independent CAN `0x0A5` has a 20 ms period, but this does not
prove the period of the internal runnable.

### Secondary hook, only for localizing the torque math

```text
P:$2B078 / flash 0x0560F0
OEM E256 B07C = JSR P:$2B07C
OEM bytes 56 E2 7C B0
```

It is located inside `P:$2B054` and fires only when the outer gates have allowed
the calculation. Therefore, it **is not suitable as the primary logger hook**:
it will miss inhibited loops. Use it only later to compare the value before/after
`P:$2B07C`.

## 3. What to write to the sample

Proposed unscaled format, 7 little-endian words (14 bytes):

| word | source | meaning | instrumentation recording | modification of the source itself |
|---:|---|---|---|---|
| 0 | logger counter | `loop_seq` modulo 65536 | **observation-safe** | — |
| 1 | `X:$2DDE` | internal lane state: LKA 1/2, LCA 4 | **observation-safe** read | **actuator**, writing prohibited |
| 2 | `X:$2DB9` | per-state: LKA 2; LCA entry 4 / sustained 5 | **observation-safe** read | **actuator**, affects torque enable |
| 3 | `X:$2D53` | torque accumulator, signed raw word | **observation-safe** read | **actuator**, writing prohibited |
| 4 | `X:$2D52` | final output/enable result (0/1 in the code found) | **observation-safe** read | **actuator**, writing prohibited |
| 5 | `X:$1CB1` | raw source `FD0E`, diagnostic view = signed clamp after `/512` | **observation-safe** read | **actuator-conservative**, do not write |
| 6 | `X:$171B` | raw source `FD0C`, diagnostic view = signed clamp after `/256` | **observation-safe** read | **actuator-conservative**, do not write |

`FD0E/FD0C` need not be called as diagnostic routines inside the loop: one load
of each raw source is sufficient. This eliminates UDS/ISO-TP latency and extra
calls. Confirmed paths:

```text
FD0C table P:$0EE5A: FD0C 0000 C0C0 0001 0000 0000
  -> callback P:$1C0C0 -> P:$20553 -> getter P:$1C177
  -> P:$1C177 begins F07C 171B (read X:$171B), scale >> 8, clamp -128..127

FD0E table P:$0EE66: FD0E 0000 C0C6 0001 0000 0000
  -> callback P:$1C0C6 -> P:$20565 -> getter P:$1C1A7
  -> P:$1C1A7 begins F07C 1CB1 (read X:$1CB1), scale >> 9, clamp -128..127
```

To minimize code/load, the first image writes only words 0..4. Words 5..6 are
added in the second image; their sources have already been established, but they
are not needed to prove `state -> per-state -> accumulator/output`.

## 4. RAM ring buffer

### Candidate not yet approved for use

```text
X:$6600..$6CFF   0x700 words = 1792 words = 256 records * 7 words
X:$6D00..$6D0F   16 words metadata
UDS namespace    0x0400CC00..0x0400DA20
```

Metadata proposal:

```text
6D00 magic=4C41       6D01 format=0001
6D02 write_index      6D03 wraps
6D04 flags            6D05 trigger_reason
6D06/6D07 hook_count  6D08 read_cursor
6D09 valid_records    6D0A previous_state
6D0B seq_begin        6D0C seq_end
6D0D..6D0F reserved
```

The candidate passed the static checks:

* all 1808 words in OEM blk2 equal `FFFF`;
* there are no recognized absolute/immediate P-space references to this range;
* it contains no known stride-5 extraction destination/area from `CV6T-14C386-AB`;
* the range starts after the end of the dense initialized-data area `X:$63A3`.

**These checks are necessary but not sufficient.** Indexed/heap/stack access is
not detected by an absolute-xref scan. Actual indirect-use areas exist nearby;
for example, `X:$655E/$6560` have code references, and `X:$6EDA` is also used.
Therefore, `X:$6600..$6D0F` remains **observation-safe by intended use,
address-unconfirmed**. Building is prohibited until linker/map/runtime ownership
is proven.

The ring runs in continuous-wrap mode and freezes on the falling edge of the
active state (`previous_state != 0 && X:$2DDE == 0`) or upon filling after a
trigger. Thus, at an unknown frequency, the last 256 loops of the active episode
are retained. No decimation in the first image: this preserves the "control-loop
frequency" requirement. `loop_seq`, `wraps`, and `valid_records` reveal gaps/
overflow.

For coherent records: the writer increments `seq_begin`, writes 7 words, then
publishes the index/`seq_end`; the DID reader operates only after freeze. Do not
read the live ring while driving.

## 5. P-space cave and size estimate

A continuous linker tail-fill has been confirmed in blk1:

```text
P:$33783..$3FFF1 inclusive
51311 words = 102622 bytes
each OEM word = E70A (ILLEGAL), OEM bytes = 0A E7
footer begins P:$3FFF2 / flash 0x07FFE4
```

It is proposed to reserve only:

```text
P:$33800..$338FF (0x100 words, flash 0x067000..0x0671FF)
```

Estimate, not finished machine code:

| part | expected P words |
|---|---:|
| trampoline + call original + full register save/restore | 12–24 |
| writer 5-word minimal | 24–40 |
| writer 7-word full + wrap/freeze metadata | 40–70 |
| FD22 paged reader | 35–70 |
| constants/guard/error path | 16–30 |
| **total** | **103–194** |

The 256-word reservation is sufficient with margin. But opcode bytes must not be
generated before an ABI/register-liveness review. It is especially important to
preserve `A/B/C/D`, `Y`, `R0/R1`, and status/loop state exactly as after the OEM
`P:$2AE86`; the assumption that "the caller does not use the registers" is
unacceptable.

## 6. Readout: options and selection

### Recommended path: temporarily repurpose `FD22` (15 bytes)

`FD22` is firmware-only, unnamed, already marked readable, and has a 15-byte payload:

```text
DID table P:$0EEA2: FD22 0000 058F 0001 0000 0000
read callback pointer at P:$0EEA4 = P:$1058F
OEM pointer bytes at flash 0x01DD48: 8F 05 01 00
runtime metadata X:$6388: FD22 0000 0001 000F
P:$1058F starts: 8748 1DAC; adds 0x2D, then copies 15 bytes
```

The new handler must return **1 status byte + 1 frozen 14-byte record** and
increment the logger-only `read_cursor`. This allows 256 samples to be retrieved
after an episode using ordinary `22 FD22` requests, without requiring an SBL or
destroying runtime RAM. Perform the reading with the vehicle stationary after
freeze. Status must contain `frozen/valid/wrapped/end`, while `loop_seq` within
the record will detect reorder/loss.

Why not `FD20`: it is the named `Mean Friction Share Histogram`; the OEM
function must not be broken unnecessarily. Its OEM entry is:
`FD20 0000 C0F9 0001 0000 0000`, payload 12 bytes.

### Spare DID `F112`: logically cleaner, but not the first choice

The OEM table entry is completely empty:

```text
P:$0EDD0: F112 0000 0000 0000 0000 0000
runtime metadata X:$62FC: F112 0200 0000 0018
```

A read callback could be installed and the declared length of 24 used, but the
runtime metadata shows read-enable `0`, and it is unknown whether it is
recomputed from the P table at boot. This requires analysis of the dispatcher
ABI/registration; for now, it is less verifiable than the already working FD22
read path.

### Existing FD22 handler / mailbox without a new handler

`P:$1058F` can be pointed to a fixed 15-byte mailbox and the latest sample read.
This is minimal, but once again yields sparse isolated instants and does not
solve the original bandwidth problem. It is acceptable only as a Stage 1
cadence/ABI probe, not as the final LKA/LCA measurement.

### SBL / ReadMemory

Excluded: loading the SBL stops the application and overwrites RAM;
`ReadMemoryByAddress 0x23` is absent. Such a readout is not valid.

## 7. Minimal reversible address plan (not yet a build plan)

Only after resolving the RAM/ABI blockers are the following clusters expected:

| cluster | OEM words / bytes | purpose | class |
|---|---|---|---|
| `P:$2A74B` / `0x054E96`, 2 words | `E256 AE86` / `56 E2 86 AE` | redirect to trampoline | **observation-only code hook** |
| `P:$33800..` / `0x067000..`, <=256 words | `E70A` repeated / `0A E7` repeated | logger + FD22 reader | **observation-only code** |
| `P:$0EEA4` / `0x01DD48`, 2 words | `058F 0001` / `8F 05 01 00` | FD22 read pointer -> cave handler | **observation-only diagnostics** |
| blk1+`0x63FEA`, flash `0x07FFEA` | `D110` / `10 D1` | CRC-16/MCRF4XX repair | integrity-only |
| blk1+`0x63FEC`, flash `0x07FFEC` | `3216` / `16 32` | SUM-16 repair | integrity-only |

Rollback—the stock VBF with the SHA-256 above. Every future edit must have an
`--expect` for the OEM bytes. No changes to `X:$2DDE/$2DB9/$2D53/$2D52`, branch
conditions, calibration cells, or motor paths in the observation image.

Integrity order for any future blk1 edit:

1. word A @ `0x07FFEA` = CRC-16/MCRF4XX over
   `blk0 ++ blk1[0x1800:0x63FEA]` (AR START confirmed);
2. word B @ `0x07FFEC` = `sum16le(linear flash[0:0x7FFEC])`, with paired
   `CV6T-14C218-AX`, including word A;
3. VBF block CRC-16/CCITT-FALSE;
4. VBF header file CRC-32;
5. independent full diff: only hook + cave + DID pointer + 2 checksum words.

Word A is checked by a continuous background monitor, so "container CRC OK" is
not sufficient.

## 8. Checks before build approval

Mandatory completion checks:

1. **RAM ownership:** prove that `X:$6600..$6D0F` is not a stack/heap/indexed
   table. A complete pointer/range analysis or linker map is required; the
   current xref scan is not proof.
2. **Callback ABI:** trace the UDS `0x22` dispatcher through the call to
   `P:$1058F`, determine input/output pointers, byte packing, permitted clobbers,
   and the exact success return (`Y0=0x000F` in the OEM handler).
3. **Hook ABI:** determine the live-out registers after `P:$2AE86` and preserve them.
4. **Worst-case execution time:** count logger cycles; set the limit before the
   build. The writer must not block, call UDS code, or disable interrupts.
5. **Cadence measurement:** parked image with a single counter/mailbox; compare
   `hook_count` with wall clock and confirm the absence of watchdog/DTC issues.
6. **Coherence controls:** monotonically increasing `loop_seq`, no torn record,
   exact 256-record wrap/freeze tests on a synthetic dump.
7. **Firmware acceptance:** OEM SHA, exact `--expect`, both internal checksums,
   VBF verify, semantic disassembly, exhaustive diff.

## 9. Staged live probes

All actions below are proposals to the user; no CAN operations were performed here.

### Stage 0 — stock baseline, observation-safe

* Stock firmware; parked/engine running.
* Read existing `FD22` once to establish whether it answers and its exact
  15-byte baseline; read `FD0E/FD0C` as a positive control with hand torque.
* No writes, no programming session while running.

### Stage 1 — counter + latest mailbox, observation-only

* Hook only `P:$2A74B`; call OEM `P:$2AE86`; increment logger counter and copy
  one latest 5-word sample.
* Parked, front wheels unloaded if possible; verify cadence, watchdog margin,
  no new DTC, steering feel unchanged.
* Negative control: state idle -> `2D53/2D52` zero as OEM inhibit path dictates.
* Roll back to stock immediately on reset, DTC, assist warning or missed counter.

### Stage 2 — full ring, observation-only

* Add 256x7 ring and auto-freeze; FD22 reads frozen records only.
* Parked test first: force no state, apply hand torque, verify that raw
  `X:$1CB1/$171B` correspond in sign/time to external `FD0E/FD0C`.
* Verify 256 records, one wrap, ordered `loop_seq`, no torn samples.

### Stage 3 — road observation, positive controls first

* Dedicated closed/test area; second operator handles logging; no diagnostic
  polling during active steering.
* Separate ignition runs: idle/suppressed, known-working LKA-left, LKA-right,
  then LCA. Auto-freeze after each episode; unload while parked.
* Acceptance is per-state, not pooled: LKA-left and LKA-right each must show the
  expected `2DDE -> 2DB9 -> 2D53/2D52` chain before interpreting LCA.

### Stage 4 — optional pinpoint logger, observation-only

* Only if Stage 3 shows divergence inside the torque stage: the second hook at
  `P:$2B078` for the before/after accumulator. Do not change the arithmetic.

### Stage 5 — actuator probes, separate image and separate approval

Do not combine with logger validation. Each operation below is **actuator**:

1. transiently force `X:$2DB9=5` only under tightly bounded parked conditions;
2. bypass/force torque-enable result around `P:$2B0B4`;
3. inject/clamp `X:$2D53`;
4. force `X:$2D52` or downstream motor request.

Each actuator probe requires a hardware kill/ignition cutoff, wheels unloaded,
zero-speed gate, driver-torque override retained, hard loop-count timeout,
magnitude clamp, independent observer, and stock rollback. Such probes must not
be initiated on the road. Do not design any actuator patch until the
observation-only ring has shown a specific divergence point.

## 10. Interpretation of the result

* `2DDE=1/2`, `2DB9=2`, nonzero `2D53`, and active `2D52` during LKA confirm the
  positive control.
* `2DDE=4`, `2DB9=5`, but `2D53=0` during LCA localizes the divergence before/in
  the accumulator.
* Nonzero `2D53`, but `2D52=0`, localizes the output/enable decision.
* `2D53/2D52` are active and `FD0E raw` is nonzero, but `FD0C raw` is near zero—
  demand is being generated, but current does not follow; this is a downstream/
  current-control issue.
* `FD0E` and `FD0C` are nonzero and consistent during both LKA and LCA—the PSCM
  applies torque; the problem is authority/gain, not enable.

## 11. Accompanying tools

* `instrumentation_audit.py`—read-only static address/OEM/cave/RAM/DID audit;
  does not write files or open CAN.
* `decode_ring.py`—decoder for the proposed 7-word raw ring or 15-byte FD22
  pages; does not open CAN.

Verified:

```text
python3 work/lca_resume/instrumentation_audit.py --selftest
AUDIT: ALL STATIC CHECKS PASS

python3 work/lca_resume/decode_ring.py --selftest
SELFTEST: ALL PASS
```
