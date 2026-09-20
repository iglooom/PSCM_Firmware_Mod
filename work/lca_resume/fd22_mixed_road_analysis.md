# FD22 mixed-road capture — first analysis

Source:

```text
/home/gl/Projects/ford/PSCM/Research/fd22_mixed_road.csv
SHA-256 03e43962302cc226797c0ab176e45fd372a7e0795d2481904e14b19fa5426985
```

Capture interval: `2026-09-19T08:34:27.605945Z` through `2026-09-19T08:43:53.102119Z` (`565.496174 s`).

## Transport integrity

- 5,650 rows
- 5,650 status `ok`
- zero timeout/error rows
- every raw response is exactly 18 bytes
- every format byte is zero

The FD22 transport and decoder operated correctly throughout the capture.

## Lane-state result

Observed state/code pairs:

| `X:$2DDE` lane state | `X:$2DB9` code | Samples |
|---:|---:|---:|
| 0 | 3 | 3,955 |
| 0 | 0 | 1,695 |

There were zero samples with the expected active markers:

- ordinary LKA: state `1`, code `2`;
- LCA entry: state `4`, code `4`;
- sustained LCA: state `4`, code `5`.

`X:$2D53` torque accumulator and `X:$2D52` torque output/result were zero in all 5,650 samples. Therefore this file does not yet contain the required code-2 positive control or code-5 LCA population and cannot locate their torque-path divergence.

## Evidence that the snapshot was live

The other sources changed substantially:

| Field | Nonzero samples | Minimum | Maximum |
|---|---:|---:|---:|
| `X:$2D54` upstream intermediate | 4,450 | -3,976 | 3,752 |
| `X:$1CB1` FD0E source | 5,635 | -8,237 | 17,055 |
| `X:$171B` FD0C source | 5,642 | -5,174 | 10,645 |

Thus the all-zero lane torque fields are not explained by a frozen FD22 callback or repeated static response.

## User-confirmed observation and revised interpretation

The driver confirmed that LCA was definitely shown during this exact capture. The cluster indication is upstream of the PSCM's internal lane-state gate, so it does not by itself prove that the PSCM accepted the request into `X:$2DDE = 4`.

The capture therefore establishes a real divergence:

```text
cluster/IPMA-side LCA indication present
PSCM X:$2DDE remained 0
PSCM X:$2DB9 never reached 4 or 5
PSCM lane torque accumulator/output remained 0
```

This reopens the pre-state-store path. It also invalidates the earlier inference that the `X:$0904` gate must have been open merely because NOPing it caused no perceptible steering change. A closed gate and an additional downstream no-torque problem can coexist; bypassing only the first would still feel like no change.

The current file did not include a simultaneous raw `0x0A5` capture, so it cannot yet distinguish:

1. IPMA/cluster displays LCA while `0x0A5` does not carry request state 6;
2. `0x0A5` carries state 6 but PSCM extraction does not produce 6;
3. PSCM sees 6, but the stock `X:$0904` gate rejects it and stores state 0;
4. the selected runtime cells are not those used by the live execution path.

The next narrow test is simultaneous FD22 plus `la_monitor.py` logging, with a physically effective ordinary-LKA episode as the positive control and displayed LCA as the test condition. If raw `0x0A5` state 2/4 produces FD22 state 1/code 2 while raw state 6 leaves FD22 at state 0/code 0 or 3, the address mapping and dispatcher are validated and the LCA-specific pre-state gate becomes the leading cause.

No firmware, EEPROM, or calibration change is justified by this file alone.
