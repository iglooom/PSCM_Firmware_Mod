# FD22 joint6 — TX-source candidate result

## Capture integrity

```text
FD22 format-4 rows:       2,298
valid rows:               2,298 / 2,298
internal duration:        230.053 s
joint CAN/FD22 overlap:   227.218 s
matches within 30 ms:     2,269
median alignment age:     5.146 ms
raw frame-0x140 samples:  11,449
```

The final 29 internal samples extended beyond the raw-CAN capture and are
excluded from synchronized confusion tables.

## Control-path result

The established LCA behavior reproduced:

```text
state 4 samples:          29
phase 5 / code 4:          8   (8/8 nonzero accumulator)
phase 6 / code 5:         14   (5/14 nonzero accumulator)
request-6 episodes:       61
request-6 duration:       19.2–181.0 ms; median 59.9 ms
```

Thus the firmware retained both proven bypasses and again entered the OEM LCA
torque path.

## Candidate result

`X:$7EBB` was `0xFFFF` in all 2,298 samples. It is not the live availability
enum. The `14C386` word is a table-driven handle/holder field, not a direct
application value that can be sampled as an enum.

The observed `X:$3F5B` is associated with raw frame bytes 2–3, but its candidate
field was fixed at 3 throughout the trusted overlap:

```text
(X:$3F5B & 0x000C) >> 2 = 3:  2,269 / 2,269
```

while transmitted `LaActAvail_D_Actl` was:

```text
CAN 0:     2 samples
CAN 1: 2,040 samples
CAN 3:   227 samples
```

Confusion table:

| sampled candidate | CAN 0 | CAN 1 | CAN 3 |
|---:|---:|---:|---:|
| 3 | 2 | 2,040 | 227 |

The candidate is therefore disproved.

## Correct frame location

Directly pairing every decoded frame-`0x140` JSON record with its raw frame
proved:

```text
LaActAvail_D_Actl = (raw_byte[7] >> 2) & 0x03
agreement: 11,449 / 11,449
```

The first captured transition illustrates it:

```text
CAN availability 0 -> 1
raw frame:
  00 00 BC 02 40 09 6E 20
  00 00 BC 02 40 0A 5A 24
                            ^^ byte 7: bits 3:2 change 00 -> 01
```

Since frame `0x140` uses `X:$3F5A..X:$3F5D`, raw bytes 6–7 reside in
`X:$3F5D`, not `X:$3F5B`. The next final-buffer observation address is
therefore `X:$3F5D`.

## Static-interpretation correction

The earlier interpretation was still one level off. The useful stride-5 record
is phase-aligned at `X:$419A`:

```text
0000 0C02 0002 7EBB 7F74
```

Here `0x0C02` is mask `0x0C`, shift `2`; `0x0002` is frame-slot bit 2;
and `0x7EBB` is a **packed-byte pointer**, not an X-word address:

```text
0x7EBB = 2 * X:$3F5D + 1
```

It therefore selects raw frame byte 7 exactly. Reading `X:$7EBB` as a word in
joint6 produced constant `0xFFFF` because the telemetry used the packed byte
pointer as an ordinary X address. The record itself is consistent with the
availability field.

## Next boundary resolved statically

The subsequent static trace found:

```text
X:$2D28 -> X:$2252 -> frame-status composer -> packed field at byte pointer 0x7EBB
```

`X:$2D28` is computed as:

```text
A = X:$2DB9 in {2,3,5}
B = X:$2DBA in {2,3}
X:$2D28 = 3 - 2*A - B
```

Format-5 joint7 telemetry observes this enum, its mirror, and both predicate
inputs. See `availability_producer_static_trace.md`.
