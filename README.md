# jlcplace

Check and correct JLCPCB pick-and-place (CPL) files for KiCad boards. jlcplace puts JLC's own EasyEDA footprint down
exactly where each CPL row tells JLC to put it, and reports which KiCad pad and net every EasyEDA pad lands on. It
works on a CPL from any tool, and it can write the CPL itself.

```
$ jlcplace check board.kicad_pcb board-CPL.csv
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (pcbnew, 209 parts, origin aux 100, 100) vs dial-me-spinner-main-CPL-JLCPCB.csv (184 rows), bottom reading jlc-2022
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

JLC assembles with the footprints of its EasyEDA library, not yours. An EasyEDA footprint's zero angle and origin
often differ from KiCad's, so a CPL that is right for KiCad's footprint puts JLC's part turned or shifted. The usual
ways of getting this wrong:

* **Turned parts.** KiCad's position file carries KiCad's angles. EasyEDA draws many packages at another angle
  (SOT-23 variants, LGA sensors, drivers, connectors).
* **Shifted parts.** The EasyEDA origin is not KiCad's anchor. Connectors are the usual case: on the board below,
  the EasyEDA origin of a 30-pin FPC connector is 1.34 mm from KiCad's.
* **The bottom side.** JLC does not document how it reads the rotation of a bottom-side part. A CPL that is right on
  top can be wrong on the bottom.
* **Pads that land on copper but on the wrong pins.** A SOT-23-6 turned 180 degrees still puts all six pads on copper,
  so a check that only asks "is it on copper?" passes it. The pins are on the wrong nets.
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
pip install -e .            # Python 3.10+, numpy
```

To read `.kicad_pcb` files, jlcplace uses KiCad's `pcbnew` Python module when it can import it (KiCad's own Python,
or a virtual environment made with `--system-site-packages` on a machine with KiCad). Without it, a built-in
S-expression reader handles KiCad 5 to 10 files, so CI does not need KiCad. A board can also be saved once as a JSON
snapshot (`jlcplace snapshot`) and checked from that.

## Quick start

**Check a CPL from any tool** (kicad-jlcpcb-tools, Fabrication-Toolkit, KiKit, hand-made, or KiCad's position file):

```
jlcplace fetch board.kicad_pcb                    # download the EasyEDA records once (rate-limited, cached)
jlcplace check board.kicad_pcb board-CPL.csv --bom board-BOM.csv \
    --report report.html --checklist checklist.md
```

`fetch` is the one command that uses the network unasked. `check`, `cpl`, `fit`, `show` and `confirm` read the
record cache and download nothing unless `--online` is given (then they fetch what the cache lacks, as `fetch` does).
A check that lacks records says which and how to get them ("no EasyEDA record in the cache ... for 2 codes (C1001,
C1002): `jlcplace fetch BOARD` downloads them, or pass --online"); those parts are checked from the database, or not
simulated.

`--bom` checks that the BOM agrees with the board and the CPL. JLC assembles from the BOM's LCSC column and never
sees the board, so the BOM's code is the part that is simulated (and written by `cpl --bom`), also where the board
names another code; such parts are listed ("JLC places C7519 from the BOM; the board says C2681320"). Parts the board
only excludes from its BOM are checked too when the BOM lists them, since JLC places what its BOM lists. Without
`--bom`, a board whose parts mostly have no LCSC code gets a hint to pass the BOM or `--lcsc-field NAME`. The CPL may use JLC's header (Designator, Mid X, Mid Y, Layer, Rotation) or any common spelling. It may also be
a KiCad position file (CSV or ASCII). UTF-8, cp1252 and UTF-16 (Excel's "Unicode Text") files are read, with any
line ends (also Excel for Mac's CR-only "CSV (Macintosh)"); a value that is not a finite number ("nan", "inf") is bad
input. Use `--units in` for a CSV position file exported in inches (`check` suggests it when most rows would sit
on their parts in inches). The CPL origin is the board's aux (drill/place file) origin; use
`--origin grid|absolute|X,Y` for other origins (X,Y in mm, or with a unit: `--origin 4in,-500mil`).

**Write the CPL:**

```
jlcplace cpl board.kicad_pcb -o board-CPL.csv
```

`cpl` takes each part's correction from the database (`--db`) or fits one, writes the rows, and checks the file it
wrote. If any row is red, it exits with code 2 and writes no file: a CPL already at that path is left unchanged,
and the run says so. `--force` writes the file anyway. `-o -` is refused: the CPL is a file (`--json -` prints the
results).

**When the records cannot decide.** Some parts need a person to decide. For example, the TS-1187A switch is numbered
1-4 in EasyEDA and 1,1,2,2 in KiCad, and which EasyEDA pins the switch joins inside is in its datasheet, not in the
records. Such a part stays red until someone records the decision. So does a polar two-terminal part whose EasyEDA pin
names and pad numbers disagree when no "+" on its EasyEDA footprint settles it (or whose "+" contradicts its only
other evidence): its polarity needs a look in JLC's preview.

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
uploaded CPL; `--source manual` does not need one.

`jlcplace fit board.kicad_pcb --db jlcplace-db` stores fitted corrections. `jlcplace show C2040` and
`jlcplace show board.kicad_pcb --ref U3` show what jlcplace knows about a code or a part.

## Results

Every CPL row gets a verdict:

| verdict | meaning |
|---|---|
| red | An EasyEDA pad lands off copper or on another pin's net. Also: a polar two-terminal part the other way round than its correction puts it, or whose polarity needs a decision; a row nothing vouches for (the pad numbers do not decide it and the correction is red, or differs from it); no EasyEDA footprint for a polar or multi-pin part, or a two-pad part's row point off its pads; a row for a part that is not on the board, is DNP or excluded, or is on the wrong layer; a duplicate row; a reference two placed parts carry (JLC cannot tell them apart); an assembled part with no row. |
| yellow | Probably fine, but look: no EasyEDA footprint for a two-pad non-polar part; a row off where a known correction puts the part ("offset": more than 0.15 mm and a quarter of the smallest pad it lands on, or 2 degrees); a row that lands as a fitted correction does whose choice of placement is uncertain (a polarity only the pad numbers decide, an orientation the pads do not fix); KiCad pads that no EasyEDA pad lands on; numbering vouched for only by a manual decision; or a BOM inconsistency. |
| green | Every pad lands on its own pin. Harmless differences are allowed: a resistor's terminals swapped, pads of the same net, or numbers that only one side has (MP, A1B12). |
| verified | Green, and the row matches a correction verified in JLC's preview or on an assembled board. |

Exit codes: 0 clean, 1 something yellow when `--fail-on yellow` is given, 2 something red, 3 bad input (a file
that cannot be read or written, a database file that cannot be read, a bad option or value, a reference that cannot
be a designator, a `--db` that is a file), 4 an internal error (a bug: the traceback is printed), 130 interrupted
(ctrl-C). `-v` (before or after the command) logs what the EasyEDA client does. Outputs:
`--report` (one self-contained HTML page, for your own review: see below), `--json`, `--junit`, `--checklist`
(`.md` or `.csv`) and `--github-annotations`.

Text from the files and from EasyEDA reaches terminals, CI logs and reports, so a reference with a control character
(a newline would start a GitHub workflow command in the log) or one that starts with `= + - @` (a formula in a
spreadsheet) is bad input: no designator looks like that. EasyEDA's and the database's texts are printed with control
characters as `\xNN`, the JUnit file holds no character XML forbids, the CSV checklist quotes a cell that would be a
formula (`'=...`), and the Markdown checklist escapes Markdown.

The HTML report has top and bottom views of the board and a card for every part. KiCad copper pads are filled and
numbered. The EasyEDA pads are outlined where JLC will put them. On the part cards they are blue on their own pin,
amber on another pin and red off copper; on the board views they take their row's verdict colour, and red and yellow
parts are labelled above them (each view has its own legend; the big "+" on a board view is the CPL origin). The
bottom is drawn seen from below, as JLC's preview shows it. The header names the board and the CPL, not their paths.
Above the board views and above the part cards the report declares the source of the outlines, the JLCEDA/EasyEDA
Official Library, with links to https://lceda.cn/ and https://easyeda.com, as the records' licence asks.

The **preview checklist** lists what only JLC's preview can settle, short enough to work through. Each part is
listed once, its other reasons folded into its item, and alike parts share one item that names the part to look at.
In this order:

* **uncertain correction**: parts whose correction is not green for a reason the preview can settle: red, a choice
  of placement in doubt (a polarity, names against numbers, an orientation the pads do not fix; those first), or a
  bare KiCad pad, a numbering, a manual decision. A green row whose fit is yellow only because the land patterns
  differ (every pad lands) is left out: the preview cannot settle that.
* **not simulated**: parts with no EasyEDA footprint, each on its own.
* **bottom reading**: bottom parts whose pins would move if JLC read the bottom rotation differently (see below),
  said in pins ("as jlc-2022+180, pin 1 would sit on pad 4"). The reading is one question for the whole board: a
  green part that moves under every other reading comes first, and when it sits right the others follow. Parts of
  one code at one rotation move alike and share an item.
* **odd angle**: rows at angles that are not multiples of 90, one item per code and side (an LED ring of 44 is one
  item), naming the part furthest from a multiple of 90.

Within each (after the bottom reading's first part), red rows come first and are flagged: fix the row before
looking at it. The Markdown, CSV, JSON and HTML
checklists carry the same items; the CSV and JSON list every part an item stands for (`refs`).

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
"+" its EasyEDA footprint draws beside its positive pad as further evidence. Each fit gets a verdict with reasons,
for example "polarity decided by pad numbers only" or "ambiguous". A mirrored match is never used. Whether a
two-terminal part may be turned round (a resistor) or not (a diode, a tantalum) is decided once, for the fit and the
check alike: from its reference prefix, its KiCad footprint name and pin functions, and its EasyEDA record's title,
part categories (tantalum, electrolytic, diode, LED, TVS, ...) and pin names. From the correction:

* Mid X, Mid Y = where the EasyEDA origin lies on the board, in the cpl frame.
* Rotation = theta + rot on the top, and 180 - theta + rot on the bottom.

The check runs the other way. For each row it computes where JLC puts every EasyEDA pad centre, finds the KiCad
copper pad that point lands in, and compares nets. Comparing nets rather than pad numbers is what catches a SOT-23-6
turned 180 degrees: every pad lands on copper, but on the wrong pins.

**The bottom-side caveat.** JLC does not document how it reads a bottom-side rotation. The reading jlcplace uses,
`jlc-2022` (180 - theta + rot, viewed from below), is the one every open tool uses. It also matches the JLC
preview data that the z2amiller fork of jlccheck recorded. Nothing offline can prove which reading the factory uses,
so every bottom row is also placed under the three other possible readings. Parts whose pins would move under any of
them go on the preview checklist. `--reading` selects another reading if JLC ever changes.

## The correction database

`--db DIR` is a directory with one JSON file per LCSC code, made to be committed. Each entry is keyed by a hash of the
EasyEDA footprint's pads and a hash of the KiCad pads and their pin functions, so one code on two KiCad footprints,
or on one footprint with other pin names (a diode with pad 1 the cathode, and one with pad 1 the anode), gets two
entries: one part's correction or verification never stands for another's. Entries hold derived numbers: rot, dx,
dy, the fit's verdict and reasons, and the verification history, plus the EasyEDA footprint's title (its package
name, e.g. `SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR`) for people reading the file. They never hold pad geometry.

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
cache the records too.

```yaml
# .github/workflows/cpl.yml
name: CPL
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install git+https://github.com/tyevco/jlcplace
      - uses: actions/cache@v4               # optional: EasyEDA records for a full simulation
        with: {path: ~/.cache/jlcplace, key: easyeda-${{ hashFiles('jlcplace-db/*.json') }}, restore-keys: easyeda-}
      # no --report: the HTML report draws EasyEDA pad outlines; never upload it (or the record cache) as an
      # artifact of a public repository. The JUnit and JSON outputs hold derived data only.
      - run: >
          jlcplace check hardware/board.kicad_pcb fab/board-CPL.csv --bom fab/board-BOM.csv
          --db jlcplace-db --github-annotations --junit cpl.xml
```

Without KiCad on the runner, the board is read with the S-expression reader. Pass `--fail-on yellow` to make yellow
fail the job as well. To fill the record cache, run `jlcplace fetch hardware/board.kicad_pcb` (on a schedule, say)
before the check; nothing else in the job talks to EasyEDA.

## Limits

* Only pad centres are placed. A row slightly off but still with every centre on its own pad passes; a known
  correction adds a message when the row is more than 0.05 mm or 0.5 degrees from the row jlcplace would write, and
  makes it yellow ("offset") beyond 0.15 mm and a quarter of the smallest pad it lands on, or 2 degrees.
* Polarity: where EasyEDA's pin names and pad numbers disagree, the "+" drawn on its footprint decides (it was right
  in all 6 such cases of JLC's preview data; the names alone were wrong once, D12 C2836396). The "+" is read from
  two crossing strokes on the silkscreen or document layer; KiCad's positive pad from the pin functions, else KiCad's
  library convention (diode pad 1 = cathode, polarised capacitor pad 1 = +). Footprints outside KiCad's libraries
  may follow another convention: give their pins functions.
* The bottom-side reading is a well-supported hypothesis, not a documented fact; see the caveat above.
* EasyEDA's rotation sense is proven from the records' corner order at multiples of 90 degrees. At other angles,
  RECT and OVAL pads are checked against their own outlines.
* Symbol pin names are used where EasyEDA has them. Multi-unit symbols are parsed through every unit, a unit's
  names before the main symbol's; on three real records (two 324-ball BGAs, a 3-unit module) they name exactly
  the footprint's pads.
* A part EasyEDA has no record for is not simulated: yellow for a two-pad non-polar part, red otherwise, unless a
  stored correction exists.
* The EasyEDA API is not a documented, stable interface. Only `fetch` and `--online` use it. jlcplace waits at least
  0.7 s between requests, backs off on errors (honouring Retry-After up to 5 minutes), and stops using it for the
  run after 5 failures in a row or once its retry waits add up to 10 minutes.
* KiCad 10 variant overrides are ignored, and pcbnew from KiCad 6 to 8 is untested; the S-expression reader covers
  those files.

## Data and licensing

jlcplace is MIT licensed (see `LICENSE`).

EasyEDA footprints and symbols come from the **JLCEDA/EasyEDA Official Library** (https://lceda.cn/,
https://easyeda.com). The copyright and related rights in that library belong to JLCEDA/EasyEDA, and its records
carry that copyright and source notice. jlcplace downloads records only into your own cache directory
(`$JLCPLACE_CACHE`, else `$XDG_CACHE_HOME/jlcplace` or `~/.cache/jlcplace`), keeps them verbatim (notice included),
and never redistributes them. The JSON, JUnit, checklist and annotation outputs hold derived numbers, pad numbers and
hashes; the correction database holds those and the EasyEDA footprint's title (a package name such as
`SOIC-8_L5.3-W5.3-P1.27-LS8.0-BL`), and the JSON report and `fit --json` name it too.

The HTML report draws EasyEDA pad outlines. It declares their source prominently, with both links, above the board
views and the part cards and again in its footer, and asks search engines not to index it. It is meant for your own
review: do not publish it, for example as a CI artifact of a public repository. This repository contains no EasyEDA
data: its tests build their EasyEDA records by hand, or read a local cache when one is present.
