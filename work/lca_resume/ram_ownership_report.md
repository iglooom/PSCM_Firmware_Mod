# RAM ownership analysis: CV6T-14C217-AR X:$6600..$6D0F

## Verdict

**Reject X:$6600..$6D0F as a proven-safe 1,808-word logger allocation.**

The range passes every positive static candidacy test performed: it is `FFFF`
in the AR X-init block, has no decoded absolute X-space references, has no
immediate address-register base inside it, and has no locally paired
immediate-base plus explicit-displacement access reaching it. The AH and BV
controls have the same erased interval and no corresponding decoded absolute
references.

Those facts are not an ownership proof. The executable contains 18,565 decoded
indirect X-space operations in AR, including unconstrained `X:(Rn)`,
`X:(Rn+N)`, postincrement/decrement, and stack-relative forms. No operational
stack base/limit or heap/arena bounds could be recovered. More importantly,
AR demonstrably uses many erased, post-link X-init addresses immediately below
the proposal (`X:$643C`, `$6440`, `$64DC`, `$6506`, `$6516`, and
`$6543..$6564`). Therefore, "erased in X-init" is known to be an invalid
ownership rule for this firmware.

This result does **not** authorize an instrumentation build or flash.

## Smaller defensible window

For a first counter/latest-sample probe only, the smallest statically defensible
window found is:

```text
X:$6600..$661F inclusive   32 words / 64 bytes
```

It is enough for a guard/magic, counter, status, one five- or seven-word latest
sample, and duplicate sequence words. It is **not** enough for the proposed
256-record ring.

Why this window is preferable to the full range:

* all 32 words are `FFFF` in AR, AH, and BV X-init blocks;
* no decoded absolute X reference names any word in it in any control;
* no recognized immediate address-register load names a base in it;
* the conservative local immediate-base plus explicit indexed-displacement
  scan found no reach into it;
* no initialized word in any of the three X-init blocks contains a value in
  `$6600..$661F`;
* it starts 0x9C words above the highest confirmed AR runtime byte cell,
  `X:$6564`, and ends before the repeated pointer-shaped/numeric constant
  `0x662E` found in all three X-init images.

**Qualification:** this 32-word interval remains `address-unconfirmed`, because
unbounded runtime-loaded pointers and stack ownership were not eliminated. It
is defensible only as the reduced candidate for a parked Stage-1 cadence/ABI
probe after obtaining a linker map, a live non-writing RAM observation, or a
proved stack/allocator map. Do not treat it as safe for road use or as permission
to build.

## Evidence and method

The reproducible scanner is `ram_ownership_analysis.py`. It reads OEM binaries
and existing recursive-descent listings only. It does not write VBF/bin files
and does not open CAN.

Run:

```bash
python3 work/lca_resume/ram_ownership_analysis.py
```

Captured output: `ram_ownership_analysis.txt`.

### X-init occupancy and controls

| build | X-init SHA-256 | last populated application word below footer | full candidate | 32-word candidate |
|---|---|---:|---:|---:|
| CV6T-AR | `4e65a6493cf770d02a125c115a50c7b6b8d58969405bbd0292fafd896750d6c0` | `X:$63A3` | 1,808/1,808 `FFFF` | 32/32 `FFFF` |
| CV6T-AH | `f3420774100b58d8bfea812e72b9f2c82cb478cb0b4581c4e231b5fbcdecb945` | `X:$62BB` | 1,808/1,808 `FFFF` | 32/32 `FFFF` |
| BV6T-AF | `de4327cf738a7e7e88d4f4bd09b18d62efa2ca7e4d9f4b9adfb1dbb0975fde92` | `X:$61F9` | 1,808/1,808 `FFFF` | 32/32 `FFFF` |

The words at `X:$7FF9..$7FFE` are the X-init trailer/descriptor and were excluded
when finding the last application-data word. In AR the populated runs above
`X:$6000` end at `$63A3`; AH ends at `$62BB`; BV ends at `$61F9`.

The AH/BV controls support that `$6600..$6D0F` is not statically initialized.
They do not prove AR runtime ownership because AR has additional runtime-only
state above its initialized tail.

### Absolute and immediate-base scan

Across the existing recursive-descent listings:

| build | decoded instructions | decoded indirect-X forms | absolute refs into `$6600..$6D0F` | local base+explicit-displacement reaches |
|---|---:|---:|---:|---:|
| CV6T-AR | 99,235 | 18,565 | 0 | 0 |
| CV6T-AH | 94,194 | 17,752 | 0 | 0 |
| BV6T-AF | 86,163 | 16,848 | 0 | 0 |

The immediate-base scan recognizes the compiler's short address-register forms
for R0..R5 (`8748/4A/4C/4E` and `8750/52/54/56/58/5A`). For every such base,
the scanner checks explicit indexed displacements in the following 24 decoded
instructions, bounded to 48 P words and stopped at a return. This intentionally
over-approximates register identity when the base decoder prints `Rn`/`RRR`.
No pair reached either the full candidate or `$6600..$661F`.

The highest AR immediate pointer bases near the boundary are:

```text
P:$317D9  immediate base X:$6560
P:$317E2  immediate base X:$655E
```

No immediate address-register base at or above `X:$6600` was found in AR, AH,
or BV. The three builds do contain SP immediate writes to `0x5555` and `0xAAFF`
in destructive memory-test code. These are test patterns, not operational stack
bounds.

### Neighboring runtime structures

The AR listing proves that the apparently erased region below `$6600` is active:

```text
P:$2FF60 / $2FF88    X:$6440 write/read
P:$2FF66 / $2FF8D / $2FFCA    X:$643C write/clear/read
P:$315EA..$315FB     copy bytes X:$655D..$6561 to a caller buffer
P:$315FF..$31608     initialize X:$655D..$6561
P:$3160F..$31619     use X:$655C..$6564
P:$317D9..$317E5     swap/copy via bases X:$6560, $655F, $655E
```

Additional decoded absolute references name `$64DC`, `$6506`, `$6516`, and
`$6543..$6556`. None of these cells is populated in the OEM X-init payload.
This is direct evidence of a runtime-created state/mailbox area, and it is the
main reason the full erased tail cannot be claimed free.

### Pointer-valued initialized words

AR has 13 initialized words whose numeric values fall inside
`$6600..$6D0F`; AH has 12 and BV has 16. The common values include:

```text
662E 6661 6726 685E 686F 6956 6A4E 6AAB 6B46 6B6C 6C7E
```

Most occur in matching cross-family numeric tables at shifted X locations and
are likely coefficients rather than pointers (for example `6AAB` repeats).
They were retained as ambiguous provenance rather than discarded. None equals
an address in `$6600..$661F`. Raw bare-word counts were not treated as xrefs,
because constants and instruction operands produce hundreds of false matches.

## Stack, heap, and initialization/allocation loops

* **Stack:** the firmware uses R7/SP-relative push/pop and local-frame forms
  extensively, but the operational initial SP and a lower/upper bound were not
  recovered. The only immediate SP values found are RAM-test patterns. Static
  safety therefore cannot exclude stack overlap.
* **Heap/arena:** no allocator with proved base, end, and allocation invariant
  was identified. Runtime-loaded pointers remain unconstrained, so heap/arena
  overlap cannot be excluded.
* **Initialization:** the X-init payload explicitly initializes application data
  through AR `X:$63A3` and leaves the proposal erased. Runtime code separately
  creates state in erased cells through at least `X:$6564`; this defeats an
  inference from X-init fill alone.
* **Indexed loops:** no immediate base plus explicit displacement was found to
  reach the proposal, but generic `+N`, postincrement/decrement, and pointers
  loaded from X-space cannot be range-bounded from the available listings.

## Safety decision and required closure

A safety proof would require at least one of:

1. the AR linker map naming all X sections, stacks, and heap/arena limits;
2. a complete interprocedural points-to analysis that bounds runtime-loaded
   address registers and all `+N`/postincrement loops; or
3. observation-only live RAM evidence showing the candidate remains untouched
   over boot, diagnostics, faults, and representative runnable activity,
   combined with a separately established stack bound.

Until then:

* do not allocate the 1,808-word ring at `$6600..$6D0F`;
* use `$6600..$661F` only as a reduced candidate for the Stage-1 counter/latest
  mailbox after one of the ownership closures above;
* do not modify OEM images or perform live writes based on this report.
