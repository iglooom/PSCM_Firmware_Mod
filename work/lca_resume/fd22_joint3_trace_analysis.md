# FD22 joint3 — LCA consumer entry trace

## Capture integrity

Files:

```text
fd22_joint3_internal.csv  c11b35f4052e2998bf5d8a5e2615f3cabcd0b892334d4238bd76696ba88ff6a7
fd22_joint3_can.csv       41122bed275ca242b2dd808023f0696ef4a65b0240af881bf5aeb38d9eaf1695
fd22_joint3_can.jsonl     1003a82134a2fd16fa2be8983451257664074f1c094870cc28b4d483a62efbfe
fd22_joint3_can.log       65a57546c45975ca2c2556771095758f8b23bb78e5d522d6bf9400105105eb7a
```

- FD22 responses: 3,555/3,555 valid, all format 1.
- Internal duration: 355.660 s.
- Joint raw-CAN/FD22 overlap: 348.465 s.
- Matched FD22 samples within 30 ms of raw request: 3,483.
- Median nearest-frame age: 5.151 ms.

The shorter-than-commanded capture is complete enough for the test: it contains sustained left LKA, right LKA, and ten sustained LCA episodes.

## Positive controls

Stable conventional-LKA episodes reproduced the known working path:

```text
raw request 2 -> state 1, code 1, phase 2, nonzero accumulator
raw request 4 -> state 2, code 1, phase 2, nonzero accumulator
```

There were 117 request-2 samples and 74 request-4 samples within the 30-ms matching window. `X:$2DC1` was 1 and `X:$2DC3` was 0 for every active-LKA sample.

This proves that `X:$2DC3 = 0` is not by itself a general lane-torque blocker.

## LCA result

Ten sustained raw-request-6 episodes were captured. The longest lasted 30.148 s. Across the complete matched set:

```text
raw request 6 samples:                1,172
state 4 samples:                      1,171
state 0 transition samples:               1

all 1,171 state-4 samples:
    X:$2DDE lane state                = 4
    X:$2DB9 per-state code            = 0
    X:$2D53 torque accumulator        = 0
    X:$2DC1 consumer entry inhibit    = 1
    X:$2DC3 adjacent precondition     = 0
    X:$2DAF state-machine phase       = 1
    X:$2DB8 state-machine value       = 0
```

No state-4 sample advanced out of phase 1 or generated a nonzero accumulator.

## Exact static/runtime match

The phase-1 LCA entry arm is:

```text
P:$2ABF7  F07C 2DDE   MOVE.W X:$2DDE,A
P:$2ABF9  4C04        CMP.W  #4,A
P:$2ABFA  A20C        Bne    P:$2AC07
P:$2ABFB  FF7C 2DC1   TST.W  X:$2DC1
P:$2ABFD  A209        Bne    P:$2AC07
P:$2ABFE  E700        NOP
P:$2ABFF  4C83        CMP.W  #3,B
P:$2AC00  A206        Bne    P:$2AC07
P:$2AC01  E685 2DAF   MOVE.W #5,X:$2DAF
P:$2AC03  E684 2DB9   MOVE.W #4,X:$2DB9
```

With the observed runtime values:

```text
phase = 1
state = 4       -> first comparison passes
X:$2DC1 = 1     -> TST is nonzero; Bne jumps to P:$2AC07
```

The jump skips the phase-5/code-4 entry writes. The observed unchanged phase 1 and code 0 are therefore the exact outcome predicted by this branch.

`X:$2DC1` had previously been called a positive gate, but the entry arm proves its phase-1 meaning is inverted: zero permits LCA entry; nonzero inhibits it. Later sustained-state arms compare it with 1 and have different transition semantics, so the cell should not be globally forced to zero.

The only direct literal initialization found remains:

```text
P:$2AA6D  MOVE.W #1,X:$2DC1
```

The live trace confirms that it remains 1 throughout this drive.

## Conclusion

The first dispatcher patch works, and the next sufficient blocker is now proven:

```text
raw request 6
 -> dispatcher X:$0904 guard bypassed
 -> X:$2DDE = 4
 -> phase-1 entry consumer
 -> X:$2DC1 = 1
 -> branch P:$2ABFD -> P:$2AC07
 -> phase remains 1, code remains 0, torque remains 0
```

This is upstream of torque scaling or motor-current handling.

## Narrow next experiment

Do not globally overwrite `X:$2DC1`: later state-machine arms use value 1 while advancing sustained LCA. The minimally scoped experiment is to neutralize only the phase-1 entry test/branch at `P:$2ABFB..P:$2ABFD`, while retaining:

- the state-4 comparison;
- the following `B == 3` condition;
- every sustained-state condition;
- all torque, limiter, fault, and actuator logic;
- FD22 telemetry.

Expected outcome if the following `B == 3` condition is already satisfied:

```text
request 6 -> phase 1 -> phase 5/code 4 -> phase 6/code 5 -> torque path
```

If phase remains 1 after that single branch is neutralized, the preceding live-register condition or following `B == 3` comparison is another independently testable blocker.
