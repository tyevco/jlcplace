---
name: diagnose-jlc-misplacement
description: Explain why parts on a JLCPCB-assembled board or in its placement preview came out shifted, rotated, reversed or on the wrong pins (origin, units, Y axis, EasyEDA or CPL-writer offsets, bottom side, polarity, pinout), using jlcplace check on the uploaded files, and say what to upload next time. Use when a JLC order or preview showed parts wrong.
argument-hint: "[board.kicad_pcb] [uploaded-CPL.csv]"
allowed-tools: Bash(jlcplace check *) Bash(jlcplace show *) Bash(jlcplace cpl *) Bash(jlcplace --version) Read Glob
---

# Diagnose a misplaced JLC order

## Steps

1. Run `jlcplace --version`. If it is missing, follow [setup-jlcplace](../setup-jlcplace/SKILL.md).
2. Collect exactly what was ordered: the board revision the Gerbers came from (`*.kicad_pcb`), the CPL and the BOM
   **as uploaded** (not regenerated), and the Gerber zip if it is at hand. Ask the user:
   * which parts looked wrong and how (beside the pads, turned, reversed, missing, or the board does not work), on
     which side, and by roughly how much. A photo or a description of the preview helps;
   * which tool or script wrote the CPL, and whether it applies corrections of its own: a plugin's rotation or offset
     table, a project script, order notes next to the CPL that mention corrections;
   * for an assembled board, whether the bottom-side parts came out right, starting with the part the checklist marks
     `look at this one first` (see "The bottom side on an assembled board" below).
3. Check the uploaded files, the Gerber zip among them when it is at hand (leave out `--gerbers` otherwise): JLC
   laid the CPL over those Gerbers from their (0, 0), and so does the check. Never add `--online`. If the check says
   records are missing, ask before running the `jlcplace fetch` it names. Pass the project's database
   (`--db jlcplace-db`, or its own path) even if it does not exist yet: a missing directory only warns.

   ```sh
   jlcplace check board.kicad_pcb uploaded-CPL.csv --bom uploaded-BOM.csv --gerbers uploaded-gerbers.zip --db jlcplace-db --checklist checklist.md --json results.json --overlay overlay
   ```

   The terminal shows only the first message of each row. For each red or yellow row, read every message in
   `results.json`, and its `row` (as uploaded) and `expected` (the row jlcplace writes), or run
   `jlcplace show board.kicad_pcb --ref J1`. Quote the distances to the user (`differs from the row jlcplace writes
   (...): 3.399 mm and 0 deg away`), and any pads that land on other pins' nets (`11->2 (+13V_OLED, not GND)`).
   `overlay/top.svg` and `overlay/bottom.svg` (PNGs too when a renderer is at hand) show each side as JLC's preview
   does, with the EasyEDA pads where the uploaded rows put them and the problem parts circled: put them beside the
   preview or the photos. `--report report.html` draws every part on its own card. Both draw EasyEDA pad outlines:
   for local viewing only, never publish them.
4. Match each reported symptom to a cause below, using jlcplace's result for that part and the pattern across parts.
   Per part, say the cause, the evidence, and the fix.
5. Write what to upload next time:
   * a CPL from `jlcplace cpl` that checks clean, with the same `--db` ([write-jlc-cpl](../write-jlc-cpl/SKILL.md)),
     written with `--gerbers uploaded-gerbers.zip` when the zip is at hand: its rows are then written from the
     zip's (0, 0) and checked against it. No red `origin` or `gerbers` line means the Gerbers, the drill files and
     the BOM can be uploaded again as they are, and only the CPL changes. A red `gerbers` line means plotting them
     again, as [prepare-jlc-order](../prepare-jlc-order/SKILL.md) says:

     ```sh
     jlcplace cpl board.kicad_pcb -o board-CPL.csv --bom uploaded-BOM.csv --gerbers uploaded-gerbers.zip --db jlcplace-db --checklist checklist.md
     ```

   * for the next preview, the checklist that `jlcplace cpl --checklist` writes for the new CPL, not the one made
     from the uploaded CPL ([review-jlc-preview](../review-jlc-preview/SKILL.md)).
6. With the user's agreement, record what the assembled board proved: the parts that came out **right**, with
   `jlcplace confirm ... --source board --cpl uploaded-CPL.csv` and no `--failed` ([review-jlc-preview](../review-jlc-preview/SKILL.md)).
   A part whose uploaded row jlcplace flags red needs no failure record, because jlcplace's correction already
   differs from that row. `confirm --failed` refuses it with exit 3 (`the row in ... is not the stored correction's
   ...: its failure says nothing about that correction; nothing recorded`), and then records none of the `--ref`s
   given with it. A part that came out wrong although its row is green: first see the bottom side and the pinout
   sections below. If neither explains it, record it with `--failed` (as under "Reversed"): its row is then red
   (`verified-wrong`) until the correction is fixed.

## Shifted: the part sits beside its pads

| pattern | cause | jlcplace shows | fix |
|---|---|---|---|
| Every part off by the same vector | The CPL and the Gerbers use different origins (drill/place, grid or page origin). | With `--gerbers`, a red line before the rows: `origin mismatch: JLC lays the CPL over the Gerbers from their (0, 0), board (0, 0) (the page origin), but the CPL was written from the aux (drill/place file) origin (100, 100) (184 of 184 rows): JLC would put every part 141.42 mm off ...; fix: ...`. Without the Gerbers the CPL may check clean, because it matches the board's own origin while the Gerbers do not. | The finding's `fix:`: write the CPL from the Gerbers' origin (`jlcplace cpl --gerbers`), or re-export everything from the drill/place origin. |
| Every row red, and a hint `origin mismatch: N of M rows would sit on their parts if shifted by ...` | A CPL from another origin than the board's drill/place origin (no Gerbers given). | `the CPL was written from the page origin (0, 0), not the board's aux origin ...` | Check again with `--gerbers`, which tells which origin JLC used, then as above. |
| Parts bunched near one corner | The CPL's numbers are in inches. | `... if the CPL's numbers were in inches: JLC reads a CPL's numbers in mm` | Write the CPL in mm (`jlcplace cpl`). |
| Parts at the right X but mirrored up and down | The Y sign is flipped: a CPL made from KiCad's Y-down coordinates. | `... with Mid Y negated` | Write the CPL with `jlcplace cpl`. |
| Only bottom parts, mirrored left and right | X negated for bottom parts (KiCad's "negative X coordinates for bottom", `--bottom-negate-x`). | Bottom rows red, top rows green. | Write the CPL with `jlcplace cpl`. |
| A few parts (often connectors, switches, modules) off by anything from about 1 mm to over 100 mm | The CPL lacks the EasyEDA footprint's origin offset, or the tool that wrote it applied a wrong offset of its own. Tell them apart as below. | `N of M EasyEDA pads land off copper ... (nearest KiCad pad centre up to D mm away)`, and in `results.json` `differs from the row jlcplace writes (...): D mm ...` | Write the CPL with `jlcplace cpl`, with a numbering decision if it asks for one. If the writer applied its own offset, tell the user where, so it is not used again. |

**Which offset was wrong.** For each such part, compare three points, all in CPL coordinates (mm from the CPL
origin, Y up):

* U, the uploaded row: `row` in `results.json`, or the CPL;
* K, KiCad's anchor. `jlcplace show` prints the part's `x:` and `y:` in KiCad's board coordinates (from the page
  origin, Y down). In CPL coordinates that is (x - ox, oy - y), with the origin (ox, oy) from the check's first line
  (`origin aux 100, 100`, or `origin 100, 100 from the Gerbers (aux)`). The `rows:` that `show` prints are already
  CPL coordinates from the board's aux origin;
* J, jlcplace's row: `expected` in `results.json`, or `show`'s row for the reading in use (`jlc-2022`).

If U is on K, and J is off K by about the distance jlcplace reports, the CPL lacks the EasyEDA origin offset: the
tool that wrote it used KiCad's anchor. If U is far from K while J is close to it, or U is far from both, the tool
that wrote the CPL
applied a wrong offset of its own (a plugin's rotation and offset table, a project script, a correction file). A
fit keyed by pad numbers that breaks on repeated numbers is the usual reason: two `MP` pads, a USB-C shell's `S1`
pads, or a switch numbered 1,1,2,2. Do not make the user's offset table bigger. Drop it for these parts and use
`jlcplace cpl`. Example: an uploaded J1 at (15.2425, -12.7252), KiCad's anchor at (16.8875, -9.75) and jlcplace's
row at (16.9416, -9.7815). The row is 3.40 mm from the anchor while jlcplace's correction is 0.06 mm, so the
project's own CPL writer caused the error.

With the Gerbers, the `origin mismatch` finding settles the first rows of the table: it names the origin the
Gerbers were plotted from and the one the CPL was written from. `--fixed-cpl fixed-CPL.csv` on the check writes the
uploaded rows shifted onto the Gerbers' origin and says how that file checks; a CPL from `jlcplace cpl --gerbers` is
better, as it fixes the parts' angles and offsets too.

## Turned: on its pads, at the wrong angle

* **Top-side parts of one package family turned by 90 or 180 degrees** (SOT-23, SOT-23-5/6, SOT-89, QFN, LGA
  sensors, connectors). EasyEDA draws the package at another zero angle than KiCad, and the CPL carried KiCad's
  angle. jlcplace shows `wrong-pad: EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 ...` or pads
  off copper. Fix: write the CPL with `jlcplace cpl`.
* **Only bottom-side parts turned, while jlcplace says green.** JLC read the bottom rotation differently from the
  `jlc-2022` reading jlcplace assumes. JLC does not document it, and nothing offline proves it. The checklist's
  "bottom reading" items say, for each bottom part, what each other reading would do (`as jlc-2022+180, pin 1 would
  sit on pad 4`). Compare those predictions with what the preview or the board showed for the part marked "look at
  this one first". `jlcplace show board.kicad_pcb --ref U10` prints the row under every reading. If one reading
  explains the bottom parts, confirm it: `jlcplace check board.kicad_pcb uploaded-CPL.csv --reading jlc-2022+180`
  (with that reading's name) should show red exactly the bottom parts that came out wrong. Then write the next CPL
  with `jlcplace cpl` and the same `--reading`. Report it at https://github.com/tyevco/jlcplace/issues, because it
  would change the default for everyone.
* **Parts at odd angles** (a ring of LEDs) off square: check the row's angle against the board. Angles that are not
  multiples of 90 are allowed. The checklist names one part per code to look at.

## The bottom side on an assembled board

An assembled board is evidence for the bottom reading too. If the checklist's `look at this one first` part (its row
green) came out right on the board, JLC read the bottom side as `jlc-2022`, the reading jlcplace writes, and the
other green bottom parts follow. Record that part with `--source board` (step 6): the readings under which it would
have moved are then ruled out for every part, and the next checklist has no `bottom reading` item for the parts that
only those readings moved (the others say which readings it ruled out). Tell the user what it settles.

## Reversed: a two-terminal part turned 180 degrees

A diode, LED, polarised capacitor or trimmer capacitor (its rotor), or a jumper or switch on a right-angle
`_Horizontal` footprint (its body), must not be turned round. KiCad's libraries put a diode or LED's cathode on pad 1
and a polarised capacitor's "+" on pad 1. Some EasyEDA footprints number them the other way. jlcplace compares the
EasyEDA pin names, the "+" drawn on the EasyEDA footprint, EasyEDA's unplated holes (a peg) and the KiCad pin
functions and holes:

* `polarity reversed: the row puts the part the other way round than the correction does ...` (red, `reversed`): the
  uploaded row is half a turn from jlcplace's. Write the CPL with `jlcplace cpl`. If the part came out right as
  uploaded, the correction is wrong: record the uploaded row (`--source board --cpl uploaded-CPL.csv`, step 6), which
  replaces it.
* `polarity decided by pad numbers only ... check the polarity mark in JLC's preview` (yellow), `polarity evidence
  conflicts: ...` (yellow), or `the polarity needs a decision` (red): nothing in the records settles it. If the preview
  showed it reversed, record it ([review-jlc-preview](../review-jlc-preview/SKILL.md): the uploaded CPL, and the two
  space presses that turned it round, `--space 2`). `jlcplace cpl` then writes the turned row.
* jlcplace says green, but the assembled part is reversed. With the user's agreement, record that:

  ```sh
  jlcplace confirm board.kicad_pcb --ref D3 --db jlcplace-db --by NAME --source board --cpl uploaded-CPL.csv --failed --note "reversed on the board"
  ```

  Its row is then red (`verified-wrong`), and `jlcplace cpl --db` refuses it until the correction is fixed: typed in
  (`--source manual --rot ...`, the user's decision), or the two space presses that turn it round in the next
  preview (of a CPL written with `--force`). If the real part's pin 1 is not the pin KiCad's footprint calls 1, that
  is a pinout problem: see below.

## Wrong pins: placed as JLC's footprint says, but the circuit is wrong

The LCSC part's pinout differs from the KiCad symbol and footprint. For example, a SOT-23 MOSFET has G-S-D where the
symbol expects G-D-S, or a regulator orders its pins differently. No CPL can fix this. The part must change, or the
symbol's pin mapping and the board must. The primary evidence is the check's: it compares the LCSC part's pin names,
through the row, with the symbol's pin functions on the pads they land on. A red `pinout` row (`pinout differs from
the symbol: pad 2 is S on the LCSC part, D on the board; ...`: no placement wires it right) or a yellow `N pin names
differ from the symbol: ...` names the pins; the checklist lists such parts first, under `pinout`. It needs pin
functions on the board's pads (KiCad fills them with "Update PCB from Schematic"); without them nothing is compared.
More evidence: a correction reason such as `pin names pick rot ...` or `numbering conflict: ... check the pinout`, and
the `landings` in `jlcplace show board.kicad_pcb --ref Q1` (EasyEDA pad, KiCad pad, net). Compare them with the
part's datasheet (lcsc.com, search the code). A preview verification does not settle a pinout (the preview shows
pads, not pin functions); a board that works does (`confirm --source board`).

## Missing parts

`no-row` (red: the CPL has no row for a part JLC places, such as a part the BOM lists that the board excludes from
its position files, which KiCad's position file then leaves out), a BOM line without an LCSC code, or a part JLC had
out of stock (JLC's order page says so). Add the row (`jlcplace cpl --bom` writes one for every part the BOM lists)
or the code, or choose another part. Then run [prepare-jlc-order](../prepare-jlc-order/SKILL.md)
steps 5 and 6 again.

The [verdict reference](../check-jlc-placement/verdicts.md) explains every status and message.
