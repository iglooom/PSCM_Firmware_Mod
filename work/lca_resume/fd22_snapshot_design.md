# Stateless FD22 request-time snapshot handler

## Scope and result

This is a design-only replacement for the readable 15-byte DID `FD22` in
`CV6T-14C217-AR`. It has no cyclic hook, logger RAM, cursor, freeze state, or
write to OEM state. No BIN/VBF is created or modified, and no CAN interface is
used.

The handler occupies **60 P words** at `P:$33800..$3383B`. It emits one
format/status byte followed by seven raw 16-bit X-memory cells in explicit
big-endian order and returns `Y0=15`.

The payload is a request-time ordered sample, not a simultaneous atomic sample
of all seven unrelated cells. Each individual 16-bit cell is loaded exactly
once, retained in `B1`, and therefore cannot tear between its high and low
payload bytes. The cells themselves may change between successive loads.

## Payload contract

Byte 0 is `0x00`, defined as **stateless raw format version 0 / no status
flags**. A stateless reader has no logger validity, wrap, cursor, or freeze
state to report; assigning those meanings would be false. Bytes 1 through 14
are:

| payload offset | field | source | byte |
|---:|---|---|---|
| 0 | format/status | constant `0x00` | — |
| 1 | lane state | `X:$2DDE` | high |
| 2 | lane state | `X:$2DDE` | low |
| 3 | per-state code | `X:$2DB9` | high |
| 4 | per-state code | `X:$2DB9` | low |
| 5 | torque accumulator | `X:$2D53` | high |
| 6 | torque accumulator | `X:$2D53` | low |
| 7 | torque output/result | `X:$2D52` | high |
| 8 | torque output/result | `X:$2D52` | low |
| 9 | FD0E raw source | `X:$1CB1` | high |
| 10 | FD0E raw source | `X:$1CB1` | low |
| 11 | FD0C raw source | `X:$171B` | high |
| 12 | FD0C raw source | `X:$171B` | low |
| 13 | immediate upstream intermediate | `X:$2D54` | high |
| 14 | immediate upstream intermediate | `X:$2D54` | low |

`X:$2D54` is the seventh field because the established dataflow places it
immediately upstream of the working value and `X:$2D53` accumulator. It is more
directly useful for separating upstream production from accumulation/output
than the selector-derived lookup argument at `X:$2D5F`.

## ABI constraints

The OEM callback analysis establishes:

- entry `R2` is the diagnostic dispatcher's byte pointer to its X-memory
  scratch buffer;
- `C` holds the configured expected length and is live across the call;
- the callback returns the exact byte count in `Y0`;
- `FD22` metadata already specifies readable length `0x000F`;
- `A`, `B`, `X0`, `Y0`, `R0`, `R1`, and condition codes are the only proven
  volatile set available to this callback class.

This handler actually clobbers only `A`, `B`, `Y0`, and condition codes. It does
not modify `R2`; it addresses offsets `0..14` from it. It preserves `C`, `D`,
`R3`, `R4`, `R5`, `N`, and `Y1`. It contains no stack instruction, so `SP` is
balanced without a prologue or epilogue.

## Exact raw words

All addresses below are P-space word addresses. Words are shown in execution
order. If ever serialized into the existing little-endian extracted block,
each 16-bit word would be stored low byte first; this document does not perform
that serialization.

```text
P:$33800  E080 D0B6 F07C 2DDE 8110 5C28 D0E6 0001
P:$33808  D1E6 0002 F07C 2DB9 8110 5C28 D0E6 0003
P:$33810  D1E6 0004 F07C 2D53 8110 5C28 D0E6 0005
P:$33818  D1E6 0006 F07C 2D52 8110 5C28 D0E6 0007
P:$33820  D1E6 0008 F07C 1CB1 8110 5C28 D0E6 0009
P:$33828  D1E6 000A F07C 171B 8110 5C28 D0E6 000B
P:$33830  D1E6 000C F07C 2D54 8110 5C28 D0E6 000D
P:$33838  D1E6 000E E58F E708
```

Conceptual straight-line form:

```text
MOVE.W  #0,A
MOVE.BP A1,X:(R2)                 ; payload[0]

; repeated once for each source, offsets (1,2), (3,4), ... (13,14)
MOVE.W  X:$source,A               ; one coherent 16-bit source load
MOVE.W  A1,B1                     ; retain low byte / entire raw word
LSRR.W  #8,A
MOVE.BP A1,X:(R2+high_offset)
MOVE.BP B1,X:(R2+low_offset)

MOVE.W  #15,Y0
RTS
```

The fixed-offset `MOVE.BP` form is deliberate. It leaves the entry value of
`R2` untouched while still making exactly fifteen byte stores.

## OEM-template provenance

`fd22_snapshot_words.py` rejects an image unless all three immutable OEM block
SHA-256 values match the callback ABI report. It then verifies the generated
words against independent OEM instruction templates rather than invoking an
assembler:

- `P:$2075B`: `E080 D0B6` — zero in `A1`, then byte-pointer store through the
  callback output base;
- `P:$20712`: `F014 5C28 E58C D0B6` — 16-bit source in `A`, logical right shift
  by eight, then high-byte store;
- `P:$2046C`: `5C68 E582 D0B6 D1E6 0001 E708` — OEM two-byte high/low packing,
  including `D1E6 displacement` as the retained-`B1` low-byte store;
- `P:$13B32..$13B40`: two conditional `8110` transfers in an OEM min/select
  idiom followed by `D17C 17B0`, independently fixing `8110` as
  `MOVE.W A1,B1`;
- `P:$20710..$20746`: the FD20 handler uses the same fixed-base displaced
  byte-pointer encoding across offsets 1 through 11; offsets 12 through 14 use
  the unchanged 16-bit displacement form;
- `P:$1059D`: `E58F E708` — OEM FD22 returns length 15 and executes `RTS`;
- every selected source extension is independently found paired with OEM
  absolute-X-load opcode `F07C` somewhere in the immutable image.

The script also confirms that all 60 destination words at `P:$33800..$3383B`
are still OEM cave fill `E70A`. This is only a read-only design prerequisite;
the script never writes those words.

## Static proof obligations

The script decodes only the exact opcodes used by this handler and fails on any
unknown word. Its symbolic checks assert:

1. exactly 15 memory writes;
2. every write is one byte to `R2` scratch, at each offset `0..14` exactly once;
3. payload source cells, field order, and high/low order match the table above;
4. every source cell is loaded exactly once before its two stores;
5. the penultimate instruction sets `Y0=15`;
6. no instruction accesses `SP`;
7. clobbers are exactly `A`, `B`, `Y0`, and condition codes, a subset of the
   proven volatile set;
8. `R2`, `C`, and all unproved/nonvolatile registers remain unmodified;
9. the CFG is finite and straight-line: no branch, loop, call, or indirect
   transfer, with one terminal `RTS` as the final instruction;
10. the generated body is exactly 60 words and fits the confirmed cave.

## Conservative cycle bound

The static upper-bound budget is:

| operation | count | cycles each (upper budget) | subtotal |
|---|---:|---:|---:|
| immediate zero to `A` | 1 | 1 | 1 |
| base byte-pointer store | 1 | 2 | 2 |
| absolute X-memory word load | 7 | 2 | 14 |
| `A1` to `B1` transfer | 7 | 1 | 7 |
| logical shift by 8 | 7 | 1 | 7 |
| displaced byte-pointer store | 14 | 2 | 28 |
| immediate `Y0=15` | 1 | 1 | 1 |
| `RTS` | 1 | 8 | 8 |
| **total** | | | **68 cycles** |

The 68-cycle value conservatively budgets two cycles for every X-memory or
byte-pointer transfer. It is a core instruction bound; external flash wait
states, arbitration stalls, interrupt latency, and the surrounding diagnostic
dispatcher are outside this handler-only count.

## DID pointer (design fact only)

The existing six-word FD22 record is:

```text
P:$0EEA2  FD22 0000 058F 0001 0000 0000
```

A future integration would need both read-pointer words changed from
`058F 0001` to `3800 0003` to target `P:$33800`. This work does **not** make
that edit and does not build a VBF.

## Reproduction

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/fd22_snapshot_words.py
```

The final line must be:

```text
PASS: 15 stores; Y0=15; stack balanced; R2/C preserved; straight-line; <= 68 cycles
```
