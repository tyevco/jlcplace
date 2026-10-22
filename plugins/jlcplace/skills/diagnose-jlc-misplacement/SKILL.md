---
name: diagnose-jlc-misplacement
description: Diagnose why parts in a JLCPCB assembly or its placement preview are shifted, rotated, reversed or on the wrong pins (origin, units, Y axis, EasyEDA angles, bottom side, polarity, pinout) with jlcplace check on the uploaded files, and say what to upload next time. Use when a JLC order or preview shows misplaced parts.
argument-hint: "[board.kicad_pcb] [uploaded-CPL.csv]"
allowed-tools: Bash(jlcplace check *) Bash(jlcplace show *) Bash(jlcplace --version) Read Glob
---

# Diagnose a misplaced JLC order

## Steps

1. Run `jlcplace --version`. If it is missing, follow [setup-jlcplace](../setup-jlcplace/SKILL.md).
2. Collect exactly what was ordered: the board revision the Gerbers came from (`*.kicad_pcb`), the CPL and the BOM
   **as uploaded** (not regenerated), and the Gerber zip if it is at hand. Ask the user which parts looked wrong and
   how (beside the pads, turned, reversed, missing, or the board does not work), on which side, and by roughly how
   much. A photo or a description of the preview helps.
3. Check the uploaded files. Ask before downloading EasyEDA records with `jlcplace fetch` if the check says it lacks
   them.

   ```sh
   jlcplace check board.kicad_pcb uploaded-CPL.csv --bom uploaded-BOM.csv --checklist checklist.md
   ```

   Add `--db jlcplace-db` if the project keeps a correction database. `--report report.html` draws every part with its
   EasyEDA pads, which helps when comparing with the preview. It is for local viewing only: never publish it.
4. Match each reported symptom to a cause below, using jlcplace's line for that part (red, yellow or green) and the
   pattern across parts. Per part, say the cause, the evidence, and the fix.
5. Write what to upload next time: a CPL from `jlcplace cpl` that checks clean ([write-jlc-cpl](../write-jlc-cpl/SKILL.md)),
   and, for an origin problem, Gerbers and drill files from the same origin ([prepare-jlc-order](../prepare-jlc-order/SKILL.md)).
   Record what the preview or the assembled board proved with [review-jlc-preview](../review-jlc-preview/SKILL.md),
   so that the next CPL is written from verified corrections.

## Shifted: the part sits beside its pads

| pattern | cause | jlcplace shows | fix |
|---|---|---|---|
| Every part off by the same vector | The CPL and the Gerbers use different origins (drill/place, grid or page origin). | The CPL may check clean against the board, because it matches the board's own origin, while the Gerbers do not. | Run the origin check from [prepare-jlc-order](../prepare-jlc-order/SKILL.md) step 6 on the uploaded zip and CPL. Re-export everything from the drill/place origin. |
| Every row red, and a hint `pass --origin absolute` | A position file relative to the page origin, with Gerbers from another origin. | `would sit on their parts if the CPL were relative to the page origin` | As above. |
| Parts bunched near one corner | The CPL's numbers are in inches. | `pass --units in` | Write the CPL in mm (`jlcplace cpl`). |
| Parts at the right X but mirrored up and down | The Y sign is flipped: a CPL made from KiCad's Y-down coordinates. | Nearly every row red, off copper. | Write the CPL with `jlcplace cpl`. |
| Only bottom parts, mirrored left and right | X negated for bottom parts (KiCad's "negative X coordinates for bottom", `--bottom-negate-x`). | Bottom rows red, top rows green. | Write the CPL with `jlcplace cpl`. |
| A few connectors, switches or modules off by 1 to 5 mm | The EasyEDA footprint's origin is not KiCad's anchor. | `N of M EasyEDA pads land off copper ... (nearest KiCad pad centre up to D mm away)` | Write the CPL with `jlcplace cpl`, which applies each part's fitted origin. |
| One part far off | A correction fitted from repeated pad numbers broke. | Off copper by tens of mm. | `jlcplace cpl`, and a numbering decision if it asks for one. |

If `jlcplace check --help` lists `--gerbers`, pass `--gerbers uploaded-gerbers.zip` to the check: the installed
version then checks the Gerbers' origin itself, and may name a common offset across all rows. Prefer its finding to
the table above when it gives one.

## Turned: on its pads, at the wrong angle

* **Top-side parts of one package family turned by 90 or 180 degrees** (SOT-23, SOT-23-5/6, SOT-89, QFN, LGA
  sensors, connectors). EasyEDA draws the package at another zero angle than KiCad, and the CPL carried KiCad's
  angle. jlcplace shows `wrong-pad: EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 ...` or pads
  off copper. Fix: write the CPL with `jlcplace cpl`.
* **Only bottom-side parts turned, while jlcplace says green.** JLC read the bottom rotation differently from the
  `jlc-2022` reading jlcplace assumes. JLC does not document it, and nothing offline proves it. The checklist's
  "bottom reading" items say, for each bottom part, what each other reading would do (`as jlc-2022+180, pin 1 would
  sit on pad 4`). Compare those predictions with what the preview showed for the part marked "look at this one
  first". `jlcplace show board.kicad_pcb --ref U10` prints the row under every reading. If one reading explains the
  bottom parts, confirm it: `jlcplace check board.kicad_pcb uploaded-CPL.csv --reading jlc-2022+180` (with that
  reading's name) should show red exactly the bottom parts that came out wrong. Then write the next CPL with
  `jlcplace cpl` and the same `--reading`. Report it at https://github.com/tyevco/jlcplace/issues, because it would
  change the default for everyone.
* **Parts at odd angles** (a ring of LEDs) off square: check the row's angle against the board. Angles that are not
  multiples of 90 are allowed. The checklist names one part per code to look at.

## Reversed: a diode, LED or polarised capacitor turned 180 degrees

KiCad's libraries put a diode or LED's cathode on pad 1 and a polarised capacitor's "+" on pad 1. Some EasyEDA
footprints number them the other way. jlcplace compares the EasyEDA pin names, the "+" drawn on the EasyEDA
footprint, and the KiCad pin functions:

* `polarity decided by pad numbers only ... check the polarity mark in JLC's preview` (yellow), or `the polarity needs
  a decision` (red): nothing in the records settles it. If the preview or the board showed it reversed, record it
  ([review-jlc-preview](../review-jlc-preview/SKILL.md): the uploaded CPL, and the two space presses that turned it
  round, `--space 2`). `jlcplace cpl` then writes the turned row.
* jlcplace says green, but the assembled part is reversed: the pad numbers agree, yet the real part's pin 1 is not
  the pin that KiCad's footprint calls 1. That is a pinout problem: see below.

## Wrong pins: placed as JLC's footprint says, but the circuit is wrong

The LCSC part's pinout differs from the KiCad symbol and footprint. For example, a SOT-23 MOSFET has G-S-D where the
symbol expects G-D-S, or a regulator orders its pins differently. No CPL can fix this. The part must change, or the
symbol's pin mapping and the board must. Evidence from jlcplace: a correction reason such as `pin names pick rot
...`, `numbering conflict: ... check the pinout`, or the `landings` in `jlcplace show board.kicad_pcb --ref Q1`
(EasyEDA pad, KiCad pad, net). Compare them with the part's datasheet (lcsc.com, search the code). If the installed
jlcplace reports a pinout status or finding in its check output, use it as the primary evidence.

## Missing parts

`no-row` (red: the CPL has no row for an assembled part), a BOM line without an LCSC code, or a part JLC had out of
stock (JLC's order page says so). Add the row or code, or choose another part. Then run [prepare-jlc-order](../prepare-jlc-order/SKILL.md)
steps 5 and 7 again.

The [verdict reference](../check-jlc-placement/verdicts.md) explains every status and message.
