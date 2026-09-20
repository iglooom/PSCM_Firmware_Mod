# FD22 joint8 — code-5 availability patch result

## Result

**The code-5-only availability patch worked.** It broke the previous
availability-withdrawal feedback loop without globally forcing availability and
without changing conventional request-2/request-4 behavior in this capture.

The former behavior was:

```text
request 6 -> phase 5/code 4/availability 3
          -> phase 6/code 5/availability 1
          -> IPMA withdraws request 6
```

Joint8 produced:

```text
request 6 -> phase 5/code 4/availability 3
          -> phase 6/code 5/availability 3
          -> request 6 remains sustained
```

## Capture quality

```text
FD22 format-5 rows:        3,288
valid rows:                3,288 / 3,288
internal duration:         329.088 s
raw frame-0x140 samples:   16,451
joint CAN/FD22 overlap:    327.504 s
matches within 30 ms:      3,272
median alignment age:      4.925 ms
maximum alignment age:     10.979 ms
```

Producer/mirror agreement remained exact:

```text
X:$2D28 == X:$2252: 3,288 / 3,288
```

Against raw CAN:

```text
all synchronized:          3,267 / 3,272
stable surrounding values: 3,223 / 3,223
```

The five all-sample mismatches were transition-alignment artifacts. There were
no disagreements where the internal and surrounding CAN values were stable.

## LCA path

| Phase/code | Samples | Availability | Nonzero accumulator |
|---|---:|---|---:|
| 5 / 4 | 255 | 3 in 255/255 | 247/255 |
| 6 / 5 | 448 | 3 in 448/448 | 203/448 |

The decisive synchronized phase-6/code-5 result was exact:

```text
internal availability = 3: 448 / 448
raw CAN availability = 3:  448 / 448
internal == CAN:            448 / 448
auxiliary predicate = 0:    448 / 448
```

Zero accumulator values during some phase-6 samples are normal zero-command
samples, not rejection: the same sustained episodes also contained nonzero
values ranging from -19 to +31, and all phase-5/code-4 samples except eight had
nonzero accumulation.

## Request-6 persistence

Nine request-6 episodes were observed. Two were one-frame transition episodes;
the seven substantive episodes lasted:

```text
0.825 s
1.828 s
4.177 s
9.461 s
18.740 s
19.182 s
39.066 s
```

Overall request-6 duration statistics, including the two one-frame events:

```text
minimum: 0.020 s
median:  4.177 s
maximum: 39.066 s
```

This is qualitatively different from the unpatched joint7 capture, where
request-6 episodes lasted only 20–120 ms with a 59.9 ms median.

For every substantive episode body (excluding 100 ms at each transition), raw
availability was exclusively `3`. Across all request-6 frames, nearest-frame
correlation was:

```text
availability 3: 4,644
availability 1:     2  (transition alignment)
LaActDeny_B_Actl:   0 in every frame
```

The longest episode held request 6 for 39.066 seconds rather than entering the
approximately one-second retry loop seen before the patch.

## Positive controls

Conventional controls were present:

```text
request 2: two episodes, 3.755–3.836 s
request 4: two episodes, 3.757–3.856 s
phase 2/code 1: 151 samples
phase 2/code 1 nonzero accumulator: 151 / 151
phase 2/code 1 availability 3:      151 / 151
```

No CAN-level denial was observed anywhere in the capture:

```text
LaActDeny_B_Actl = 0: 16,451 / 16,451 frame-0x140 samples
LaDenyStats_B_Dsply = 0: 8,187 / 8,187 frame-0x1B5 samples
```

The LCA display level was active in 3,562 of 8,187 display-frame samples.

## Boundaries

The logs prove the request/availability handshake and OEM torque-state path are
now sustained. They do not establish subjective steering quality or absence of
stored/actual PSCM DTCs; those require the driver's observation and a separate
DTC query. Auxiliary availability predicate `X:$2DBA` remained zero throughout
this particular capture, so its dynamic suppression behavior was not exercised,
although its code path and instruction bytes were retained unchanged.
