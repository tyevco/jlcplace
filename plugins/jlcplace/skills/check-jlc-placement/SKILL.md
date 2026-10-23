---
name: check-jlc-placement
description: Check a JLCPCB pick-and-place file (CPL, centroid, XY or position file, from KiCad or any plugin) against its KiCad board with jlcplace check, and explain each red, yellow, green or verified row and its fix. Use when the user asks whether a CPL is right for JLCPCB assembly or wants one checked; to write a corrected one, use write-jlc-cpl.
argument-hint: "[board.kicad_pcb] [cpl.csv]"
allowed-tools: Bash(jlcplace check *) Bash(jlcplace show *) Bash(jlcplace --version) Read Glob
---

# Check a CPL against its board

JLC assembles each part with the footprint of its EasyEDA library, not the KiCad one. `jlcplace check` puts that
EasyEDA footprint down exactly where each CPL row tells JLC to put it. It then reports which KiCad pad, and which net,
each EasyEDA pad lands on. The board is a `.kicad_pcb` file or a `jlcplace snapshot` JSON file. KiCad does not need
to be installed.

## Steps

1. Run `jlcplace --version`. If the command is missing, follow [setup-jlcplace](../setup-jlcplace/SKILL.md) first.
2. Find the inputs. If you find more than one candidate for a file, ask the user which one to use.
   * the board: `*.kicad_pcb`;
   * the CPL: `*CPL*.csv`, `*-pos.csv`, `*.pos` or `*-all-pos.csv`;
   * the JLC BOM, if there is one: `*BOM*.csv`. JLC assembles from the BOM's LCSC column, so pass it whenever it
     exists;
   * the Gerbers ordered with the CPL, if they are at hand: a Gerber zip or directory. JLC lays the CPL over them
     from their own (0, 0), so pass them whenever they exist;
   * the correction database: a directory of `C<digits>.json` files, often `jlcplace-db/`.
3. Run the check. Leave out `--bom` or `--gerbers` when there is none. Pass the project's database path
   (`jlcplace-db` unless it has another) even before it exists: a missing directory only warns.

   ```sh
   jlcplace check board.kicad_pcb board-CPL.csv --bom board-BOM.csv --gerbers board-gerbers.zip --db jlcplace-db --checklist checklist.md
   ```

4. If the output has a line `jlcplace: no EasyEDA record in the cache ...` (right under the first line when the cache
   lacks nearly every record, else after the summary), those parts were not simulated. The line names the
   `jlcplace fetch` command that downloads the records once (it contacts the EasyEDA API, rate-limited, and caches
   them locally); with a BOM, `--bom` fetches the BOM's codes too. Ask before running it, and never add `--online`
   to the check instead:

   ```sh
   jlcplace fetch board.kicad_pcb --bom board-BOM.csv
   ```

   Then run the check again.
5. Read the result: the exit code first, then any red line before the rows (a finding about the whole order), then
   any `jlcplace:` hint lines, then the rows. See the next two sections.
6. Report to the user:
   * every finding, every red row, then every yellow row, by reference, with the message in plain words and the fix;
   * the summary line (`N results: r red, y yellow, g green, v verified`);
   * the preview checklist (written with `--checklist`), which is the user's to-do list for JLC's preview.

## Exit codes

0: no red row (yellow rows still need a look). 1: something yellow, only with `--fail-on yellow`. 2: something red.
3: bad input (a missing or unreadable file, a bad option or value; the message says which). 4: an internal error
(a bug; report it with the traceback at https://github.com/tyevco/jlcplace/issues). 130: interrupted. 141: stdout
was closed by its reader (`| head`), which is not an error.

Exit 2 means the check worked and found a problem. Do not hide red rows: never retry with other options until they
go away, delete rows, or edit a row's numbers by hand.

## When most rows are red, the file is wrong, not the parts

jlcplace then says so in one line. Act on it before you look at individual rows:

* With `--gerbers`, a red line before the rows, `origin mismatch: JLC lays the CPL over the Gerbers from their (0,
  0), ... but the CPL was written from ...`: the CPL and the Gerbers use different origins, and the rows are red
  because of it. Its `fix:` names the ways out: plot the Gerbers from the CPL's origin, write the CPL from the
  Gerbers' (`jlcplace cpl BOARD --gerbers GERBERS`), or shift every row. A red `gerbers` line: the Gerbers are not
  this board's as it is (mirrored, in the wrong unit, another board's or an older plot, a side or the outline
  missing or from another origin, two plots in one set). Re-plot them: see
  [prepare-jlc-order](../prepare-jlc-order/SKILL.md).
* Without `--gerbers`, `jlcplace: origin mismatch: N of M rows would sit on their parts if shifted by (...) mm: the
  CPL was written from the page origin (0, 0), not the board's aux origin ...`. The position file was exported
  without "use drill/place file origin". Which origin JLC uses depends on the Gerbers: pass them with `--gerbers`.
  The `--origin` it names shows per-part results, and is right only if the Gerbers use that origin too.
* `... if the CPL's numbers were in inches: JLC reads a CPL's numbers in mm: write it again in mm`. The position file
  is in inches, which is kicad-cli's default. Re-export it in mm, or write the CPL with
  [write-jlc-cpl](../write-jlc-cpl/SKILL.md).
* `... with Mid Y negated`: the CPL was written from KiCad's Y-down coordinates. Write it with write-jlc-cpl.
* `N of M rows are displaced alike from where they belong, by (...) mm (...), while K sit on their parts`: not an
  origin error. Those rows (often one footprint's) carry an offset of their writer's: read them one by one.
* `N of M rows have no LCSC code on the board: pass --bom BOM.csv ... or --lcsc-field NAME`. The codes are only in
  the BOM, or in a footprint field with an unusual name. Pass the BOM, or name the field.
* Every row red and far off, with no line about it: hand over to
  [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md).

For an origin, unit or Y error, `--fixed-cpl fixed-CPL.csv` writes the CPL's own rows with it taken out and prints
how that file checks. Without `--gerbers` its note says the moved rows are right only for Gerbers plotted from the
board's origin. A CPL from `jlcplace cpl` is better still: the fixed file keeps its writer's angles.

## Verdicts

| verdict | meaning | what to do |
|---|---|---|
| red | An EasyEDA pad lands off copper or on another pin's net, or the row is unusable (no row for an assembled part, a duplicate row, a part that is DNP or not on the board, the wrong layer, a polarity or numbering nobody has decided). | Fix before ordering. |
| yellow | Probably right, but look at it: no EasyEDA footprint for a two-pad non-polar part, a row off a known correction ("offset"), a fit unsure of its choice (polarity decided by pad numbers only), bare KiCad pads, a manual decision, a BOM inconsistency. | Read the message. Check the part in JLC's preview. |
| green | Every EasyEDA pad lands on its own pin. Harmless differences are allowed: a resistor turned round, pads of the same net. | Nothing. |
| verified | Green, and the row matches a correction verified in JLC's preview or on an assembled board. | Nothing. |

The [verdict reference](verdicts.md) lists every status and message, what each one means and how to fix it. Read it
when a message is not obvious.

To see one part in detail (its EasyEDA footprint, the fitted correction, the rows each bottom reading gives, and where
every pad lands), run:

```sh
jlcplace show board.kicad_pcb --ref U3
```

Its `x:` and `y:` are KiCad's board coordinates (from the page origin, Y down). Its `rows:`, like a CPL and the
check's `row` and `expected`, are CPL coordinates (from the drill/place origin, Y up): KiCad's (x, y) is
(x - aux_x, aux_y - y) there, with the origin from the check's first line (`origin aux 100, 100`, or
`origin 100, 100 from the Gerbers (aux)`).

## The bottom side is never proven offline

JLC does not document how it reads the rotation of a bottom-side part. A green bottom row is right under the reading
that every open tool uses, `jlc-2022`. jlcplace also tries the three other possible readings. It lists the bottom
parts whose pins would move under any of them as "bottom reading" items of the preview checklist, with one part to
look at first. Say so whenever the board has bottom parts: JLC's placement preview stays the final check. Walk it with
[review-jlc-preview](../review-jlc-preview/SKILL.md).

## Outputs

* `--checklist checklist.md` (or `.csv`): what only JLC's preview can settle, in order.
* `--json results.json`, `--junit cpl.xml`, `--github-annotations`: for CI and scripts. They hold derived data only.
* `--report report.html`: one self-contained page with top and bottom views and a card for every part.
* `--overlay overlay/`: `top.svg` and `bottom.svg` (and PNGs when a renderer is at hand), each side as JLC's preview
  shows it, with the EasyEDA pads where the rows put them and the red and yellow parts circled.
* The report and the overlay images draw EasyEDA pad outlines, so they are for the user's own review only. Never
  commit them, publish them, or upload them as CI artifacts.
* `-q`: no terminal summary. `--fail-on yellow`: exit 1 on yellow too, for CI.
* In CI, jlcplace's GitHub Action (`uses: tyevco/jlcplace@<commit SHA>` with `board`, `cpl`, and `bom`, `gerbers`,
  `db` when the project has them) installs jlcplace, fetches the EasyEDA records into the Actions cache and runs the
  check, with annotations and the checklist in the job summary; it uploads nothing. Commit the database
  (`jlcplace fit board.kicad_pcb --db jlcplace-db`) so that the job does not depend on EasyEDA.

## Next

* Red rows in another tool's CPL: write a corrected one with [write-jlc-cpl](../write-jlc-cpl/SKILL.md).
* A preview or an assembled order showed parts wrong: [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md).
* Before paying: [review-jlc-preview](../review-jlc-preview/SKILL.md).
