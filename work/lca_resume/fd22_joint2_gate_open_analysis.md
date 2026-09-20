# FD22 joint2 — gate-open road result

## Proven installed image and flash completion

Flash trace:

```text
/home/gl/Projects/ford/PSCM/Research/lca_gate_fd22_flash.log
SHA-256 268d7c2457829af6fea678a4a9e1a001b47b74a53c16aae00433624bfaeaadfe
```

The trace recorded the required live identities before programming:

```text
F188 = CV6T-14C217-AR
F124 = CV6T-14C218-AX
F111 = BV6T-14C262-AA
```

All three erase operations completed positively, all three application blocks completed TransferExit, finalise `31 01 0304` returned positive `71 01 0304 10 02`, and reset `11 01` returned positive `51 01`.

## Capture provenance

```text
fd22_joint2_internal.csv  SHA-256 65476b95379f4850892727f167c001ca1734c0f3342a9f58c302680a605a1724
fd22_joint2_can.csv       SHA-256 a754079e8e9b4b7d96c970f823f5fc9035b31cd233c8f34310a911e098514e2d
fd22_joint2_can.jsonl     SHA-256 ca8f6e86a7ecb0320b962c1f3f1e46415b1ae8e4e77ff8dd56cdb1de798b4405
fd22_joint2_can.log       SHA-256 f59aa536d835b721b22e6ed2e2abbc9bbef17fa0d1ecc1408fb99a875a74a266
```

Joint interval:

```text
2026-09-20T06:59:17.481846Z .. 2026-09-20T07:09:15.766545Z
598.284699 seconds
```

Integrity:

- FD22: 6,000/6,000 status `ok`; no timeout/error rows.
- Joint interval: 5,979 FD22 samples.
- Raw `0x0A5`: 29,872 frames total.
- Median nearest-prior `0x0A5` age at FD22 sample: 9.98 ms; maximum 22.25 ms.

## LKA positive controls

Raw request 2 occurred in three sustained episodes; raw request 4 occurred in three sustained episodes.

Stable samples more than 0.3 seconds from transitions:

| Raw `0x0A5` request | Meaning | Samples | Internal state | Internal code | Accumulator nonzero |
|---:|---|---:|---:|---:|---:|
| 2 | LKA-left | 93 | 1 | 1 | 93/93 |
| 4 | LKA-right | 93 | 2 | 1 | 93/93 |

This definitively corrects the earlier static labels: active LKA uses internal code 1 in these live captures. Code 2 appears transiently after the active request, not as the sustained active-LKA code.

## Gate-open LCA result

Raw request 6 occurred in eleven episodes. Ten had usable FD22 overlap, including sustained episodes of approximately:

```text
9.963 s, 4.980 s, 3.676 s, 23.721 s, 8.617 s,
1.808 s, 2.712 s, 5.623 s, 11.730 s, 16.369 s
```

Across the complete joined population:

```text
raw request 6 -> internal state 4/code 0: 896 samples
raw request 6 -> internal state 0/code 0:   1 transition sample
```

For 832 stable request-6 samples more than 0.3 seconds from transitions:

```text
X:$2DDE lane state         = 4 in 832/832
X:$2DB9 per-state code     = 0 in 832/832
X:$2D53 accumulator        = 0 in 832/832
X:$2D52 unclassified cell  = 0 in 832/832
X:$2D54 intermediate       nonzero in 832/832, range -2184..2632
```

## Conclusions

### First gate conclusively bypassed

The patch had exactly its intended runtime effect:

```text
stock FD22 capture:
raw request 6 -> X:$2DDE = 0

gate-open FD22 capture:
raw request 6 -> X:$2DDE = 4
```

Therefore the OEM `X:$0904` guard was rejecting request 6 in the stock image. The earlier inference that it was already open is retracted.

### A second blocker is now proven

State 4 alone is not sufficient. Despite sustained `X:$2DDE = 4`, the downstream state machine leaves `X:$2DB9 = 0` and never creates a lane torque accumulator value.

The remaining divergence is now tightly localized:

```text
X:$2DDE = 4
    -> four LCA consumer arms at P:$2ABF7/$2AC76/$2ACAE/$2ACEA
    -> expected LCA state-machine code
    -> X:$2DB9 remains 0
```

The common additional condition at all four LCA consumer arms is `X:$2DC1`:

```text
P:$2ABFB  TST.W X:$2DC1
P:$2AC7A  compare X:$2DC1 with 1
P:$2ACB2  compare X:$2DC1 with 1
P:$2ACEE  compare X:$2DC1 with 1
```

`X:$2DC1` is therefore the leading second-blocker candidate, but it must be observed before patching. Other state-machine preconditions exist, and the earlier static claim that its initializer makes it permanently 1 has not been validated in the running application.

## Next narrow step

Revise the FD22 observation payload to include at least:

- `X:$2DC1` — common LCA consumer gate;
- `X:$2DC3` — nearby shared state-machine precondition;
- `X:$2DAF` — state-machine phase selected by the consumers;
- retain `X:$2DDE`, `X:$2DB9`, and `X:$2D53`.

Do not bypass `X:$2DC1` yet. If it is zero throughout state-4 episodes while LKA positive controls remain valid, trace its live owner/source first; only then decide whether enabling it or correcting its source is appropriate.
