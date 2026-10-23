# jlcplace

Check and correct JLCPCB pick-and-place (CPL) files for KiCad boards. jlcplace puts JLC's own EasyEDA footprint down
exactly where each CPL row tells JLC to put it, over the Gerbers you order with, and reports which KiCad pad and net
every EasyEDA pad lands on. It works on a CPL from any tool, and it can write the CPL itself.

Documentation: [tyevco.github.io/jlcplace](https://tyevco.github.io/jlcplace/)

```
$ jlcplace check board.kicad_pcb board-CPL.csv --gerbers gerbers.zip
jlcplace 0.2.1: dial-me-spinner-main.kicad_pcb (pcbnew, 209 parts, origin 100, 100 from the Gerbers (aux)) vs dial-me-spinner-main-CPL-JLCPCB.csv (184 rows) over the Gerbers gerbers.zip, bottom reading jlc-2022
RED     J1   C160411    bottom  14 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 3.40 mm away)
RED     J20  C262671    top     11 of 32 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 4.48 mm away)
RED     J40  C160404    bottom  6 of 6 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6 (nearest KiCad pad centre up to 1.40 mm away)
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only (...)
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 3 red, 2 yellow, 179 green, 0 verified
Preview checklist (12 items, 99 parts): look at these in JLC's placement preview
  uncertain correction  BZ1: yellow fit correction (auto): polarity decided by pad numbers only (...) ...
  not simulated         R26
  bottom reading        U10 (look at it first: it settles the bottom reading), J1, J40, U11, U12, U30, U32, U40 U41
  odd angle             C107 of C102..C112 C114..C124 C126..C136 C138..C148 (C1525, 44 parts at 8 to 353 deg: look at C107)
                        D107 of D102..D112 D114..D124 D126..D136 D138..D148 (C55109522, 44 parts at 8 to 353 deg: look at D107)
```

## Why

JLC lays the CPL's coordinates over the Gerbers, from the Gerbers' own (0, 0), and assembles with the footprints of
its EasyEDA library, not yours. Nothing is matched by pad shape: a wrong origin or angle is placed exactly as written.
An EasyEDA footprint's zero angle and origin often differ from KiCad's, so a CPL that is right for KiCad's footprint
puts JLC's part turned or shifted. The usual ways of getting this wrong:

* **Every part shifted** by one vector: the Gerbers and the CPL were exported from different origins (KiCad's page
  origin for one, the drill/place file origin for the other). The Y axis is a second trap: the sheet's y grows
  downward, the CPL's Mid Y upward from its origin. `--gerbers` finds where the Gerbers' (0, 0) lies on the board and
  checks the CPL from there, as JLC does.
* **Turned parts.** KiCad's position file carries KiCad's angles. EasyEDA draws many packages at another angle
  (SOT-23 variants, LGA sensors, drivers, connectors).
* **Shifted parts.** The EasyEDA origin is not KiCad's anchor. Connectors are the usual case: on the board below,
  the EasyEDA origin of a 30-pin FPC connector is 1.34 mm from KiCad's.
* **The bottom side.** JLC does not document how it reads the rotation of a bottom-side part. A CPL that is right on
  top can be wrong on the bottom.
* **Pads that land on copper but on the wrong pins.** A SOT-23-6 turned 180 degrees still puts all six pads on copper,
  so a check that only asks "is it on copper?" passes it. The pins are on the wrong nets.
* **Pinout, not rotation.** Two parts in one package can have different pin functions: a MOSFET whose pins are G, S,
  D on a symbol whose pads are G, D, S lands every pad on copper at every rotation that fits, and the board does not
  work. jlcplace compares the LCSC part's pin names, through the row, with the symbol's pin functions.
* **Guessed corrections.** Existing plugins correct rotations from tables keyed by footprint names or regular
  expressions. They never look at the footprint JLC actually uses for the LCSC code, so they cannot tell when the
  guess is wrong, and they cannot check a CPL.

This tool exists because a real board shipped a bad CPL. The dial-me-spinner boards' first CPLs had 7 misplaced rows
(main J1, J20, J40; base J1, J2, J3, SW1), 1.4 to 4.5 mm off and one 127 mm off: repeated pad numbers broke the origin
fit that made them. `jlcplace check` flags exactly those 7 rows. From EasyEDA records alone, `jlcplace cpl` writes the
fixed CPLs again, to within a micron, once SW1's numbering has been decided (see below).

## Install

```
git clone https://github.com/tyevco/jlcplace && cd jlcplace
pip install -e .            # Python 3.10+ (CI tests 3.10 to 3.13 on Linux, macOS and Windows), numpy
```

To read `.kicad_pcb` files, jlcplace uses KiCad's `pcbnew` Python module when it can import it (KiCad's own Python,
or a virtual environment made with `--system-site-packages` on a machine with KiCad). Without it, a built-in
S-expression reader handles KiCad 5 to 10 files, so CI does not need KiCad; it also reads a file from a newer KiCad
than the pcbnew at hand, which the default `--reader auto` falls back to (`--reader pcbnew` says which version wrote it
and that pcbnew cannot read it). A board can also be saved once as a JSON
snapshot (`jlcplace snapshot`) and checked from that.

## Quick start

**Check a CPL from any tool** (kicad-jlcpcb-tools, Fabrication-Toolkit, KiKit, hand-made, or KiCad's position file):

```
jlcplace fetch board.kicad_pcb --bom board-BOM.csv   # download the EasyEDA records once (rate-limited, cached)
jlcplace check board.kicad_pcb board-CPL.csv --bom board-BOM.csv \
    --report report.html --checklist checklist.md
```

`--gerbers` (a directory, a `.zip` or one file) is the Gerber set you order with. JLC reads the CPL from the
Gerbers' own (0, 0), so with `--gerbers` that point is the CPL origin of the check, whatever `--origin` says; see
[The Gerbers' origin](#the-gerbers-origin). Without it, the summary reminds you once that the Gerbers must be plotted
from the CPL's origin.

`fetch` is the one command that uses the network unasked. `check`, `cpl`, `fit`, `show` and `confirm` read the
record cache and download nothing unless `--online` is given (then they fetch what the cache lacks, as `fetch` does).
A check that lacks records says which and how to get them ("no EasyEDA record in the cache ... for 2 codes (C1001,
C1002): `jlcplace fetch board.kicad_pcb --bom board-BOM.csv` downloads them (rate-limited), or pass --online"), right
after the summary's header when the cache lacks nearly all of them (every row would say "not simulated"); those parts
are checked from the database, or not simulated.

`--bom` checks that the BOM agrees with the board and the CPL. JLC assembles from the BOM's LCSC column and never sees
the board, so the BOM's code is the part that is simulated (and written by `cpl --bom`), also where the board names
another code; such parts are listed ("JLC places C7519 from the BOM; the board says C2681320"). JLC never sees KiCad's
attributes and places what its BOM and CPL list: a part the board excludes from its BOM or its position files (KiCad's
own Jack_3.5mm_CUI_SJ1-3523N_Horizontal comes so) is checked like any other when the BOM lists it, or, without
`--bom`, when only the position files exclude it and the CPL has its row; the position-file exclusion is then yellow
(untick it if JLC is to assemble the part). A part the BOM lists without a row is red, as KiCad's position file leaves
such a part out; a row that nothing else lists is yellow (JLC does not place it). `fetch BOARD` fetches the codes of
parts excluded only from the position files, and `cpl --bom` writes rows for the parts the BOM lists. Without `--bom`,
a board whose parts mostly have no LCSC code gets a hint to pass the BOM or `--lcsc-field NAME`. The CPL may use JLC's
header (Designator, Mid X, Mid Y, Layer, Rotation) or any common spelling. It may also be a KiCad position file (CSV or
ASCII). UTF-8, cp1252 and UTF-16 (Excel's "Unicode Text") files are read, with any line ends (also Excel for Mac's
CR-only "CSV (Macintosh)"); a value that is not a finite number ("nan", "inf") is bad input. `--units in` reads a CSV
position file exported in inches as meant, but JLC reads a CPL's numbers in mm: such a CPL is red (a `units` finding),
and the fix is to export it again in mm. Without `--gerbers`, the CPL origin is the board's aux (drill/place file)
origin; use `--origin grid|absolute|X,Y` for other origins (X,Y in mm, or with a unit: `--origin 4in,-500mil`).

**The common offset.** When at least 60 % of the rows (and 3) are displaced alike from where they belong, `check`
says so in one line: the vector, the origin the CPL was written from (the page origin, the grid origin, or a point
that is none of the board's origins), or numbers in another unit (write the CPL again in mm), or a Y axis left pointing
down, with the fix: the exact `--origin` to pass when the Gerbers use that origin too, or to export the Gerbers, drill
files and CPL again from one origin. An origin error moves every row: when more than 10 % of the rows sit where they
belong, the line only says which rows are displaced alike (parts whose EasyEDA origin is not KiCad's anchor, rows
edited by hand), with no origin and no fix for every row. `check --fixed-cpl FILE` writes the CPL with the offset taken
out (its own rows and angles, moved, or in mm, or with Mid Y negated) and checks it; rows moved onto the CPL origin
without `--gerbers` are right only if the Gerbers are plotted from that origin too, which the note says is unchecked:

```
$ jlcplace check board.kicad_pcb board-pos.csv          # KiCad's position file, from the page origin
...
jlcplace: origin mismatch: 182 of 184 rows would sit on their parts if shifted by (-100.000, 100.000) mm: the CPL was
written from the page origin (0, 0), not the board's aux origin (100, 100). JLC lays the CPL over the Gerbers from the
Gerbers' own origin: export the Gerbers, drill files and CPL from one origin (KiCad: 'Use drill/place file origin' in
all three); if the Gerbers use that origin too, check with --origin absolute
```

**Write the CPL:**

```
jlcplace cpl board.kicad_pcb -o board-CPL.csv
```

`cpl` takes each part's correction from the database (`--db`) or fits one, writes the rows, and checks the file it
wrote. A part with neither an EasyEDA footprint nor a stored correction gets KiCad's angle and the centre of its
courtyard (else of its fabrication-layer drawing): JLC places a part by its centre, and KiCad's anchor is pin 1 on
most through-hole connectors (a right-angle coaxial connector's can be over 10 mm from its body's centre). Without either drawing the row is KiCad's
anchor, and the correction's reason says when that is a pin off the middle of its pads (pin 1 of a connector, not an
exposed pad). With `--gerbers` it writes the rows relative to the Gerbers' (0, 0), the origin JLC will read them
from. If any row is red, it exits with code 2 and writes no file: a CPL already at that path is left unchanged, and
the run says so. `--force` writes the file anyway. `-o -` is refused: the CPL is a file (`--json -` prints the
results).

**When the records cannot decide.** Some parts need a person to decide. For example, the TS-1187A switch is numbered
1-4 in EasyEDA and 1,1,2,2 in KiCad, and which EasyEDA pins the switch joins inside is in its datasheet, not in the
records. Such a part stays red until someone records the decision. So does a polar two-terminal part whose EasyEDA pin
names and pad numbers disagree when no "+" on its EasyEDA footprint settles it (or whose "+" contradicts its pin
names where the pad numbers say nothing): its polarity needs a look in JLC's preview. A "+" that disagrees with the
pad numbers alone is followed, yellow ("polarity evidence conflicts"): in JLC's preview data the "+" disagreed with
the pad numbers 5 times and was right every time.

```
jlcplace confirm board.kicad_pcb --ref SW1 --db jlcplace-db --by you --source manual --note "pins 1-2, 3-4 joined"
```

After checking parts in JLC's placement preview, record that too, with the CPL you uploaded: `jlcplace confirm
board.kicad_pcb --ref U3 --db jlcplace-db --by you --source preview --cpl board-CPL.csv`. What the preview showed is
that CPL's row, which need not be jlcplace's correction (a CPL from another tool), so the row is what is recorded: its
correction replaces the stored one when they differ. If you had to move a part in the preview, add the keys you
pressed (`--space N --right N --up N`): they are relative to the uploaded row, never to the stored correction. Parts
that share a database entry (one code on one footprint) must agree on it, and a verified entry is not given other
numbers until its own row is recorded as failed (`--failed`). `--source board` (the assembled board) also takes the
uploaded CPL; `--source manual` does not need one. The rows are read from `--origin` (default aux), as `check` reads
them without the Gerbers; for a CPL written from the Gerbers' (0, 0) (`cpl --gerbers`), pass the same `--gerbers`
(`show` takes it too). A row that puts every EasyEDA pad off copper is refused, and so is a row of a part with no
EasyEDA footprint whose point lies off the part (off its pads where the check calls the row red for that, off its pads
and its courtyard, or, with no courtyard, off its pads and outside the board outline, or further off them than 5 mm
and their own size): it is usually read from another origin (or, for a bottom part, another bottom reading: the
message names the readings that land the pads, and if the CPL was written with one, pass it), and the correction
stored from it would be that far off. Read from the Gerbers'
(0, 0), or a few mm from the part, a row without presses is off itself, as the check reports it, and a part that came
out right was moved by JLC: there is nothing to record; with presses, they are what is wrong (miscounted, or of the
wrong sign): confirm again with those that were made. `confirm` needs the part's EasyEDA
record in the cache (or `--online`): a verification is stored under the EasyEDA footprint it was made with, so that a
later version of the footprint makes it stale. A verification records the numbers it was of. After `--failed`,
`check --db` holds the stored correction's row red ("verified wrong by JLC's preview on 2026-10-22 (U3, you: pin 1 at
the wrong corner)"), for every part that shares its entry, and `cpl --db` writes no CPL with it unless `--force`, until
the correction is fixed (the presses that put the part right, or `--rot --dx --dy`) and confirmed again, or a later
verification of the same row goes right; a refit of the same numbers, or of numbers within 0.15 mm and 2 degrees of
them, does not clear it.

`jlcplace fit board.kicad_pcb --db jlcplace-db` stores fitted corrections. `jlcplace show C2040` and
`jlcplace show board.kicad_pcb --ref U3` show what jlcplace knows about a code or a part.

## The Gerbers' origin

JLC lays the CPL's Mid X / Mid Y over the Gerbers from the Gerbers' own (0, 0). KiCad plots board point b, with plot
origin o (the drill/place file origin when "Use drill/place file origin" is ticked, else the page origin), at
g = (bx - ox, oy - by): Gerber y up, as the CPL's. So a CPL is right only when it was written from the origin the
Gerbers were plotted from.

`--gerbers DIR|ZIP` reads the Gerber set (RS-274X, with or without X2 attributes; Excellon drill files are listed and
skipped; of the layers, only the outer copper, the paste, the profile and files nothing names are parsed) and finds
the board point that is its (0, 0): from KiCad's pin attributes (`TO.P`) when it has them, else by matching pad
positions (the board's own origins are tried too, so a periodic layout plotted from a round origin is not taken one
pitch off). That point becomes the CPL origin of the check, as it is JLC's; the summary header says so ("origin 100,
100 from the Gerbers (aux)"). From it, rows displaced alike by more than 0.05 mm are an origin error. Then, red for
the whole run, said first:

* **origin**: the rows are displaced alike (the common offset above) from where they belong from the Gerbers' origin,
  so the CPL was written from another origin; or `--origin` names another point than the Gerbers' (0, 0):

  ```
  RED     origin mismatch: JLC lays the CPL over the Gerbers from their (0, 0), board (0, 0) (the page origin), but the
          CPL was written from the aux (drill/place file) origin (100, 100) (184 of 184 rows): JLC would put every
          part 141.42 mm off, displaced by (-100.000, +100.000) mm; fix: plot the Gerbers and drill files from the aux
          (drill/place file) origin (KiCad: 'Use drill/place file origin' ticked), or write the CPL from the Gerbers'
          origin (`jlcplace cpl BOARD --gerbers GERBERS` does; KiCad's position file: 'Use drill/place file origin'
          unticked), or shift every row by (+100.000, -100.000) mm (`check --fixed-cpl FILE` writes it)
  ```

  Every row below it is red too: they are checked as JLC will read them.
* **gerbers**: the set is mirrored (y not inverted, x mirrored, half a turn), 25.4 times too large or too small (or,
  when its pin attributes fit only at another scale, says by how much), not this board's (under half of its pads,
  SMD and plated, have a Gerber pad at their place), or older than the board (pads moved, came or went since it was
  plotted, or its pin attributes put other pins or nets than the board's at a pad: re-plot it); its paste plotted from
  another origin than its copper; a side the board has SMD pads on without its copper layer; two plots in one set
  (two top copper layers, two job files: KiCad never deletes an old plot); its outline plotted from another origin
  than its copper (compared with the board's Edge.Cuts). A side with copper but no paste is yellow when the CPL places
  parts there. Without a trustworthy origin, the rows are checked from `--origin` (default aux).

`jlcplace cpl --gerbers` writes the rows from the Gerbers' (0, 0), so the CPL matches the Gerbers it is ordered with.

## KiCad export checklist

One origin for everything JLC gets, and JLC's units and axes:

1. Place the drill/place file origin (Place > Drill/Place File Origin), usually at the board's bottom-left corner.
2. **Plot** (Gerbers): tick *Use drill/place file origin*
   (`kicad-cli pcb export gerbers --use-drill-file-origin`).
3. **Generate Drill Files**: origin *Drill/place file origin* (`kicad-cli pcb export drill --drill-origin plot`).
4. **Component Placement** (or write the CPL with `jlcplace cpl`): units **mm**, format **CSV**, tick *Use
   drill/place file origin*, both sides in one file (`kicad-cli pcb export pos --format csv --units mm
   --use-drill-file-origin`). KiCad's position file has KiCad's angles and anchors, not JLC's: check it, or let
   `jlcplace cpl` write the rows.
5. The Y axis: the sheet's y grows downward, the CPL's Mid Y upward from its origin. KiCad's position file negates y
   for you; a CPL written from sheet coordinates is mirrored top to bottom (`check` says "with Mid Y negated").
6. Check before upload: `jlcplace check board.kicad_pcb board-CPL.csv --bom board-BOM.csv --gerbers gerbers.zip`.

## Use it from Claude Code

This repository is also a Claude Code plugin marketplace. In Claude Code:

```text
/plugin marketplace add tyevco/jlcplace
/plugin install jlcplace@jlcplace
```

Or in one step (Claude Code 2.1.275 or later): `/plugin install jlcplace --marketplace tyevco/jlcplace`. To update,
run `claude plugin marketplace update jlcplace`, then `claude plugin update jlcplace@jlcplace`, and restart.

The plugin's skills make Claude use jlcplace for the KiCad to JLCPCB assembly hand-off:

* **check-jlc-placement**: check a CPL from any tool against the board and explain every red and yellow row.
* **write-jlc-cpl**: write a corrected CPL with `jlcplace cpl`, and record numbering decisions.
* **prepare-jlc-order**: Gerbers, drill files and CPL from one origin, a BOM with LCSC codes, the check, and a
  pre-upload checklist (with kicad-cli, or KiCad's dialogs).
* **diagnose-jlc-misplacement**: an order or preview with parts shifted, turned, reversed or on the wrong pins:
  origin, units, Y axis, EasyEDA angles and origins, the bottom side, polarity or pinout.
* **review-jlc-preview**: walk JLC's placement preview with the checklist and record the results with
  `jlcplace confirm`.

Claude installs the jlcplace CLI when it is missing, if you agree. It asks before anything downloads EasyEDA records.
See [plugins/jlcplace/README.md](plugins/jlcplace/README.md).

## Results

Every CPL row gets a verdict:

| verdict | meaning |
|---|---|
| red | An EasyEDA pad lands off copper or on another pin's net. Also: the LCSC part's pin names, through the row, a permutation of the symbol's pin functions that no placement fixes (status `pinout`: a MOSFET whose pins are G, S, D on a G, D, S symbol is wired wrong whatever the rotation); a polar two-terminal part the other way round than its correction puts it (status `reversed`), or whose polarity needs a decision; a row nothing vouches for (the pad numbers do not decide it and the correction is red, or differs from it); no EasyEDA footprint for a polar or multi-pin part, or a two-pad part's row point off its pads; the row of a stored correction verified wrong (`confirm --failed`, status `verified-wrong`), whatever its pads land on; a row for a part that is not on the board, is DNP or board-only, or is on the wrong layer; a duplicate row; a reference two placed parts carry (JLC cannot tell them apart); a part JLC places (assembled, or listed by the BOM whatever the board excludes it from) with no row. |
| yellow | Probably fine, but look: no EasyEDA footprint for a two-pad non-polar part, or a two-pad trimmer capacitor (the checklist asks which pad is its rotor, which turned round it is on the other one); a row off where a known correction puts the part ("offset": more than 0.15 mm and a quarter of the smallest pad it lands on, or 2 degrees, or a quarter turn that only alike lands let land); a row that lands as a fitted correction does whose choice of placement is uncertain (a polarity only the pad numbers decide, or whose evidence conflicts: a "+" against the pad numbers; an orientation the pads do not fix); KiCad pads that no EasyEDA pad lands on; a row point outside the board outline (the Gerbers' outline with `--gerbers`, unless their check finds it wrong, else the board's Edge.Cuts, a cutout counting as outside); a row away from a stored correction verified wrong (nothing verified vouches for it); numbering vouched for only by a manual decision; pin names that differ from the symbol's otherwise, more of them than agree (check the LCSC part's pinout); a row for a part the board excludes from its BOM or position files that nothing else lists (JLC does not place it); a part the board excludes from its position files that the BOM or the CPL lists (it is checked, and JLC places it: it never sees KiCad's attributes); or a BOM inconsistency. |
| green | Every pad lands on its own pin. Harmless differences are allowed: a resistor's terminals swapped, pads of the same net, or numbers that only one side has (MP, A1B12). |
| verified | Green, and the row matches a correction verified in JLC's preview or on an assembled board. |

With `--gerbers`, the run as a whole can be red too, said before the rows (a **finding**): `origin` when the CPL was
written from another origin than the Gerbers' (or `--origin` names another), with what JLC will do, by how much
every part moves and the fix; `gerbers` when the Gerbers are not this board's as it is (mirrored, in the wrong unit,
another board's, or older than it: pads moved, came or went since they were plotted; a side, a paste layer or the
outline missing or from another origin; two plots in one set); `units` when `--units` read the CPL's numbers in
another unit than mm, which JLC reads them in.

Exit codes: 0 clean, 1 something yellow when `--fail-on yellow` is given, 2 something red, 3 bad input (a file that
cannot be read or written, a database file that cannot be read, a bad option or value, a reference that cannot be a
designator, a `--db` that is a file), 4 an internal error (a bug: the traceback is printed), 130 interrupted (ctrl-C),
141 the reader of stdout went away (`jlcplace ... | head`). `-v` (before or after the command) logs what the EasyEDA
client does. Outputs: `--report` (one self-contained HTML page, for your own review: see below), `--overlay DIR` (images
of the board as JLC's preview shows it, for your own review: see below), `--json`, `--junit`, `--checklist` (`.md` or
`.csv`), `--github-annotations` and, for `check`, `--fixed-cpl` (the CPL with a common offset taken out, see above). A
finding is the first line of the summary (also under `-q`), a `findings` entry and an `origin` object in the JSON (the
CPL origin used, the Gerbers' check, the rows' common offset), a JUnit test case (`origin`, a passing one when the
Gerbers' origin is the CPL's), an annotation on the CPL, a banner in the HTML report, and the checklists' origin line.

Text from the files and from EasyEDA reaches terminals, CI logs and reports, so a reference with a control character
(a newline would start a GitHub workflow command in the log) or one that starts with `= + - @` (a formula in a
spreadsheet) is bad input: no designator looks like that. EasyEDA's and the database's texts are printed with control
characters as `\xNN`, the JUnit file holds no character XML forbids, the CSV checklist quotes a cell that would be a
formula (`'=...`), and the Markdown checklist escapes Markdown.

The HTML report has top and bottom views of the board and a card for every part. KiCad copper pads are filled and
numbered. The EasyEDA pads are outlined where JLC will put them. On the part cards they are blue on a KiCad pad of
the same number, amber on a pad of another number and red off copper; on the board views they take the colour of
their row's verdict (blue for a green row), and red and yellow parts are labelled above them (each view has its own
legend; the big "+" on a board view is the CPL origin). The
bottom is drawn seen from below, as JLC's preview shows it. The header names the board and the CPL, not their paths.
Above the board views and above the part cards the report declares the source of the outlines, the JLCEDA/EasyEDA
Official Library, with links to https://lceda.cn/ and https://easyeda.com, as the records' licence asks.

`--overlay DIR` writes `top.svg` and `bottom.svg`, one image per side like JLC's placement preview (the bottom seen from
below): the board outline (from the Gerbers with `--gerbers`, else the board's Edge.Cuts), the KiCad copper, the EasyEDA
pads where JLC puts them in their row's verdict colour, red and yellow parts circled and labelled (none past 40 on a
side: a CPL from another origin makes every row red, and the header says why; the circles stay inside the drawing), an
arrow toward EasyEDA pads that land far off the board (a CPL from another origin: the header says how far), the CPL
origin (+) and the Gerbers' (0, 0) (x), a header with the findings, and the source of the outlines. `top.png` and
`bottom.png` are written too when a renderer is at hand (`pip install cairosvg`, or Chrome or Chromium on the PATH, or
`JLCPLACE_CHROMIUM`); otherwise a note says so.

The **preview checklist** lists what only JLC's preview can settle, short enough to work through. Each part is
listed once, its other reasons folded into its item, and alike parts share one item that names the part to look at.
It starts with the origin: the Gerbers' check (or the finding), or, without `--gerbers`, a reminder to plot them
from the CPL's origin. Then, in this order:

* **pinout**: parts whose LCSC pin names differ from the symbol's pin functions (red for a permutation no placement
  fixes). The part's datasheet settles it, not the preview: JLC's preview shows where pads land, not what the pins
  are (`confirm --source manual` records the datasheet's answer, a yellow accepted row; on an entry the preview
  already verified only `--source board`, a board that works, or editing the entry does).
* **uncertain correction**: parts whose correction is not green for a reason the preview can settle: red, a choice
  of placement in doubt (a polarity, names against numbers, an orientation the pads do not fix; those first), or a
  bare KiCad pad, a numbering, a manual decision. A green row whose fit is yellow only because the land patterns
  differ (every pad lands) is left out: the preview cannot settle that.
* **not simulated**: parts with no EasyEDA footprint, each on its own (not a part without an LCSC code: JLC does
  not place it, so its preview never shows it); a trimmer capacitor's item asks which pad is its rotor.
* **outside the outline**: rows whose point lies outside the board outline (a part overhanging the edge, a row of
  another part); the parts of one code on one side are one item, naming the one furthest out.
* **bottom reading**: bottom parts whose pins would move if JLC read the bottom rotation differently (see below),
  said in pins ("as jlc-2022+180, pin 1 would sit on pad 4"). The reading is one question for the whole board: a
  green part that moves under every other reading comes first, and when it sits right the others follow. Recorded
  with `jlcplace confirm` (`--source preview`, or `--source board` on the assembled board), a bottom part shown right
  rules out every reading under which it would have moved, for every part (only its own verification counts, at the
  rotation of its row: not one of a part that shares its entry at another angle, nor its own of a row at another
  rotation, such as one written under another `--reading`): a part that moves only under readings ruled out has no
  item, and the others say which readings are ruled out, by which part. Parts of one code at one rotation move alike
  and share an item.
* **odd angle**: rows at angles that are not multiples of 90, one item per code and side (an LED ring of 44 is one
  item), naming the part furthest from a multiple of 90; a part verified itself at its angle is left out, and so are
  the verified rows of its entry on its side once one of them was verified at an odd angle (C107 for the ring of
  capacitors).

Within each (after the bottom reading's first part), red rows come first and are flagged: fix the row before
looking at it (for a pinout: the LCSC part or the symbol is wrong). The Markdown, CSV, JSON and HTML checklists carry
the same items and the origin line (the CSV's last row, category `origin`); the CSV and JSON list every part an item
stands for (`refs`).

## How the check works

Frames (`src/jlcplace/model.py`):

* **board**: KiCad's coordinates, in mm, y down.
* **library**: the footprint as drawn in its KiCad library.
* **ee**: the EasyEDA footprint as drawn, relative to its origin.
* **cpl**: JLC's coordinates, relative to the CPL origin, y up.

KiCad places a part at board = P + R(theta) · D · library, where D mirrors y on the bottom. The **correction** for a
part is the rigid map between the two footprints:

    library = R(rot) · ee + (dx, dy)

It is fitted from pad numbers and pad geometry, with symbol pin names, nets and, for a polar two-terminal part, the
"+" its EasyEDA footprint draws beside its positive pad as further evidence, and the unplated holes both footprints
have (a connector's peg goes into its hole one way only: a right-angle header whose pins also land turned round is
placed by its pad numbers and its peg); a polar part never takes the side that most of its evidence (pad numbers, pin
names, the "+", the holes) rejects. Each fit gets a verdict with reasons, for example "polarity decided by pad
numbers only" or "ambiguous". A mirrored match is never used: when only the mirror image of the EasyEDA footprint
lands, the part gets no row ("mirrored footprint or wrong footprint"). When a normal placement lands every pad too,
that is the correction, red, so that `cpl --force` and `confirm` can write its row: on a symmetric land pattern (a
trimmer's three pins and two mounting pads) a mirror image that matches the pad numbers only renumbers it ("numbering
mirrored between the libraries"), and which numbering is the part's is in its datasheet. Whether a two-terminal part
may be turned round (a resistor) or not (a diode, a tantalum, a jumper or switch on a right-angle "_Horizontal"
footprint, whose body would face the other way) is decided once, for the fit and the check alike: from its reference
prefix, its KiCad footprint name and pin functions, and its EasyEDA record's title, part categories
(tantalum, electrolytic, diode, LED, TVS, ...) and pin names. From the correction:

* Mid X, Mid Y = where the EasyEDA origin lies on the board, in the cpl frame.
* Rotation = theta + rot on the top, and 180 - theta + rot on the bottom.

The check runs the other way. For each row it computes where JLC puts every EasyEDA pad centre, finds the KiCad
copper pad that point lands in, and compares nets. Comparing nets rather than pad numbers is what catches a SOT-23-6
turned 180 degrees: every pad lands on copper, but on the wrong pins.

**Pin names.** With the EasyEDA record at hand, the LCSC part's symbol pin names, through the row, are compared with the
KiCad pin functions of the pads they land on. Names that are a permutation of the symbol's (a pin on the net of another
pin's function) with no placement known to wire it better (one that permutes no names and agrees with more of them) are
red, status `pinout`, in the fit and in the check, whatever the correction says: no rotation wires that part right. So
is a named pin on a pad whose function says nothing (NC, ~) when the board's pads of its function get no pin of that
function (a dual LM358's VCC on a single op-amp's NC pad). Other name differences are yellow when most of the names
disagree, else a message. The two libraries' spellings are read as one: Input / VI, Output / VO, VCC / V+, GND / VSS /
V-, 1IN+ / IN1+ / INA+ / VIN+ / +IN / + (an op-amp's input), VCC+ / VS+ / V+ and VCC- / -VS / V- (split supplies), R- /
RK, DP1 / D+ (an index only one side has), P0.00_XL1 / P0.00/XL1; G against GND (a gate, or a crystal's ground) is not
compared, and names that say where a pad is, not what it is (TAB, EP, PAD, THERMAL: a regulator's tab is often its
output), say nothing. Names swapped between pads of one net, and a two-terminal part's names (its polarity, which the
fit decides from the pad numbers, the pin names and the + mark), are not a pinout.

**The bottom-side caveat.** JLC does not document how it reads a bottom-side rotation. The reading jlcplace uses,
`jlc-2022` (180 - theta + rot, viewed from below), is the one every open tool uses. It also matches the JLC
preview data recorded in the z2amiller fork of kicad-jlcpcb-tools. Nothing offline can prove which reading the
factory uses, so every bottom row is also placed under the three other possible readings. Parts whose pins would move
under any of them go on the preview checklist. `--reading` selects another reading if JLC ever changes.

## The correction database

`--db DIR` is a directory with one JSON file per LCSC code, made to be committed. Each entry is keyed by a hash of the
EasyEDA footprint's pads and a hash of the KiCad pads and their pin functions, so one code on two KiCad footprints,
or on one footprint with other pin names (a diode with pad 1 the cathode, and one with pad 1 the anode), gets two
entries: one part's correction or verification never stands for another's. Entries hold derived numbers: rot, dx,
dy, the fit's verdict and reasons, and the verification history, plus the EasyEDA footprint's title (its package
name, e.g. `SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR`) for people reading the file; without the record, the check reads
in it what the fit found non-polar (BI, a bidirectional TVS; a crystal's title). They never hold pad geometry.

Status, strongest first:

* **board-verified**: right on an assembled board.
* **preview-verified**: right in JLC's preview.
* **manual**: decided or typed in by a person.
* **auto**: fitted.

When EasyEDA changes a footprint, its entries go stale. jlcplace then refits, and when the refit disagrees with the
stored correction, the part becomes yellow until it is checked again.

## CI

Commit the database and run the check; it downloads nothing unless `--online` is given. With no EasyEDA records at
hand, a part with a stored correction is still simulated: its KiCad pads, taken back through the correction, stand
in for the EasyEDA pads. That catches rows that no longer match the stored corrections (a part moved, a CPL edited by
hand). The verdict cannot be better than the correction's, and a row whose verdict the correction caps says so
("yellow because the stored auto correction is yellow: ..."). A two-pad non-polar part with neither a record nor a
stored correction is still checked against its own copper: a row point off its pads is red. For a full simulation,
the recipe below also keeps the EasyEDA records in an Actions cache: `jlcplace fetch` downloads the ones the cache
lacks (it is the only step that talks to EasyEDA, at most one request per 0.7 s), and the cache is saved under a new
key when the board changes.

```yaml
# .github/workflows/cpl.yml
name: CPL
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    env:
      JLCPLACE_CACHE: ~/.cache/jlcplace       # the EasyEDA record cache (jlcplace expands the ~)
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "jlcplace @ git+https://github.com/tyevco/jlcplace@975d8dc4358587dd88aff66d35695356e27b2db8"   # a commit SHA
      # EasyEDA records: never upload them, or an HTML report (it draws EasyEDA pad outlines), as an artifact of a
      # public repository; the JUnit and JSON outputs hold derived data only.
      - uses: actions/cache@v4
        with:
          path: ~/.cache/jlcplace
          key: easyeda-${{ hashFiles('hardware/board.kicad_pcb', 'fab/board-BOM.csv') }}
          restore-keys: easyeda-
      - run: jlcplace fetch hardware/board.kicad_pcb --bom fab/board-BOM.csv
        continue-on-error: true               # EasyEDA down or rate-limiting: the check says which records it lacks
      - run: >
          jlcplace check hardware/board.kicad_pcb fab/board-CPL.csv --bom fab/board-BOM.csv
          --db jlcplace-db --github-annotations --junit cpl.xml
```

Pin the install to a commit SHA, so that a new jlcplace does not change the verdicts under you. jlcplace has no
release tag yet; the recipe pins a commit of `main`, so replace it with the one you tested with.
`fetch` downloads the codes on the board and, with `--bom`, the BOM's (JLC places the BOM's codes, also for a board
that keeps its codes in the BOM only); it takes LCSC codes too (`jlcplace fetch hardware/board.kicad_pcb C7519`).
Without KiCad on the runner, the board is read with the S-expression reader. Pass `--fail-on yellow` to make yellow
fail the job as well (with the records missing, routine yellows such as "not simulated" fail it too). The check
itself downloads nothing: drop the cache and fetch steps to check from the committed database alone.

### GitHub Action

From another repository, after the job (or step) that writes the fab outputs, the action does all of the above in one
step: it installs jlcplace in a virtual environment of its own, restores the EasyEDA records from the calling
repository's Actions cache (keyed on the board, the BOM and `args`), fetches what the cache lacks (the board read with
the `--lcsc-field` and `--reader` of `args`; rate-limited), saves the cache (what a fetch cut short got too, so the next
run goes on from it), and runs `jlcplace check` with annotations on the CPL's lines, the summary and the preview
checklist in the job summary, and JUnit, JSON and checklist files. EasyEDA being down, or rate-limiting the runner
(GitHub's runners often get a 403 after a few dozen records), leaves the check with the records at hand: a multi-pin or
polar part whose record is missing is red (not simulated) unless the `db` holds its correction. Commit one (`jlcplace
fit BOARD --db jlcplace-db` stores every part's) so that the step does not depend on EasyEDA:

```yaml
      - uses: tyevco/jlcplace@main            # or a commit SHA
        id: cpl
        with:
          board: hardware/board.kicad_pcb
          cpl: fab/board-CPL.csv
          bom: fab/board-BOM.csv                # optional: JLC places the BOM's codes
          gerbers: fab/gerbers.zip              # optional, recommended: the Gerbers the CPL is ordered with
          db: jlcplace-db                       # optional: the committed correction database
          fail-on: red                          # or yellow
          args: --lcsc-field LCSC_PN            # optional: more `jlcplace check` arguments, split at whitespace
```

Inputs: `board` and `cpl` (required), `bom`, `gerbers`, `db`, `fail-on` (default `red`), `args`, `python-version`
(default `"3.12"`, set up for the action alone: the job's PATH and Python stay as they were; empty uses the runner's
Python) and `version` (default: the action's own ref). Outputs: `exit-code`, `red` and `yellow` (counts, a red finding
such as an origin mismatch included; empty when the check could not run), and the paths `junit`, `checklist` and `json`.
The step fails as `jlcplace check` does; add `continue-on-error: true` to read the outputs in a later step anyway. The
action uploads nothing and writes no HTML report or overlay image (they draw EasyEDA pad outlines); its JUnit, JSON and
checklist files hold derived data only.

## Limits

* Only pad centres are placed. A row slightly off but still with every centre on its own pad passes; a known
  correction adds a message when the row is more than 0.05 mm or 0.5 degrees from the row jlcplace would write, and
  makes it yellow ("offset") beyond 0.15 mm and a quarter of the smallest pad it lands on, or 2 degrees, and when
  it is a quarter turn from that row although every pad lands as the correction puts it (a one-pad part, whose long
  pad would lie across its land).
* Polarity: where EasyEDA's pin names and pad numbers disagree, the "+" drawn on its footprint decides (it was right
  in all 6 such cases of JLC's preview data; the names alone were wrong once, D12 C2836396). The "+" is read from
  two crossing strokes on the silkscreen or document layer; KiCad's positive pad from the pin functions, else KiCad's
  library convention (diode pad 1 = cathode, polarised capacitor pad 1 = +), which also names a two-terminal part's
  pads when they have no pin functions, so that EasyEDA's pin names (+ / -, A / K / C) count against it (on that
  part only: an op-amp's + and -, a transistor's C are no polarity). Where only the "+" disagrees with the pad
  numbers, the "+" is followed and the part is yellow. Footprints outside KiCad's libraries may follow another
  convention: give their pins functions.
* The bottom-side reading is a well-supported hypothesis, not a documented fact; see the caveat above.
* EasyEDA's rotation sense is proven from the records' corner order at multiples of 90 degrees. At other angles,
  RECT and OVAL pads are checked against their own outlines.
* Symbol pin names are used where EasyEDA has them. Multi-unit symbols are parsed through every unit, a unit's
  names before the main symbol's; on three real records (two 324-ball BGAs, a 3-unit module) they name exactly
  the footprint's pads.
* A part EasyEDA has no record for is not simulated: yellow for a two-pad non-polar part or trimmer capacitor (its
  rotor is a checklist item), red otherwise, unless a stored correction exists. For a red one the check says where
  its row point lies: the centre of its courtyard, or KiCad's anchor and the pin that is; `cpl` writes it at the
  courtyard's centre, which is a guess at where JLC's footprint has its origin.
* The EasyEDA API is not a documented, stable interface. Only `fetch` and `--online` use it. jlcplace waits at least
  0.7 s between requests, backs off on errors (honouring Retry-After up to 5 minutes), and stops using it for the
  run after 5 failures in a row or once its retry waits add up to 10 minutes.
* KiCad 10 variant overrides are ignored, and pcbnew from KiCad 6 to 8 is untested; the S-expression reader covers
  those files.
* The pinout check needs pin functions on the board's pads (KiCad fills them from the schematic: "Update PCB from
  Schematic"); a board without them is checked by pads and nets only. Names that both libraries number, differently
  (INT1 / INT2, CC1 / CC2, 1IN+ / 2IN+), are not compared, so a swap between them is not seen.
* `--gerbers` reads the outer copper, paste and profile layers, not the Excellon drill files, and tries plain shifts,
  mirrors, a half turn and the inch / mm mix-ups: a set turned by a quarter turn, or scaled otherwise, is reported as
  not this board's. Gerber files are untrusted: a set whose files would expand together (step and repeat, blocks,
  macros, apertures, the points of arcs) past a million objects or ten million vertices is bad input, and so is one
  that exhausts the memory. The common offset needs 60 % of the rows (and 3) displaced alike, and
  at most 10 % in place; rows wrong each in their own way are judged one by one.

## Data and licensing

jlcplace is MIT licensed (see `LICENSE`).

EasyEDA footprints and symbols come from the **JLCEDA/EasyEDA Official Library** (https://lceda.cn/,
https://easyeda.com). The copyright and related rights in that library belong to JLCEDA/EasyEDA, and its records
carry that copyright and source notice. jlcplace downloads records only into your own cache directory
(`$JLCPLACE_CACHE`, else `$XDG_CACHE_HOME/jlcplace` or `~/.cache/jlcplace`), keeps them verbatim (notice included),
and never redistributes them. The JSON, JUnit, checklist and annotation outputs hold derived numbers, pad numbers and
hashes; the correction database holds those and the EasyEDA footprint's title (a package name such as
`SOIC-8_L5.3-W5.3-P1.27-LS8.0-BL`), and the JSON report and `fit --json` name it too.

The HTML report and the overlay images draw EasyEDA pad outlines. They declare their source prominently, with both
links (the report above the board views and the part cards and again in its footer, an image under its drawing), and
the report asks search engines not to index it. They are meant for your own review: do not publish them, for example
as a CI artifact of a public repository. This repository contains no EasyEDA
data: its tests build their EasyEDA records by hand, or read a local cache when one is present.
