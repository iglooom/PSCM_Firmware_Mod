# Vehicle test plan — LKA/LCA on the PSCM

Offline analysis found no BV6T/CV6T difference in five layers
(`PSCM_BV6T_vs_CV6T_lane_assist.md`, `PSCM_14C218_calibration_compare.md`).
The remaining path is measurement on the car.

**Principle: establish a positive control before trusting any negative.**
We know LKA works on the vehicle. So the first goal is to reproduce *LKA* by
spoofing. Until spoofed LKA produces steering torque, a "no torque" result for
LCA means nothing — it could equally be a bad checksum, bus contention, or an
unmet precondition.

## What the DBC tells us (this shrank the problem a lot)

`CM_ SG_ 165` states each checksum's scope explicitly:

```
LaActvStats_No_Cs    (47|8  -> byte 5)  protects
    LdwActvIntns_D_Req  (9|2)   LdwActvStats_D_Req (14|3)
    LkaActvStats_D_Req  (30|3)   <-- the LKA/LCA field
LaActvReq_No_RollCnt (55|4 -> byte 6 high nibble) counts that same group

LaStePar_No_Cs       (23|8  -> byte 2)  protects
    LaRampType_B_Req (31|1)  LaRefAng_No_Req (27|12)  LaCurvature_No_Calc (51|12)
```

Two independent groups. The LKA/LCA state and the physical steering command
are protected **separately** — so a spoof must satisfy both.

The groups interleave inside bytes 3 and 6, so a byte-wise checksum must
either cover whole bytes (including foreign bits) or operate on extracted
signal values. `solve_checksum.py` searches both families.

## Stage 1 — passive observation (zero risk, do first)

```bash
python3 work/vehicle/lane_observe.py --selftest
python3 work/vehicle/lane_observe.py --iface can0 --csv run.csv
```

Read-only. The PSCM publishes its own verdict on `0x140`:

| `LaActAvail_D_Actl` | meaning |
|---|---|
| 3 | LKA/LCA and LDW available |
| 2 | LCA/LKA available, LDW suppressed |
| 1 | LCA/LKA suppressed, LDW available |
| 0 | LCA/LKA and LDW suppressed |

If CV6T ever reports 2 or 3, **the module itself claims LCA capability** and
the hypothesis is refuted with no transmission at all.

Also answers: is there an IPMA on this vehicle (does `0x0A5` appear)?

## Stage 2 — checksum corpus (offline solving)

```bash
python3 work/vehicle/capture_lane.py --advice          # read this first
python3 work/vehicle/capture_lane.py --iface can0 --seconds 300 --out corpus.jsonl
# or, without python-can:
candump -l can0                                        # then:
python3 work/vehicle/capture_lane.py --from-candump candump-*.log --out corpus.jsonl

python3 work/vehicle/solve_checksum.py corpus.jsonl --stats   # variability first
python3 work/vehicle/solve_checksum.py corpus.jsonl           # then solve
```

**Corpus quality beats corpus size.** The solver can only pin a checksum down
if the protected bytes vary. Thousands of idle frames constrain nothing; a few
hundred varied ones solve it. Ideally capture while the camera actually
engages LDW/LKA, so `LkaActvStats_D_Req` and the rolling counter move.

## Stage 3 result: the PSCM itself suppresses lane assist

The spoof is **working** — but the wheel cannot move, and the PSCM says why.

`work/vehicle/precondition_check.py` decodes `LaActAvail_D_Actl` (59|2@0+ on
`0x140 PSCM_h_FrP01`), the PSCM's own verdict on whether lane assist is
available. Measured on **both** captures:

| capture | camera | `LaActAvail_D_Actl` | speed |
|---|---|---|---|
| `candump-2026-09-14_111406.log` | **real, working** | **0** — LKA/LCA *and* LDW suppressed | 0.00 kph |
| `corpus.jsonl` | real, suppressed | **1** — LKA/LCA suppressed, LDW available | (no 0x1E0) |

`LaActDeny_B_Actl = 0` in both — this is not an active denial, it is simply
**not available**.

Decoded independently by `lane_observe.py` (different code path, same DBC
geometry) to guard against a decoding error: agreement confirmed.

### What this means

The dashboard *did* show the yellow LKA line, which proves our `0x0A5` is
accepted — correct checksums, correct rolling counter, no contention. But
`LaActAvail = 0` was the PSCM's verdict **with the real camera working and the
vehicle stationary**, before any spoofing. So:

> **No camera message can move the wheel in this state.** The precondition
> fails inside the PSCM, not on the bus. Stationary spoofing cannot produce
> steering torque, and a null result here says nothing about LKA vs LCA.

This is a *measured* null, not a mysterious one — and it was visible in data
captured before the first spoof attempt.

### Consequence for the experiment

The stationary positive control **cannot succeed** and must be abandoned. The
LKA-before-LCA logic still stands, but it has to run where
`LaActAvail_D_Actl` reads 2 or 3 — i.e. while driving on a lane-marked road.

Check it costs nothing:

```bash
python3 work/vehicle/precondition_check.py --iface can0 --seconds 20
```

Run it *while driving* with the real camera. If `LaActAvail` never leaves 0/1,
lane assist is unavailable on this vehicle for a reason unrelated to the
camera, and the whole spoofing approach needs rethinking. If it reaches 2 or 3,
that is the condition under which the spoof must be armed.

Corpus: 14 074 × `0x0A5`, 14 139 × `0x140`, 28 269 × `0x010` over 283 s
(stationary, camera reporting *suppressed*).

Measured cycle times — `0x0A5` **20 ms**, `0x140` **20 ms**, `0x010` **10 ms**
(the emulator's assumed 20 ms for `0x0A5` was right; `0x1B5`/`0x298` were not
captured and remain assumptions).

Only the rolling counter varied, yet that was enough:

```
LaActvStats_No_Cs (byte 5):  cs = (-(b3 + b6) + 0x75) & 0xFF        BYTE-sum
LaStePar_No_Cs    (byte 2):  cs = (-(nib(b3) + nib(b6)) + 0x06) & 0xFF  NIBBLE-sum
                              where nib(x) = (x>>4) + (x&0xF)
```

Both reproduce **all 14 074 frames with zero mismatches** (asserted in
`ipma_spoof.py --selftest`).

### How the corpus gave this up despite being nearly constant

`LaStePar_No_Cs` protects only `LaRampType`/`LaRefAng`/`LaCurvature` — all
constant here — yet byte 2 took 16 distinct values, one per counter step. The
counter is not in that signal group, so the checksum must be **byte-oriented**,
covering byte 6 whole (where `LaCurvature`'s high nibble shares a byte with the
counter). That ruled out the signal-value family immediately.

The per-step slopes then separated the two algorithms:

| checksum | slope per counter step | implies |
|---|---|---|
| byte 5 | `-0x10` | counter enters as the whole byte (`cnt<<4`) → byte-sum |
| byte 2 | `-0x01` | counter enters as a nibble → nibble-sum |

**They are different algorithms** — an earlier version of the spoofer applied
one rule to both fields, which would have produced a permanently invalid
`LaStePar_No_Cs`. The corpus-backed self-test caught it.

### The residual uncertainty, stated precisely

Byte 3 was **constant** (`0x78`) in the corpus, and byte 3 is what the
experiment changes. 16 byte-subset hypotheses fit group A and 32 fit group B;
the ones above are the minimal members. Any rule differing only in *constant*
bytes (0, 1, 4, 7) is indistinguishable here — those terms fold into the
constant `k`.

Critically, the chosen rules **do** depend on byte 3 (self-tested), so they
make a real prediction when `LkaActvStats_D_Req` changes. If the prediction is
wrong the PSCM rejects the frame and LKA will not engage — so a **working LKA
spoof simultaneously validates the checksum**. The positive control is
self-certifying; try the alternates from `solve_rollcnt_cs.py` if the first
fails.

## Stage 3 — silence the real IPMA, then spoof LKA (the positive control)

### Silencing the camera

`work/vehicle/silence_ipma.py` holds the IPMA in a **programming** session,
which stops its application broadcasts:

```
TST_PhysicalReqIPMA   0x706   (tester -> IPMA)
TST_PhysicalRespIPMA  0x70E   (IPMA -> tester)

10 02    programmingSession        <-- this is what quiets the module
3E 80    testerPresent, every 2 s  <-- keeps S3 from expiring
11 01    hardReset on exit
```

> **Two corrections, both learned the hard way.**
>
> 1. **ID:** an early draft said `0x7A6`. Wrong — the DBC gives
>    `BO_ 1798 TST_PhysicalReqIPMA` = **0x706**, `BO_ 1806` = **0x70E**.
> 2. **Session:** the first working version sent `10 03`
>    (extendedDiagnostic). The camera answered `50 03` — and kept
>    transmitting. `10 03` does not stop broadcasts.
>
> This was already documented in this repo. `BCM/Research/work/flash/bcmflash.py`
> (`class BusQuiet`) records, measured on the vehicle:
>
> * a module in **programmingSession stops** its normal application frames;
> * **TesterPresent quiets nothing** — it only refreshes S3 on a module that is
>   *already* in a non-default session, which is what makes the quiet persist;
> * an earlier `10 03 + 85 02 + 28 03 01` attempt **did not work**, and because
>   every request carried the suppress bit, the failure was invisible.
>
> Lesson: check the sibling project's measured findings before designing a new
> procedure against the same bus.

**Physical, not functional.** `bcmflash.py` broadcasts on `0x7DF` because it
wants the whole bus quiet. Here we need the opposite — the PSCM must keep
sending `0x140` and the BCM must keep driving the cluster — so the request is
addressed **physically to the camera only**. A bonus: a physical request is
answered, so unlike the functional broadcast this tool can *confirm* the
session was entered rather than hoping.

```bash
python3 work/vehicle/silence_ipma.py --selftest
python3 work/vehicle/silence_ipma.py --iface can0 --dry-run
python3 work/vehicle/silence_ipma.py --iface can0 --execute
```

Safety, asserted by the self-test (it drives the real transmit paths through a
fake socket and inspects every byte):

* Sends **only** `0x10`, `0x3E`, `0x11`. Never `27` (security), `31` (routine),
  `34/36/37` (download), `2E` (write) — without those nothing can be erased or
  programmed, even though the module is in its bootloader.
* Triple recovery: `11 01` hardReset on exit, S3 timeout ~5 s after the last
  TesterPresent, and an ignition cycle.
* Refuses to continue if the camera does not answer (exit 3) or rejects the
  session (exit 4).
* Retries the session request 3× — a first request on an idle bus is often
  lost (the same reason `bcmflash.py` repeats its functional arm 20×).

Expect DTCs and possibly a cluster warning while held; they clear after a
normal drive cycle.

**Then verify** before spoofing:

```bash
python3 work/vehicle/lane_observe.py --iface can0 --seconds 10
```

`0x0A5` must read **0 frames**. If it does not, the session did not take and
two senders would contend — stop and reassess. If `10 02` is refused with
NRC 22 `conditionsNotCorrect`, try with the vehicle stationary and the engine
off; some modules refuse programming while they see valid road input.

Run the silencer in one terminal and the emulator in another; the silencer must
keep running for the whole test.

### Spoofing ALL IPMA messages, not just 0x0A5

The IPMA transmits three functional messages, and the dashboard needs all of
them:

| ID | Message | Receiver | Why it matters |
|---|---|---|---|
| `0x0A5` | `IPMA_h_FrP01` | **PSCM** | the steering request itself |
| `0x1B5` | `IPMA_h_FrP02` | **BCM** | drives the cluster lane indicators |
| `0x298` | `IPMA_h_FrP00` | BCM/HCM | camera health; absence ⇒ "camera fault" |

### The IPMA sends exactly three IDs — measured, not assumed

`work/vehicle/find_ipma_ids.py` diffs a capture across a silence window
(camera working → silenced → working). Any ID present before and after but
absent during is the camera's:

```
    ID   before   during    after
 0x0a5      141        0       90     20.0 ms
 0x1b5       71        0       45     40.1 ms
 0x298       71        0       45     40.1 ms

70 other IDs unaffected — other modules kept transmitting, as intended
```

This is ground truth for *this* vehicle and confirms the message list was
right. Two assumptions were wrong, though:

* **cycle time 40 ms, not 100 ms** for `0x1B5`/`0x298`;
* the **payloads** were badly wrong (below).

### Why the first spoof produced an IPMA malfunction

`0x1B5` and `0x298` were synthesised from DBC defaults. Compared against the
real frames:

```
0x1B5  real  000002D7FAD0FFFC
       mine  0EC00001F0100000     ALL EIGHT bytes wrong, 17 signals differ
0x298  real  00000019D82B0000
       mine  0000001940000000     5 signals differ
```

Zeroing is not neutral — it is a positive assertion. `TsrVLim1_D_Dsply` = 255
("unknown") became 0, a *valid* speed limit; `TsrFusionStat_D_Dsply_UB` = 1
became 0, marking the field invalid. The BCM saw an incoherent camera,
declared it faulty, and suppressed lane assist.

Root cause: the stage-2 capture used the default
`--ids 0x0A5,0x140,0x010`, so **no real sample of `0x1B5`/`0x298` existed**.

**Lesson: when emulating a device, replay what it actually sent. Defaults are
not neutral.**

### Fix: replay real frames

`--template` accepts either a `capture_lane.py` corpus **or a raw candump log**
and starts every frame from the camera's own bytes, overwriting only the few
fields the experiment needs. Cycle times come from the template. The tool
refuses to run (exit 5) if `0x1B5`/`0x298` are missing.

```bash
# 1. invisibility control -- verbatim replay, changes nothing
python3 work/vehicle/ipma_spoof.py --template candump-....log --mode replay --arm

# 2. only if the malfunction is gone: the lane-assist command
python3 work/vehicle/ipma_spoof.py --template candump-....log --mode lka-left --arm

# 3. last, if the cluster indicators are wanted
python3 work/vehicle/ipma_spoof.py --template candump-....log --mode lka-left \
        --drive-display --arm
```

`--mode replay` is byte-exact (self-tested): it ignores mode, rolling counter
and checksum arguments entirely. **If the malfunction persists under pure
replay, the problem is outside these three messages** — stop and investigate
rather than proceeding.

Dashboard-relevant signals in `0x1B5` (all set by the emulator):

```
LaLLineStats_D_Dsply / LaRLineStats_D_Dsply  2 = "Not overridable" (lines seen)
LkaVLvl_B_Dsply / LcaVLvl_B_Dsply / LdwVLvl_B_Dsply  1 = in operating speed range
LaMenuEnbl_B_Actl 1, LkaMenuStats_B_Actl 1, LcaMenuStats_B_Actl 1
```

plus `0x298` `CamraStats_D_Dsply = 0` ("Front Camera OK").

**The cluster is a second, independent readout.** If the lane graphic appears,
the spoof is being accepted bus-wide — not just by the PSCM. That is a far
stronger positive control than torque alone.

### Running it

```bash
python3 work/vehicle/ipma_spoof.py --selftest
python3 work/vehicle/ipma_spoof.py --mode lka-left --dry-run
python3 work/vehicle/ipma_spoof.py --mode lka-left --arm    # solved rules are the defaults
```

The solved rules are now the defaults (`--cs-a`, `--cs-b` to override). The
tool still refuses to arm if either is cleared, because an invalid checksum is
silently dropped by the PSCM and is indistinguishable from "the feature does
not exist". It has a dead-man timeout and sends idle frames on exit.

Success = cluster shows lane assist active, and/or measurable steering torque.
**Until this passes, no negative result is interpretable.**

## Stage 4 — the actual experiment

With LKA spoofing confirmed, change **one variable**:
`LkaActvStats_D_Req` 2/4 → 6 ("LCA in Progress"). The self-test proves the two
frames differ in exactly one byte (byte 3), so nothing else can confound it:

```
lka-left : 0x0A5 = 000C002800000800
lca      : 0x0A5 = 000C006800000800
                         ^^ only this byte differs
```

| Result | Conclusion |
|---|---|
| LKA (2/4) acts, LCA (6) does not | **Hypothesis confirmed** — CV6T ignores LCA |
| Both act | Hypothesis refuted |
| Neither | Test invalid — stage 3 control failed |

Repeat on a BV6T module for a true A/B.

## Safety

EPS applies real torque to the steering. First attempt: engine off, hands
clear of the rim, ready to cut ignition. The user runs all vehicle commands.

## Status

| Tool | State |
|---|---|
| `lane_observe.py` | ready, 13 self-tests pass |
| `capture_lane.py` | ready, 5 self-tests pass |
| `solve_checksum.py` | ready, 6 self-tests pass |
| `solve_rollcnt_cs.py` | ready, 6 self-tests pass |
| `silence_ipma.py` | ready, 12 self-tests pass — 10 02 programmingSession, physical, no program/write services |
| `ipma_spoof.py` | ready, 25 self-tests pass — checksums verified on 14074 real frames; requires --template for 0x1B5/0x298 |

Open unknowns, to be filled from a real capture:

* **0x1B5 / 0x298 cycle times** — not in the corpus (only 0x0A5/0x140/0x010
  were captured); still assumed 100 ms. Capture them with
  `capture_lane.py --ids 0x1B5,0x298` before relying on the emulator's timing.
* **0x1B5 / 0x298 checksums** — neither message declares one in the DBC, but
  confirm against captured frames before trusting the emulator's output
* whether lane assist has a **road-speed precondition** that no amount of
  correct spoofing will bypass while stationary (if so, spoofing `0x1E0`
  vehicle speed would be the next lever — it has its own checksum,
  `VehicleSpeedCS`, "Convention A (EuCD)")
