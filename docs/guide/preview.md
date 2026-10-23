# JLC's preview and confirm

A CPL is the pick-and-place file you upload to JLCPCB with your BOM: one row per part, giving its reference,
position, side and rotation. When you order assembly, JLC draws its own footprints at your CPL's rows on a picture
of your board: the placement preview. This page explains what jlcplace leaves for you to look at in that preview,
how to look at it, and how to record what you saw with `jlcplace confirm` so that the next check knows.

The examples continue in the `dms` directory of [Getting started](./getting-started), with the EasyEDA records
fetched and the CPL that `jlcplace cpl` wrote there, `dial-me-spinner-main-CPL-JLCPCB.csv`. One example uses
dial-me-spinner's second board as well, whose files are in the same directory; fetch its records first:

```sh
jlcplace fetch dial-me-spinner-base.kicad_pcb
```

## Why the preview is still the last check

jlcplace places JLC's EasyEDA footprints at your rows on your computer, and it finds most mistakes that way. A few
questions cannot be answered offline:

* **How JLC reads a bottom-side rotation.** JLC does not document it. jlcplace assumes the reading every open tool
  uses (`jlc-2022`), and also places every bottom row under the three other possible readings. Parts whose pins
  would move under another reading can only be settled by looking at JLC's preview (see
  [How the check works](../concepts/how-it-works)).
* **Choices the EasyEDA record cannot make.** A two-pad polar part may land on both pads either way round, and its
  record may carry no pin names or "+" to say which way is right. A switch may be numbered differently in EasyEDA and
  in KiCad, and only its datasheet says which pins are joined.
* **Parts with no EasyEDA footprint.** JLC still places them, but jlcplace has nothing to simulate.
* **Rows at angles that are not multiples of 90 degrees.** jlcplace's handling of EasyEDA rotations is proven at
  multiples of 90. At other angles, a look confirms the part sits square on its pads.

The preview checklist lists exactly these, with alike parts folded into one item, so the list stays short enough to
work through before you pay.

## Get the checklist

Every `check` and `cpl` prints the checklist at the end of its summary. `--checklist` also writes it to a file, as
Markdown tick boxes (`.md`) or as a spreadsheet (`.csv`):

```sh
jlcplace check dial-me-spinner-main.kicad_pcb dial-me-spinner-main-CPL-JLCPCB.csv --bom dial-me-spinner-main-BOM-JLCPCB.csv --checklist checklist.md
```

```text
jlcplace 0.2.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs dial-me-spinner-main-CPL-JLCPCB.csv (184 rows), bottom reading jlc-2022
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 0 red, 2 yellow, 182 green, 0 verified
Preview checklist (12 items, 99 parts): look at these in JLC's placement preview
  uncertain correction  BZ1: yellow fit correction (auto): polarity decided by pad numbers only ...
  not simulated         R26
  bottom reading        J1 (look at it first: it settles the bottom reading), J40, U10, U11, U12, U30, U32, U40 U41
  odd angle             C107 of C102..C112 C114..C124 C126..C136 C138..C148 (C1525, 44 parts at 8 to 353 deg: look at C107)
                        D107 of D102..D112 D114..D124 D126..D136 D138..D148 (C55109522, 44 parts at 8 to 353 deg: look at D107)
```

Twelve items cover 99 of the 184 parts JLC places on this board. The file says the same thing at more length:

```md
# JLC preview checklist: dial-me-spinner-main.kicad\_pcb

CPL dial-me-spinner-main-CPL-JLCPCB.csv, bottom reading `jlc-2022`, jlcplace 0.2.0; 12 items, 99 parts.

Open the order's placement preview at JLC and look at each part below. Tick it when the part sits on its pads with pin 1 / polarity where the board wants it. An item for several parts names the one to look at.

## Uncertain correction

- [ ] **BZ1** (C910763, bottom, rot 158): yellow fit correction (auto): polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview; also the bottom reading matters (as ccw-from-top or ccw-from-top+180, pin 1 would land off copper (2 pins move); as jlc-2022+180, pins 1 and 2 would swap); rotation 158 is not a multiple of 90

## Not simulated

- [ ] **R26** (C4167968, top, rot 0): placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it

## Bottom reading

- [ ] **J1** (C160411, bottom, rot 120): look at this one first: it moves under every other reading, so it shows how JLC reads the bottom side; when it sits right, record it (\`jlcplace confirm --source preview\`, or \`--source board\` on the assembled board) and the other bottom parts' items go. If JLC read the bottom rotation another way: as ccw-from-top or ccw-from-top+180 or jlc-2022+180, pin 1 would land off copper (14 pins move); also rotation 120 is not a multiple of 90
- [ ] **J40** (C160404, bottom, rot 138): if JLC read the bottom rotation another way: as ccw-from-top or jlc-2022+180, pin 1 would land off copper (6 pins move); as ccw-from-top+180, pin 1 would land off copper (5 pins move); also rotation 138 is not a multiple of 90
- [ ] **U10** (C2688382, bottom, rot 235): if JLC read the bottom rotation another way: as ccw-from-top or ccw-from-top+180, pin 1 would land off copper (61 pins move); as jlc-2022+180, pin 2 would sit on pad 34 (57 pins move); also rotation 235 is not a multiple of 90
...
- [ ] **U40 U41** (C39159, bottom, rot 180, 2 parts): if JLC read the bottom rotation another way: as ccw-from-top+180 or jlc-2022+180, pin 1 would sit on pad 5 (8 pins move) (2 parts of one code at one rotation: they move alike)

## Odd angle

- [ ] **C107** of C102–C112 C114–C124 C126–C136 C138–C148 (C1525, top, 44 parts): 44 parts of one code at 8 to 353 degrees, none a multiple of 90: check that one, C107 at 135, sits square on its pads; the others are the same part on the same side
- [ ] **D107** of D102–D112 D114–D124 D126–D136 D138–D148 (C55109522, top, 44 parts): 44 parts of one code at 8 to 353 degrees, none a multiple of 90: check that one, D107 at 135, sits square on its pads; the others are the same part on the same side
```

Each part appears once. When a part has several reasons, the others follow "also" in its item: BZ1 is listed for its
polarity, and its bottom-side reading and its angle are noted there. The CSV checklist has one row per item with a
`refs` column naming every part the item stands for, and an empty `checked` column to fill in. The JSON output
(`--json`) and the HTML report (`--report`) carry the same items.

A red row is never something to settle in the preview. Its item is flagged `(red: fix the row first)` in the Markdown
file and listed first within its category. Fix the CPL, check it again, and upload the new one.

## Work through it

The preview is part of JLC's order form, not a separate tool. Order the boards with PCB assembly, upload the Gerbers,
then the BOM and the CPL; after the parts list, the order shows your board with JLC's parts drawn on it at the CPL's
rows, top and bottom. That is the placement preview. Nothing is paid or produced until you confirm the order, so you
can look, change the CPL and upload it again. JLC changes its order form now and then, so the names of the steps may
differ from these.

Take the categories in order. For each part, find it in the preview (KiCad shows where it is on your board) and
compare JLC's drawing with your board: whether it sits on its pads, and where its pin 1 or polarity mark is.

### Uncertain correction

For each part, jlcplace works out how JLC's footprint must be turned and shifted to sit on your KiCad footprint: the
part's correction. An uncertain correction is one the fit could not settle, for a reason the preview can.

BZ1 is a two-pad buzzer, which jlcplace treats as polar. Its row lands both pads where the correction puts them, but
turned 180 degrees it would land both pads too, and EasyEDA's record has no pin names and no drawn "+" to say which
way is right. Only the pad numbers decided it. **Look at:** the polarity mark JLC draws on BZ1, against the "+" pad
of your KiCad footprint.

### Not simulated

R26 has an LCSC code, C4167968, but EasyEDA has no footprint for it, so nothing was placed. It is a resistor and its
row point lies on its pads, so it is only yellow. A polar or multi-pin part without a footprint would be red.
**Look at:** that R26 sits on its two pads. A resistor may face either way.

### Outside the outline

Not on this board. A row whose point lies outside the board outline (the Gerbers' outline with `--gerbers`, else the
board's Edge.Cuts; a cutout counts as outside) puts the part's centre off the board: right for a part that overhangs
the edge, wrong for a row of another part or from another origin. The parts of one code on one side are one item,
naming the one furthest out. **Look at:** that the part sits on its pads, overhanging the edge as the board means it
to.

### Bottom reading

These are bottom-side parts whose pins would move if JLC read a bottom rotation another way than `jlc-2022`. Each
item says, in pins, what each other reading would do. For U10, `as ccw-from-top or ccw-from-top+180, pin 1 would
land off copper (61 pins move)` means that under either of those readings its pin 1 would land off copper and all 61
of its pins would move. `as jlc-2022+180, pin 2 would sit on pad 34 (57 pins move)` means that under that reading
its pin 2 would land on KiCad pad 34.

The reading is one question for the whole board, and the first item answers it. **Look at J1 first.** Under every
other reading J1 would sit off its pads, with 13 or 14 of its 14 pads off copper, which is easy to see. If J1 sits on
its pads with pin 1 where the board wants it, JLC reads the bottom side as jlcplace assumed, and the other bottom
parts follow. Record it (see [A part that was right in the preview](#a-part-that-was-right-in-the-preview)): a
reading under which a part verified in the preview or on the assembled board would have moved is ruled out, for every
part, so once J1 is verified the bottom reading items go. A part verified at a reading that does not move it rules
out only the readings that do; the items that remain say which readings are ruled out, and by which part. Parts of
one code at one rotation share an item (U40 and U41) because they move alike.

::: warning If J1 is not on its pads
Then JLC reads bottom rotations another way than jlcplace assumed, and every bottom row on the board is in doubt. Do
not put the bottom parts right one by one in the preview. Compare what the preview shows with what each item says
the other readings would do, then write and check the CPL for the reading that matches:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb dial-me-spinner-main-CPL-JLCPCB.csv --reading jlc-2022+180
jlcplace cpl dial-me-spinner-main.kicad_pcb -o dial-me-spinner-main-CPL-JLCPCB.csv --reading jlc-2022+180
```

The first command shows what the current CPL does under that reading (on this board, every bottom part in the
checklist is red). The second writes a CPL for it. Upload that and look again.
:::

### Odd angle

Rows at angles that are not multiples of 90 degrees, one item per code and side. This board has a ring of 44
capacitors and 44 LEDs at angles from 8 to 353 degrees. Each group is one item, naming the part furthest from a
multiple of 90 (C107 and D107, both at 135 degrees). **Look at:** that the named part sits square on its pads, turned
as far as its pads are.

## Record what you saw

`jlcplace confirm` stores what you checked in a correction database: a directory of JSON files, one per LCSC code,
made to be committed with your project (see [The correction database](./database)). Later checks and `cpl` runs
with `--db` read it, so a part you have seen right in the preview is marked verified, and a part that was wrong
cannot quietly come back.

`--source` says what you checked:

| `--source` | what you checked | `--cpl` | status it stores |
|---|---|---|---|
| `preview` | JLC's placement preview | the CPL you uploaded (or the correction typed in with `--rot`, `--dx`, `--dy`) | `preview-verified` |
| `board` | the assembled boards | the CPL you ordered with (or the correction typed in) | `board-verified` |
| `manual` | a decision of your own, such as a pinout from the datasheet | not needed | `manual` |

`--by` names who checked, and `--note` adds free text. Both are stored with the verification.

`--cpl` matters because the preview showed that file's row, which need not be jlcplace's correction: the CPL may
come from another tool. jlcplace records the uploaded row. When its correction differs from the stored one, it
replaces the stored one, and the output ends with `replaces the auto correction ...` (a verified entry is protected:
see [A part that was wrong](#a-part-that-was-wrong-failed)).

### A part that was right in the preview

J1 sat on its pads, so the bottom reading is settled. Record it:

```sh
jlcplace confirm dial-me-spinner-main.kicad_pcb --ref J1 --db jlcplace-db --by you --source preview --cpl dial-me-spinner-main-CPL-JLCPCB.csv
```

```text
J1 C160411: preview-verified, verified: rot 0, dx -0.0002, dy -0.0626; row X 16.9416 Y -9.7815 rot 120 (jlcplace-db/C160411.json)
```

The line gives the part's new status (`preview-verified`) and verdict (`verified`), the correction that was verified,
the CPL row that correction gives (the uploaded row), and the database file. The database did not exist yet, so
`confirm` created it and fitted J1's correction before recording the verification.

`--ref` can be given more than once. BZ1's polarity mark and R26 were right too:

```sh
jlcplace confirm dial-me-spinner-main.kicad_pcb --ref BZ1 --ref R26 --db jlcplace-db --by you --source preview --cpl dial-me-spinner-main-CPL-JLCPCB.csv
```

```text
BZ1 C910763: preview-verified, verified: rot 0, dx -0.0001, dy -0.0001; row X 20.3979 Y 8.2412 rot 158 (jlcplace-db/C910763.json)
R26 C4167968: preview-verified, verified: rot 0, dx 0.0000, dy 0.0000; row X 7.9170 Y -20.5264 rot 0 (jlcplace-db/C4167968.json)
```

R26 has no EasyEDA footprint, so its entry holds the uploaded row's correction. Check again with the database:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb dial-me-spinner-main-CPL-JLCPCB.csv --bom dial-me-spinner-main-BOM-JLCPCB.csv --db jlcplace-db
```

```text
jlcplace 0.2.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs dial-me-spinner-main-CPL-JLCPCB.csv (184 rows), bottom reading jlc-2022
184 results: 0 red, 0 yellow, 181 green, 3 verified
Preview checklist (4 items, 90 parts): look at these in JLC's placement preview
  odd angle  C107 of C102..C112 C114..C124 C126..C136 C138..C148 (C1525, 44 parts at 8 to 353 deg: look at C107)
             D107 of D102..D112 D114..D124 D126..D136 D138..D148 (C55109522, 44 parts at 8 to 353 deg: look at D107)
             J40 (138), U10 (235)
```

The yellow rows are gone and three rows are verified. A confirmed part leaves the checklist. J1, verified, would
have moved under every other bottom reading, so none of them is left: the bottom reading items are gone, and the
odd angles of J40 and U10, folded into their bottom reading items before, are items of their own. An odd angle item
stays until a part of it is looked at: a part verified at its angle (BZ1, J1) has been, and so has every part of its
code on its side once one of them was verified at an odd angle (C107 for the ring of capacitors).

`confirm` needs the part's EasyEDA record in the cache, because a verification is stored under the EasyEDA footprint
it was made with: when EasyEDA later changes that footprint, the verification no longer applies. If the record is
missing, `jlcplace fetch` downloads it, or pass `--online`.

Parts that share a database entry (one LCSC code on one KiCad footprint, such as U40 and U41) must agree on it:
`confirm` refuses to record two of them together when their rows give different corrections. Everything is checked
before anything is written.

### A decision of your own

On dial-me-spinner's other board, the tactile switch SW1 is numbered 1 to 4 in EasyEDA and 1, 1, 2, 2 in KiCad.
Which EasyEDA pins are joined inside the switch is in its datasheet, not in EasyEDA's record, so the fit cannot
decide the numbering and the row stays red. (SW1 is also the row the project's first CPL put 127 mm from its pads.)
Once you have read the datasheet, record the decision:

```sh
jlcplace confirm dial-me-spinner-base.kicad_pcb --ref SW1 --db jlcplace-db --by you --source manual --note "pins 1-2, 3-4 joined"
```

```text
SW1 C318884: manual, yellow: rot 0, dx 0.0000, dy 0.0000; row X 0.0000 Y 0.0000 rot 0 (jlcplace-db/C318884.json)
```

```sh
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --db jlcplace-db
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs released-base-CPL.csv (17 rows), bottom reading jlc-2022
YELLOW  SW1  C318884    top     renumbered: numbering accepted by the correction (manual): 2->1
             correction (db, manual, yellow): accepted manually (you): pins 1-2, 3-4 joined
17 results: 0 red, 1 yellow, 15 green, 1 verified
Preview checklist (3 items): look at these in JLC's placement preview
  uncertain correction  SW1: yellow db correction (manual): accepted manually (you): pins 1-2, 3-4 joined; numbering conflict: EE 2 -> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview
  odd angle             J2 (310), J3 (30)
```

A manual decision makes SW1 yellow, not green: nothing but your reading of the datasheet vouches for it, so it stays
on the checklist. Once the preview shows the switch right, `--source preview` with the uploaded CPL makes it
verified. Until then, refitting the board with `jlcplace fit --db` puts SW1 back to red (see
[Status and precedence](./database#status-and-precedence)).

The `1 verified` row is J3. It is the same connector on the same KiCad footprint as J1 on the main board, so J1's
verification counts for it (see [Keys](./database#keys-which-parts-share-an-entry)).

### The assembled boards

When the boards arrive and a part is right on them, record that with `--source board` and the CPL you ordered with.
A board verification is the strongest status:

```sh
jlcplace confirm dial-me-spinner-main.kicad_pcb --ref J1 --db jlcplace-db --by you --source board --cpl dial-me-spinner-main-CPL-JLCPCB.csv
```

```text
J1 C160411: board-verified, verified: rot 0, dx -0.0002, dy -0.0626; row X 16.9416 Y -9.7815 rot 120 (jlcplace-db/C160411.json)
```

### A part you put right in the preview

When a part sits wrong, JLC's preview lets you turn it in quarter turns and nudge it with the arrow keys. Tell
`confirm` what you pressed, and it records the row as the preview showed it after the presses:

| key in the preview | option | how jlcplace counts each press |
|---|---|---|
| space | `--space N` | a quarter turn counter-clockwise, as the preview shows the part: 90 degrees added to the row's rotation |
| right arrow (left: negative) | `--right N` | 0.0635 mm (1/400 inch) to the right on the screen |
| up arrow (down: negative) | `--up N` | 0.0635 mm up on the screen |

The presses are relative to the row you uploaded, never to the stored correction, which is why they need `--cpl`.
jlcplace takes the preview to show the bottom side seen from below, so a right press on a bottom part moves it to the
right as seen from below, which is towards smaller Mid X in the CPL. These conventions are those of
Fabrication-Toolkit's rotation and position offsets, not documented by JLC. jlcplace's tests have checked space
presses on top-side parts against recorded JLC previews, not arrow presses or presses on bottom-side parts (see
[Limits](../about/limits#keys-pressed-in-jlc-s-preview)): after recording presses, upload the CPL that `cpl` writes and
look at those parts in the preview again.

The example below uses the made-up board that [`docs/examples/make_examples.py`](../examples/make_examples.py)
builds, not dial-me-spinner (its `--keep DIR` option leaves the board `example.json`, its CPLs and its made-up
EasyEDA records in DIR). Its bad CPL has the SOT-23-6 U1 at KiCad's angle, half a turn from where JLC's
footprint needs it. Every pad lands on copper, each on the wrong pin, as jlcplace's report draws it:

![A part card from jlcplace's HTML report: a SOT-23-6 whose EasyEDA pads land on copper, but pad 1 on KiCad pad 4, pad 2 on pad 5, and so on](/img/card-rotated.png)

Say you had uploaded that CPL, seen U1's pin-1 mark at the wrong corner, and pressed space twice. Record it with
the presses (`--cache cache` because the example's made-up records live in its own directory):

```sh
jlcplace confirm example.json --ref U1 --db jlcplace-db --by you --source preview --cpl example-CPL-bad.csv --space 2 --cache cache
```

```text
U1 C1001: preview-verified, verified: rot 180, dx 0.0000, dy 0.0000; row X 8.0000 Y 7.5000 rot 180 (jlcplace-db/C1001.json)
```

The uploaded row had rotation 0; two presses make it 180, which is also where jlcplace's fit puts U1, so the fitted
correction is what was verified. The presses are stored with the verification. `cpl` now writes the row the preview
showed:

```sh
jlcplace cpl example.json -o example-CPL.csv --db jlcplace-db --cache cache
grep U1 example-CPL-bad.csv example-CPL.csv
```

```text
jlcplace 0.2.0: example.json (json, 14 parts, origin aux 100, 118) vs example-CPL.csv (7 rows), bottom reading jlc-2022
7 results: 0 red, 0 yellow, 6 green, 1 verified
Preview checklist (1 item): look at these in JLC's placement preview
  bottom reading  Q1
jlcplace: wrote example-CPL.csv (7 rows)
example-CPL-bad.csv:U1,8.0000mm,7.5000mm,Top,0
example-CPL.csv:U1,8.0000mm,7.5000mm,Top,180
```

### A part that was wrong: `--failed`

When the preview or the boards show a part wrong and you do not put it right there, record the failure with
`--failed`. It marks the stored correction red and drops its status to `auto`, and the verification is kept with
`"ok": false` and the numbers it was of. Suppose the preview had shown U1 of the example board with its pin-1 mark at
the wrong corner, though it was uploaded with the good CPL:

```sh
jlcplace confirm example.json --ref U1 --db jlcplace-db --by you --source preview --cpl example-CPL-good.csv --failed --note "pin-1 dot at the wrong corner" --cache cache
```

```text
U1 C1001: auto, red: rot 180, dx 0.0000, dy 0.0000; row X 8.0000 Y 7.5000 rot 180 (jlcplace-db/C1001.json)
jlcplace: U1: its stored correction is verified wrong by JLC's preview on 2026-10-23 (U1, you: pin-1 dot at the wrong corner): `check --db` holds its row red, and `cpl --db` does not write it (unless --force), until the correction is fixed and confirmed again
```

From then on, the stored correction's row is red (status `verified-wrong`), even where the pad numbers put every pad
on its own pin: what the preview showed wrong may be the footprint's numbering, or a body its pads do not orient. So
are the rows of the parts that share its entry (one code on one footprint). `cpl --db` writes no CPL with that row,
unless `--force`:

```sh
jlcplace cpl example.json -o example-CPL.csv --db jlcplace-db --cache cache
```

```text
jlcplace 0.2.0: example.json (json, 14 parts, origin aux 100, 118) vs the rows jlcplace would write (7 rows), bottom reading jlc-2022
RED     U1  C1001      top     verified wrong by JLC's preview on 2026-10-23 (U1, you: pin-1 dot at the wrong corner): the row is the stored correction's; fix the correction (`jlcplace confirm` with the presses that put the part right in the preview, or --rot/--dx/--dy) and confirm it again
7 results: 1 red, 0 yellow, 6 green, 0 verified
Preview checklist (2 items): look at these in JLC's placement preview
  uncertain correction  U1: red db correction (auto): verification failed (preview, you): pin-1 dot at the wrong corner
  bottom reading        Q1
jlcplace: example-CPL.csv not written: the rows above are red; the stored correction of U1 was verified wrong: fix it and confirm again (--force writes it anyway); the existing example-CPL.csv is left unchanged
```

`check --db` says the same of a CPL from another tool whose row is that correction's. A row away from it (more than
0.15 mm and a quarter of the smallest pad it lands on, or 2 degrees) is yellow instead: it is not the row that was
wrong, but nothing verified vouches for it either.

Then fix the correction: record what is right, the presses that put the part right on the next upload, or, if you
know the numbers, the correction typed in with `--rot`, `--dx` and `--dy` and `--source manual` (see the
[command reference](../reference/cli#jlcplace-confirm)), and confirm it in the preview again. Other numbers end the
failure, and so does a later verification of the same row that went right (`--source board` when the assembled
board was right after all).

A failure says something only about the row that was checked, so `--failed` needs the uploaded row to be the stored
correction's row. Otherwise nothing is recorded:

```sh
jlcplace confirm example.json --ref U1 --db jlcplace-db --by you --source preview --cpl example-CPL-bad.csv --failed --cache cache
```

```text
jlcplace: U1: the row in example-CPL-bad.csv is not the stored correction's (rot 180, dx 0.0000, dy 0.0000): its failure says nothing about that correction; nothing recorded
```

`--failed` is also how you take back a verification that turns out wrong. A preview- or board-verified entry is not
given other numbers by a verification that is no stronger. Record the failure of its own row first, then the
right one.

## Next

* [The correction database](./database): what `confirm` writes, and how to keep and share it.
* [Continuous integration](./ci): check every push against the database.
* [Command reference](../reference/cli#jlcplace-confirm): every option of `confirm`.
