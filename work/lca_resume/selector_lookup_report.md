# CV6T-AR selector lookup semantics

Static read-only analysis of extracted `14C217` blocks. P-space addresses are word addresses; raw words are little-endian 16-bit words as loaded with `struct '<H'`. No VBF/bin was modified and no live CAN access was used.

## Result

**PROVEN:** the CV6T-AR `state 1 -> -1, state 2 -> +1, otherwise -> 0` selector does **not** zero the five downstream lookup outputs in the OEM default calibration. All five maps are identical, seven-point, flat `0x0100` (Q8 unity) maps. Their interpolated result is `0x0100` for negative, zero, and positive inputs, including out-of-range endpoint clamps.

The implementation is a bank of **five multiplicative per-channel scheduling gains**, not an additive directional-bias term and not one multiplicative gain applied to the completed whole demand. In the supplied calibration every scheduled gain is unity, so the selector is numerically neutral. The previous inference “state 4 selects zero, therefore LCA torque may be suppressed” is refuted for this path.

## Reproduction

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/selector_lookup.py --selftest
python3 work/lca_resume/selector_lookup.py
```

Observed test result: **5 tests, all PASS**.

Inputs already identified by SHA-256 in `dataflow_report.md`:

```text
CV6T-AR  6e60963a3583993d1b872ebd88bb9b2f1acdeb397a7954a1ae75be3ce7d933b0
CV6T-AH  1b9e232ac420ef492f454346cae71dc7795b22377a3cb40a32b6b1cd42d29af0
BV6T-AF  f71464911868f697caed4024b56aff78849f7c83a4226e1f2f81c2e9cd3a3e13
```

---

# PROVEN

## P1. Selector and actual lookup argument

CV6T-AR `P:$2B0E8`:

```text
F07C 2DDE 4C01 A203 E6FF 2D5E A907
4C02 A203 E681 2D5E A902 E680 2D5E
```

This is:

```text
X:$2DDE == 1 -> X:$2D5E = -1
X:$2DDE == 2 -> X:$2D5E = +1
otherwise     -> X:$2D5E =  0
```

The following code does not pass literal `-1/0/+1` directly to the map. It multiplies the selector by the current filtered magnitude at `X:(R0)` and stores the signed result in `X:$2D5F`:

```text
P:$2B106  F114 F07C 2D5E 6C11 8016 D07C 2D5F E708
```

Thus the real argument is `x = selector * runtime_magnitude`. State 4 makes `x=0`; states 1 and 2 select opposite signs of the same runtime magnitude.

## P2. Five table layouts and raw defaults

The runtime tables begin at:

```text
X:$03B7, X:$03CC, X:$03E1, X:$03F6, X:$040B
```

They are `0x15` words apart. The corresponding contiguous serialized OEM default-calibration records are at P-space `P:$0E489`, `$0E49E`, `$0E4B3`, `$0E4C8`, and `$0E4DD`. Each record is byte-for-byte identical:

```text
FDF4 FEFA FF7D 0000 0083 0106 020C
0100 0100 0100 0100 0100 0100 0100
5000 966E 0000 0100 0100 0100 0A00
```

Decoded as signed axis plus the ordinate array used by the caller:

```text
axis[0..6] = -524, -262, -131, 0, 131, 262, 524
value[0..6] = 256, 256, 256, 256, 256, 256, 256
```

The final seven words are retained as raw trailer/metadata. Their individual meanings are not needed by the lookup caller and are not assigned names here.

The code independently confirms the table geometry:

* each call passes `Y0=7` points and table bases separated by `0x15` words;
* the value-array bases are table base + 7: `03BE`, `03D3`, `03E8`, `03FD`, `0412`;
* `P:$2B3CA..$2B43D` validates monotonic table fields at the five runtime X records;
* the exact seven-word axis occurs five times in CV6T-AR at the five P addresses above and does not occur in CV6T-AH or BV6T-AF.

First call raw words:

```text
P:$2B110
874A 03B7 F77C 2D5F 874B 2D60 874C 2D61 E587 E256 256F
```

The four clones use bases `03CC/03E1/03F6/040B` and output pairs `2D67/68`, `2D6A/6B`, `2D6D/6E`, `2D70/71`.

## P3. Helper `P:$2256F`

`P:$2256F` is a seven-point bracket/fraction helper. Its raw head is:

```text
827B DD3F F016 7876 81A9 A505
8643 0000 8649 0000 A931
7C52 4440 FFFF E028 8930 5FD4 A507
7C52 4440 FFFE D017 8649 00FF A923
```

Its behavior, corroborated by how `P:$2B110` supplies and consumes its outputs, is:

1. compare the signed input (`Y1`) to the first axis point;
2. low clamp to lower index 0 and fraction 0;
3. compare with the final axis point;
4. high clamp to lower index `n-2` and unsigned fraction `0xFF`;
5. otherwise binary-search for `axis[i] <= x < axis[i+1]`;
6. store lower index `i` and an unsigned Q8 interpolation fraction derived through `P:$18A87` from `x-axis[i]` and `axis[i+1]-axis[i]`.

Caller `P:$2B11B..$2B13B` then evaluates the ordinary Q8 linear interpolation:

```text
y = ((0x100-fraction)*value[i] + fraction*value[i+1]) >> 8
```

The exact rounding convention inside the generic divider `P:$18A87` is not required for the result here: adjacent ordinates are always equal (`0x0100`), so every fraction and every bracket produces exactly `0x0100`.

## P4. Outputs for selector -1, 0, +1

For a direct helper test with `x=-1,0,+1`, the reproducible model reports:

| x | lower index | modeled Q8 fraction | each of five outputs |
|---:|---:|---:|---:|
| -1 | 2 | 254 | `0x0100` (256) |
| 0 | 3 | 0 | `0x0100` (256) |
| +1 | 3 | 1 | `0x0100` (256) |

For the real argument `x=selector*runtime_magnitude`, the index and fraction depend on the unknown magnitude. The output does not: low clamp, every interior interval, and high clamp all interpolate two identical `0x0100` ordinates. Therefore:

```text
selector -1 -> five outputs = 0100 0100 0100 0100 0100
selector  0 -> five outputs = 0100 0100 0100 0100 0100
selector +1 -> five outputs = 0100 0100 0100 0100 0100
```

## P5. Multiplicative scheduling, not additive bias or whole-demand gain

The five lookup results are stored at `X:$2D62`, `$2D69`, `$2D6C`, `$2D6F`, and `$2D72`. `P:$2B305` pairs them with five independently produced values:

```text
2D62 * 2D66 -> filtered channel result X:$2D86
2D69 * 2D76 -> filtered channel result X:$2D8E
2D6C * 2D7A -> filtered channel result X:$2D94
2D6F * 2D7E -> filtered channel result X:$2D9A
2D72 * 2D82 -> filtered channel result X:$2DA0
```

Representative raw product head:

```text
P:$2B305
F07C 2D62 8110 F07C 2D66 4CB0 4C30 E700
7217 7297 7097 78D0
```

The other four products repeat the same multiply/filter shape at `P:$2B326`, `$2B347`, `$2B368`, and `$2B389`. No lookup result is added as an independent signed offset. Also, there is no single selector-derived factor multiplied after the five channels have been combined. This proves the implementation category: per-channel multiplicative scheduling. With all factors equal to Q8 unity, it is neutral in the supplied image.

## P6. Caller and cadence

The state/lookup chain is called in fixed order inside the torque/output stage:

```text
P:$2AEAA  E256 B0E8   JSR P:$2B0E8   ; selector and X:$2D5F
P:$2AEAC  E256 B10E   JSR P:$2B10E   ; five flat maps
P:$2AEAE  E256 B305   JSR P:$2B305   ; five products/filters
P:$2AEB0  E256 AEC2   JSR P:$2AEC2   ; downstream aggregate
P:$2AEB2  E256 AF2D   JSR P:$2AF2D
P:$2AEB4  E256 B054   JSR P:$2B054
```

`P:$2AE86` is itself called unconditionally once by the registered lane cyclic callback:

```text
P:$2A743
E256 A7D7 E256 AD8C E256 A8C4 E256 AD62
E256 AE86 E256 B8EA E256 BA5A E256 B8A2 E256 B8CD E708
```

The lifecycle object control is:

```text
X:$6132  A756 0002
X:$6134  A743 0002   ; cyclic callback
X:$6136  A75E 0002
X:$6138  A75B 0002
```

Therefore the selector bank runs once per enabled invocation of `P:$2A743` (the internal `P:$2AE86` inhibit branch may choose its reset path). The absolute callback period has not been established statically and is not stated in Hz. CAN `0x0A5` having a 20 ms period is not proof of this internal cadence.

## P7. AH/BV controls and analogous computations

Exact whole-image controls:

| build | exact selector shape | exact seven-word AR axis | five-map bank in corresponding torque stage |
|---|---:|---:|---:|
| CV6T-AR | `P:$2B0E8` | five (`P:$0E489..$0E4DD`) | present |
| CV6T-AH | none | none | absent |
| BV6T-AF | none | none | absent |

CV6T-AH's structurally corresponding enabled stage is materially shorter:

```text
P:$293E1 ...
P:$293FA  E256 940C
P:$293FC  E256 9468
P:$293FE  E256 955E
P:$29400  A90A
P:$29401  E680 2894 ...  ; inhibit/reset path
```

Its aggregate at `P:$2940C` calls four direct computations and combines `X:$288F`, `$2897`, and `$2890` into `$2896`; it has no lane-state sign selector and no five lookup/product bank. For example, direct computation `P:$293B4..$293DE` consumes `X:$28D0` and calibration `X:$0135` and stores `X:$288F`.

BV6T-AF is compiled differently again. Its analogous final path uses a dynamic code-5 ramp source and then directly multiplies/limits the selected ramp contribution:

```text
P:$27460  E256 7346 E256 73BE
P:$27464  F07C 23AB ... F07C 21EE ...
P:$2746B  7217 7297 7017 7850
P:$2746F  4C68 8016 D07C 21F0
P:$27473  E256 7476 E708
```

Its accumulator store and code-2/code-5 enable control remain:

```text
P:$274AC  D07C 23AA
P:$274AE  F07C 23BF 4C02 A303 E700 4C05 A203 E700 E581 A901 E580
```

These controls prove that the AR selector/map bank is not a relocated byte-identical mechanism in AH or BV. The AH shape is consistent with a build in which a flat-unity scheduling layer was absent or optimized/factored away; source-level equivalence cannot be proven from machine code alone.

---

# REFUTED

1. **“State 4 -> selector zero makes the five lookup outputs zero.”** Refuted: all five outputs are `0x0100` at zero and everywhere else.
2. **“The selector supplies an additive left/right torque bias.”** Refuted for this path: its only identified consumers are interpolation gains subsequently multiplied by five channel values; no lookup output is independently added.
3. **“The selector is a varying gain on the entire completed torque demand.”** Refuted: it schedules five separate upstream channels, not one post-sum demand. Moreover, all five OEM maps are flat unity.
4. **“The AR-only selector explains LCA zero torque in the supplied calibration.”** Refuted for the decoded selector path. Any actual zero must arise elsewhere (outer inhibit, source channel, limiter, state-dependent ramp, or runtime calibration override).
5. **“AH/BV contain the same selector at another obvious relocation.”** Refuted for the exact selector and exact axis signatures by whole-image scans; semantically different compiled logic remains a separate question.

# OPEN

1. **Runtime provenance/override:** the serialized defaults at `P:$0E489..$0E4F1` match the five runtime table records, but the complete loader/NVM override path into `X:$03B7..$041F` has not been fully reconstructed. A valid learned/service calibration could theoretically replace the defaults at runtime.
2. **Axis physical units:** the signed breakpoints are proven raw values; their engineering unit and the scale of the filtered magnitude feeding `X:$2D5F` remain unknown.
3. **Divider rounding:** the exact rounding of generic helper `P:$18A87` remains open. It cannot change the flat-map outputs and therefore does not affect the semantic conclusion.
4. **AH/BV source-level equivalence:** AH and BV corresponding torque paths are identified structurally, but whether the AR five-map layer was added, optimized away elsewhere, or represents a source-feature variant is not recoverable conclusively from these binaries alone.
5. **Absolute cadence:** one execution per lane cyclic callback is proven; callback frequency in Hz is not.

## Final classification

* **PROVEN:** AR has five signed-input, seven-point per-channel gain maps; OEM default ordinates are flat Q8 unity; outputs are `0x0100` for selector -1/0/+1 and for any magnitude.
* **REFUTED:** this selector path is an additive directional bias, a sign-varying whole-demand gain, or an LCA state-4 zeroing mechanism in the supplied calibration.
* **OPEN:** runtime override provenance, physical units, exact generic-divider rounding, source-level relationship to AH/BV, and absolute callback period.
