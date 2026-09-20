# CV6T-14C217-AR UDS 0x22 DID callback ABI

## Scope and result

This is a static, read-only analysis of the OEM `CV6T-14C217-AR` image. No VBF,
flash block, RAM, or CAN interface was modified or opened.

**Result:** an OEM read callback is called as follows:

```c
/* conceptual ABI, not C source */
uint16_t did_read(byte_pointer24 out /* R2 */);
/* exact number of data bytes returned in Y0 */
```

`R2` is a DSP56800E **byte pointer** to a temporary X-memory output buffer on
the diagnostic dispatcher's stack. The handler writes only DID data bytes (not
`0x62`, the DID, or an NRC), then returns the exact byte count in `Y0`. For
`FD22`, that count must remain 15.

Changing the `FD22` read-pointer field is sufficient for the proposed 15-byte
reader; no metadata change is required. However, the pointer field is two
16-bit P words. A target in `P:$3xxxx` requires changing **both**
`P:$0EEA4` (low 16 bits) and `P:$0EEA5` (high word). Changing only the single
word at `P:$0EEA4` is not sufficient.

## Inputs and immutable source

```text
blk0 SHA-256 21d095f6ce8496695951a6ad2f012d428da87ea21c781b4f4a4d0992994e5074
blk1 SHA-256 6e60963a3583993d1b872ebd88bb9b2f1acdeb397a7954a1ae75be3ce7d933b0
blk2 SHA-256 4e65a6493cf770d02a125c115a50c7b6b8d58969405bbd0292fafd896750d6c0
```

The scripts named below reject any other input hashes.

## Dispatcher path

### Service and DID selection

`P:$103CF` is the common DID service selector. Its two service tests are:

```text
P:$103DA  5DC2 0022 A204    compare service byte with 0x22; branch if unequal
P:$103E1  5DC2 002E A204    compare service byte with 0x2E; branch if unequal
```

`P:$103AD` searches the 57-entry, six-word DID table rooted at `P:$0ED5E`.
The read path at `P:$10504` computes the selected six-word record address and
loads the long read callback from record words `+2,+3`. The relevant pointer
load is:

```text
P:$10511  8369              MOVE.W P:(R1)+,A1   ; pointer low word
P:$10512  7C07              LSR16 A,A
P:$10513  8369              MOVE.W P:(R1)+,A1   ; pointer high word
P:$10514  7C01              SXT.L A,A
P:$10515  E021              MOVE.L A.L,R1       ; preserve/test pointer
...
P:$10525  E020              MOVE.L A.L,R0       ; indirect-call register
P:$10526  E614              JSR (R0)
```

This is a direct read from the P table during request dispatch. There is no
boot-time copied callback pointer to update.

### Argument and output buffer

Immediately before `JSR (R0)`, the dispatcher constructs `R2` with:

```text
P:$10522  80B6              ASLA SP,R2
P:$10523  8A32 FFE8         ADDA #$FFE8,R2,R2
P:$10525  E020              MOVE.L A.L,R0
P:$10526  E614              JSR (R0)
```

`ASLA SP,R2` converts the word-addressed stack position to a byte address;
subtracting `0x18` selects its local scratch area. Therefore:

```text
entry R0 = callback code address (used by JSR itself)
entry R2 = output X-memory byte pointer
entry C  = configured expected length and is live across the callback
```

No OEM read handler examined a DID argument or request-data pointer. A custom
read handler must not assume that any register other than `R2` is an input.

After the callback, the dispatcher reconstructs `R2` rather than relying on its
old value, passes the returned count to `P:$32056`, and that routine appends the
scratch bytes to the shared diagnostic response. The `0x62` and DID bytes are
owned by the surrounding diagnostic framework, not by the callback.

## Return convention and status

At `P:$10527`, opcode `7956` is `CMP.W C,Y0` (ERM Table A-7 maps `EEE=010` to
`C` and `aaa=101` to `Y0`). Thus:

* callback `Y0` is the exact payload length, not a Boolean and not an NRC;
* callback success for `FD22` is exactly `Y0=0x000F`;
* after a valid append, the dispatcher returns `Y0=1` to its caller;
* dispatcher failure returns `Y0=0` and writes the NRC/status in its diagnostic
  control block.

Observed failure paths in `P:$10504` are:

| condition | action |
|---|---|
| read callback pointer is zero | store `0x12`, return `Y0=0` |
| callback length differs from configured length `C` | store `0x13`, return `Y0=0` |
| callback length equals configured length | append exactly that many bytes, return `Y0=1` |

The outer selector at `P:$103CF` also stores `0x12` and returns `Y0=0` when
the DID lookup reaches the 57-entry limit. A service byte other than `0x22` or
`0x2E` stores `0x11` and returns `Y0=0`. These are the literal OEM response
codes; the implementation uses `0x12` for the no-DID/null-reader cases rather
than the more usual UDS `0x31`.

There is no separate callback status return. A logger status byte must therefore
be payload byte 0, as proposed, while `Y0` still returns 15.

The length check occurs **after** the handler has written its scratch output.
It is not a bounds check. A handler that writes too many bytes can corrupt the
stack even if it later returns 15. Static code review must prove exactly 15
stores (or a loop bounded to 15).

There is no exception or callback-fault wrapper between `JSR (R0)` and the
length comparison. An illegal instruction, unbalanced stack, bad pointer, or
non-returning callback follows the processor's ordinary fault/reset behavior;
the dispatcher cannot convert it to a negative response.

## OEM handler controls

### FD0C: one-byte control

Table and metadata:

```text
P:$0EE5A  FD0C 0000 C0C0 0001 0000 0000  -> P:$1C0C0
X:$6358  FD0C 0000 0001 0001             -> readable, length 1
```

`P:$1C0C0` calls `P:$20553`. The leaf saves `R5`, copies `R2` to `R5`, calls
the value getter `P:$1C177`, stores the returned byte through `X:(R5)`, restores
`R5`, and sets `Y0=1`. `P:$1C177` reads `X:$171B`, scales by 256, clamps to
`-128..127`, and returns the resulting byte in `Y0`.

### FD0E: independent one-byte control

```text
P:$0EE66  FD0E 0000 C0C6 0001 0000 0000  -> P:$1C0C6
X:$6360  FD0E 0000 0001 0001             -> readable, length 1
```

`P:$1C0C6` calls the structurally identical leaf `P:$20565`. It copies `R2` to
saved `R5`, calls `P:$1C1A7`, writes one byte through the saved pointer, restores
`R5`, and returns `Y0=1`. Its getter independently reads `X:$1CB1`, scales by
512, and clamps to `-128..127`.

These two controls independently establish `R2` as the output pointer and
`Y0=1` as the one-byte return length.

### FD08 and FD20: multi-byte packing controls

`FD08` metadata says length 2 and its leaf `P:$20464` returns `Y0=2`. It shifts
the value right by eight for offset 0 and emits the unshifted low byte at offset
1. `FD20` metadata says length 12; `P:$20710` returns `Y0=12` and repeats the
same high-byte/low-byte order for six words. These controls establish that OEM
multi-byte UDS values are explicitly emitted **most-significant byte first**.

### FD22: 15-byte control and proposed replacement target

```text
P:$0EEA2  FD22 0000 058F 0001 0000 0000
             read pointer = 0x0001:0x058F = P:$1058F
X:$6388  FD22 0000 0001 000F
             read enabled = 1, expected payload length = 15
```

The complete OEM handler behavior is:

```text
R0 = 0x1DAC + 0x2D = byte pointer 0x1DD9
B  = 0
R1 = R2
while (unsigned B < 15):
    A.low8 = BP[X:(R0++)]
    BP[X:(R1++)] = A.low8
    B++
Y0 = 15
return
```

It is a byte-for-byte copy to the dispatcher-provided `R2` buffer and confirms
that an odd source byte pointer and word boundaries do not change stream order.

## Byte-pointer packing and endianness

DSP56800E X memory is word-addressed, but `MOVE.BP` uses a 24-bit byte pointer:

* byte pointer bit 0 selects the octet within the X word;
* an **even** byte pointer addresses the word's low octet;
* an **odd** byte pointer addresses the word's high octet;
* post-increment advances one byte, not one word.

Consequently, writing stream bytes `AA BB CC` at an even byte pointer produces
physical X words `BBAA 00CC`, while the diagnostic byte stream remains
`AA BB CC`. This physical little-octet-first packing must not be confused with
UDS numeric byte order. OEM numeric handlers FD08 and FD20 deliberately emit
high byte first. A custom opaque logger payload may define another order, but
its decoder must match it explicitly. If the logger format remains
little-endian 16-bit words, emit low byte then high byte deliberately; do not
use `MOVE.W` into the BP scratch area.

## Register clobbers and preservation rule

Observed handler-visible behavior:

| handler | definitely clobbered | explicitly/observably preserved |
|---|---|---|
| FD0C / FD0E | `A`, `Y0`, condition codes | `R2`; `R5` and `SP` are saved/restored |
| FD08 | `A`, `B`, `Y0`, condition codes | does not modify `R2` |
| FD22 | `A`, `B`, `X0`, `Y0`, `R0`, `R1`, condition codes | `R2`, `C`, `D`, `R3-R5`, `N`, `Y1`, balanced `SP` |

The OEM controls prove that `A`, `B`, `X0`, `Y0`, `R0`, and `R1` are available
scratch registers. `R2` is the input pointer; although the dispatcher rebuilds
it after the call, preserving it avoids surprising nested helpers. `C` is live
across the call for the exact-length comparison and **must be preserved**.
The one-byte wrappers' save/restore of `R5` is evidence that `R5` is callee
saved. Static evidence here does not prove that `D`, `R3`, `R4`, `N`, or `Y1`
are freely clobberable.

For the new FD22 reader, the safe rule is:

* use only `A/B/X0/Y0/R0/R1` as scratch, plus `R2` as the output pointer;
* return 15 in `Y0`;
* preserve `C/D/R3/R4/R5/N/Y1` and restore `R2` if a helper changes it;
* leave `SP` exactly balanced and do not alter interrupt/mode state.

This matches the existing FD22 clobber footprint and avoids relying on an
unproved wider compiler ABI.

## Is the DID table pointer alone sufficient?

**Yes, at the field level, for a replacement that emits exactly 15 bytes.**
The reasons are independently visible:

1. `FD22` already has read-enable `1` and expected length `0x000F` in
   `X:$6388`; those values already match the proposed status-plus-14-byte page.
2. The read dispatcher loads the callback directly from P-table words `+2,+3`
   on every request.
3. The replacement needs no request parameter and receives its output pointer
   in `R2` exactly as the OEM handler does.
4. Returning `Y0=15` satisfies the dispatcher's configured-length check.

The exact pointer edit is a two-word long pointer. For example, a handler at
`P:$33800` would require:

```text
P:$0EEA4  058F -> 3800    low 16 bits
P:$0EEA5  0001 -> 0003    high word
```

No claim is made here that the proposed cave code or RAM ownership is otherwise
ready to flash; this report closes only the DID callback ABI question.

## Reproduction

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/did_callback_abi.py --selftest
python3 work/lca_resume/did_callback_abi.py --json
python3 work/lca_resume/verify_did_callback_abi.py
```

Expected final lines:

```text
ALL STATIC ABI CHECKS PASS
SELFTEST: ALL STATIC ABI AND BYTE-POINTER CHECKS PASS
```

The scripts only read the three extracted OEM block files and print results.
They assert source hashes, DID entries, runtime metadata, dispatcher opcodes,
failure paths, all named OEM handlers, and byte-order anchors.
