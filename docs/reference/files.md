# File formats

This page describes every file jlcplace reads and writes: which columns and spellings it accepts, and what each
field of its own files means. A CPL (component placement list) is the pick-and-place file you upload to JLCPCB with
your BOM: one row per part, with its reference, the X and Y of its centre, its side and its rotation.

| file | read by | written by |
|---|---|---|
| [KiCad board](#the-board) `.kicad_pcb` | every command that takes `BOARD` | |
| [Board snapshot](#board-snapshot) `.json` | every command that takes `BOARD` | `snapshot` |
| [CPL](#cpl-files-jlcplace-reads) | `check`, `confirm --cpl` | [`cpl -o`](#the-cpl-jlcplace-writes) |
| [BOM](#bom) | `check --bom`, `cpl --bom` | |
| [Correction database](#correction-database) `DIR/C*.json` | `check`, `cpl`, `show` (with `--db`) | `fit --db`, `confirm` |
| EasyEDA record cache `C*.json` | every command but `snapshot` | `fetch`, and `--online` (see [the record cache](./environment#the-record-cache)) |
| [JSON results](#json-results) | | `check --json`, `cpl --json`, `fit --json`, `show --json` |
| [Preview checklist](#preview-checklist) `.md` or `.csv` | | `check --checklist`, `cpl --checklist` |
| [JUnit XML](#junit-xml) | | `check --junit`, `cpl --junit` |
| [GitHub annotations](#github-annotations) (standard output) | | `check --github-annotations`, `cpl --github-annotations` |
| [HTML report](#html-report) | | `check --report`, `cpl --report` |

The small examples on this page use the documentation's example board, a made-up 14-part board with made-up EasyEDA
records. [`docs/examples/make_examples.py`](../examples/make_examples.py) writes it: run
`python docs/examples/make_examples.py --keep out/example --no-images` in a clone, and `out/example` holds the board
snapshot `example.json`, a good and a bad CPL, the record cache `cache/` and two reports. The larger examples use
the main board of [dial-me-spinner](https://github.com/tyevco/dial-me-spinner), a public KiCad project; [The example
files](../guide/check#the-example-files) says how to download it.

## The board

`BOARD` is a `.kicad_pcb` file from KiCad 5 to 10, or a [snapshot](#board-snapshot) of one. jlcplace reads the
`.kicad_pcb` with KiCad's `pcbnew` Python module when it can import it, and otherwise with its own S-expression
reader; `--reader sexpr` or `--reader pcbnew` picks one. The first line of every summary names the reader used.

Each part's LCSC code comes from a footprint field. jlcplace looks for these names in this order, ignoring case,
spaces, `_` and `-`, and takes the first field that holds a code:

`LCSC`, `LCSC Part`, `LCSC Part #`, `LCSC#`, `LCSC_PN`, `LCSC PN`, `JLCPCB Part`, `JLCPCB Part #`, `JLC`, `JLC_PN`,
`JLCPCB`, `LCSC P/N`, `LCSC Part No.`, `LCSC Part Number`, `JLCPCB PN`, `JLCPCB P/N`, `JLCPCB Part No.`,
`JLCPCB Part Number`, `JLC Part`, `JLC Part #`, `JLC Part Number`, `Part #`, `PN`, `P/N`, `Part No.`, `Part Number`,
`MPN`.

`--lcsc-field NAME` (repeatable) puts your own names first. A code is `C` followed by digits with no leading zero, so
`C0402` typed into the field is read as no code (it is a package size). `LCSC: C2040` and `c2040` are read as
`C2040`. A BOM given with `--bom` overrides the board's codes (see [BOM](#bom)).

## CPL files jlcplace reads

`check` reads a CPL from any tool, and `confirm --cpl` reads the CPL you uploaded. Both take the same formats.

### Layout

* A header line that names the five columns, then one line per part. Other lines may come before the header (a
  title, notes): jlcplace takes the first line that names all five columns.
* Columns are separated by commas, tabs or semicolons. jlcplace tries them in that order and takes the first under
  which some line names all five columns.
* The columns can be in any order. Other columns are allowed and ignored.
* Blank lines, lines whose first cell starts with `#`, and lines with an empty designator are skipped.
* Rows are read in file order. A designator that appears twice is kept twice, and `check` reports both rows as
  duplicates (red).

### Column names

Header names are matched ignoring case, spaces, `_`, `-`, `.` and a leading `#`, so `Mid X`, `MidX`, `mid_x` and
`# Mid-X` are the same name.

| column | names accepted |
|---|---|
| designator | `Designator`, `Designators`, `Ref`, `Reference`, `RefDes` |
| X | `Mid X`, `Pos X`, `Center X`, `Centre X`, `X`, `Position X`, `Location X` |
| Y | `Mid Y`, `Pos Y`, `Center Y`, `Centre Y`, `Y`, `Position Y`, `Location Y` |
| side | `Layer`, `Side`, `TB`, `Mount Side`, `Mounting Side`, `Board Side`, `Placement Side` |
| rotation | `Rotation`, `Rot`, `Angle`, `Orientation`, `Rotate` |

### Units

X and Y are lengths relative to the CPL origin (the board's aux origin unless `--origin` says otherwise), Y up. A
length's unit is found in this order:

1. A unit written in the cell: `12.5mm`, `500mil`, `500 mil`, `0.5in`, `0.5"`. The unit names are `mm`,
   `millimeter(s)`, `mil`, `mils`, `thou`, `th`, `in`, `inch`, `inches` and `"`.
2. A unit in the column header, in parentheses or brackets: `Center-X(mm)`, `Mid X [mil]`, `Center X (in)`.
3. `--units` (`mm`, `in` or `mil`; default `mm`).

A cell with a comma and no point is read with a decimal comma: `12,5` is 12.5 mm. Every value must be a finite
number: `nan`, `inf` or `1e400` in a cell is bad input (exit code 3, with the file and line).

### Side and rotation

| side | values accepted |
|---|---|
| top | `Top`, `T`, `F`, `Front`, `F.Cu`, `TopLayer`, `Top Side`, `Component`, `Component Side` |
| bottom | `Bottom`, `B`, `Bot`, `Back`, `B.Cu`, `BottomLayer`, `Bottom Side`, `Solder`, `Solder Side` |

Case, spaces, `_`, `-` and `.` are ignored here too. The rotation is in degrees, with any sign and any size: `-90` is
read as 270 and `450` as 90. A `°` or `deg` after the number, and a decimal comma, are allowed.

### Designators

A designator that holds a control character, or that starts with `=`, `+`, `-` or `@`, is bad input (exit code 3). No
KiCad designator looks like that, and such text could start a GitHub workflow command in a CI log or run as a formula
in a spreadsheet. The board and the BOM are checked the same way.

### Text encoding and line ends

jlcplace reads UTF-8 with or without a byte order mark, UTF-16 and UTF-32 with a byte order mark (Excel's "Unicode
Text"), and otherwise cp1252 (what Excel on a Western Windows system writes as "CSV"). Lines may end in LF, CRLF or
CR alone (Excel for Mac's "CSV (Macintosh)").

Here is the example board's good CPL rewritten with semicolons and decimal commas. It checks the same as the
original:

```sh
head -3 example-CPL-semicolons.csv
jlcplace check example.json example-CPL-semicolons.csv --cache cache
```

```text
Designator;Mid X;Mid Y;Layer;Rotation
C1;8,0000mm;3,8000mm;Top;0
C2;4,8000mm;7,5000mm;Top;90
jlcplace 0.1.0: example.json (json, 14 parts, origin aux 100, 118) vs example-CPL-semicolons.csv (7 rows), bottom reading jlc-2022
7 results: 0 red, 0 yellow, 7 green, 0 verified
Preview checklist (1 item): look at these in JLC's placement preview
  bottom reading  Q1
```

The same rows saved as UTF-16 with tabs and CRLF line ends, as Excel's "Unicode Text" saves them:

```sh
file example-CPL-unicode.txt
jlcplace check example.json example-CPL-unicode.txt --cache cache
```

```text
example-CPL-unicode.txt: Unicode text, UTF-16, little-endian text, with CRLF line terminators
jlcplace 0.1.0: example.json (json, 14 parts, origin aux 100, 118) vs example-CPL-unicode.txt (7 rows), bottom reading jlc-2022
7 results: 0 red, 0 yellow, 7 green, 0 verified
...
```

### KiCad position files

`check` also reads KiCad's own position files, from **File > Fabrication Outputs > Component Placement** or
`kicad-cli pcb export pos`. Such a file carries KiCad's footprint anchor and angle with no correction for JLC's
footprints, so checking one shows what that file would do as a CPL (see [Why parts end up
misaligned](../concepts/why)).

The **ASCII** format (`.pos`) says its unit in a `## Unit = mm` or `## Unit = inches` line, and jlcplace uses it.
The header line is `# Ref Val Package PosX PosY Rot Side`, the fields are separated by spaces, and the file ends at
`## End`:

```sh
kicad-cli pcb export pos --format ascii --units mm --use-drill-file-origin dial-me-spinner-main.kicad_pcb -o main.pos
head -7 main.pos
jlcplace check dial-me-spinner-main.kicad_pcb main.pos
```

```text
Wrote position data to 'main.pos'.
### Footprint positions - created on 2026-10-22T14:53:57+0000 ###
### Printed by KiCad version 9.0.9-9.0.9~ubuntu24.04.1
## Unit = mm, Angle = deg.
## Side : All
# Ref     Val                Package                                                  PosX       PosY       Rot  Side
BZ1       PKMCS0909E4000-R1  Buzzer_Murata_PKMCS0909E4000-R1                       20.3980     8.2413   22.0000  bottom
C1        22u                C_0805_2012Metric                                     16.9889   -18.7280    0.0000  bottom
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (pcbnew, 209 parts, origin aux 100, 100) vs main.pos (187 rows), bottom reading jlc-2022
RED     BZ1  C910763    bottom  2 of 2 EasyEDA pads land off copper: 1, 2 (nearest KiCad pad centre up to 3.14 mm away)
RED     C43  C1525      top     not-placed: the board part is DNP: JLC must not place it
...
187 results: 20 red, 1 yellow, 166 green, 0 verified
...
```

Three of the 20 red rows are DNP parts (C43, R11 and U43) that KiCad's file lists unless you export with
`--exclude-dnp`. The other 17 are parts whose JLC footprint is turned or shifted from KiCad's.

The **CSV** format has the header `Ref,Val,Package,PosX,PosY,Rot,Side` and does not say its unit. `kicad-cli`
writes it in inches unless told `--units mm`, and jlcplace reads bare numbers as millimetres, so pass `--units in`
for such a file. `check` says so when most rows would sit on their parts in inches:

```sh
kicad-cli pcb export pos --format csv --use-drill-file-origin dial-me-spinner-main.kicad_pcb -o main-pos.csv
head -3 main-pos.csv
jlcplace check dial-me-spinner-main.kicad_pcb main-pos.csv -q
```

```text
Wrote position data to 'main-pos.csv'.
Ref,Val,Package,PosX,PosY,Rot,Side
"BZ1","PKMCS0909E4000-R1","Buzzer_Murata_PKMCS0909E4000-R1",0.803071,0.324461,22.000000,bottom
"C1","22u","C_0805_2012Metric",0.668854,-0.737323,0.000000,bottom
jlcplace: 187 of 187 rows would sit on their parts if the CPL's numbers were in inches: pass --units in
```

With `--units in`, this file gives the same 187 results as `main.pos`.

::: tip The origin of a position file
KiCad writes positions relative to the page origin unless "Use drill/place file origin" is ticked
(`--use-drill-file-origin` for `kicad-cli`). jlcplace measures from the board's aux origin, the drill/place file
origin. For a file made without it, `check` suggests `--origin absolute`.
:::

## The CPL jlcplace writes

`jlcplace cpl BOARD -o FILE` writes the CPL in JLC's own sample layout:

```sh
jlcplace cpl example.json -o example-CPL.csv --cache cache
cat -A example-CPL.csv
```

```text
jlcplace 0.1.0: example.json (json, 14 parts, origin aux 100, 118) vs example-CPL.csv (7 rows), bottom reading jlc-2022
7 results: 0 red, 0 yellow, 7 green, 0 verified
Preview checklist (1 item): look at these in JLC's placement preview
  bottom reading  Q1
jlcplace: wrote example-CPL.csv (7 rows)
Designator,Mid X,Mid Y,Layer,Rotation^M$
C1,8.0000mm,3.8000mm,Top,0^M$
C2,4.8000mm,7.5000mm,Top,90^M$
J1,13.0000mm,13.5000mm,Top,0^M$
Q1,18.9875mm,5.0000mm,Bottom,90^M$
R1,14.0000mm,7.5000mm,Top,0^M$
R2,18.0000mm,6.0000mm,Top,90^M$
U1,8.0000mm,7.5000mm,Top,180^M$
```

(`cat -A` shows each line end: `^M$` is CRLF. It is GNU cat's option; on macOS, `cat -e` shows the same.)

| column | what is written |
|---|---|
| `Designator` | the part's reference |
| `Mid X`, `Mid Y` | where JLC is to put the origin of its EasyEDA footprint, in mm with 4 decimals and `mm` after the number, relative to the CPL origin, Y up. Never `-0.0000mm`. This point need not be KiCad's footprint anchor: J1's EasyEDA origin is 1.3 mm from KiCad's |
| `Layer` | `Top` or `Bottom` |
| `Rotation` | degrees from 0 up to but not including 360, rounded to 0.001 degree and written without trailing zeros (`90`, `157.5`); an angle that rounds to 360 is written `0` |

* The header is exactly `Designator,Mid X,Mid Y,Layer,Rotation`, and lines end in CRLF. The file is UTF-8 without a
  byte order mark.
* There is one row for each part JLC assembles that has an LCSC code: not DNP, not excluded from the position files
  or the BOM, not board-only. With `--bom`, also for each part the BOM lists that the board excludes from its position
  files or its BOM (JLC never sees KiCad's attributes), unless an assembled part carries its reference. Rows are
  sorted naturally (R2 before R10). A part whose only match is a mirror image of
  KiCad's footprint gets no row, and the check calls it red.
* The rows go to a temporary file next to the target first. jlcplace reads that file back and checks it, so the
  check is of what is in the file, and then renames it over the target.
* When a row is red, `cpl` writes no file, unless you pass `--force`. A CPL already at that path is left as it was.
  See [Writing a CPL](../guide/cpl).

## BOM

`--bom` takes the BOM you upload to JLC. JLC's template has the columns `Comment`, `Designator`, `Footprint` and
`LCSC Part #`, as dial-me-spinner's BOM does:

```sh
head -4 dial-me-spinner-main-BOM-JLCPCB.csv
```

```text
Comment,Designator,Footprint,LCSC Part #,MPN,Qty
PKMCS0909E4000-R1,BZ1,Buzzer_Murata_PKMCS0909E4000-R1,C910763,PKMCS0909E4000-R1,1
22u,"C1,C8,C9",C_0805_2012Metric,C45783,CL21A226MAQNNNE,3
10u/25V,"C2,C6,C41",C_0603_1608Metric,C96446,CL10A106MA8NRNC,3
```

The header names are matched as for a CPL, and the separators, encodings and line ends are the same. Only the
designator column is required.

| column | names accepted | what jlcplace does with it |
|---|---|---|
| designators | `Designator`, `Designators`, `Reference`, `References`, `Ref`, `Refs`, `RefDes` | the parts on the line, split at commas, semicolons and spaces |
| LCSC code | `LCSC`, `LCSC Part`, `LCSC Part #`, `LCSC Part Number`, `LCSC Part No`, `LCSC#`, `LCSC PN`, `JLCPCB Part`, `JLCPCB Part #`, `JLCPCB PN`, `JLCPCB Part Number`, `Supplier Part`, `Supplier Part #` | the part JLC places: it replaces the board's code for every listed part, since JLC assembles from the BOM and never sees the board |
| comment | `Comment`, `Value`, `Val` | names the line in messages |
| footprint | `Footprint`, `Package`, `Pattern` | read, not checked |
| quantity | `Qty`, `Quantity`, `Count` | must equal the number of designators |

Other columns, such as `MPN`, are ignored. A quantity that is not a number is bad input (exit code 3).

With a BOM, `check` and `cpl` also check that it agrees with the board and the CPL, and print each problem on a
`BOM` line: a line with no designators or no LCSC code, a quantity that does not match, a designator twice in the
BOM, a designator that is not on the board or that the board marks DNP or excluded, a BOM code that differs from the
board's, a BOM part with no CPL row, a CPL row with no BOM line, and a part JLC should assemble that the BOM does not
list. Here R2's line is missing from the example's BOM:

```sh
cat example-BOM.csv
jlcplace check example.json example-CPL-good.csv --bom example-BOM.csv --cache cache
```

```text
Comment,Designator,Footprint,LCSC Part #,Qty
BUCK,U1,SOT-23-6,C1001,1
FPC-6,J1,FPC-6_P1.0mm,C1004,1
NMOS,Q1,SOT-23,C1005,1
100k,R1,R_0603,C1002,1
10u,C1,C_0402,C1003,1
100n,C2,C_0402,C1003,1
jlcplace 0.1.0: example.json (json, 14 parts, origin aux 100, 118) vs example-CPL-good.csv (7 rows), bottom reading jlc-2022
BOM     R2: a CPL row but no BOM line (JLC will not place it)
BOM     R2 (C1002): JLC should assemble it but the BOM does not list it
7 results: 0 red, 0 yellow, 7 green, 0 verified; 2 BOM issues
Preview checklist (1 item): look at these in JLC's placement preview
  bottom reading  Q1
```

BOM issues count as yellow: with `--fail-on yellow` this run exits with code 1. A message that names a "BOM line N"
counts the BOM's rows after the header, starting at 1.

## Board snapshot

`jlcplace snapshot BOARD -o FILE.json` saves what jlcplace read from a board as JSON. Every command takes the
snapshot as `BOARD`: a path ending in `.json` is read as a snapshot (`--reader json` forces it). A snapshot holds your
board's own data only, no EasyEDA data.

```sh
jlcplace snapshot dial-me-spinner-main.kicad_pcb -o main.json
head -8 main.json
```

```text
jlcplace: wrote main.json: 209 parts (pcbnew), origin aux (100, 100)
{
 "kicad_version": "20241229",
 "origin": [
  100.0,
  100.0
 ],
 "origin_kind": "aux",
 "parts": [
```

The file has sorted keys and a one-space indent. One part from it, R1:

```json
  {
   "angle": 0.0,
   "board_only": false,
   "body": [
    -0.93,
    -0.47,
    0.93,
    0.47
   ],
   "body_layer": "courtyard",
   "dnp": false,
   "exclude_bom": false,
   "exclude_pos": false,
   "fields": {
    "Datasheet": "",
    "Description": "",
    "LCSC": "C11490",
    "MPN": "0402WGF2204TCE",
    "Manufacturer": "UNI-ROYAL",
    "Reference": "R1",
    "Value": "2.2M"
   },
   "footprint": "Resistor_SMD:R_0402_1005Metric",
   "layer": "top",
   "lcsc": "C11490",
   "lcsc_field": "LCSC",
   "pads": [
    {
     "angle": 0.0,
     "copper": true,
     "h": 0.64,
     "kind": "smd",
     "net": "VSYS",
     "number": "1",
     "pin_name": "",
     "pin_type": "",
     "poly": null,
     "rr": 0.25,
     "shape": "roundrect",
     "w": 0.54,
     "x": -0.51,
     "y": 0.0
    },
    ...
   ],
   "ref": "R1",
   "tht": false,
   "value": "2.2M",
   "x": 85.0913,
   "y": 104.049
  },
```

| field | what it is |
|---|---|
| `kicad_version` | the board file's format version, such as `20241229` |
| `origin` | the CPL origin, `[x, y]` in mm in KiCad's board coordinates (Y down) |
| `origin_kind` | `aux` (the drill/place file origin), `grid`, `absolute` (the page origin) or `user` (from `--origin X,Y`) |
| `path` | the board path as given to `snapshot` |
| `source` | the reader that read the board: `pcbnew` or `sexpr` |
| `parts` | every footprint on the board |

Each part:

| field | what it is |
|---|---|
| `ref`, `value`, `footprint` | reference, value, and the footprint as `Library:Name` |
| `layer` | `top` or `bottom` |
| `x`, `y`, `angle` | the footprint's anchor in board coordinates (mm, Y down) and its orientation in degrees, as KiCad shows them |
| `lcsc`, `lcsc_field` | the LCSC code and the field it came from (`""` when there is none) |
| `fields` | every footprint field, by name |
| `dnp`, `exclude_pos`, `exclude_bom`, `board_only` | KiCad's attributes: do not populate, exclude from position files, exclude from BOM, not in schematic |
| `tht` | the footprint type is through-hole |
| `pads` | the pads, in the footprint's own frame as drawn in its library (mm, Y down) |
| `body`, `body_layer` | the box `[x0, y0, x1, y1]` of the footprint's courtyard drawing (`body_layer` `courtyard`), else of its fabrication-layer drawing (`fab`), in that frame; `null` and `""` when neither is drawn. `cpl` writes a part with no EasyEDA footprint at its centre |

Each pad:

| field | what it is |
|---|---|
| `number` | the pad number (`""` for unnumbered pads such as paste apertures) |
| `x`, `y`, `angle` | the pad's anchor (the hole for a through-hole pad) and rotation, in the footprint's library frame |
| `w`, `h` | the size along the pad's own axes |
| `shape` | `rect`, `roundrect`, `oval`, `circle`, `trapezoid`, `chamfered` or `custom` |
| `rr` | the corner radius ratio of a rounded rectangle |
| `poly` | the exact copper outline as a list of `[x, y]` points, for custom, chamfered and trapezoid pads and pads whose copper is offset from the hole; otherwise `null` |
| `kind` | `smd`, `tht` (plated through-hole), `npth` or `connect` |
| `copper` | whether the part is soldered to it: SMD copper on the part's side, plated holes, connect pads |
| `net`, `pin_name`, `pin_type` | the net, the schematic pin's name (KiCad's pin function) and its type |

A snapshot keeps the origin it was made with. `--origin aux` (the default) keeps it; `--origin absolute` or
`--origin X,Y` replaces it; any other named origin (`--origin grid`) is bad input, since the snapshot does not know it.

## Correction database

`--db DIR` is a directory with one JSON file per LCSC code, `DIR/C910763.json` and so on. `fit --db` and `confirm`
create the directory and write the files; `check`, `cpl` and `show` read them. The files are made to be committed:
they hold derived numbers, two hashes and the EasyEDA package name, never EasyEDA pad geometry. [The correction
database](../guide/database) explains how to use it; this section is the format.

Here `fit` stores BZ1's correction, then the file is shown:

```sh
jlcplace fit dial-me-spinner-main.kicad_pcb --ref BZ1 --db jlcplace-db
cat jlcplace-db/C910763.json
```

```text
YELLOW  BZ1  C910763    bottom  rot 0, dx -0.0001, dy -0.0001 (number, rms 0.350, 2 matched)  polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview  [stored]
1 part: 0 red, 1 yellow, 0 green; database jlcplace-db
```

```json
{
 "entries": [
  {
   "correction": {
    "dx": -0.0001,
    "dy": -0.0001,
    "mirror": false,
    "rot": 0.0
   },
   "ee": {
    "key": "ee-sha1:6d0e30fa0fc30d81",
    "title": "BUZ-SMD_L9.0-W9.0_PKMCS0909E4000-R1"
   },
   "fit": {
    "alternatives": [
     {
      "conflicts": 0,
      "dx": -0.0001,
      "dy": 0.0001,
      "equivalent": true,
      "matched": 2,
      "mirror": true,
      "names": 0,
      "rot": 180.0
     },
     ...
    ],
    "doubt": "polarity",
    "matched": 2,
    "method": "number",
    "polar": true,
    "rms": 0.35,
    "score": 26
   },
   "fitted": "2026-10-22",
   "kicad": {
    "fpid": "dial_spinner:Buzzer_Murata_PKMCS0909E4000-R1",
    "pad_hash": "sha1:57e1e0869d64f04b"
   },
   "reasons": [
    "polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview",
    "rms 0.350 mm: the land patterns differ (every pad lands)"
   ],
   "status": "auto",
   "tool": "jlcplace 0.1.0",
   "verdict": "yellow",
   "verifications": []
  }
 ],
 "lcsc": "C910763"
}
```

A `confirm` adds to `verifications`. After `jlcplace confirm dial-me-spinner-base.kicad_pcb --ref SW1 --db jlcplace-db
--by you --source manual --note "pins 1-2, 3-4 joined"`, SW1's entry in `C318884.json` ends:

```json
   "reasons": [
    "numbering conflict: EE 2 -> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview",
    "only 1 of 2 EasyEDA pads with a number KiCad has matched by number",
    "accepted manually (you): pins 1-2, 3-4 joined"
   ],
   "status": "manual",
   "tool": "jlcplace 0.1.0",
   "verdict": "yellow",
   "verifications": [
    {
     "board": "dial-me-spinner-base.kicad_pcb",
     "by": "you",
     "date": "2026-10-22",
     "layer": "top",
     "note": "pins 1-2, 3-4 joined",
     "ok": true,
     "presses": null,
     "ref": "SW1",
     "source": "manual"
    }
   ]
  }
 ],
 "lcsc": "C318884"
}
```

### Fields

The file holds `lcsc` (the code) and `entries`, a list. Each entry is one correction for the code on one pair of
footprints:

| field | what it is |
|---|---|
| `ee.key` | `ee-sha1:` and 16 hex digits: a hash of the EasyEDA footprint's pads (numbers, positions, sizes, angles). It changes when EasyEDA moves, resizes or renumbers a pad, and not when EasyEDA edits only the record's other data |
| `ee.title` | the EasyEDA footprint's title, its package name, for people reading the file; without the record, the check reads in it what made the fit judge a two-terminal part non-polar (BI, a bidirectional TVS; a crystal's title) |
| `kicad.fpid` | the KiCad footprint (`Library:Name`) the entry was made on, for people reading the file |
| `kicad.pad_hash` | `sha1:` and 16 hex digits: a hash of the KiCad footprint's soldered copper pads, combined with the pins' functions when they say something (a diode with pad 1 the cathode and one with pad 1 the anode get different keys) |
| `correction.rot` | degrees, 0 to 360: how the EasyEDA footprint is turned relative to the KiCad footprint as drawn in its library |
| `correction.dx`, `correction.dy` | mm, 4 decimals: where the EasyEDA footprint's origin lies in the KiCad footprint's library frame (Y down) |
| `correction.mirror` | `true` when only a mirror image of the EasyEDA footprint matches KiCad's. No CPL row can place that; such a part stays red |
| `fit.method` | how the correction was found: `number` (pad numbers), `geometry` (pad positions), `manual` (typed in or taken from an uploaded row) or `none` |
| `fit.rms` | mm: the fit's residual over the matched pads |
| `fit.matched` | pads matched by number |
| `fit.score` | 0 to 100: how well the fit is supported. It is stored, and written to the JSON outputs, for information only: nothing decides by it |
| `fit.alternatives` | the other placements under which every EasyEDA pad also lands: `rot`, `dx`, `dy`, `mirror`, `matched` (pads on their own number), `conflicts` (pads on a pad of another number and another net), `names` (how well the pin names agree) and `equivalent` (`true` when it gives every pad the same net as the chosen placement, or only swaps a non-polar part's two terminals) |
| `fit.doubt` | present when the choice among those placements may be wrong: `polarity`, `names` or `orientation` |
| `fit.polar` | present for a two-terminal part: whether it was judged polar (may not be turned round) |
| `verdict` | `green`, `yellow`, `red` or `verified` |
| `reasons` | why: the fit's findings first, then what later runs added ("accepted manually (you): ...", "verification failed (preview, you)", "taken from the row of U3 in board-CPL.csv ...") |
| `status` | `auto` (fitted), `manual` (decided or typed in), `preview-verified` (right in JLC's preview) or `board-verified` (right on an assembled board) |
| `verifications` | one object per `confirm`: `by`, `date` (YYYY-MM-DD), `source` (`manual`, `preview` or `board`), `ok` (`false` with `--failed`), `board` (the board's file name), `ref`, `layer`, `presses` (`{"space": n, "right": n, "up": n}` or `null`), `note` and `correction` (the numbers it was of: `rot`, `dx`, `dy`, `mirror`; older entries lack it). When the latest verification of the entry's numbers failed, the entry is red and its rows are `verified-wrong` |
| `tool` | the jlcplace version that stored the entry |
| `fitted` | the date the entry's correction was stored |

### How an entry is found

For a part, jlcplace opens the file of its LCSC code and takes the entries whose `kicad.pad_hash` is the part's.
Among those whose `ee.key` is the key of the EasyEDA footprint at hand, the strongest status wins
(board-verified, then preview-verified, then manual, then auto), and among equals the later entry. When entries
exist for the KiCad pads but none for this EasyEDA footprint, EasyEDA has changed the footprint since: the strongest
of them is used as "stale", and jlcplace fits the part again and compares (see [When EasyEDA changes a
footprint](../guide/database#when-easyeda-changes-a-footprint)). An entry without an `ee` or `kicad` block, or with an
empty key, matches any footprint.

### Writing and errors

jlcplace writes each file to a temporary file and renames it, with sorted keys, a one-space indent, UTF-8 text
(non-ASCII characters kept as they are) and a newline at the end, so that the files diff well in git.

An entry needs at least `correction` with `rot`, `dx` and `dy`; every other field is optional when you write one by
hand. To type a correction in, `jlcplace confirm --rot R --dx X --dy Y` is safer: it stores the entry under the
part's keys.

A file that is not JSON, or whose entry holds no usable correction, is bad input: the command stops with exit code 3
and names the file. Here a merge left conflict markers in BZ1's file:

```sh
grep -n -A4 "<<<<<<<" conflict-db/C910763.json
jlcplace check dial-me-spinner-main.kicad_pcb released-main-CPL.csv --db conflict-db -q
```

```text
63:<<<<<<< HEAD
64-   "status": "auto",
65-=======
66-   "status": "preview-verified",
67->>>>>>> board-rev-b
jlcplace: conflict-db/C910763.json: not a valid jlcplace correction file (Expecting property name enclosed in double quotes: line 63 column 1 (char 1329))
```

[Merge conflicts](../guide/database#merge-conflicts) says how to resolve one.

## JSON results

`--json FILE` writes the results of `check` or `cpl` as JSON; `--json -` writes them to standard output and moves
the summary to standard error. The file holds derived data only: verdicts, messages, pad numbers, nets and
distances, the EasyEDA package name, no EasyEDA geometry.

```sh
jlcplace check example.json example-CPL-bad.csv --cache cache --json report.json -q
```

The top of `report.json`:

```json
{
 "tool": "jlcplace 0.1.0",
 "board": {
  "path": "example.json",
  "source": "json",
  "kicad_version": "",
  "origin": [
   100.0,
   118.0
  ],
  "origin_kind": "aux",
  "parts": 14
 },
 "cpl": "example-CPL-bad.csv",
 "reading": "jlc-2022",
 "summary": {
  "results": 7,
  "rows": 7,
  "red": 2,
  "yellow": 0,
  "green": 5,
  "verified": 0,
  "bom_issues": 0,
  "worst": "red"
 },
 "results": [
  ...
 ],
 "bom": [],
 "checklist": [
  ...
 ]
}
```

| field | what it is |
|---|---|
| `tool` | the jlcplace version |
| `board` | the board: `path` as given, `source` (`pcbnew`, `sexpr` or `json`), `kicad_version`, `origin` (`[x, y]`, mm, board coordinates), `origin_kind`, and `parts` (how many) |
| `cpl` | the CPL path as given; for `cpl`, the file it wrote, or `""` when it wrote none |
| `reading` | the bottom-side reading used (`--reading`) |
| `summary` | `results`, `rows` (CPL rows), the count of each verdict, `bom_issues`, and `worst`, the worst verdict of the run (BOM issues make a run with no red or yellow row yellow) |
| `results` | one object per CPL row, in file order, then one per assembled part that has no row |
| `bom` | the BOM messages |
| `checklist` | the preview checklist items |

One result, U1, turned 180 degrees in the bad CPL:

```json
  {
   "ref": "U1",
   "lcsc": "C1001",
   "layer": "top",
   "status": "wrong-pad",
   "verdict": "red",
   "messages": [
    "EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 (EN, not BST), 2->5 (VIN, not GND), 3->6 (SW, not FB), 4->1 (BST, not EN), 5->2 (GND, not VIN), 6->3 (FB, not SW)",
    "differs from the row jlcplace writes (8.0000, 7.5000, top, 180): 0.000 mm and 180 deg away"
   ],
   "uncovered": [],
   "convention_sensitive": false,
   "line": 8,
   "row": {
    "ref": "U1",
    "x": 8.0,
    "y": 7.5,
    "layer": "top",
    "rotation": 0.0
   },
   "expected": {
    "ref": "U1",
    "x": 8.0,
    "y": 7.5,
    "layer": "top",
    "rotation": 180.0
   },
   "landings": [
    {
     "ee": "1",
     "kicad": "4",
     "net": "EN",
     "dist": 0.0
    },
    ...
   ],
   "correction": {
    "rot": 180.0,
    "dx": 0.0,
    "dy": 0.0,
    "mirror": false,
    "method": "number",
    "rms": 0.0,
    "verdict": "green",
    "status": "auto",
    "score": 85,
    "matched": 6,
    "source": "fit",
    "reasons": [
     "fits by pad numbers: 6/6 matched, rms 0.000 mm",
     "rot 0 also lands every pad, on other nets; ruled out by pad numbers"
    ],
    "alternatives": 3
   },
   "easyeda": {
    "found": true,
    "title": "EXAMPLE_SOT-23-6",
    "pads": 6,
    "key": "example-sot23-6:1767268800",
    "proxy": false
   }
  },
```

| field | what it is |
|---|---|
| `ref`, `lcsc`, `layer` | the reference, the LCSC code simulated, and the side of the part on the board (of the row when the part is not on the board) |
| `status` | `ok`, `offset`, `off-copper`, `wrong-pad`, `reversed`, `renumbered`, `pinout`, `verified-wrong`, `not-simulated`, `no-row`, `not-on-board`, `layer-mismatch`, `not-placed`, `duplicate` or `duplicate-ref` (see [Verdicts and statuses](../concepts/verdicts)) |
| `verdict` | `red`, `yellow`, `green` or `verified` |
| `messages` | what the check found, most important first |
| `uncovered` | KiCad pads that no EasyEDA pad lands on |
| `convention_sensitive` | `true` for a bottom part whose pins would move if JLC read the bottom rotation another way |
| `line` | the row's line in the CPL file, counting from 1; `null` for a result without a row |
| `row` | the row as read: `x` and `y` in mm relative to the CPL origin (Y up), `layer`, `rotation`; `null` for a part with no row |
| `expected` | the row jlcplace would write for the part, when a correction is known; else `null` |
| `landings` | one per EasyEDA pad: `ee` (its number), `kicad` (the KiCad pad it lands in, `null` off copper), `net` (that pad's net), `dist` (mm to the nearest KiCad copper pad centre, `null` when there is no copper to measure to) |
| `correction` | the part's correction: `rot`, `dx`, `dy`, `mirror`, `method`, `rms`, `verdict`, `status`, `score`, `matched`, `source` (`db`, `fit` or `fallback`), `reasons`, and `alternatives` (how many); `null` when there is none |
| `easyeda` | `found: true` with the footprint's `title`, its number of `pads` and its record `key` (EasyEDA's footprint id and update time), or `found: false` with a `message`; `proxy` is `true` when the part was simulated with its KiCad pads through the stored correction because the record was missing; `null` for a part jlcplace did not look up |

Each checklist item:

| field | what it is |
|---|---|
| `category` | `uncertain correction`, `not simulated`, `bottom reading` or `odd angle` |
| `ref` | the part to look at |
| `refs` | every part the item stands for (alike parts share one item) |
| `lcsc`, `layer` | the code and side |
| `rotation` | the row's rotation; `null` for a group whose rows differ |
| `what` | what to look for |
| `verdict` | the row's verdict (for a group, the worst) |
| `also` | the part's other categories, folded into this item |
| `first` | `true` for the bottom part to look at first: it settles the bottom reading for the board |

`fit --json` writes `{"tool", "board", "fits": [...]}`, one object per part fitted: `ref`, `lcsc`, `layer`,
`footprint`, `easyeda` (the footprint's title, or `null`), `message` (why there is no footprint), `rot`, `dx`,
`dy`, `mirror`, `method`, `rms`, `matched`, `verdict`, `score`, `reasons`, `db` (`stored`, `not stored: ...`, or
`""` without `--db`) and the full `alternatives`. `show --json` writes the same data `show` prints.

## Preview checklist

`--checklist FILE` writes the preview checklist: the parts to look at in JLC's placement preview (see [JLC's preview
and confirm](../guide/preview)). A path ending in `.csv` gets CSV; any other path gets Markdown.

```sh
jlcplace check example.json example-CPL-bad.csv --cache cache --checklist checklist.md -q
cat checklist.md
```

```text
# JLC preview checklist: example.json

CPL example-CPL-bad.csv, bottom reading `jlc-2022`, jlcplace 0.1.0; 1 item.

Open the order's placement preview at JLC and look at each part below. Tick it when the part sits on its pads with pin 1 / polarity where the board wants it. An item for several parts names the one to look at.

## Bottom reading

- [ ] **Q1** (C1005, bottom, rot 90): if JLC read the bottom rotation another way: as ccw-from-top or jlc-2022+180, pin 1 would land off copper (3 pins move)
```

The Markdown is a title, one line naming the CPL, a short instruction, then a `## ` section per category with one
tick box per item: the part (or the parts of a group), its code, side and rotation, "(red: fix the row first)" for
a red row, and what to look for. References, file names and messages are escaped, so that nothing in them becomes a
link, an image, HTML or emphasis when the checklist is posted to a pull request or a job summary.

The CSV has one line per item, the same items in the same order:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb released-main-CPL.csv --bom dial-me-spinner-main-BOM-JLCPCB.csv --checklist checklist.csv -q
head -4 checklist.csv
```

```text
category,ref,lcsc,layer,rotation,row verdict,what,refs,checked
uncertain correction,BZ1,C910763,bottom,158,yellow,"yellow fit correction (auto): polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview; also the bottom reading matters (as ccw-from-top or ccw-from-top+180, pin 1 would land off copper (2 pins move); as jlc-2022+180, pins 1 and 2 would swap); rotation 158 is not a multiple of 90",BZ1,
not simulated,R26,C4167968,top,0,yellow,"placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it",R26,
bottom reading,J1,C160411,bottom,120,green,"look at this one first: it moves under every other reading, so it shows how JLC reads the bottom side; when it sits right, the other bottom parts follow. If JLC read the bottom rotation another way: as ccw-from-top or ccw-from-top+180 or jlc-2022+180, pin 1 would land off copper (14 pins move); also rotation 120 is not a multiple of 90",J1,
```

| column | what it is |
|---|---|
| `category` | `uncertain correction`, `not simulated`, `bottom reading` or `odd angle` |
| `ref` | the part to look at |
| `lcsc`, `layer` | the code and side |
| `rotation` | the row's rotation; empty for a group whose rows differ |
| `row verdict` | the row's verdict (for a group, the worst) |
| `what` | what to look for |
| `refs` | every part the item stands for, separated by spaces (the odd-angle item for 44 LEDs lists all 44) |
| `checked` | empty: a column for you to fill in |

The CSV is UTF-8 with LF line ends. A cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return gets an
apostrophe in front, so that a spreadsheet shows it as text instead of running it as a formula.

## JUnit XML

`--junit FILE` writes one JUnit test case per result, for CI systems that show test reports:

```sh
jlcplace check example.json example-CPL-bad.csv --cache cache --junit report.xml -q
cat report.xml
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="jlcplace" tests="7" failures="2">
  <testsuite name="jlcplace example-CPL-bad.csv" tests="7" failures="2" errors="0" skipped="0" timestamp="2026-10-22T14:58:12">
    <testcase classname="jlcplace.example" name="C1" />
    <testcase classname="jlcplace.example" name="C2" />
    <testcase classname="jlcplace.example" name="J1">
      <failure type="red" message="8 of 8 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 (nearest KiCad pad centre up to 1.30 mm away)">8 of 8 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 (nearest KiCad pad centre up to 1.30 mm away)
KiCad pads that no EasyEDA pad lands on: 1, 2, 3, 4, 5, 6, MP
differs from the row jlcplace writes (13.0000, 13.5000, top, 0): 1.300 mm and 0 deg away</failure>
    </testcase>
    <testcase classname="jlcplace.example" name="Q1">
      <system-out>green ok
bottom reading matters, check it in JLC's preview: ccw-from-top: 1-&gt;off, 2-&gt;off, 3-&gt;off; jlc-2022+180: 1-&gt;off, 2-&gt;off, 3-&gt;off</system-out>
    </testcase>
    <testcase classname="jlcplace.example" name="R1" />
    <testcase classname="jlcplace.example" name="R2" />
    <testcase classname="jlcplace.example" name="U1">
      <failure type="red" message="wrong-pad: EasyEDA pads land on other-numbered KiCad pads of other nets: 1-&gt;4 (EN, not BST), 2-&gt;5 (VIN, not GND), 3-&gt;6 (SW, not FB), 4-&gt;1 (BST, not EN), 5-&gt;2 (GND, not VIN), 6-&gt;3 (FB, not SW)">EasyEDA pads land on other-numbered KiCad pads of other nets: 1-&gt;4 (EN, not BST), 2-&gt;5 (VIN, not GND), 3-&gt;6 (SW, not FB), 4-&gt;1 (BST, not EN), 5-&gt;2 (GND, not VIN), 6-&gt;3 (FB, not SW)
differs from the row jlcplace writes (8.0000, 7.5000, top, 180): 0.000 mm and 180 deg away</failure>
    </testcase>
  </testsuite>
</testsuites>
```

* One `testsuite`, named `jlcplace` and the CPL's file name, with the time of the run.
* One `testcase` per result: `classname` is `jlcplace.` and the board's file name without its extension, `name` is
  the reference.
* A red result has a `failure` whose `type` is `red`, whose `message` is the result's first line as the summary
  shows it, and whose text is every message. A yellow result fails the same way with `--fail-on yellow`; otherwise
  its messages go to `system-out`, as do a green result's.
* BOM issues are one more test case, named `BOM`: a failure with `--fail-on yellow`, else `system-out`.
* Characters that XML 1.0 cannot hold are replaced by U+FFFD, so that every JUnit reader can open the file.

## GitHub annotations

`--github-annotations` prints one GitHub Actions workflow command per red or yellow result to standard output.
GitHub shows them on the pull request, at the CPL row's line:

```sh
jlcplace check example.json example-CPL-bad.csv --cache cache --github-annotations -q
```

```text
::error file=example-CPL-bad.csv,line=4,title=jlcplace J1 off-copper::8 of 8 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 (nearest KiCad pad centre up to 1.30 mm away); KiCad pads that no EasyEDA pad lands on: 1, 2, 3, 4, 5, 6, MP; differs from the row jlcplace writes (13.0000, 13.5000, top, 0): 1.300 mm and 0 deg away
::error file=example-CPL-bad.csv,line=8,title=jlcplace U1 wrong-pad::EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 (EN, not BST), 2->5 (VIN, not GND), 3->6 (SW, not FB), 4->1 (BST, not EN), 5->2 (GND, not VIN), 6->3 (FB, not SW); differs from the row jlcplace writes (8.0000, 7.5000, top, 180): 0.000 mm and 180 deg away
```

A result that fails the run (red, or yellow with `--fail-on yellow`) is an `::error`, any other yellow result a
`::warning`. `file` is the CPL path as given, `title` is `jlcplace`, the reference and the status, and the text is
the result's messages joined with `; `. Each BOM issue is one more `::warning` titled `jlcplace BOM` (`::error`
with `--fail-on yellow`). Since both go to standard output, `--github-annotations` cannot be combined with
`--json -`. See [Continuous integration](../guide/ci).

## HTML report

`--report FILE.html` writes one self-contained page: no scripts or styles from elsewhere, everything inline. It has
the counts, top and bottom views of the board with JLC's pads outlined where the CPL puts them (the bottom seen from
below, as JLC's preview shows it), the preview checklist, the BOM issues, and a card for every part. It names the
board and the CPL by file name, not by path, and asks search engines not to index it.

It is meant to be read, not parsed: the JSON results hold the same verdicts as data. The report draws EasyEDA pad
outlines, so it is for your own review and not for publishing; see [Data and licensing](../about/licensing) and
[Checking a CPL](../guide/check#outputs).
