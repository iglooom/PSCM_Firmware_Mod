# FD22 joint5 — candidate-copy-chain result

## Capture integrity and recovery

```text
fd22_joint5_internal.csv  d76a5fc5c2869313c4a7855b5d5d5cd5e596f3eb2bd0dfec6fadb50caf9f9ccf
fd22_joint5_can.csv       63bf6b095a902a6b767239896b0bd8c11a7ecbd1bb19832204f6a9f3c744cc66
fd22_joint5_can.jsonl     5533a9154b0d3ac73f0474882a27bdc2e403a2ba5bd2144de2d3d4e36f9190fd
fd22_joint5_can.log       08bd41eab534693f34c8dcccc0c1ec1e36250553204d49ec5ed15e01be4365ab
```

The capture used the older format-2 logger against format-3 firmware. The logger correctly rejected the unexpected version instead of silently assigning the values to the old columns:

```text
logged status: 3,886 error; 8 timeout
```

Every one of the 3,886 non-timeout raw responses was a structurally valid 18-byte `62 FD 22` format-3 response and was recovered directly from `raw_response`. No road-test rerun is needed.

- recovered FD22 responses: 3,886/3,886;
- capture duration: 392.677 s;
- joint overlap: 392.677 s;
- matched within 30 ms: 3,886;
- median/max nearest-request age: 5.108/11.093 ms.

## LCA path reproduced

The capture again observed:

```text
request 6 -> state 4 -> phase 5/code 4 -> phase 6/code 5
```

All 16 phase-5/code-4 samples had a nonzero torque accumulator. The phase-entry result is reproducible.

Conventional LKA was also a positive control:

```text
22 request-2 samples -> state 1, phase 2, code 1, nonzero accumulator
```

## Candidate chain is lossless

Across all 3,886 recovered samples, the three observed cells were always identical:

```text
X:$2213  X:$226C  X:$220F   samples
   1        1        1       3,188
   0        0        0         626
   3        3        3          72
```

There were zero disagreements at either copy boundary. The runtime evidence therefore confirms that:

```text
X:$2213 -> X:$226C -> X:$220F
```

is a lossless copy chain. Neither `P:$29249` nor `P:$29CB4` changes the value.

## Critical correction: this is not the 0x140 availability source

The earlier joint4 interpretation tentatively labelled this chain as the producer of CAN `LaActAvail_D_Actl`. Joint5 disproves that binding.

Nearest-time comparison with decoded CAN frame `0x140` produced:

```text
internal X:$2213   CAN LaActAvail_D_Actl   samples
       1                    1               2,260
       1                    3                 923
       0                    1                 614
       3                    3                  44
       3                    1                  28
       0                    0                   8
       1                    0                   5
       0                    3                   4
```

Only 2,312/3,886 (59.50%) agreed. In particular, 923 samples had internal value 1 while transmitted availability remained 3. This discrepancy is far too large to be explained by the 5-ms median alignment age.

Therefore:

- `X:$220F` is a state-machine input whose value 3 satisfies the retained `B == 3` comparison;
- `X:$2213 -> X:$226C -> X:$220F` is real, but it is not the direct producer chain for `LaActAvail_D_Actl`;
- the actual transmit source for the two-bit availability field remains unidentified.

## Correct next boundary

The signal-configuration record proves frame `0x140` uses TX payload buffer `X:$3F5A..X:$3F5D`:

```text
record X:$41F2, ID 0x140, TX, payload buffer X:$3F5A
```

The next investigation must trace the packer that writes `LaActAvail_D_Actl` into that buffer and then identify its source cell. Instrumenting more cells from the disproven `$2213/$226C/$220F` chain would not advance the question.

Do not patch or force `$2213`, `$226C`, or `$220F` as CAN availability. Joint5 proves that interpretation is false.
