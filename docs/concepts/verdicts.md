# Verdicts and statuses

Every row of a CPL that jlcplace checks gets one result. A CPL (component placement list) is the pick-and-place file
you upload to JLCPCB: one row per part, with its reference, position, side and rotation. Each result has a
**verdict**, which says what to do, and a **status**, which says what happened. The summary prints the verdict, then
the status and the first message (the status is left out where the message already says it). The other outputs
carry every message, and the JSON output and the HTML report name the status as well.

The examples use the dial-me-spinner files from [Checking a CPL](../guide/check#the-example-files).

## Verdicts

| verdict | what it means | what to do | exit code |
|---|---|---|---|
| red | JLC would place the part wrong, the row should not be there, or nothing vouches for the row | fix the row, or settle the part, before you order | 2 |
| yellow | probably right, but something needs a look | look at it, usually in JLC's preview (the [preview checklist](../guide/preview) lists what to look at) | 0, or 1 with `--fail-on yellow` |
| green | every EasyEDA pad lands on its own pin; harmless differences are allowed | nothing | 0 |
| verified | green, and the row matches a correction someone verified in JLC's preview or on an assembled board | nothing | 0 |

The exit code follows the worst result; BOM issues count as yellow. The other exit codes (3 for bad input, 4 for a
bug, 130, 141) are in [Exit codes and environment](../reference/environment).

## Statuses

| status | verdict | what happened | what to do |
|---|---|---|---|
| `ok` | green or verified; yellow or red in [the cases below](#ok) | every EasyEDA pad lands on its own pin | nothing, unless the verdict is yellow or red: then read the message |
| `off-copper` | red | some EasyEDA pads land off copper: the part is turned or shifted | fix the row's Rotation or position ([why](./why)) |
| `wrong-pad` | red; [yellow](#wrong-pad) against a fitted correction | EasyEDA pads land on copper, but on other pins' nets | fix the Rotation: a part turned 180 degrees, or a polar part reversed |
| `renumbered` | green when the difference is harmless; else the correction's verdict | EasyEDA pads land on KiCad pads of other numbers, and that is accepted | nothing when green; otherwise settle the correction |
| `offset` | yellow | every pad lands, but the row is off where a known correction puts the part | compare the row with the one jlcplace writes |
| `verified-wrong` | red | the row is the stored correction's, and JLC's preview, the assembled board or a person showed that correction wrong (`jlcplace confirm --failed`) | fix the correction and confirm it again ([below](#verified-wrong)) |
| `not-simulated` | yellow for a non-polar two-pad part whose row point is on its pads; red otherwise | no EasyEDA footprint, and no stored correction | `jlcplace fetch` the record; for a part EasyEDA does not know, look in JLC's preview |
| `no-row` | red | JLC assembles the part (it has an LCSC code and is placed) but the CPL has no row for it | add the row, or take the part out of the assembly |
| `not-on-board` | red | the row's reference is not on the board | remove the row, or fix the reference |
| `layer-mismatch` | red | the row says one side, the part is on the other | fix the row's Layer |
| `not-placed` | red for a DNP, board-only (KiCad's "Not in schematic") or "exclude from position files" part; yellow when the part is only excluded from the BOM | the row is for a part JLC must not place | remove the row, or fix the part's flags in KiCad |
| `duplicate` | red | the CPL has more than one row for the reference (every copy gets this) | keep one row |
| `duplicate-ref` | red | two parts that JLC places carry the same reference: JLC cannot tell them apart | give one of them another reference in KiCad ([Troubleshooting](../guide/troubleshooting#duplicate-references)) |

Here the released base CPL is edited by hand: R4's row deleted, C1 moved to the bottom side, C2 repeated, U2 moved
0.3 mm, and rows added for TP1 (a test point excluded from the position files) and R9 (not on the board):

```sh
{ grep -v -E '^(R4|C1|U2),' released-base-CPL.csv; echo 'C1,-5.3476mm,-16.0420mm,Bottom,0'; echo 'C2,-7.5714mm,-19.0100mm,Top,90'; echo 'U2,18.7720mm,-0.0773mm,Top,0'; echo 'TP1,23.3720mm,-3.8450mm,Top,0'; echo 'R9,10.0000mm,10.0000mm,Top,0'; } > edited-base-CPL.csv
jlcplace check dial-me-spinner-base.kicad_pcb edited-base-CPL.csv
```

```text
jlcplace 0.1.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs edited-base-CPL.csv (19 rows), bottom reading jlc-2022
RED     C1   C277470    top     layer-mismatch: the row says bottom, the part is on the top
RED     C2   C96446     top     duplicate: 2 rows for C2
RED     C2   C96446     top     duplicate: 2 rows for C2
RED     R4   C25741     top     JLC assembles it (it has an LCSC code) but the CPL has no row
RED     R9   -          top     not-on-board: R9 is not on the board
RED     SW1  C318884    top     renumbered: the pads land as the correction (auto) puts them, but it is red, so nothing vouches for this numbering: 2->1
             correction (fit, auto, red): numbering conflict: EE 2 -> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview
RED     TP1  -          top     not-placed: the board part is excluded from position files, excluded from the BOM: JLC must not place it
YELLOW  U2   C7519      top     offset: differs from the row jlcplace writes (18.7720, -0.3773, top, 0): 0.300 mm and 0 deg away, more than 0.15 mm or 2 deg: the part sits off where the correction puts it
20 results: 7 red, 1 yellow, 12 green, 0 verified
...
```

There are 20 results for 19 rows: R4 has no row, so it gets a result of its own. SW1 is red in the released CPL
too, until someone records how the switch is wired (see [Writing a CPL](../guide/cpl#sw1-a-part-the-records-cannot-decide)).
The indented line under it gives the reason its correction is red.

## Notes on each status

### `ok` {#ok}

Usually green. A row matching a verified correction, within 0.05 mm and 0.5 degrees of the row jlcplace writes, is
**verified**. U2's correction, confirmed from the preview, makes U2 verified:

```sh
jlcplace confirm dial-me-spinner-base.kicad_pcb --ref U2 --db verified-db --by you --source preview --cpl released-base-CPL.csv
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --db verified-db
```

```text
U2 C7519: preview-verified, verified: rot 270, dx 0.0000, dy 0.0000; row X 18.7720 Y -0.3773 rot 0 (verified-db/C7519.json)
jlcplace 0.1.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs released-base-CPL.csv (17 rows), bottom reading jlc-2022
RED     SW1  C318884    top     renumbered: the pads land as the correction (auto) puts them, but it is red, so nothing vouches for this numbering: 2->1
             correction (fit, auto, red): numbering conflict: EE 2 -> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview
17 results: 1 red, 0 yellow, 15 green, 1 verified
...
```

An `ok` row is **yellow** when:

* `lands as the correction puts it, but the fit was unsure of that placement: ...`: the row is where the fitted
  correction puts the part, but the fit chose among placements that all land (a polarity only the pad numbers
  decide, pin names against pad numbers, an orientation the pads do not fix). BZ1 on the main board is this case.
  Look at the part in JLC's preview.
* `KiCad pads that no EasyEDA pad lands on: ...`: JLC's footprint leaves some of your soldered pads bare. Thermal
  vias inside a covered pad, and similar copper of a pin that is covered, are only mentioned, not counted. Check that
  the part is the one you meant.
* `the pad numbers do not decide this placement (...), and no correction is known`: the pads land, but too few
  EasyEDA pad numbers match KiCad's to say the pins are right, and there is no correction to compare with.
* `yellow because the stored ... correction is yellow: ...`: the row was simulated from a stored correction,
  without the EasyEDA record, and capped ([below](#capped)).
* `the row point is outside the board outline (Edge.Cuts), 10.00 mm beyond its edge: look at it in JLC's preview`:
  JLC puts the part's centre off the board (a cutout counts as off it). A part overhanging the edge can have its
  centre there; a row of another part, or from another origin, too. With `--gerbers` the outline is the Gerbers',
  unless their check finds it wrong (plotted from another origin, another board's: a `gerbers` finding says so), and
  then the board's Edge.Cuts.
  Any other row outside the outline gets the same message, last.

An `ok` row is **red** when the part's correction is red and nothing else vouches for the row:

* `the pads land as the correction puts them, but it is red: the polarity needs a decision (...)`: a polar
  two-terminal part whose correction could not settle its polarity.
* `the pad numbers do not decide this placement (...) and the correction is red (...): nothing vouches for it`.

Settle the part in JLC's preview or from its datasheet, and record it with `jlcplace confirm` (see
[JLC's preview and confirm](../guide/preview)).

### `off-copper`

`N of M EasyEDA pads land off copper: 1, 2, ... (nearest KiCad pad centre up to D mm away)`. The pad numbers are
EasyEDA's, and D is how far the worst of them is from the nearest KiCad pad centre. When the part has a correction,
a later message gives the row jlcplace would write: `differs from the row jlcplace writes (X, Y, side, rotation): D
mm and A deg away`. A difference in rotation points to a turned part ([why](./why#zero-angle)), a difference in
position to a shifted one ([why](./why#origin)), and every row off by the same large distance to the CPL's origin or
units ([why](./why#origin-units)). The summary shows only the first message; the others are in the JSON output, the
annotations and the HTML report.

### `wrong-pad` {#wrong-pad}

Every pad on copper, some on another pin's net. The message has one of three forms:

* `EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 (net, not net) ...`: red. The usual case: a
  part turned 180 degrees ([why](./why#symmetric)) or a polar part reversed ([why](./why#polarity)).
* `the pads land by number, but the correction puts them on other nets: ...` (or `the pad numbers do not decide this
  placement, but ...`): the pads land on their own numbers, but the correction says the numbers are not the pins.
  Red against a verified correction, for a polar two-terminal part, or when the numbers do not decide the row;
  yellow against a fitted correction, which could itself be wrong.
* `the pads land by number, but the correction is red and puts them on other nets: the polarity needs a decision
  (...)`: red.

### `renumbered`

EasyEDA pads on KiCad pads of other numbers, accepted:

* `the two terminals are swapped (non-polar part): 1->2, 2->1`: green. A resistor or capacitor turned 180 degrees.
* `numbering accepted by the correction (manual): 2->1`: the correction gives every pad the net the row does, so the
  row takes the correction's verdict: green, yellow, or verified. SW1 with a recorded decision is yellow this way.
* `the pads land as the correction (auto) puts them, but it is red, so nothing vouches for this numbering: 2->1`:
  red, as for SW1 without one.

Smaller differences only add a message: `EasyEDA pads on other-numbered KiCad pads of the same net`, and
`numbers only one side has` (EasyEDA's mounting pads, or `A1B12` on KiCad's A1 and B12).

### `offset`

Every pad lands on its own pin, but the row is more than 0.15 mm (or a quarter of the smallest pad it lands on, when
that is larger) or 2 degrees from the row of a known correction that is not red. Also yellow `offset`: a row a quarter
turn from the correction's row whose pads all land as the correction puts them, which only alike lands allow (a long
pad lying across its land). The first message gives both rows. Use the row jlcplace writes unless you know why
yours differs. A row more than 0.05 mm or 0.5 degrees off, but within those limits, stays green with a message,
`differs from the row jlcplace writes (...)`.

### `verified-wrong` {#verified-wrong}

The row is where the stored correction puts the part (within the `offset` limits), and the latest verification of
that correction failed: `verified wrong by JLC's preview on 2026-10-22 (U3, you: pin 1 at the wrong corner): the row
is the stored correction's; fix the correction (...) and confirm it again`. Red whatever the pads land on: what was
wrong may be the footprint's numbering, or a body its pads do not orient. The parts that share the entry (one code
on one footprint) are red alike, and `jlcplace cpl --db` writes no CPL with such a row unless `--force`. Record the
row that is right (the presses that put it right in the preview, or the correction typed in with `--rot --dx --dy`),
then confirm it in the preview again; a later verification of the same row that went right ends it too. A row away
from that correction is yellow, with `the stored correction was verified wrong by ...; this row is ... from its row`:
it is not the row that was wrong, but nothing verified vouches for it either.

### `not-simulated`

There is nothing to place: no EasyEDA footprint for the code, and no stored correction. The message says why and
what follows:

```sh
mkdir no-records
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --cache no-records
```

```text
jlcplace 0.1.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs released-base-CPL.csv (17 rows), bottom reading jlc-2022
jlcplace: no EasyEDA record in the cache no-records for 15 of the 15 codes JLC places (C7519, C11702, C19666, C25741, C25905, C77131, C96446, C151296 and 7 more), so the rows below are checked without JLC's footprints (red where a part needs one): `jlcplace fetch dial-me-spinner-base.kicad_pcb --cache no-records` downloads them (rate-limited), or pass --online
RED     D1   C151296    top     placement not simulated: no EasyEDA footprint (no EasyEDA record in the cache (`jlcplace fetch` or --online downloads it)); a polar two-pad part; the row point is the centre of its courtyard
RED     J1   C165948    top     placement not simulated: no EasyEDA footprint (no EasyEDA record in the cache (`jlcplace fetch` or --online downloads it)); not a two-pad non-polar part (20 copper pads); the row point is 1.01 mm from the centre of its courtyard
...
YELLOW  C1   C277470    top     placement not simulated: no EasyEDA footprint (no EasyEDA record in the cache (`jlcplace fetch` or --online downloads it)); a two-pad non-polar part, and the row point is on it
...
17 results: 8 red, 9 yellow, 0 green, 0 verified
...
```

A resistor or capacitor whose row point lies on its pads is yellow: turned either way it is fine, and it is where it
should be. If its row point is more than 0.1 mm outside its pads (`the row point is ... mm off the part's pads`), it
is red. A polar
two-pad part, a one-pad part, or anything with more pads is red, and the message ends with where the row point lies:
`the row point is the centre of its courtyard`, `the row point is KiCad's anchor, pin 1, 13.50 mm from the centre of
its courtyard: JLC places a part by its centre` (KiCad's anchor of a through-hole connector is usually pin 1), or how
far it is from that centre (J1's EasyEDA origin, which the released CPL uses, is 1.01 mm from it). `jlcplace cpl`
writes such a part at its courtyard's centre. Inside the parentheses, `no EasyEDA record in the
cache` means `jlcplace fetch` has not been run for it; `Component not found` means EasyEDA has no footprint for the
code (see [Troubleshooting](../guide/troubleshooting#component-not-found)). A row for a part without an LCSC code is
not simulated either, and its first message is `the board part has no LCSC code`; with `--bom`, such a part that the
BOM does not list is yellow, `not in the BOM: JLC does not place it`.

### Capped by a stored correction {#capped}

With a correction database and no EasyEDA records, as in a CI job, a part with a stored correction is still
simulated: its KiCad pads, taken back through the correction, stand in for EasyEDA's. That simulation can only be as
good as the correction, so the row's verdict is capped by the correction's, and the first message says so:

```sh
jlcplace fit dial-me-spinner-main.kicad_pcb --db main-db
jlcplace check dial-me-spinner-main.kicad_pcb released-main-CPL.csv --db main-db --cache no-records
```

```text
...
184 parts: 0 red, 7 yellow, 177 green; database main-db
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs released-main-CPL.csv (184 rows), bottom reading jlc-2022
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview
YELLOW  D20  C2500      top     ok: yellow because the stored auto correction is yellow: rms 0.281 mm: the land patterns differ (every pad lands)
...
184 results: 0 red, 7 yellow, 177 green, 0 verified
...
```

With the records, D20 and the four other capped parts are green: their corrections are yellow only because the two
land patterns differ a little. Every row simulated this way carries the message `simulated with the KiCad pads
through the stored auto correction: ...` (or the correction's own status). See [Continuous integration](../guide/ci).

### `no-row`, `not-on-board`, `layer-mismatch`, `duplicate`

These compare the CPL's list of rows with the board's list of parts JLC assembles: parts that are placed (not DNP,
not board-only, not excluded from the position files), not excluded from the BOM, and have an LCSC code. A `no-row`
result may add why jlcplace cannot write that row either: `no row is possible: the EasyEDA footprint is a mirror
image of the KiCad one`, or `no correction to make a row from (...)`.

### `not-placed`

`the board part is DNP: JLC must not place it`, or excluded from the position files, or board-only: red. A part that
is only excluded from the BOM is yellow, since JLC places what the BOM it receives lists. With `--bom`, a row for such
a part that the BOM does list is simulated like any other, with the message `the board excludes it from the BOM, but
the BOM lists it: JLC will place it`.

### `duplicate-ref`

`R1 is on the board 2 times: JLC cannot tell them apart`. KiCad lets two footprints share a reference (for example a
footprint and a DNP alternative). When more than one of them is placed, a CPL row cannot say which one it means, so
none of them is simulated. A footprint the board excludes from its position files or its BOM beside an assembled one
of the same reference (KiCad 6, which had no DNP attribute, marked alternatives so) is not counted: the BOM line and
the row are the assembled part's.

## Messages that do not change the verdict

* `differs from the row jlcplace writes (X, Y, side, rotation): D mm and A deg away`: more than 0.05 mm or 0.5
  degrees from the correction's row, within the `offset` limits.
* `bottom reading matters, check it in JLC's preview: ...`: the pins would move under another reading of the bottom
  rotation. The part goes on the preview checklist ([How the check works](./how-it-works#bottom)).
* `KiCad pads that no EasyEDA pad lands on, of pins an EasyEDA pad covers (vias, a cut lead's stub): ...`.
* `EasyEDA pads on other-numbered KiCad pads of the same net: ...` and `numbers only one side has: ...`.
* `simulated with the KiCad pads through the stored ... correction: ...`.

## Corrections have verdicts too

A part's correction (see [How the check works](./how-it-works#fitting)) has its own verdict, green, yellow, red or
verified, with reasons. It shows in `jlcplace fit` and `jlcplace show`, on the indented `correction (...)` line under
a `renumbered` or `wrong-pad` row, and in the preview checklist's "uncertain correction" items. A correction verdict
is not a row verdict: the yellow correction of U2 on the main board (`rms 0.213 mm: the land patterns differ`) gives a
green row.

A correction also has a status, from the [correction database](../guide/database), strongest first:

| status | where it comes from |
|---|---|
| `board-verified` | someone confirmed it on an assembled board (`jlcplace confirm --source board`) |
| `preview-verified` | someone confirmed it in JLC's preview (`--source preview`) |
| `manual` | a person decided it or typed it in (`--source manual`); a red correction accepted this way becomes yellow |
| `auto` | fitted; also a correction whose verification failed (`--failed`), which is then red, and its row `verified-wrong`, until other numbers replace it or a later verification of it goes right |

When EasyEDA changes a footprint, the stored entries for it go stale: the part is fitted again, and a refit that
differs from the stored correction is at best yellow (`the refit differs: check it in JLC's preview, then confirm it
again`).

## BOM issues

With `--bom`, lines starting `BOM` follow the red and yellow rows, and the count line adds `N BOM issues`. They count
as yellow: they fail a run only with `--fail-on yellow`. They are:

* `REF: JLC places C... from the BOM; the board says C... (checked with C...)`: JLC assembles from the BOM, so the
  BOM's code is the one simulated.
* `REF: a CPL row but no BOM line (JLC will not place it)`, `REF (C...): JLC should assemble it but the BOM does not
  list it`, `REF: in the BOM but the CPL has no row for it`, `REF: in the BOM but not on the board`.
* `REF: in the BOM but DNP on the board` (or excluded, or board-only), `REF: N times in the BOM`.
* `BOM line N (...): Qty Q but N designators`, `... no designators`, `... no LCSC code`, `... 'X' is not an LCSC
  code`.

[Checking a CPL](../guide/check#bom-check-against-the-bom-jlc-assembles-from) has an example.
