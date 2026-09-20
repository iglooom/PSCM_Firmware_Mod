# FD22 + raw CAN joint-road analysis

## Sources

```text
fd22_joint_internal.csv
SHA-256 62ec037bd6bbf8910c7d9a3b2659343cbbcff1ea490ab8b2fa3c03a3a243798b

fd22_joint_can.csv
SHA-256 2fac1d9c907a05ffd1c23bb38ac63c5b4912cab9ef95476fbbd7574e145fb134

fd22_joint_can.jsonl
SHA-256 5cea15dd70b5a558acc5915128547d3f04a9ce2f8db2e2d96a76f16b75d5e55a

fd22_joint_can.log
SHA-256 49449721579b92f97dac25e82a555b7ccce27f2c242338b3fe414ac26edbd9a8
```

The two loggers overlap from `2026-09-19T09:12:28.429029Z` through `2026-09-19T09:17:54.303202Z`, or `325.874173 s`.

## Integrity

- FD22: 5,319/5,319 valid responses over its complete run; no timeout/error rows.
- The exact overlap contains 3,255 FD22 responses.
- Raw CAN contains 29,871 `0x0A5` frames over its complete run and 16,224 during the overlap.
- Median age of the most recent `0x0A5` frame at each FD22 sample was approximately 16 ms.

## Decisive positive control

During the overlap, OEM frame `0x0A5` requested LKA-right (`LkaActvStats_D_Req = 4`) in two sustained episodes:

| UTC interval | Duration | FD22 samples | Internal result | Nonzero accumulator |
|---|---:|---:|---|---:|
| `09:15:07.347858–09:15:11.043357` | 3.695 s | 37 | state `2`, code `1` | 37/37 |
| `09:16:47.095025–09:16:49.585737` | 2.491 s | 25 | state `2`, code `1` | 25/25 |

Across all 62 samples, raw request `4` mapped consistently to internal state `2`, code `1`, and a nonzero `X:$2D53` torque accumulator. This validates all of the following on the running application:

1. raw `0x0A5` decode and absolute-time joining;
2. the lane-state runtime address `X:$2DDE`;
3. the per-state runtime address `X:$2DB9`;
4. the accumulator address `X:$2D53`;
5. the dispatcher is executing and accepting an ordinary LKA request.

It also corrects the earlier oversimplified state mapping: in this capture, right-LKA request `4` maps to internal state `2`, code `1`; it does not map to state `1`, code `2`.

For stable request-4 samples more than 0.3 s from transitions:

```text
X:$2D53 accumulator: 50/50 nonzero, range -792..2452, median 675
X:$2D54 upstream:    50/50 nonzero, range -964..2452, median 836
```

`X:$2D52` remained zero even during this accepted LKA positive control. It must therefore not be treated as a proven final actuator-output value; its earlier label is demoted pending reclassification. This does not weaken the state-store result because `X:$2DDE`, `X:$2DB9`, and `X:$2D53` all respond coherently to request 4.

## Decisive LCA result

During the same overlap, OEM `0x0A5` requested LCA (`LkaActvStats_D_Req = 6`) in three sustained episodes:

| UTC interval | Duration | FD22 samples | Internal result | Nonzero accumulator |
|---|---:|---:|---|---:|
| `09:15:04.615981–09:15:07.327763` | 2.712 s | 28 | state `0`, code `0` | 0/28 |
| `09:16:34.180124–09:16:41.691600` | 7.511 s | 75 | state `0`, code `0` | 0/75 |
| `09:16:41.953443–09:16:47.075014` | 5.122 s | 51 | state `0`, code `0` | 0/51 |

All 154 in-episode samples remained internal state `0`, code `0`, with zero `X:$2D53` accumulator and zero `X:$2D52` result. Stable samples more than 0.3 s from transitions give the same result in 136/136 cases.

At the same time, `X:$2D54` remained live and nonzero in all 136 stable request-6 samples (`-1592..2256`). The FD22 reader was therefore not frozen.

## Proven divergence

```text
Positive control:
raw 0x0A5 request 4 (LKA-right)
    -> X:$2DDE = 2
    -> X:$2DB9 = 1
    -> X:$2D53 nonzero

Test condition:
raw 0x0A5 request 6 (LCA)
    -> X:$2DDE = 0
    -> X:$2DB9 = 0
    -> X:$2D53 = 0
```

The LCA failure is now localized before the lane-state consumers and before the common torque path. It is not presently an accumulator/output/current-loop problem.

## Static-code correspondence

The CV6T-AR dispatcher at `P:$2A772` loads the processed request from `X:$2DCB` and directly accepts request 2/4. Its request-6 arm at `P:$2A77E` uniquely reads `X:$0904`, requires it to equal 1, and otherwise stores zero to `X:$2DDE`:

```text
P:$2A772  load X:$2DCB
...
P:$2A779  compare request 4
P:$2A77B  store state 2 to X:$2DDE
...
P:$2A77E  compare request 6
P:$2A780  load X:$0904
P:$2A782  compare 1
P:$2A784  store state 4 to X:$2DDE
P:$2A787  store state 0 to X:$2DDE   ; failed request/gate path
```

The observed raw-request-6/internal-state-0 behavior exactly matches this failed gate arm. `X:$0904 != 1` is now the leading explanation, but one observation remains necessary to distinguish it conclusively from a mismatch between raw `0x0A5` and processed `X:$2DCB`.

## Next narrow step

Create an observation-only FD22 revision that replaces the now-less-useful FD0E and FD0C fields with:

- `X:$2DCB` — processed request consumed by the dispatcher;
- `X:$0904` — LCA-specific configuration gate.

Retain `X:$2DDE`, `X:$2DB9`, `X:$2D53`, `X:$2D52`, and `X:$2D54`.

Expected decisive outcomes during raw request 6:

| `X:$2DCB` | `X:$0904` | `X:$2DDE` | Meaning |
|---:|---:|---:|---|
| 6 | not 1 | 0 | LCA-specific gate conclusively blocks entry |
| not 6 | any | 0 | extraction/processed-request path loses LCA before the gate |
| 6 | 1 | 0 | static branch interpretation/address model must be revisited |

Do not alter EEPROM or torque behavior before this observation.
