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
   * the correction database: a directory of `C<digits>.json` files, often `jlcplace-db/`.
3. Run the check. Leave out `--bom` when there is no BOM. Pass the project's database path (`jlcplace-db` unless it
   has another) even before it exists: a missing directory only warns.

   ```sh
   jlcplace check board.kicad_pcb board-CPL.csv --bom board-BOM.csv --db jlcplace-db --checklist checklist.md
   ```

4. If the output has a line `jlcplace: no EasyEDA record in the cache ... for N codes`, those parts were not
   simulated. Tell the user that `jlcplace fetch BOARD` downloads the records once (it contacts the EasyEDA API,
   rate-limited, and caches them locally). Ask before running it, and never add `--online` to the check instead:

   ```sh
   jlcplace fetch board.kicad_pcb
   ```

   If the BOM names codes the board does not have, add them: `jlcplace fetch board.kicad_pcb C7519`. Then run the
   check again.
5. Read the result: the exit code first, then any `jlcplace:` hint lines, then the rows. See the next two sections.
6. Report to the user:
   * every red row, then every yellow row, by reference, with the message in plain words and the fix;
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

jlcplace then prints a hint. Act on it before you look at individual rows:

* `would sit on their parts if the CPL's numbers were in inches: pass --units in`. The position file is in inches,
  which is kicad-cli's default. JLC's CPL is in mm. Re-export the file in mm, or write the CPL with
  [write-jlc-cpl](../write-jlc-cpl/SKILL.md).
* `would sit on their parts if the CPL were relative to the page origin ... pass --origin absolute`. The position file
  was exported without "use drill/place file origin". Pass `--origin absolute` to see per-part results. The upload
  is only right if the Gerbers and the drill file use the page origin too. Re-exporting everything from the
  drill/place origin is safer: see [prepare-jlc-order](../prepare-jlc-order/SKILL.md).
* `N of M rows have no LCSC code on the board: pass --bom BOM.csv ... or --lcsc-field NAME`. The codes are only in
  the BOM, or in a footprint field with an unusual name. Pass the BOM, or name the field.
* Every row red and far off, with no hint: the Y sign is flipped, or the file uses some other origin. Hand over to
  [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md).

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
(x - aux_x, aux_y - y) there, with the aux origin from the check's first line (`origin aux 100, 100`).

## The bottom side is never proven offline

JLC does not document how it reads the rotation of a bottom-side part. A green bottom row is right under the reading
that every open tool uses, `jlc-2022`. jlcplace also tries the three other possible readings. It lists the bottom
parts whose pins would move under any of them as "bottom reading" items of the preview checklist, with one part to
look at first. Say so whenever the board has bottom parts: JLC's placement preview stays the final check. Walk it with
[review-jlc-preview](../review-jlc-preview/SKILL.md).

## Outputs

* `--checklist checklist.md` (or `.csv`): what only JLC's preview can settle, in order.
* `--json results.json`, `--junit cpl.xml`, `--github-annotations`: for CI and scripts. They hold derived data only.
* `--report report.html`: one self-contained page with top and bottom views and a card for every part. It draws
  EasyEDA pad outlines, so it is for the user's own review only. Never commit it, publish it, or upload it as a CI
  artifact.
* `-q`: no terminal summary. `--fail-on yellow`: exit 1 on yellow too, for CI.

## Next

* Red rows in another tool's CPL: write a corrected one with [write-jlc-cpl](../write-jlc-cpl/SKILL.md).
* A preview or an assembled order showed parts wrong: [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md).
* Before paying: [review-jlc-preview](../review-jlc-preview/SKILL.md).
