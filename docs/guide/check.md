# Checking a CPL

`jlcplace check` takes a KiCad board and a CPL and tells you, row by row, where JLC will put each part. A CPL
(component placement list, the pick-and-place file you upload to JLCPCB with the BOM) has one row per part: its
reference, the X and Y of its centre, its side and its rotation.

For every row, jlcplace takes the footprint JLC itself uses for the part's LCSC code (from JLC's EasyEDA library),
puts its pads down exactly where the row tells JLC to put the part, and finds the KiCad copper pad, and the net, that
each of those pads lands on. A row is green when every pad lands on its own pin, and red when a pad lands off copper
or on another pin's net. The CPL can come from any tool: kicad-jlcpcb-tools, Fabrication-Toolkit, KiKit, KiCad's own
position file, a spreadsheet, or [`jlcplace cpl`](./cpl).

This page covers what `check` reads, how to read its summary, the options that change what is checked, and every
output it can write. [Getting started](./getting-started) is the short tour; the
[command reference](../reference/cli#jlcplace-check) lists every option.

## The example files

The examples on this page use the two boards of [dial-me-spinner](https://github.com/tyevco/dial-me-spinner), a
public KiCad project. Its first fab release shipped CPLs with 7 wrong rows (main J1, J20 and J40; base J1, J2, J3 and
SW1). Commit a5f70dc corrected them: its CPLs are saved below as `released-main-CPL.csv` and `released-base-CPL.csv`,
and the first release's as `old-main-CPL.csv` and `old-base-CPL.csv`. [Getting started](./getting-started) downloads
the same files.

::: details Get the example files
```sh
B=https://raw.githubusercontent.com/tyevco/dial-me-spinner
NEW=a5f70dcb261cfebc490fd5aa2956613b593ebfa3   # the fixed CPLs
OLD=8816f0b662914a1e1aea03265aba6b4ec9f2c327   # the first fab release, 7 rows wrong
for b in main base; do
  curl -sSfLO $B/$NEW/hardware/kicad/$b/dial-me-spinner-$b.kicad_pcb
  curl -sSfLO $B/$NEW/hardware/fab/$b/dial-me-spinner-$b-BOM-JLCPCB.csv
  curl -sSfL -o released-$b-CPL.csv $B/$NEW/hardware/fab/$b/dial-me-spinner-$b-CPL-JLCPCB.csv
  curl -sSfL -o old-$b-CPL.csv $B/$OLD/hardware/fab/$b/dial-me-spinner-$b-CPL-JLCPCB.csv
done
ls
```

```text
dial-me-spinner-base-BOM-JLCPCB.csv
dial-me-spinner-base.kicad_pcb
dial-me-spinner-main-BOM-JLCPCB.csv
dial-me-spinner-main.kicad_pcb
old-base-CPL.csv
old-main-CPL.csv
released-base-CPL.csv
released-main-CPL.csv
```

Then download JLC's footprints for both boards (see [EasyEDA records](#easyeda-records-offline-by-default) below):

```sh
jlcplace fetch dial-me-spinner-main.kicad_pcb dial-me-spinner-base.kicad_pcb
```

```text
...
C318884: SW-SMD_4P-L5.1-W5.1-P3.70-LS6.5-TL_H1.5, 4 pads (cached)
C3682423: DSBGA-8_L1.6-W0.9-R2-C4-P0.40-BL, 8 pads (cached)
58 codes: 57 footprints, 1 not found, 0 failed; cache .../.cache/jlcplace
```
:::

## What check reads

### The board

`BOARD` is the `.kicad_pcb` file, from KiCad 5 to 10. From it jlcplace takes every footprint's position, angle and
side, its copper pads with their numbers and nets, its LCSC code, and its DNP and "exclude from position files /
BOM" flags. It also takes the board's drill/place file origin (KiCad's auxiliary origin), which is where CPL
coordinates are measured from.

KiCad does not have to be installed. When KiCad's `pcbnew` Python module can be imported, jlcplace reads the board
with it; otherwise it uses its own S-expression reader. The first line of the summary says which one it used
(`pcbnew` or `sexpr`). `--reader sexpr` or `--reader pcbnew` picks one.

`BOARD` can also be a JSON snapshot that `jlcplace snapshot` wrote. A snapshot holds the board exactly as one reader
saw it, so you can read the board once with KiCad's own reader and check it later on a machine without KiCad (a CI
runner, for example). On a machine with KiCad:

```sh
jlcplace snapshot dial-me-spinner-main.kicad_pcb -o dial-me-spinner-main.json
```

```text
jlcplace: wrote dial-me-spinner-main.json: 209 parts (pcbnew), origin aux (100, 100)
```

Anywhere else:

```sh
jlcplace check dial-me-spinner-main.json released-main-CPL.csv
```

```text
jlcplace 0.2.0: dial-me-spinner-main.kicad_pcb (pcbnew, 209 parts, origin aux 100, 100) vs released-main-CPL.csv (184 rows), bottom reading jlc-2022
...
184 results: 0 red, 2 yellow, 182 green, 0 verified
...
```

The first line names the board the snapshot was made from and the reader that made it.

**LCSC codes.** jlcplace looks for each part's LCSC code in its footprint fields, in this order: `LCSC`,
`LCSC Part`, `LCSC Part #`, `LCSC#`, `LCSC_PN`, `LCSC PN`, `JLCPCB Part`, `JLCPCB Part #`, `JLC`, `JLC_PN`, `JLCPCB`,
then the spellings other tools use (`LCSC P/N`, `LCSC Part Number`, `JLCPCB PN`, `JLC Part #` and others), then
`Part #`, `PN`, `P/N`, `Part No.`, `Part Number` and `MPN`. Case, spaces, `_` and `-` are ignored, and a field whose
value is not a code is skipped (`C0402` typed into the field is a package, not a code: LCSC codes have no leading
zero). If your codes live in a field with another name, name it with `--lcsc-field NAME`; if they live only in the
BOM you upload to JLC, pass the BOM with `--bom` (see [below](#bom-check-against-the-bom-jlc-assembles-from)).

### The CPL

`CPL` is the file to check. jlcplace finds the header row by its column names, whatever their spelling: JLC's own
`Designator, Mid X, Mid Y, Layer, Rotation`, and the common alternatives (`Ref`, `Reference`, `PosX`, `Center-X`,
`Side`, `Rot`, `Angle`, ...). Columns may be separated by commas, semicolons or tabs. KiCad's position file is read
too, in either of its formats (CSV, or the column-aligned `.pos` text file). The file may be UTF-8 (with or without a
byte order mark), cp1252, or UTF-16 with a byte order mark (Excel's "Unicode Text"), with any line ends.

**Units.** A value with a unit (`12.5mm`, `500mil`, `0.5in`) is converted. A bare number takes the unit of its
column header (`Center-X(mil)`), or of the `## Unit =` line of a KiCad `.pos` file; otherwise it is read as mm. KiCad's
CSV position file does not record its unit: for one exported in inches, pass `--units in` (or `--units mil`).
`check` suggests it when most rows would sit on their parts in that unit (see
[Troubleshooting](./troubleshooting#every-row-is-red-and-the-numbers-look-small)).

**Origin.** CPL coordinates are measured from an origin, in mm, with y pointing up. jlcplace takes them from the
board's drill/place file origin unless you say otherwise:

| `--origin` | CPL coordinates are measured from |
|---|---|
| `aux` (the default) | KiCad's drill/place file origin, the one KiCad's position export uses with "Use drill/place file origin" |
| `grid` | KiCad's grid origin |
| `absolute` | KiCad's page origin (0, 0), the one KiCad's position export uses without that option |
| `X,Y` | that point, in mm or with units: `--origin 100,100`, `--origin 4in,-500mil` |

The first line of the summary prints the origin in use (`origin aux 100, 100`). When almost every row is red and
would sit on its part measured from the page origin, `check` says to pass `--origin absolute` (see
[Troubleshooting](./troubleshooting#every-row-is-red-by-about-the-same-distance)).

### A CPL from KiCad's position file

KiCad's position file (File > Fabrication Outputs > Component Placement, or `kicad-cli pcb export pos`) has the right
columns, so it is tempting to upload it as the CPL. jlcplace reads it like any other CPL:

```sh
kicad-cli pcb export pos --format csv --units mm --side both --use-drill-file-origin --exclude-dnp -o dial-me-spinner-main-pos.csv dial-me-spinner-main.kicad_pcb
jlcplace check dial-me-spinner-main.kicad_pcb dial-me-spinner-main-pos.csv --bom dial-me-spinner-main-BOM-JLCPCB.csv
```

```text
Wrote position data to 'dial-me-spinner-main-pos.csv'.
jlcplace 0.2.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs dial-me-spinner-main-pos.csv (184 rows), bottom reading jlc-2022
RED     BZ1  C910763    bottom  2 of 2 EasyEDA pads land off copper: 1, 2 (nearest KiCad pad centre up to 3.14 mm away)
...
RED     J20  C262671    top     32 of 32 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 1.34 mm away)
...
RED     U2   C2681320   top     wrong-pad: EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 (/Power & link/LSW_CT, not VSYS), 2->5 (/Power & link/LSW_QOD, not GND), 3->6 (VLED, not LED_PWR_EN), 4->1 (VSYS, not /Power & link/LSW_CT), 5->2 (GND, not /Power & link/LSW_QOD), 6->3 (LED_PWR_EN, not VLED)
             correction (fit, auto, yellow): rms 0.213 mm: the land patterns differ (every pad lands)
...
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 17 red, 1 yellow, 166 green, 0 verified
...
```

17 rows are red. The position file carries KiCad's anchors and angles, and JLC's footprints are drawn differently:
the EasyEDA origin of the FPC connector J20 is 1.34 mm from KiCad's anchor, the SOT-23-6 parts U2 and U21 are drawn
turned 180 degrees (every pad still lands on copper, but on the wrong pins), and on the bottom side JLC reads the
rotation another way. [Why parts end up misaligned](../concepts/why) goes through these cases.

## EasyEDA records: offline by default

To simulate a row, jlcplace needs JLC's footprint for the part's LCSC code: the EasyEDA record. `check` reads records
from a cache on your disk and never downloads anything unless you pass `--online`. Download them once with `fetch`,
which takes boards or LCSC codes:

```sh
jlcplace fetch C7519 C318884
```

```text
C7519: SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL, 6 pads (cached)
C318884: SW-SMD_4P-L5.1-W5.1-P3.70-LS6.5-TL_H1.5, 4 pads (cached)
2 codes: 2 footprints, 0 not found, 0 failed; cache .../.cache/jlcplace
```

A record that is not yet in the cache is downloaded (the line ends in `fetched`), at most one request every 0.7 s.
With `--online`, `check` downloads the records the cache lacks itself, the same way. Without the records, a row
cannot be simulated: it is yellow for a two-pad non-polar part (a resistor, a capacitor) whose row point lies on its
pads, and red otherwise, and the summary ends with a line naming the missing codes (see
[Troubleshooting](./troubleshooting#almost-every-row-says-placement-not-simulated)). The cache location is set by
`JLCPLACE_CACHE` or `--cache DIR` (see [Exit codes and environment](../reference/environment)).

## Reading the summary

Here is the old CPL of the base board, from the first fab release:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb old-base-CPL.csv
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs old-base-CPL.csv (17 rows), bottom reading jlc-2022
RED     J1   C165948    top     15 of 16 EasyEDA pads land off copper: 2, 3, 4, A1B12, A4B9, A5, A6, A7 ... (nearest KiCad pad centre up to 2.40 mm away)
RED     J2   C160404    top     6 of 6 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6 (nearest KiCad pad centre up to 1.40 mm away)
RED     J3   C160411    top     14 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 3.40 mm away)
RED     SW1  C318884    top     4 of 4 EasyEDA pads land off copper: 1, 2, 3, 4 (nearest KiCad pad centre up to 127.26 mm away)
17 results: 4 red, 0 yellow, 13 green, 0 verified
Preview checklist (3 items): look at these in JLC's placement preview
  uncertain correction  SW1: red fit correction (auto): numbering conflict: EE 2 -> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview; only 1 of 2 EasyEDA pads with a number KiCad has matched by number
  odd angle             J2 (310), J3 (30)
```

**The first line** says what was compared with what: the board, the reader that read it (`sexpr`), its number of
footprints (all of them, including fiducials and mounting holes JLC does not place), the CPL origin (`aux`, at
(100, 100) mm in KiCad's coordinates), the CPL and its number of rows, and the bottom reading (how JLC is assumed to
read the rotation of a bottom-side part; see [`--reading`](#reading-the-bottom-side-rotation)).

**One line per red or yellow row**, red first, then by reference: the verdict, the reference, the LCSC code (`-` when
the part has none), the side, and the first message. Green rows are counted, not listed. Here the three connectors
are 1.4 to 3.4 mm off their pads, and SW1 is 127 mm away: its row says (-90, 90) instead of (0, 0). The numbers
after "land off copper" are EasyEDA pad numbers, which can differ from KiCad's: on this USB-C connector, `A1B12` is
the EasyEDA pad for KiCad's pads A1 and B12.

In the checklist line, `EE 2 -> KiCad 1` means that EasyEDA's pad 2 lands on KiCad's pad 1 (EE is short for
EasyEDA).

Some rows get a second, indented line that starts with `correction`. The correction is how JLC's footprint must be
turned and shifted to sit on the KiCad footprint; it comes from the database (`db`) or is fitted from the pads of both
footprints (`fit`). Then come its status (`auto`: fitted by jlcplace, not confirmed by a person) and its own verdict.
When a row's pads land on pads of other numbers and the correction is itself red or yellow, that line gives the
correction's own reason:

```text
RED     SW1  C318884    top     renumbered: the pads land as the correction (auto) puts them, but it is red, so nothing vouches for this numbering: 2->1
             correction (fit, auto, red): numbering conflict: EE 2 -> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview
```

That is SW1 in the released base CPL (`jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv`): its
row is where the fit puts the switch, but the fit itself cannot tell whether EasyEDA's pads 1 and 2 are one terminal,
as KiCad's two pads numbered 1 are. [Writing a CPL](./cpl#sw1-a-part-the-records-cannot-decide)
shows how to record that decision.

**The count line** adds up the verdicts: red (fix the row), yellow (probably right, but look), green (every pad on its
own pin) and verified (green, and the row matches a correction someone confirmed in JLC's preview or on an assembled
board). With `--bom` it also counts BOM issues. [Verdicts and statuses](../concepts/verdicts) lists every case.

**The preview checklist** lists what an offline simulation cannot settle, to look at in JLC's placement preview
before you order: corrections in doubt, parts with no footprint to simulate, bottom-side parts whose pins would move
if JLC read bottom rotations another way, and rows at angles that are not multiples of 90 degrees. Alike parts share
one item. [JLC's preview and confirm](./preview) works through it.

**Lines that start with `jlcplace:`** come last. They are hints, not results: the codes the cache lacked, or a
suggestion such as `--units in` or `--origin absolute` when most rows would sit on their parts that way.

The summary shows only the first message of each row. The other messages (the KiCad pads nothing lands on, how far
the row is from the one jlcplace would write) are in every output below.

## Options that change what is checked

### `--bom`: check against the BOM JLC assembles from {#bom-check-against-the-bom-jlc-assembles-from}

JLC assembles from the LCSC codes in your BOM and never sees the board. With `--bom`, jlcplace does the same: it
simulates the part the BOM names, also where the board names another code. It also checks that the BOM, the board
and the CPL agree: every CPL row has a BOM line and the other way round, every part JLC should assemble is listed,
quantities match their designators, and no designator appears twice. The BOM may use JLC's columns (`Comment,
Designator, Footprint, LCSC Part #`) or common alternatives.

Here the base board's BOM has been edited the way a hurried copy and paste would: U2's line carries C2681320, the
code of a part on the main board, and RT1's line is gone.

```sh
diff dial-me-spinner-base-BOM-JLCPCB.csv edited-base-BOM.csv
```

```text
13d12
< 10k B3435,RT1,R_0402_1005Metric,C77131,NCP15XH103F03RC,1
16c15
< USBLC6-2SC6,U2,SOT-23-6,C7519,USBLC6-2SC6,1
---
> USBLC6-2SC6,U2,SOT-23-6,C2681320,USBLC6-2SC6,1
```

Checked against the CPL that [`jlcplace cpl`](./cpl) wrote, with the correction database from that page (which
settles SW1):

```sh
jlcplace check dial-me-spinner-base.kicad_pcb dial-me-spinner-base-CPL-JLCPCB.csv --db jlcplace-db --bom edited-base-BOM.csv
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs dial-me-spinner-base-CPL-JLCPCB.csv (17 rows), bottom reading jlc-2022
RED     U2   C2681320   top     6 of 6 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6 (nearest KiCad pad centre up to 1.21 mm away)
YELLOW  SW1  C318884    top     renumbered: numbering accepted by the correction (manual): 2->1
             correction (db, manual, yellow): accepted manually (you): TS-1187A datasheet: EasyEDA pins 1-2 and 3-4 are each one terminal
BOM     U2: JLC places C2681320 from the BOM; the board says C7519 (checked with C2681320)
BOM     RT1: a CPL row but no BOM line (JLC will not place it)
BOM     RT1 (C77131): JLC should assemble it but the BOM does not list it
17 results: 1 red, 1 yellow, 15 green, 0 verified; 3 BOM issues
...
```

The CPL row for U2 is right for C7519, the part on the board. JLC would place C2681320, another SOT-23-6 part whose
EasyEDA footprint is drawn differently, so its pads land off copper. BOM issues are yellow: they make the run fail
only with `--fail-on yellow`.

The BOM also supplies codes the board lacks. A board whose parts carry no LCSC code at all gets a hint to pass
`--bom` or `--lcsc-field` (see [Troubleshooting](./troubleshooting#every-row-says-the-board-part-has-no-lcsc-code)).

### `--db`: use the correction database

`--db DIR` is a directory of stored corrections and verifications, one JSON file per LCSC code, made to be committed
with the board. With it, a part's correction comes from the database when an entry matches the part's code, its
EasyEDA footprint and its KiCad pads; otherwise it is fitted. Rows that match a correction someone verified in JLC's
preview or on an assembled board are `verified`, and decisions such as SW1's numbering stay made:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --db jlcplace-db
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs released-base-CPL.csv (17 rows), bottom reading jlc-2022
YELLOW  SW1  C318884    top     renumbered: numbering accepted by the correction (manual): 2->1
             correction (db, manual, yellow): accepted manually (you): TS-1187A datasheet: EasyEDA pins 1-2 and 3-4 are each one terminal
17 results: 0 red, 1 yellow, 16 green, 0 verified
...
```

Without that database the same CPL has SW1 red. A part with a stored correction is simulated even when its EasyEDA
record is not in the cache: its KiCad pads, taken back through the correction, stand in for EasyEDA's, and its
verdict is capped by the correction's. That is how a CI job checks a CPL with no records at hand (see
[Continuous integration](./ci)). `check` never writes to the database; `fit` and `confirm` do (see
[The correction database](./database)). If `DIR` does not exist, `check` warns and goes on without stored
corrections.

### `--reading`: the bottom-side rotation {#reading-the-bottom-side-rotation}

JLC does not document how it reads the rotation of a part on the bottom side. jlcplace assumes `jlc-2022` (180 minus
KiCad's angle, plus the correction, viewed from below), the reading every open tool uses. Every bottom row is also
placed under the three other possible readings, and parts whose pins would move go on the preview checklist under
"bottom reading". `--reading` makes another reading the one that decides the verdicts; you should only need it if JLC
changes how it reads bottom rotations. Under `jlc-2022+180`, 10 of the 33 bottom rows of the released main CPL turn
red:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb released-main-CPL.csv --reading jlc-2022+180
```

```text
jlcplace 0.2.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs released-main-CPL.csv (184 rows), bottom reading jlc-2022+180
...
RED     U12  C7519      bottom  wrong-pad: EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 (USB_DM, not USB_DP), 2->5 (/MCU, flash & debug/VBUS_NRF, not GND), 3->6 (USB_DP, not USB_DM), 4->1 (USB_DP, not USB_DM), 5->2 (GND, not /MCU, flash & debug/VBUS_NRF), 6->3 (USB_DM, not USB_DP)
...
184 results: 10 red, 1 yellow, 173 green, 0 verified
...
```

[How the check works](../concepts/how-it-works) explains the four readings.

## Outputs

Every output below can be written in the same run, alongside the terminal summary. `-q` drops the summary (the
`jlcplace:` hint lines still print).

```sh
jlcplace check dial-me-spinner-base.kicad_pcb old-base-CPL.csv -q --report report.html --json results.json --junit results.xml --checklist checklist.md --github-annotations
```

### `--report`: the HTML report

One self-contained HTML page, with no external resources, to open in a browser. Its header names the board and the
CPL (not their paths). The top gives the verdict counts and draws both sides of the board: KiCad's copper pads
filled, and JLC's pads outlined where each row puts them, in the colour of the row's verdict (blue for a green or
verified row, amber for yellow, red for red). Red and yellow parts are
labelled and link to their cards. The bottom side is drawn seen from below, as JLC's preview shows it, and the purple
`+` is the CPL origin.

![The top of an HTML report: the verdict counts, a note on the source of the EasyEDA pad outlines, and the top and bottom of the board with each part's EasyEDA pads outlined in its row's colour](/img/report-overview.png)

*The top of a report for a small made-up board ([the documentation's example](../examples/make_examples.py)), with
two red rows: the connector J1 and the SOT-23-6 U1.*

Below the board views come the preview checklist and a card for every row: red first, then yellow, green and
verified (green and verified cards start closed). A card draws the part's KiCad pads, filled and numbered, and JLC's
pads outlined where the row puts them: **blue** on a pad of their own number, **amber** on a pad of another number,
**red and dashed** off copper. The purple `+` is the row's point and the dotted circle is the point jlcplace would write. Beside the drawing
are the row, the row jlcplace would write, the KiCad and EasyEDA footprints, the correction, and every message.

![The card of a connector whose row is 1.3 mm off: every EasyEDA pad outlined red and dashed beside the KiCad pads it should cover](/img/card-offset.png)

*The example board's J1, a 6-pin FPC connector placed at KiCad's anchor: its EasyEDA origin is 1.3 mm away, so every pad lands off
copper. The CPL row and the row jlcplace would write differ only in Y.*

![The card of a SOT-23-6 turned 180 degrees: every EasyEDA pad on copper, outlined amber, each on the pad opposite its own number](/img/card-rotated.png)

*U1, a SOT-23-6 whose EasyEDA footprint is drawn turned 180 degrees, placed at KiCad's angle. Every pad lands on
copper, so a check that only asks "is it on copper?" passes it, but each pad is on the opposite pin (EasyEDA pad 1 on
KiCad pad 4) and on another net.*

![The card of the same SOT-23-6 with the right row: every EasyEDA pad outlined blue on the KiCad pad of its own number](/img/card-ok.png)

*U1 with the row jlcplace writes (rotation 180): every pad on its own pin.*

::: warning Keep the report to yourself
The report draws EasyEDA's pad outlines, which belong to the JLCEDA/EasyEDA Official Library; the report says so
above the board views and the cards. It is for your own review: do not publish it, for example as a CI artifact of a
public repository. The other outputs hold derived data only. See [Data and licensing](../about/licensing).
:::

### `--json`: the results as data

Everything the run found, as strict JSON: the board and CPL, the counts, one entry per result (the row, the row
jlcplace would write, every message, where each EasyEDA pad landed, the correction), the BOM issues and the
checklist. It holds pad numbers and derived numbers, no EasyEDA geometry. `--json -` writes it to standard output and
moves the summary to standard error.

`jq`, a command-line JSON tool, picks out parts of it:

```sh
jq '.summary' results.json
```

```text
{
  "results": 17,
  "rows": 17,
  "red": 4,
  "yellow": 0,
  "green": 13,
  "verified": 0,
  "bom_issues": 0,
  "worst": "red"
}
```

```sh
jq -c '.results[] | select(.verdict == "red") | {ref, status, line, row, expected}' results.json
```

```text
{"ref":"J1","status":"off-copper","line":7,"row":{"ref":"J1","x":28.7642,"y":-2.0977,"layer":"top","rotation":90.0},"expected":{"ref":"J1","x":29.7791,"y":0.0,"layer":"top","rotation":90.0}}
{"ref":"J2","status":"off-copper","line":8,"row":{"ref":"J2","x":-13.4861,"y":-10.2626,"layer":"top","rotation":310.0},"expected":{"ref":"J2","x":-12.586,"y":-11.335,"layer":"top","rotation":310.0}}
{"ref":"J3","status":"off-copper","line":9,"row":{"ref":"J3","x":-10.7252,"y":11.7784,"layer":"top","rotation":30.0},"expected":{"ref":"J3","x":-7.7815,"y":13.4775,"layer":"top","rotation":30.0}}
{"ref":"SW1","status":"off-copper","line":16,"row":{"ref":"SW1","x":-90.0,"y":90.0,"layer":"top","rotation":0.0},"expected":{"ref":"SW1","x":0.0,"y":0.0,"layer":"top","rotation":0.0}}
```

`line` is the row's line in the CPL file, `row` is what it says, and `expected` is the row jlcplace would write from
the part's correction. [File formats](../reference/files) describes every field.

### `--junit`: one test case per row

A JUnit XML file for CI systems that show test results: one test case per result, named by reference. Red rows are
failures (yellow ones too with `--fail-on yellow`) whose text is every message of the row:

```text
<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="jlcplace" tests="17" failures="4">
  <testsuite name="jlcplace old-base-CPL.csv" tests="17" failures="4" errors="0" skipped="0" timestamp="2026-10-22T14:05:31">
    <testcase classname="jlcplace.dial-me-spinner-base" name="C1" />
...
    <testcase classname="jlcplace.dial-me-spinner-base" name="SW1">
      <failure type="red" message="4 of 4 EasyEDA pads land off copper: 1, 2, 3, 4 (nearest KiCad pad centre up to 127.26 mm away)">4 of 4 EasyEDA pads land off copper: 1, 2, 3, 4 (nearest KiCad pad centre up to 127.26 mm away)
KiCad pads that no EasyEDA pad lands on: 1, 2
differs from the row jlcplace writes (0.0000, 0.0000, top, 0): 127.279 mm and 0 deg away</failure>
    </testcase>
...
```

### `--checklist`: the preview checklist as a file

The preview checklist as a Markdown file with tick boxes, or as CSV when the name ends in `.csv`. Both hold the same
items as the summary. Red rows are flagged "fix the row first": there is no point checking them in the preview
before the row is fixed.

::: code-group

```text [checklist.md]
# JLC preview checklist: dial-me-spinner-base.kicad\_pcb

CPL old-base-CPL.csv, bottom reading `jlc-2022`, jlcplace 0.2.0; 3 items.

Open the order's placement preview at JLC and look at each part below. Tick it when the part sits on its pads with pin 1 / polarity where the board wants it. An item for several parts names the one to look at.

## Uncertain correction

- [ ] **SW1** (C318884, top, rot 0) **(red: fix the row first)**: red fit correction (auto): numbering conflict: EE 2 -\> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview; only 1 of 2 EasyEDA pads with a number KiCad has matched by number

## Odd angle

- [ ] **J2** (C160404, top, rot 310) **(red: fix the row first)**: rotation 310 is not a multiple of 90: check the part sits square on its pads
- [ ] **J3** (C160411, top, rot 30) **(red: fix the row first)**: rotation 30 is not a multiple of 90: check the part sits square on its pads
```

```text [checklist.csv]
category,ref,lcsc,layer,rotation,row verdict,what,refs,checked
uncertain correction,SW1,C318884,top,0,red,"red fit correction (auto): numbering conflict: EE 2 -> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview; only 1 of 2 EasyEDA pads with a number KiCad has matched by number",SW1,
odd angle,J2,C160404,top,310,red,rotation 310 is not a multiple of 90: check the part sits square on its pads,J2,
odd angle,J3,C160411,top,30,red,rotation 30 is not a multiple of 90: check the part sits square on its pads,J3,
```

:::

The CSV has a `checked` column to fill in, and its `refs` column lists every part an item stands for.

### `--github-annotations`: annotations in GitHub Actions

Prints one GitHub Actions workflow command per red or yellow row, naming the CPL as given on the command line and
the row's line in it. In a GitHub Actions job, GitHub shows them as annotations on that file. Red rows are errors; yellow rows and BOM issues
are warnings (errors too with `--fail-on yellow`):

```text
::error file=old-base-CPL.csv,line=7,title=jlcplace J1 off-copper::15 of 16 EasyEDA pads land off copper: 2, 3, 4, A1B12, A4B9, A5, A6, A7 ... (nearest KiCad pad centre up to 2.40 mm away); numbers only one side has: 1->A9; KiCad pads that no EasyEDA pad lands on: A1, A4, A5, A6, A7, A8, A12, B1, B5, B6, B7, B8, B9, B12, S1; differs from the row jlcplace writes (29.7791, 0.0000, top, 90): 2.330 mm and 0 deg away
...
::error file=old-base-CPL.csv,line=16,title=jlcplace SW1 off-copper::4 of 4 EasyEDA pads land off copper: 1, 2, 3, 4 (nearest KiCad pad centre up to 127.26 mm away); KiCad pads that no EasyEDA pad lands on: 1, 2; differs from the row jlcplace writes (0.0000, 0.0000, top, 0): 127.279 mm and 0 deg away
```

The annotations go to standard output, so they cannot be combined with `--json -`. [Continuous
integration](./ci) has a complete workflow.

## Exit codes and `--fail-on`

The exit code says what the count line says, so scripts and CI jobs can act on it:

| code | meaning |
|---|---|
| 0 | nothing red (and, with `--fail-on yellow`, nothing yellow) |
| 1 | something yellow or a BOM issue, and `--fail-on yellow` was given |
| 2 | something red |
| 3 | bad input: a file that cannot be read or written, a bad option or value |

By default only red fails. The released base CPL with the database has one yellow row (SW1, accepted manually):

```sh
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --db jlcplace-db -q
echo "exit code $?"
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --db jlcplace-db -q --fail-on yellow
echo "exit code $?"
```

```text
exit code 0
exit code 1
```

`--fail-on yellow` also makes yellow rows failures in the JUnit file and errors in the annotations. Codes 4 (a bug),
130 (interrupted) and 141 (the reader of the output went away) are in
[Exit codes and environment](../reference/environment).
