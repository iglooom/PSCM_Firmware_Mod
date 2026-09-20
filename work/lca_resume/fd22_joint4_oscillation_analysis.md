# FD22 joint4 — phase-entry bypass and feedback oscillation

## Capture integrity

```text
fd22_joint4_internal.csv  e0dcf0f38e9b0f190d97389839463664eab1cdcf481557796091fce8da02d3b9
fd22_joint4_can.csv       1f0df0bc8d273debc046b48c11933661da0f90c1fd6cee592c6f2a8444ae306c
fd22_joint4_can.jsonl     05977cd2a8b61816fde92750164ff507d4097dc630d88b5e0509f0a9ee5ce685
fd22_joint4_can.log       22a9b01f19405edff16bdc4e23dd57d3b7e96bb63a26050be9f2d16ab573615c
```

- 1,513/1,513 valid FD22 responses, all format 2.
- Internal duration: 151.329 s.
- Joint overlap: 150.680 s.
- 1,506 FD22 samples matched within 30 ms of raw request.
- Median/max nearest-request age: 4.999/10.719 ms.

The user's dashboard observation was that LCA repeatedly activated briefly and deactivated.

## Phase-entry bypass succeeded

During request 6 the application now reached every intended state-machine boundary:

```text
state 4, phase 1, code 0   entry
state 4, phase 5, code 4   entered LCA path; accumulator nonzero
state 4, phase 6, code 5   sustained-LCA arm reached
```

`X:$220F` was 3 for every sampled phase-5/code-4 and phase-6/code-5 state. Therefore the retained `B == 3` comparison is satisfied and is not another blocker.

Observed request-6 samples included:

```text
phase 5 / code 4: 7 samples while raw request remained 6,
                  plus 4 boundary samples aligned with request 7;
                  all 11 had a nonzero accumulator.
phase 6 / code 5: 8 samples while raw request remained 6;
                  accumulator was sometimes nonzero and sometimes already ramped to zero.
```

This proves that bypassing only the phase-1 `X:$2DC1` test was sufficient to enter the OEM LCA torque path. It does not force phase, code, or torque directly.

## The new failure is a closed-loop availability/request oscillation

The raw IPMA request did not remain at 6. There were 47 request-6 bursts, each only 0–121 ms long. Most were separated by approximately 1.0–1.1 seconds of request 7.

The repeated sequence on the bus was:

```text
PSCM feedback LaActAvail_D_Actl changes to 3
~49 ms median later: IPMA request becomes 6
~39 ms median later: PSCM feedback changes to 1
~30 ms median later: IPMA request becomes 7
about 1 second later: PSCM retries availability 3
```

For 28 clean multi-frame cycles:

```text
availability 3 -> request 6: median 49.1 ms
request 6 -> availability 1: median 38.6 ms
availability 1 -> request 7: median 30.4 ms
request-6 burst duration:    median 41.2 ms
```

No deny flag was asserted: `LaActDeny_B_Actl` remained 0. The dashboard LCA-level field remained asserted over the relevant interval, so the user's visible on/off behavior is consistent with the active-request/availability handshake rather than loss of lane detection.

## Localization

```text
IPMA sends request 6
 -> PSCM state 4
 -> phase 5/code 4
 -> phase 6/code 5
 -> accumulator becomes nonzero
 -> PSCM availability feedback falls 3 -> 1
 -> IPMA withdraws request 6 and sends request 7
 -> state machine resets
 -> PSCM retries availability 3 about one second later
 -> cycle repeats
```

The immediate problem is therefore no longer an LCA dispatcher or consumer-entry gate. It is the PSCM feedback status becoming incompatible with sustained request 6 after entry.

## Superseded next-boundary hypothesis

This report originally proposed `X:$2213 -> X:$226C -> X:$220F` as the availability producer chain. Joint5 observed all three cells and disproved that binding:

- the three cells copied one another exactly in 3,886/3,886 recovered samples;
- they agreed with CAN `LaActAvail_D_Actl` in only 2,312/3,886 samples;
- 923 samples had internal value 1 while transmitted availability was 3.

`X:$220F` remains the live state-machine value used by the retained `B == 3` comparison, but it is not the direct source of the CAN availability field. The true next boundary is the `0x140` TX payload buffer at `X:$3F5A..X:$3F5D` and the packer/source that supplies its two-bit availability field. See `fd22_joint5_candidate_chain_analysis.md`.
