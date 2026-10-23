# Troubleshooting

Each entry starts from what you see, says why, and says what to do. Every example is a real run on the
dial-me-spinner boards (see [Checking a CPL](./check#the-example-files)), some on a copy damaged on purpose. A CPL is
the pick-and-place file you upload to JLCPCB with the BOM: one row per part, with its reference, position, side and
rotation.

Most examples pass `--db jlcplace-db`, the correction database made in
[Writing a CPL](./cpl#sw1-a-part-the-records-cannot-decide), so that the tactile switch SW1 is settled and only the
problem at hand shows.

| what you see | go to |
|---|---|
| Almost every row is red, all about the same distance off | [CPL measured from the page origin](#every-row-is-red-by-about-the-same-distance) |
| Almost every row is red, and the CPL's numbers look small | [CPL in inches](#every-row-is-red-and-the-numbers-look-small) |
| Almost every row says "placement not simulated" | [Records not in the cache](#almost-every-row-says-placement-not-simulated) |
| Every row says "the board part has no LCSC code" | [Codes in another field, or only in the BOM](#every-row-says-the-board-part-has-no-lcsc-code) |
| "is on the board 2 times" or "2 rows for" | [Duplicate references](#duplicate-references) |
| "Component not found" | [A part EasyEDA does not know](#component-not-found) |
| "the pcbnew reader needs KiCad's Python module" | [KiCad is not installed](#kicad-is-not-installed) |
| A line starting `jlcplace:` and exit code 3 | [Files and options jlcplace cannot use](#exit-code-3) |
| A part you confirmed is red again | [The database was not found](#a-recorded-decision-is-ignored) |
| `cpl` says "not written" | [Writing a CPL](./cpl#when-a-row-is-red-no-file-is-written) |
| `\u5e95` and the like in output captured on Windows | [Windows console](#windows-console) |
| Exit code 141 | [The reader of the output went away](#exit-code-141) |

## Almost every row is red, all about the same distance off {#every-row-is-red-by-about-the-same-distance}

**What you see.** Every row is off copper by roughly the same large distance, and the last line suggests
`--origin absolute`. Here the CPL is KiCad's position file, exported without "Use drill/place file origin":

```sh
kicad-cli pcb export pos --format csv --units mm --side both -o base-pos.csv dial-me-spinner-base.kicad_pcb
jlcplace check dial-me-spinner-base.kicad_pcb base-pos.csv --db jlcplace-db
```

```text
Wrote position data to 'base-pos.csv'.
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs base-pos.csv (17 rows), bottom reading jlc-2022
RED     C1   C277470    top     2 of 2 EasyEDA pads land off copper: 1, 2 (nearest KiCad pad centre up to 141.46 mm away)
RED     C2   C96446     top     2 of 2 EasyEDA pads land off copper: 1, 2 (nearest KiCad pad centre up to 141.37 mm away)
...
RED     U2   C7519      top     6 of 6 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6 (nearest KiCad pad centre up to 141.43 mm away)
17 results: 17 red, 0 yellow, 0 green, 0 verified
...
jlcplace: 17 of 17 rows would sit on their parts if the CPL were relative to the page origin, not the board's aux origin: pass --origin absolute (and check that the Gerbers use the same origin)
```

**Why.** CPL coordinates are measured from an origin. jlcplace measures them from the board's drill/place file origin
(KiCad's auxiliary origin, here at (100, 100) mm), the one KiCad's position export uses when "Use drill/place file
origin" is ticked. This file was measured from KiCad's page origin, so every row is off by the same 141 mm, the
distance between the two origins.

**Fix.** Decide which origin your Gerbers use; the CPL has to use the same one. Usually that is the drill/place file
origin: export the position file again with "Use drill/place file origin" ticked (`--use-drill-file-origin` for
`kicad-cli`). If the Gerbers really are plotted from the page origin, tell jlcplace with `--origin absolute`:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb base-pos.csv --db jlcplace-db --origin absolute
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin absolute 0, 0) vs base-pos.csv (17 rows), bottom reading jlc-2022
RED     J1   C165948    top     16 of 16 EasyEDA pads land off copper: 1, 2, 3, 4, A1B12, A4B9, A5, A6 ... (nearest KiCad pad centre up to 1.57 mm away)
RED     U1   C3682423   top     4 of 8 EasyEDA pads land off copper: A1, A2, D1, D2 (nearest KiCad pad centre up to 0.40 mm away)
RED     U2   C7519      top     2 of 6 EasyEDA pads land off copper: 2, 5 (nearest KiCad pad centre up to 1.15 mm away)
...
17 results: 3 red, 1 yellow, 13 green, 0 verified
...
```

The three rows still red are real: KiCad's position file has KiCad's anchors and angles, not JLC's (see
[A CPL from KiCad's position file](./check#a-cpl-from-kicad-s-position-file)). `--origin` also takes `grid` (KiCad's
grid origin) or a point (`--origin 100,100`, `--origin 4in,-500mil`).

## Almost every row is red, and the numbers look small {#every-row-is-red-and-the-numbers-look-small}

**What you see.** Rows are off by different, large distances, and the last line suggests `--units in`. This is
KiCad's CSV position file exported in inches:

```sh
kicad-cli pcb export pos --format csv --units in --side both --use-drill-file-origin -o base-pos-in.csv dial-me-spinner-base.kicad_pcb
head -3 base-pos-in.csv
jlcplace check dial-me-spinner-base.kicad_pcb base-pos-in.csv --db jlcplace-db -q
echo "exit code $?"
```

```text
Wrote position data to 'base-pos-in.csv'.
Ref,Val,Package,PosX,PosY,Rot,Side
"C1","2.2u/25V","C_0805_2012Metric",-0.210535,-0.631575,0.000000,top
"C2","10u/25V","C_0603_1608Metric",-0.298087,-0.748425,90.000000,top
jlcplace: 17 of 17 rows would sit on their parts if the CPL's numbers were in inches: pass --units in
exit code 2
```

**Why.** KiCad's CSV position file does not say which unit it is in, and jlcplace reads bare numbers as mm. (`-q`
dropped the summary, but hint lines still print.)

**Fix.** Pass the unit, or export in mm:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb base-pos-in.csv --db jlcplace-db --units in
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs base-pos-in.csv (17 rows), bottom reading jlc-2022
...
17 results: 3 red, 1 yellow, 13 green, 0 verified
...
```

The same three rows stay red as in the file measured from the page origin above: they carry KiCad's anchors and
angles. `--units mil` is there for files in mils. A value that
carries its unit (`12.5mm`), a column header with a unit (`Center-X(mil)`), and KiCad's `.pos` text format (whose
`## Unit = inches` line says it) need no option.

## Almost every row says "placement not simulated" {#almost-every-row-says-placement-not-simulated}

**What you see.** Nothing is green. Multi-pin and polar parts are red, resistors and capacitors yellow, every one
"placement not simulated", and the line under the header names the codes the cache lacks:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs released-base-CPL.csv (17 rows), bottom reading jlc-2022
jlcplace: no EasyEDA record in the cache .../.cache/jlcplace for 15 of the 15 codes JLC places (C7519, C11702, C19666, C25741, C25905, C77131, C96446, C151296 and 7 more), so the rows below are checked without JLC's footprints (red where a part needs one): `jlcplace fetch dial-me-spinner-base.kicad_pcb` downloads them (rate-limited), or pass --online
RED     D1   C151296    top     placement not simulated: no EasyEDA footprint (no EasyEDA record in the cache (`jlcplace fetch` or --online downloads it)); a polar two-pad part; the row point is the centre of its courtyard
RED     J1   C165948    top     placement not simulated: no EasyEDA footprint (no EasyEDA record in the cache (`jlcplace fetch` or --online downloads it)); not a two-pad non-polar part (20 copper pads); the row point is 1.01 mm from the centre of its courtyard
...
YELLOW  C1   C277470    top     placement not simulated: no EasyEDA footprint (no EasyEDA record in the cache (`jlcplace fetch` or --online downloads it)); a two-pad non-polar part, and the row point is on it
...
17 results: 8 red, 9 yellow, 0 green, 0 verified
Gerbers not checked: plot them from this CPL's origin (aux 100, 100), as JLC lays the CPL over them from their own 0, 0 (--gerbers checks it)
Preview checklist (17 items): look at these in JLC's placement preview
  not simulated  D1, J1, J2, J3, Q1, SW1, U1, U2, C1, C2, C3, C4, R1, R2, R3, R4, RT1
```

**Why.** `check`, `cpl`, `fit`, `show` and `confirm` never download anything unless you pass `--online`. They read
EasyEDA records from the cache, and this cache is empty. Without JLC's footprint, jlcplace cannot place a part's pads;
it only checks that a two-pad non-polar part's row point lies on its pads.

**Fix.** Download the records once with `fetch`, which is the only command that uses the network unasked:

```sh
jlcplace fetch dial-me-spinner-base.kicad_pcb
```

Each line ends in `fetched` for a record it downloaded, or `cached` for one it already had; the last line names the
cache. Then check again. Or pass `--online` to the check, which downloads what the cache lacks the same way.

If you did run `fetch`, compare the cache it names with the one in the `jlcplace:` line: they differ when
`JLCPLACE_CACHE` or `--cache` was set for one command and not the other (see
[Exit codes and environment](../reference/environment)). In CI, a committed correction database lets parts be
simulated without records (see [Continuous integration](./ci)).

## Every row says "the board part has no LCSC code" {#every-row-says-the-board-part-has-no-lcsc-code}

**What you see.** Every row has `-` for its code, and the last line suggests `--bom` or `--lcsc-field`. This board is
a copy of the base board whose `LCSC` field was renamed `Supplier Code`:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --db jlcplace-db
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs released-base-CPL.csv (17 rows), bottom reading jlc-2022
RED     D1   -          top     not-simulated: the board part has no LCSC code
RED     J1   -          top     not-simulated: the board part has no LCSC code
...
YELLOW  RT1  -          top     not-simulated: the board part has no LCSC code
17 results: 8 red, 9 yellow, 0 green, 0 verified
Preview checklist (2 items): look at these in JLC's placement preview
  odd angle  J2 (310), J3 (30)
jlcplace: 17 of 17 rows have no LCSC code on the board: pass --bom BOM.csv (JLC assembles from it) or --lcsc-field NAME
```

**Why.** jlcplace looks for the code in fields with the usual names (`LCSC`, `LCSC Part #`, `JLCPCB Part #`, `MPN`
and others: [the full list](./check#the-board)). `Supplier Code` is not one of them. The same happens when the codes
are only in the BOM you upload to JLC and not on the board at all.

**Fix.** Name the field:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --db jlcplace-db --lcsc-field "Supplier Code"
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs released-base-CPL.csv (17 rows), bottom reading jlc-2022
YELLOW  SW1  C318884    top     renumbered: numbering accepted by the correction (manual): 2->1
             correction (db, manual, yellow): accepted manually (you): TS-1187A datasheet: EasyEDA pins 1-2 and 3-4 are each one terminal
17 results: 0 red, 1 yellow, 16 green, 0 verified
...
```

Or pass the BOM, which is where JLC takes the codes from anyway. `--bom dial-me-spinner-base-BOM-JLCPCB.csv` gives
the same result here.

`fetch` needs the same option, or it finds no codes to download:

```sh
jlcplace fetch dial-me-spinner-base.kicad_pcb
jlcplace fetch dial-me-spinner-base.kicad_pcb --lcsc-field "Supplier Code"
```

```text
0 codes: 0 footprints, 0 not found, 0 failed; cache .../.cache/jlcplace
C7519: SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL, 6 pads (cached)
...
15 codes: 15 footprints, 0 not found, 0 failed; cache .../.cache/jlcplace
```

When the codes are only in the BOM, `fetch` reads them from it with `--bom`
(`jlcplace fetch board.kicad_pcb --bom board-BOM.csv`). It also takes the codes themselves:

```sh
jlcplace fetch C7519 C318884
```

```text
C7519: SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL, 6 pads (cached)
C318884: SW-SMD_4P-L5.1-W5.1-P3.70-LS6.5-TL_H1.5, 4 pads (cached)
2 codes: 2 footprints, 0 not found, 0 failed; cache .../.cache/jlcplace
```

## Duplicate references {#duplicate-references}

**What you see.** `R3 is on the board 2 times`. In this copy of the base board, R4 was renamed R3:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --db jlcplace-db
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs released-base-CPL.csv (17 rows), bottom reading jlc-2022
RED     R3   C11702     top     duplicate-ref: R3 is on the board 2 times: JLC cannot tell them apart
RED     R4   -          top     not-on-board: R4 is not on the board
...
17 results: 2 red, 1 yellow, 14 green, 0 verified
...
```

**Why.** JLC matches CPL rows and BOM lines to parts by reference. With two parts called R3, it cannot tell which row
or which BOM code belongs to which, so jlcplace does not simulate either. R4's row is red because the board has no R4
any more.

**Fix.** Give every part its own reference in KiCad (annotate the schematic again and update the PCB from it), then
write or export the CPL again.

A CPL that lists one part twice is red the same way, on every copy of the row:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb dup-row-CPL.csv --db jlcplace-db
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs dup-row-CPL.csv (18 rows), bottom reading jlc-2022
RED     R3   C11702     top     duplicate: 2 rows for R3
RED     R3   C11702     top     duplicate: 2 rows for R3
...
```

Delete the extra row, or write the CPL with [`jlcplace cpl`](./cpl).

## "Component not found": a part EasyEDA does not know {#component-not-found}

**What you see.** `fetch` says `not found` for a code, and the part is "not simulated":

```sh
jlcplace fetch C4167968
```

```text
C4167968: not found (Component not found; cached)
1 code: 0 footprints, 1 not found, 0 failed; cache .../.cache/jlcplace
```

On the main board, that code is the 499 kΩ 0.5 % resistor R26. Its line from
`jlcplace check dial-me-spinner-main.kicad_pcb released-main-CPL.csv`:

```text
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
```

**Why.** EasyEDA's library has no footprint for C4167968, so there is nothing to place. jlcplace remembers the answer
for 7 days before `fetch` asks again (`fetch --refresh` asks now). For a two-pad non-polar part like R26, jlcplace
can still check that the row point lies on its pads, so the row is yellow. A polar part (a diode, an LED) or a part
with more pads is red: its orientation cannot be checked at all. Either way the part goes on the preview checklist
under "not simulated", and `jlcplace cpl` gives it KiCad's angle at the centre of its courtyard (KiCad's own position
for a centred part like R26; a through-hole connector's anchor is usually pin 1, off its centre).

**Fix.** Look at the part in JLC's placement preview. When it sits right, record that with the CPL you uploaded:

```sh
jlcplace confirm dial-me-spinner-main.kicad_pcb --ref R26 --db jlcplace-db --by you --source preview --cpl released-main-CPL.csv
```

```text
R26 C4167968: preview-verified, verified: rot 0, dx 0.0000, dy 0.0000; row X 7.9170 Y -20.5264 rot 0 (jlcplace-db/C4167968.json)
```

From then on, jlcplace simulates R26 with its stored correction (its own KiCad pads stand in for EasyEDA's), and the
row is verified:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb released-main-CPL.csv --db jlcplace-db
```

```text
jlcplace 0.2.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs released-main-CPL.csv (184 rows), bottom reading jlc-2022
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview
184 results: 0 red, 1 yellow, 182 green, 1 verified
...
```

If the part sits wrong in the preview, move it there and record the keys you pressed (see
[A part you put right in the preview](./preview#a-part-you-put-right-in-the-preview)). Or choose an equivalent part with another LCSC code that EasyEDA knows.

## KiCad is not installed {#kicad-is-not-installed}

**What you see.**

```sh
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --reader pcbnew
```

```text
jlcplace: dial-me-spinner-base.kicad_pcb: the pcbnew reader needs KiCad's Python module (No module named 'pcbnew'); try --reader sexpr
```

**Why.** `--reader pcbnew` asks for KiCad's own Python module, which this Python cannot import: KiCad is not
installed, or the virtual environment cannot see KiCad's Python packages.

**Fix.** You do not need KiCad. Leave `--reader` out: jlcplace then uses `pcbnew` when it can import it and its own
reader (`sexpr`, KiCad 5 to 10 files) when it cannot. The first line of the summary says which it used. If you want
the board read by KiCad itself, save a snapshot on a machine with KiCad (`jlcplace snapshot`) and check that anywhere
(see [Checking a CPL](./check#the-board)). To let a virtual environment see KiCad's module, make it with
`python3 -m venv --system-site-packages` on a machine with KiCad (see [Getting started](./getting-started#install)).

When `pcbnew` cannot load a file (for example a file from a newer KiCad), jlcplace falls back to its own reader and
prints a warning naming the file.

## Files and options jlcplace cannot use: exit code 3 {#exit-code-3}

**What you see.** One line starting with `jlcplace:`, no summary, and exit code 3. The line names the file, and the
line in it when there is one. Some real ones:

| message | cause | fix |
|---|---|---|
| `jlcplace: dial-me-spinner-base-BOM-JLCPCB.csv: no header with Designator, Mid X, Mid Y, Layer and Rotation columns` | the BOM was given where the CPL goes | swap the files: `check BOARD CPL --bom BOM` |
| `jlcplace: base-CPL.csv: no such file` | a typo in the path | |
| `jlcplace: truncated.kicad_pcb: unbalanced '(': the file is truncated` | an incomplete copy of the board | save or copy the board again |
| `jlcplace: nan-CPL.csv: line 13 (R3): not a length: 'nan'` | a cell that is not a finite number (`nan`, `inf`) | fix that cell |
| `jlcplace: conflict-db/C318884.json: not a valid jlcplace correction file (Expecting property name enclosed in double quotes: line 63 column 1 (char 1399))` | a database file with git conflict markers (`<<<<<<<`) | resolve the conflict in that file (see [Merge conflicts](./database#merge-conflicts)) |
| `jlcplace: --db released-base-CPL.csv: not a directory (the database is a directory of JSON files)` | `--db` given a file | give it the database directory |
| `jlcplace check: error: argument --units: invalid choice: 'cm' (choose from 'mm', 'in', 'mil')` | an option value jlcplace does not take (after a usage line) | see [the command reference](../reference/cli#jlcplace-check) |

A reference with a control character in it, or one that starts with `=`, `+`, `-` or `@` (a spreadsheet formula), is
bad input too: no designator looks like that, and it could do harm in a CI log or a spreadsheet. When one output
cannot be written (a read-only directory, a full disk), the other outputs are still written and the run exits with
code 3.

## A part you confirmed is red again {#a-recorded-decision-is-ignored}

**What you see.** A decision you recorded with `confirm`, such as SW1's numbering, no longer applies, and the first
line of the output is a warning:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --db jlcplace_db -q
```

```text
jlcplace: warning: --db jlcplace_db does not exist: no stored corrections are used (`jlcplace fit --db` or `confirm` creates it)
```

**Why.** The database is `jlcplace-db`; `jlcplace_db` is a typo. `check`, `cpl` and `show` do not create a database,
so they warn and go on without one, and SW1 is red as it was before the decision. (This run exits with code 2.)

**Fix.** Give the right path. A part can also turn yellow or red again on its own when EasyEDA changes its footprint:
its stored entry no longer matches, jlcplace fits it again and says so (see
[When EasyEDA changes a footprint](./database#when-easyeda-changes-a-footprint)).

## Windows console {#windows-console}

**What you see.** In output captured on Windows (a CI log, a pipe, `> out.txt`), parts of file names, net names or
EasyEDA titles appear as escapes such as `\u5e95\u677f`.

**Why.** When Python's output on Windows goes to a pipe or a file, it is encoded in the system's code page (cp1252 on
Western systems), which cannot hold every character. jlcplace writes such characters as escapes instead of stopping
with an error. Reports written to files (`--report`, `--json`, `--checklist` and the rest) are UTF-8 and keep every
character. Control characters from files and from EasyEDA are always shown as `\xNN`, on every system, so that they
cannot start a GitHub workflow command in a CI log.

Setting `PYTHONIOENCODING=cp1252` shows the same thing on Linux or macOS, here with a CPL saved under a Chinese name:

```sh
PYTHONIOENCODING=cp1252 jlcplace check dial-me-spinner-base.kicad_pcb 底板-CPL.csv --db jlcplace-db
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs \u5e95\u677f-CPL.csv (17 rows), bottom reading jlc-2022
...
```

**Fix.** Nothing is wrong with the check. To keep the characters, set the environment variable `PYTHONIOENCODING`
to `utf-8` for the run whose output you capture, or read the HTML or JSON output.

Two more things about Windows. jlcplace colours the verdicts only when its output is a console and the environment
variable `NO_COLOR` is not set; if an older Windows console shows codes such as `←[1;31m` instead of colours, set
`NO_COLOR`. And PowerShell keeps a command's exit code in `$LASTEXITCODE`, cmd in `%ERRORLEVEL%`.

## Exit code 141 {#exit-code-141}

**What you see.** A pipeline gives exit code 141 for jlcplace. Here `jq` (a command-line JSON tool) stopped at once on
a typo in its filter (`red` without quotes), before jlcplace had written anything:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb old-base-CPL.csv -q --json - | jq ".results[] | select(.verdict == red) | .ref"; echo "exit codes ${PIPESTATUS[*]}"
```

```text
jq: error: red/0 is not defined at <top-level>, line 1:
.results[] | select(.verdict == red) | .ref
jq: 1 compile error
exit codes 141 3
```

**Why.** jlcplace's standard output went to a program that had already exited, so there was nowhere to write the
results. jlcplace stops quietly with 141 (128 + SIGPIPE, the code a shell reports for a program stopped this way)
rather than printing an error. The same happens with `| head` or a pager you quit early, when jlcplace has more to
write than the reader took.

**Fix.** Nothing is wrong with the check, but its verdict was lost with the output. Fix the program reading the
output:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb old-base-CPL.csv -q --json - | jq '.results[] | select(.verdict == "red") | .ref'; echo "exit codes ${PIPESTATUS[*]}"
```

```text
"J1"
"J2"
"J3"
"SW1"
exit codes 2 0
```

When a script needs jlcplace's verdict, take jlcplace's own exit code (`${PIPESTATUS[0]}` in bash), or write the
results to a file with `--json results.json` and read that.
