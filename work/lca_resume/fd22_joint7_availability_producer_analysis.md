# FD22 joint7 — availability producer confirmed

## Capture quality

```text
FD22 format-5 rows:       1,615
valid rows:               1,615 / 1,615
internal duration:        161.575 s
raw frame-0x140 samples:  8,085
joint CAN/FD22 overlap:   160.081 s
matches within 30 ms:     1,600
median alignment age:     5.331 ms
request-6 episodes:       13
request-6 duration:       20.0–120.0 ms; median 59.9 ms
```

The last 15 internal rows extended beyond the raw-CAN capture and are excluded
from synchronized comparisons.

## Internal producer chain

The producer and mirror agreed without exception:

```text
X:$2D28 == X:$2252:  1,615 / 1,615
```

Every internal sample also satisfied the statically derived truth table:

```text
A = X:$2DB9 in {2,3,5}
B = X:$2DBA in {2,3}
X:$2D28 = 3 - 2*A - B

truth-table agreement: 1,615 / 1,615
```

Observed tuples:

| `X:$2DB9` code | `X:$2DBA` auxiliary | `X:$2D28` availability | samples |
|---:|---:|---:|---:|
| 3 | 0 | 1 | 1,439 |
| 0 | 0 | 3 | 148 |
| 3 | 3 | 0 | 21 |
| 5 | 0 | 1 | 5 |
| 4 | 0 | 3 | 2 |

This independently exercises three output values (`0`, `1`, and `3`) and both
predicate inputs.

## Correlation to transmitted availability

Among all 1,600 synchronized samples:

```text
X:$2D28 == CAN LaActAvail_D_Actl: 1,596 / 1,600
X:$2252 == CAN LaActAvail_D_Actl: 1,596 / 1,600
```

The four disagreements occurred inside frame-to-frame `1 <-> 3` transitions.
Raw availability was changing every approximately 20 ms while FD22 sampled at
10 Hz. When both the internal enum and the surrounding raw frames were stable,
the result was exact:

```text
stable synchronized samples: 1,564
X:$2D28 == CAN:              1,564 / 1,564

confusion:
  internal 0 / CAN 0:   20
  internal 1 / CAN 1: 1,408
  internal 3 / CAN 3:  136
  all off-diagonal:       0
```

This closes the complete data path:

```text
X:$2DB9, X:$2DBA
 -> P:$2B8A2..P:$2B8CC
 -> X:$2D28
 -> P:$2B8DA
 -> X:$2252
 -> status-frame composer
 -> byte pointer 0x7EBB = high byte of X:$3F5D
 -> frame 0x140 byte 7 bits 3:2
 -> LaActAvail_D_Actl
```

## LCA transition that causes withdrawal

The two entry stages were observed directly:

| phase | code | availability | samples | nonzero accumulator |
|---:|---:|---:|---:|---:|
| 5 | 4 | 3 | 2 | 2/2 |
| 6 | 5 | 1 | 5 | 3/5 |

Thus the feedback drop is not caused by a later unknown CAN packer or a global
fault. The application deliberately includes per-state code `5` in predicate
A, changing availability from `3` to `1` as LCA advances from phase 5/code 4
to phase 6/code 5. IPMA then withdraws request 6.

## Narrow candidate modification

The availability predicate is implemented as:

```text
P:$2B8AA  load X:$2DB9
P:$2B8AC  CMP #2
P:$2B8AD  Beq P:$2B8B4
P:$2B8AF  CMP #5
P:$2B8B0  Beq P:$2B8B4
P:$2B8B2  CMP #3
P:$2B8B3  Bne P:$2B8B7
P:$2B8B5  A = 1
P:$2B8B7  A = 0
```

The narrow candidate is to neutralize only the code-5 branch at `P:$2B8B0`:

```text
A303  ->  E700
Beq P:$2B8B4 -> NOP
```

For code 5, execution would then continue to the retained code-3 comparison
and select `A=0`. This preserves:

- code-2 and code-3 availability behavior;
- auxiliary predicate `X:$2DBA` and output values 0/2;
- both proven LCA-entry bypasses;
- OEM torque limits, saturation, fault handling, and actuator path;
- all conventional LKA behavior.

This is substantially narrower than forcing availability to 3 globally. It is
still a control modification and requires a new independently verified image
and parked validation before a controlled road test.
