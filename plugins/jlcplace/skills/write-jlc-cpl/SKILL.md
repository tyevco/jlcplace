---
name: write-jlc-cpl
description: Write a corrected JLCPCB pick-and-place (CPL) file for a KiCad board with jlcplace cpl, which fits each part's EasyEDA rotation and origin and refuses to keep red rows, and record numbering or polarity decisions with jlcplace confirm. Use when the user wants a JLCPCB CPL written or its rotations and offsets fixed; for the whole order, use prepare-jlc-order.
argument-hint: "[board.kicad_pcb] [output-CPL.csv]"
allowed-tools: Bash(jlcplace cpl *) Bash(jlcplace check *) Bash(jlcplace show *) Bash(jlcplace --version) Read Glob
---

# Write a corrected CPL

`jlcplace cpl` writes the CPL that JLC needs. Each row puts JLC's EasyEDA footprint exactly on the KiCad footprint.
The correction for each part comes from the correction database (`--db`), or is fitted from the EasyEDA record.
jlcplace then checks the file it wrote. A KiCad position file is not this file: it carries KiCad's angles and anchors,
and many EasyEDA footprints (SOT-23 variants, connectors, LGA sensors) use other ones.

## Steps

1. Run `jlcplace --version`. If the command is missing, follow [setup-jlcplace](../setup-jlcplace/SKILL.md) first.
2. Find the board (`*.kicad_pcb`), the JLC BOM (`*BOM*.csv`) and the Gerbers (a zip or directory) if there are,
   and the project's correction database (a directory of `C<digits>.json` files, often `jlcplace-db/`). Pass the BOM
   whenever it exists: JLC assembles from the BOM's LCSC codes, and `cpl --bom` uses those codes. Pass the Gerbers
   the CPL will be ordered with whenever they exist: JLC reads the rows from their (0, 0).
3. Make sure the EasyEDA records are cached. Without them, every multi-pin or polar part is red ("no EasyEDA record
   in the cache") and nothing is written. If the user agrees to the download (it contacts the EasyEDA API,
   rate-limited, once), run the command below. Never add `--online` to `cpl` or `check` instead:

   ```sh
   jlcplace fetch board.kicad_pcb --bom board-BOM.csv
   ```

4. Write the CPL:

   ```sh
   jlcplace cpl board.kicad_pcb -o board-CPL.csv --bom board-BOM.csv --gerbers board-gerbers.zip --db jlcplace-db --checklist checklist.md
   ```

   Leave out `--bom` or `--gerbers` when there is none. Always pass the project's database (`jlcplace-db`, or the project's own
   path), even before it exists: jlcplace then only warns and uses no stored corrections, and `jlcplace confirm`
   creates it with the first decision, so every later `cpl` and `check` uses it. Do not pass `-o -`: the CPL must be
   a file (exit 3).
5. Read the result:
   * Exit 0 and `jlcplace: wrote board-CPL.csv (N rows)`: done. Yellow rows are written, and they need a look in
     JLC's preview.
   * Exit 2 and `jlcplace: board-CPL.csv not written: the rows above are red (--force writes it anyway)`: nothing was
     written. If a CPL was already at that path, it is left unchanged. Resolve each red row as described in the next
     section, then run the command again.
   * Exit 3: bad input. The message names the file or option.

## Resolving red rows

Read the message, and for detail `jlcplace show board.kicad_pcb --ref REF`. The common cases:

* **The numbering needs a decision**: `renumbered: ... it is red, so nothing vouches for this numbering`, or a
  correction reason `numbering conflict: EE 2 -> KiCad 1 ... check the pinout`. EasyEDA and KiCad number the pads
  differently, and the records cannot say which EasyEDA pins are joined inside the part. Example: the TS-1187A
  switch is numbered 1-4 in EasyEDA and 1,1,2,2 in KiCad. Work it out from the LCSC part's datasheet:
  * Find which terminals are joined inside the part, by position, in the datasheet's PCB layout or schematic (the
    TS-1187A letters them: A-B and C-D joined, the pairs 6 mm apart along the long side).
  * Find the KiCad pads' positions: `jlcplace show` gives only numbers and nets, so read the footprint's pads from
    the board file, or look at the part's card in `jlcplace check ... --report report.html` (KiCad pads numbered,
    each EasyEDA pad's landing on hover; local only, never publish it).
  * Check that the KiCad pads at the positions of each joined pair share one number and net. The `landings` in
    `show` (EasyEDA pad, KiCad pad, net) then say which EasyEDA pad sits on which of them, and whether every one
    lands. Alternatives listed as `equivalent=true` in `show` land the same way.
  * Getting the datasheet: lcsc.com, search the code. The PDF link can be a page that redirects to the real file, and
    many datasheets are drawings with no text layer, so render the page and look at it.

  Tell the user what you found, in the datasheet's own terms. Ask the user to decide. Record the decision only when
  the user agrees, under their name:

  ```sh
  jlcplace confirm board.kicad_pcb --ref SW1 --db jlcplace-db --by NAME --source manual --note "A-B and C-D joined, the 6 mm pairs (datasheet)"
  ```

  Then run `jlcplace cpl` again with the same `--db`. The row is now yellow ("numbering accepted by the correction
  (manual)") and it is written. It stays on the preview checklist.
* **The polarity needs a decision**: a diode, LED, or tantalum or electrolytic capacitor whose EasyEDA pin names and
  pad numbers disagree, with no "+" drawn on the EasyEDA footprint to settle it. Find the cathode or "+" pad on the
  EasyEDA footprint, from the part's datasheet or JLC's preview. Find it on the KiCad footprint too: by KiCad's
  library convention, a diode or LED's pad 1 is the cathode and a polarised capacitor's pad 1 is "+". Record the
  decision as above, or verify the part in JLC's preview ([review-jlc-preview](../review-jlc-preview/SKILL.md)).
* **ambiguous**: several placements land every pad, and nothing tells them apart. Decide from the datasheet, or in the
  preview, as above.
* **Mirrored**: `the mirrored fit beats every normal one: mirrored footprint or wrong footprint`, or `no row is
  possible: the EasyEDA footprint is a mirror image of the KiCad one`. No row can fix this. The LCSC part does not
  match the KiCad footprint. Pick another part, or fix the footprint.
* **No EasyEDA footprint** for a polar or multi-pin part (`Component not found`): check the code on lcsc.com. If the
  code is right, the part can only be checked in JLC's preview.
* **Not on the board, DNP, wrong layer, duplicate**: fix the board or the BOM. See the
  [verdict reference](../check-jlc-placement/verdicts.md).

Use `--force` only when the user explicitly accepts the red rows, for example because they will fix them in JLC's
preview. The exit code is still 2, and each such row must be on the user's preview to-do list.

## What the CPL contains

* JLC's header `Designator,Mid X,Mid Y,Layer,Rotation`, values in mm, Top or Bottom, CRLF line ends.
* One row for every part JLC assembles that has an LCSC code: placed, not DNP, not excluded from the position files
  or the BOM. With `--bom`, the BOM's code is used. Parts with no code get no row, so JLC does not place them.
* Coordinates in mm, Y up, from the Gerbers' own (0, 0) with `--gerbers` (the first line says `origin 100, 100
  from the Gerbers (aux)`): JLC lays the CPL over the Gerbers from there. Gerbers that are not this board's as it is
  (mirrored, another board's, an older plot) are a red `gerbers` line, and nothing is written. Without `--gerbers`,
  from the board's drill/place (aux) origin, the default `--origin aux` (the page origin (0, 0) when the board has
  none): the Gerbers and the drill files must then be plotted from that origin (see
  [prepare-jlc-order](../prepare-jlc-order/SKILL.md)), which the summary reminds of. `--origin grid`, `absolute` or
  `X,Y` names another origin; with `--gerbers`, an `--origin` other than the Gerbers' is red.
* Bottom-side rotations use the `jlc-2022` reading (180 - theta + rot, seen from below), which every open tool uses.
  JLC does not document it. The checklist's "bottom reading" items are how the preview proves it.

## The correction database

`--db DIR` is a directory of one JSON file per LCSC code. Commit it with the board: it holds derived numbers and
hashes, no EasyEDA geometry. `jlcplace fit board.kicad_pcb --db jlcplace-db` stores the fitted corrections.
`jlcplace confirm` records decisions and preview or board verifications. Later runs of `cpl` and `check` use them,
strongest first: board-verified, preview-verified, manual, auto. When EasyEDA changes a footprint, its entries go
stale, and the part is refitted.

## Then

Upload this CPL together with the BOM it was written with. Walk the checklist in JLC's preview with
[review-jlc-preview](../review-jlc-preview/SKILL.md). For the whole order (Gerbers, drill, BOM), see
[prepare-jlc-order](../prepare-jlc-order/SKILL.md).
