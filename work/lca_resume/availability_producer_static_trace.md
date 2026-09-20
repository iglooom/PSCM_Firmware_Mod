# Availability producer static trace and joint7 observation

## Correct packed-byte interpretation

The field record is phase-aligned at `X:$419A`, not at `X:$4198`:

```text
record X:$419A
+0  0000          secondary source
+1  0C02          mask 0x0C, right shift 2
+2  0002          frame-slot bit 0x02
+3  7EBB          packed-byte pointer
+4  7F74          area/base
```

`0x7EBB` is a **packed byte pointer**, not an X-word address:

```text
0x7EBB = 2 * X:$3F5D + 1
```

It therefore selects the high byte of `X:$3F5D`, which is raw frame-`0x140`
byte 7. Mask `0x0C`, shift 2 is exactly `LaActAvail_D_Actl`. Joint6 read
`X:$7EBB` as a word address, explaining the constant `0xFFFF`; that result did
not invalidate the correctly phase-aligned record.

## Setter path

The only wrapper loading `&record[+3] = X:$419D` is:

```text
P:$0FF2C  MOVE.L #$419D,R2
P:$0FF2E  ZXT.B   ...
P:$0FF2F  JSR     P:$31147
```

It is called by the frame-status composer at `P:$1DB90`. The composer obtains
three status values from `X:$2250..X:$2252` immediately before packing the
frame.

The producer copy is explicit:

```text
P:$2B8DA  X:$2D28 -> X:$2252
P:$2B8DD  X:$2DB8 -> X:$2251
P:$2B8E0  X:$2DAA -> X:$2250
```

## Candidate availability enum

`P:$2B8A2..P:$2B8CC` computes `X:$2D28` from two predicates:

```text
A = 1 when X:$2DB9 is one of {2, 3, 5}, else 0
B = 1 when X:$2DBA is one of {2, 3},    else 0

X:$2D28:
  A=1 B=1 -> 0
  A=1 B=0 -> 1
  A=0 B=1 -> 2
  A=0 B=0 -> 3

Equivalent: 3 - 2*A - B
```

This explains the observed LCA withdrawal without invoking another torque
limiter:

```text
LCA reaches phase 6 / per-state code 5
 -> A becomes 1
 -> when B remains 0, X:$2D28 becomes 1
 -> candidate mirror X:$2252 becomes 1
 -> transmitted availability is expected to become 1
 -> IPMA withdraws request 6
```

The key distinction is that code `5`, which denotes the reached LCA phase in
live captures, is explicitly included in the availability-degrading set.
Conventional sustained LKA uses code `1`, which is not included.

This is a strong static trace, but the exact `X:$2D28 -> CAN` temporal binding
is left for one passive runtime observation before any predicate is changed.

## Joint7 firmware

```text
CV6T-14C217-AR_LCA_AVAIL_PRODUCER_TRACE.VBF
SHA-256 db35a62e2fe7f79e1e3f331808a4c6ee50826db5c68e404828eb4aa3bf40bae2
```

FD22 format 5 observes:

```text
X:$2DDE  lane state
X:$2DB9  per-state code / predicate A input
X:$2D53  torque accumulator
X:$2DAF  state-machine phase
X:$2D28  candidate availability enum
X:$2252  output-stage mirror
X:$2DBA  predicate B input
```

The image retains only the two previously proven control bypasses. It adds no
write to availability, status, torque, payload, or OEM runtime state.

## Offline verification

```text
Builder test:              PASS
Format-5 decoder test:     PASS
Builder self-test:         PASS
Logger self-test:          PASS
Independent verifier:      PASS
Container/block/file CRCs: PASS
Internal checksum A/B:     7AB1 / 4790 PASS
Block-1 CRC-16:            5590 PASS
VBF file checksum:         254C94F3 PASS
Payload differences:       139 bytes, all classified
VBFlasher verify/dry-run:  PASS
```

## Acceptance test

For synchronized samples, require:

1. `X:$2D28 == X:$2252` after allowing only short copy-boundary transitions;
2. `X:$2D28 == LaActAvail_D_Actl` across sustained values and transitions;
3. the four-case truth table above holds for every sampled
   `(X:$2DB9, X:$2DBA, X:$2D28)` tuple;
4. phase 5/code 4 and phase 6/code 5 still show nonzero torque accumulator;
5. conventional LKA code 1 remains the positive control if captured.

Only after those checks pass should the code-5 availability decision be
considered for a narrowly scoped modification. No global availability force is
justified.
