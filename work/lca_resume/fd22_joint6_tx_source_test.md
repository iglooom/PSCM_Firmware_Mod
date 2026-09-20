# FD22 joint6 — pre-test hypothesis and correction

Joint6 disproved the attempted **word-address** readings:

- `X:$7EBB` was `0xFFFF` in all 2,298 samples;
- `(X:$3F5B & 0x000C) >> 2` remained 3 while CAN availability was mainly 1;
- raw frame analysis located availability at byte 7 bits 3:2, in `X:$3F5D`.

Subsequent static tracing corrected the table interpretation. The phase-aligned
record begins at `X:$419A`:

```text
0000 0C02 0002 7EBB 7F74
```

`0x7EBB` is a packed-byte pointer (`2 * X:$3F5D + 1`), not an X-word address.
Thus the record is consistent with byte 7 / mask `0x0C` / shift 2; joint6
sampled it using the wrong addressing mode.

Use these result documents:

```text
work/lca_resume/fd22_joint6_tx_source_analysis.md
work/lca_resume/availability_producer_static_trace.md
```

Do not treat ordinary word read `X:$7EBB` or the `X:$3F5B` candidate as
availability. Format-5 joint7 instead observes the statically traced producer
`X:$2D28`, its output mirror `X:$2252`, and auxiliary predicate `X:$2DBA`.
