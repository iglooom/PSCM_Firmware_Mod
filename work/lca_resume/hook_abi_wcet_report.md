# Hook ABI and WCET analysis for `P:$2A74B`

## Scope and result

This is a read-only analysis of `CV6T-14C217-AR`. No firmware, VBF, or CAN
state was modified.

**Result:** the call at `P:$2A74B` has no register or condition-code live-out
into the remainder of `P:$2A743`. The following instruction is another
argument-free call. The subsequent callees define their working registers and
condition codes before use and preserve the CodeWarrior non-volatile subset.
Therefore the exact minimum hook save/restore sequence is **empty**: use only
volatile registers in an inline leaf writer, do not touch `R5`, `C10`, `D10`,
`M01`, loop registers, or `OMR`, and return with `SP` unchanged. The OEM memory
state is live and must remain read-only except for a separately proven logger
RAM area.

A fixed five-word Stage-1 mailbox can be implemented with an architectural
added cost of **31 core clock cycles** over the OEM direct call. A seven-word
mailbox costs **37 cycles**. An **80-cycle no-interrupt design budget** is
recommended for the proposed seven-word ring writer until final assembled code
can be counted. This is a code-design limit, not evidence of available control-
loop or watchdog margin.

## Evidence and sources

Inputs:

- OEM block: `bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin`
- Generated listing: `work/disasm/out/CV6T-AR.lst`
- NXP/Freescale *DSP56800E and DSP56800EX Core Reference Manual*, Rev. 0
- CodeWarrior *56800/E Digital Signal Controllers: MC56F8xxx/DSP5685x Family
  Targeting Manual*, chapter 6
- Reproducible checks: `work/lca_resume/analyze_hook_abi_wcet.py`

The repository disassembler has operand-name defects for some one-word ALU
opcodes (for example, it prints opcode `8F10` as `TST.W R0` even where the
immediately preceding definition and branch prove that the tested value is
`A`). Conclusions below use the raw words, reliable absolute moves/calls, CFG,
and the compiler ABI; they do not rely on those bad operand labels.

## Caller decode and post-call use

The complete cyclic callback is a straight-line list of calls:

```text
P:$2A743  E256 A7D7  JSR P:$2A7D7
P:$2A745  E256 AD8C  JSR P:$2AD8C
P:$2A747  E256 A8C4  JSR P:$2A8C4
P:$2A749  E256 AD62  JSR P:$2AD62
P:$2A74B  E256 AE86  JSR P:$2AE86       ; proposed replacement site
P:$2A74D  E256 B8EA  JSR P:$2B8EA
P:$2A74F  E256 BA5A  JSR P:$2BA5A
P:$2A751  E256 B8A2  JSR P:$2B8A2
P:$2A753  E256 B8CD  JSR P:$2B8CD
P:$2A755  E708       RTS
```

There is no instruction between the return from `P:$2AE86` and the call to
`P:$2B8EA`: no return-value move, compare, conditional branch, argument setup,
or stack cleanup. `P:$2A743` itself has no frame allocation. Its calls have no
caller-supplied register arguments.

The first consumer, `P:$2B8EA`, immediately calls two argument-free helpers:

```text
P:$2B8EA  E256 B941  JSR P:$2B941
P:$2B8EC  E256 B955  JSR P:$2B955
P:$2B8EE  F07C 2D53  MOVE.W X:$2D53,A
...
P:$2B902  E708       RTS
```

`P:$2B941` begins by reading `X:$2DDE`, then loads `A` from `X:$2DB9` before
its tests. `P:$2B955` starts with its own frame and saved-register prologue,
then initializes a local and loads `A` from `X:$2D20`. Thus neither helper has
a register/flag input inherited from `P:$2AE86`.

The remaining top-level consumers are the same shape:

- `P:$2BA5A` creates a frame, saves `C10`, and defines its ALU inputs from
  absolute X memory before use.
- `P:$2B8A2` is a leaf and begins `MOVE.W X:$2DBA,A`.
- `P:$2B8CD` is a leaf and begins with an immediate definition of `A`.

### Live-out classification at `P:$2AE86` return

| State | Live into code after `P:$2A74B`? | Evidence / requirement |
|---|---|---|
| `A`, `B`, `Y`, `X0`, `Y0`, `Y1` | No | CodeWarrior volatile; no use before the following callees define/clobber them. |
| `R0`–`R4`, `N`, `N3` | No | CodeWarrior volatile; no argument setup and no proven read-before-definition in the consumers. |
| CCR flags (`N/Z/V/C`, etc.) | No | `JSR` itself does not alter them, but the next callee establishes its own predicates; the caller does not branch on them. |
| `C10`, `D10`, `R5` | ABI-live / must be preserved | CodeWarrior non-volatile set. Do not use them in the logger. (`C2`/`D2` remain volatile.) |
| `SP` | Live | Must be identical on hook return; the software return stack resides in X memory. |
| `M01`, `OMR`, `LA/LC`, `LA2/LC2`, HWS | Environment-live | Do not touch. `M01` and `OMR.CM` have required C-runtime values; loop/control state is not scratch state. |
| OEM X memory | Live | Later consumers communicate through global memory, not call-return registers. |

The **proven immediate memory live-ins** to the post-hook consumers include:

- `X:$2DDE` and `X:$2DB9`: read by `P:$2B941` and `P:$2B955`;
- `X:$2D53`: read at `P:$2B8EE` and in paths inside `P:$2B955`;
- `X:$2D20`, `X:$2D21`, `X:$2D22`, `X:$2DBA`: downstream state used to
  derive `X:$2D26` and later outputs;
- further global state read by nested callees.

`X:$2D52` is not proven locally live in the remaining instructions of this
callback, but it is a persistent output/result and a requested observation
source. It must be treated as live globally. The logger may read it but must
not write it. The same conservative rule applies to every OEM X-memory cell:
only the separately validated logger allocation may be written.

## `P:$2AE86` calling convention and stack behavior

`P:$2AE86` is a `void(void)`-shaped orchestration routine. It begins with
`TST.W X:$2D57`, not a stack prologue. On its enabled path it calls:

```text
P:$2AEAA  JSR P:$2B0E8
P:$2AEAC  JSR P:$2B10E
P:$2AEAE  JSR P:$2B305
P:$2AEB0  JSR P:$2AEC2
P:$2AEB2  JSR P:$2AF2D
P:$2AEB4  JSR P:$2B054
```

On the inhibited path it writes zeros to `X:$2D53`, `$2D54`, `$2D52`,
`$2D4B`, and `$2D49`. Both paths converge on `RTS` at `P:$2AEC1`. There is no
return-value contract: the caller consumes memory only.

The observed compiler convention matches the CodeWarrior manual:

- volatile: `Y`, `X0`, `A`, `B`, accumulator extension portions, `R0`–`R4`,
  `N`, `N3`, flags;
- non-volatile: `C10`, `D10`, and `R5` when used as a pointer/frame pointer;
- `SP` is a 24-bit word pointer, the stack grows upward, and the compiler keeps
  it odd/long-aligned.

A representative downstream non-leaf, `P:$2B955`, demonstrates the convention:

```text
P:$2B955  827B  ADDA #2,SP
P:$2B956  D22B  MOVE.L C10,X:(SP)+
P:$2B957  D33F  MOVE.L D10,X:(SP)
P:$2B958  827B  ADDA #2,SP          ; local long
...
P:$2BA46  9F7E  SUBA #2,SP
P:$2BA47  F33B  MOVE.L X:(SP)-,D10
P:$2BA48  F23B  MOVE.L X:(SP)-,C10
P:$2BA49  E708  RTS
```

Its explicit stack delta is zero at return and it restores `C10/D10`.
`P:$2BA5A` similarly saves/restores `C10`. This also explains listing uses of
`R7`: in these encodings it is the stack pointer alias.

A DSP56800E absolute `JSR` pushes the 16-bit PC and SR (which contains the
upper PC bits), in that order, increasing `SP` by two words. `RTS` discards the
saved SR except for the PC-high bits, restores PC, and decreases `SP` by two.

For an inline leaf writer trampoline:

- the replacement `JSR trampoline` uses the same two-word frame as the OEM
  direct call;
- the nested `JSR P:$2AE86` temporarily adds two more words;
- after the OEM return only the trampoline frame remains;
- the final `RTS` restores the pre-hook `SP` exactly.

Therefore peak stack use increases by **exactly two X-memory words** versus the
OEM call. A separately called writer is unnecessary and would add another
transient two-word frame; inline it.

## Exact minimum save/restore sequence

Under the proven caller liveness and CodeWarrior ABI, the sequence is:

```text
; after JSR P:$2AE86 returns
; SAVE:    none
; writer:  may clobber only volatile A/flags (and R0 if a ring pointer is needed)
; RESTORE: none
RTS
```

This is minimal in the literal sense: zero save instructions and zero restore
instructions. Broadly saving `A/B/C/D/Y/R0/R1/SR` would add latency and stack
risk without preserving any caller-live volatile value. In particular, do not
write `SR` merely to preserve dead flags; SR writes can affect interrupt
priority/mode state. Do not use `C10`, `D10`, or `R5`, because doing so would
create a real save/restore obligation.

This conclusion provides **observable ABI equivalence**, not bit-for-bit values
in dead volatile registers. If a future hook site places an instruction that
uses a returned register or flags before the next call, rerun liveness and add
only that state to the save set.

## Cycle analysis

### Architectural instruction costs used

From the core reference manual:

- `JSR <ABS19>`: 4 cycles, two program words;
- `RTS`: 8 cycles;
- `MOVE.W X:xxxx,reg` or `MOVE.W reg,X:xxxx`: 2 cycles;
- `MOVE.W X:xxxx,X:xxxx`: 3 cycles;
- register ALU increment: 1 cycle.

### Fixed five-word mailbox

An auditable Stage-1 writer, using `A` and flags only, is:

```text
JSR    P:$2AE86                    ; 4
MOVE.W X:$6D06,A                   ; 2  counter (candidate address only)
INC.W  A                           ; 1
MOVE.W A1,X:$6D06                  ; 2
MOVE.W A1,X:$6600                  ; 2  sample[0]
MOVE.W X:$2DDE,X:$6601             ; 3  sample[1]
MOVE.W X:$2DB9,X:$6602             ; 3  sample[2]
MOVE.W X:$2D53,X:$6603             ; 3  sample[3]
MOVE.W X:$2D52,X:$6604             ; 3  sample[4]
RTS                                ; 8
```

Total beyond the replacement-site `JSR trampoline`: **31 cycles**. Since the
replacement-site JSR costs the same four cycles as the removed OEM JSR, the
net WCET increment over OEM is also **31 cycles**. Adding `X:$1CB1` and
`X:$171B` as two absolute-to-absolute moves adds six cycles, for **37 cycles**.
The sample addresses above remain candidates because RAM ownership is a
separate open blocker; this is not build-ready assembly.

At a *confirmed* 60 MHz core clock, 31 and 37 cycles would be approximately
0.517 and 0.617 microseconds. The MC56F8366 is rated up to 60 MHz, but the
firmware's actual configured core clock has not been established, so these
numbers are illustrations, not timing claims. In general, elapsed time is
`cycles / f_core`.

### Seven-word ring writer bound

The final writer is not assembled, so an exact WCET claim would be false. Set a
pre-build acceptance budget of **80 architectural cycles added to the OEM
path**, composed as follows:

| block | cycles |
|---|---:|
| extra inner JSR + trampoline RTS | 12 |
| counter update/publication | 7 |
| load write pointer | 2 |
| seven absolute-load/indirect-store pairs | 21 |
| publish pointer/index | 4 |
| end/wrap comparisons and branches, worst path | 10 |
| wrap/valid/freeze metadata, worst path | 20 |
| pipeline/interlock reserve | 4 |
| **design bound** | **80** |

This budget requires a leaf, fixed-trip writer: no loops, division, diagnostic
calls, interrupt masking, waiting, locks, or retry. Final machine code must be
recounted instruction by instruction, including taken-branch timing and any
assembler-inserted NOPs; if it exceeds 80 cycles, reject it rather than silently
raising the limit.

The bounds exclude interrupt service time and non-core memory wait states. An
interrupt may preempt the writer because interrupts remain enabled; ISR time is
system interference, not hook execution time. Only internal P flash and X RAM
should be used.

## Watchdog and control-loop margin

No numerical margin is supported by current evidence:

- the absolute period/deadline of the `P:$2A743` runnable is unknown;
- the actual core/PLL clock is unverified;
- COP enable, timeout, service point, and current slack were not traced;
- no stock execution-time measurement or high-water stack measurement exists;
- no observation image has been run.

The 20 ms CAN cadence does not establish the internal runnable period. The
MC56F8366's maximum 60 MHz rating does not establish this firmware's clock.
Consequently, it is valid to state a bounded cycle increment but **not** a
watchdog percentage or control-loop utilization margin.

Before build authorization, measure parked on target: actual hook cadence,
core clock, stock and instrumented runnable duration, COP service interval,
reset/DTC behavior, and stack high-water mark. The Stage-1 31-cycle fixed
mailbox is the appropriate first probe after RAM ownership is independently
resolved.

## Reproduction

```text
python3 work/lca_resume/analyze_hook_abi_wcet.py
STATIC CHECKS: PASS
OEM P:$2A74B: E256 AE86
5-word fixed-mailbox added cost: 31 cycles
7-word fixed-mailbox added cost: 37 cycles
7-word ring conservative design budget: 80 cycles
Extra peak stack for inline writer: 2 words
```

The script asserts the OEM caller words, the start/end of `P:$2AE86`, the
post-hook consumer entry words, and the `P:$2B955` save/restore frame. It only
reads the OEM binary.
