# PSCM LCA enablement — shared briefing

## The question

The vehicle's PSCM shows **LCA on the dashboard but applies no steering
torque**. We now know the camera side is fine, so the question is entirely
about the PSCM firmware:

> Does the CV6T PSCM firmware contain code that tests the extracted
> `LkaActvStats_D_Req` enum for the value **6 (LCA in Progress)** and branches
> away from the torque path — while values **2 / 4 (LKA Intervention
> Left/Right)** proceed?

If such a test exists, LCA may be enableable by patching. If the LCA torque
path genuinely is not in the build, it is not.

## What is already ESTABLISHED (do not re-derive)

### The IPMA sends real LCA commands — measured on the road

From an instrumented drive (`drive1.jsonl`, 141 097 samples), grouped by
`LkaActvStats_D_Req`:

| state | n | `LaRefAng_No_Req` range | non-zero |
|---|---|---|---|
| 2 LKA Interv LEFT | 562 | −13.40 … +5.00 mRad | 87.4 % |
| 4 LKA Interv RIGHT | 1018 | −3.70 … +25.50 mRad | 86.9 % |
| **6 LCA in Progress** | **1114** | **−4.40 … +9.20 mRad** | **97.8 %** |

Per-episode character:

```
LKA LEFT    4 episodes  mean 2.80 s  mean angle range  9.32 mRad
LKA RIGHT   6 episodes  mean 3.39 s  mean angle range 11.40 mRad
LCA         3 episodes  mean 7.44 s  mean angle range  6.67 mRad   (one ran 10.42 s)
```

LCA episodes are **twice as long with a smaller angle range** — smooth
continuous guidance, exactly the signature of lane centering. The camera is
computing and transmitting genuine LCA steering requests.

### The enum

`LkaActvStats_D_Req`, DBC geometry `30|3@0+` → **byte 3, shift 4, mask 0x70**.
`VAL_TABLE_ LaLkaLcaActiveState`:

```
0 LKA Idle            1 LKA Idle / LCA Suppressed
2 LKA Interv Left     3 LKA Suppr Left
4 LKA Interv Right    5 LKA Suppr Right
6 LCA in Progress     7 LKA/LCA Suppressed Both
```

**Note the bit structure:** 2 = `010`, 4 = `100`, 6 = `110`. LCA is exactly
"left bit OR right bit" — bit1 and bit2 both set. A test for LCA could be
`== 6`, or `(v & 6) == 6`, or a bit-pair test — not necessarily a literal 6.

### Where the value lands in RAM

Both builds extract it identically (spec word `0x7004`, twice, two
destinations):

| | BV6T-14C217-AF | CV6T-14C217-AR |
|---|---|---|
| dest 1 | `X:$7EDF` | `X:$7ED7` |
| dest 2 | `X:$8628` | `X:$863C` |

### What has already been RULED OUT (five layers, all negative)

1. CAN/signal configuration (`14C386`) — lane-assist config bit-identical.
2. Field-extraction records — same spec `0x7004`, same count, both builds.
3. Absolute references to the lane RAM cells — **0 in both** after opcode
   filtering. The cells are reached only via pointer/indexed addressing.
4. Code shape — CV6T is a strict **superset** (+52 structs, +185 functions,
   +43 % calibration curves). Feature removal predicts the opposite.
5. `14C218` calibration anchors — 5 parameters at identical addresses, none
   lane-related.

> **Two documented false positives.** (a) "57 references in BV6T vs 0 in CV6T"
> — bare 16-bit words that merely *equalled* `0x7EDF`; in 512 KB any value
> occurs ~8× by chance. (b) `LkaActvStats` appearing BV6T-only in the
> extraction table — an artefact of slot-bit reuse across descriptor groups.
> **A value match is not a cross-reference.** Require an actual
> absolute-addressing opcode in the preceding word.

## Target images

```
bins/CV6T-14C217-AR/    main firmware, THE CURRENT BUILD ON THE VEHICLE
bins/CV6T-14C217-AH/    same family, older — use as a same-family CONTROL
bins/BV6T-14C217-AF/    the build believed to support LCA
bins/CV6T-14C218-AX/    CV6T calibration      bins/BV6T-14C218-AF/
bins/CV6T-14C386-AB/    CV6T signal config    bins/BV6T-14C386-AA/
```

**Critical control:** same-family CV6T-AH vs CV6T-AR differs **74.5 %** by raw
word diff, versus 74.6 % cross-family. Recompilation noise dominates, so
**raw diffing proves nothing.** Any claimed BV6T/CV6T difference must also be
shown ABSENT between AH and AR, or it is noise.

## Architecture and tooling

Freescale **MC56F8366**, **DSP56800E** core, **word-addressed** (an address of
`N` refers to word `N`, byte `2N`). Big-endian words.

`work/disasm/flow56800e.py` — recursive-descent disassembler, 20 self-tests,
already validated on this image. Read it before writing anything new.

```bash
python3 work/disasm/flow56800e.py --selftest
python3 work/disasm/flow56800e.py --func 0x19DD5 --max 60     # disassemble
python3 work/disasm/flow56800e.py --find-imm 0x7004           # find immediates
python3 work/disasm/flow56800e.py --xref-x 0x7ED7             # X-space refs
python3 work/disasm/dis_at.py                                 # arbitrary address
```

Other useful tools: `extraction_table.py` (per-signal extraction records),
`lane_consumer.py` (RAM-cell X-refs, opcode-filtered), `struct_profile.py`
(pointer-base struct counts), `signal_config.py` (CAN descriptors).

**Word-address trap:** on this core, searching for a byte address finds
nothing — grep for `addr/2`. This is what finally located the PSCM's
self-check after byte-address searches failed for a whole round.

## Rules of engagement

* **Read-only.** Never modify a VBF or a `bins/` file. Deliverable is a report.
* Ground every claim in an address plus the command that produced it.
* **Never fabricate disassembly.** A well-supported "not found" is a real
  result; invented instructions are worse than nothing because they would
  justify a flash of a steering controller.
* Apply the AH-vs-AR control to every candidate difference.
* stdlib python3 (`/usr/bin/python3` is 3.14); a venv is fine for your tooling.
* Work under `/home/gl/Projects/ford/PSCM/Research/work/`.

## Safety context

The PSCM applies **real steering torque**. Any eventual modification is far
higher risk than the IPMA calibration work. Nothing here will be flashed on the
strength of a plausible-looking finding alone — the bar is a traced,
disassembled code path, corroborated across builds.
