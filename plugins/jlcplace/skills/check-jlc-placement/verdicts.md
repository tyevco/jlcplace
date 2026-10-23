# jlcplace verdicts and messages

Every CPL row gets one line in the summary:

```text
RED     J20  C262671    top     11 of 32 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 4.48 mm away)
```

The columns are the verdict, the reference, the LCSC code, the side, then the status and its first message. The status
is left out when the message already says it. An indented `correction (...)` line under a `renumbered`, `wrong-pad`
or `reversed` row gives the key reason of the part's red or yellow correction. Green and verified rows are only
counted. In the JSON output (`--json`), each result has `status`, `verdict`, `messages`, the `row` it read, the
`expected` row (where a known correction puts the part) and the `landings` (EasyEDA pad, the KiCad pad it lands on,
the net, the distance).

## Findings: lines about the whole order

A finding comes right after the first line, before the rows, and makes the run red (exit 2; the count line adds
`; origin red`, or `gerbers`, `units`). Under `-q` it is still printed (`jlcplace: red: origin mismatch: ...`).

| finding | meaning | fix |
|---|---|---|
| `origin mismatch: JLC lays the CPL over the Gerbers from their (0, 0), board (100, 100) (the aux (drill/place file) origin), but the CPL was written from the page origin (0, 0) (14 of 17 rows): JLC would put every part 141.42 mm off ...; fix: ...` (`--gerbers`) | The CPL and the Gerbers use different origins (or the CPL's numbers are in another unit, or its Y axis points down). Every row is checked as JLC will read it, so they are red too. | Its `fix:`: plot the Gerbers and drill files from the CPL's origin, write the CPL from the Gerbers' (`jlcplace cpl BOARD --gerbers GERBERS`), or shift every row (`check --fixed-cpl FILE` writes that CPL and checks it). |
| `origin mismatch: --origin puts the CPL's origin at ..., but JLC lays the CPL over the Gerbers from their (0, 0) ...` | `--origin` names another point than the Gerbers' (0, 0). The rows are checked from the Gerbers'. | Drop `--origin`, or plot the Gerbers from that point. |
| `gerbers`: `the Gerbers are mirrored ...`, `... 25.4 times too large ...`, `the Gerbers do not seem to be this board's ...`, `the Gerbers' pads do not match the board's (...): are these the board's Gerbers?`, `the Gerbers are older than the board, re-plot them: ...`, `the Gerbers hold pads of another plot than the board's ...`, `the Gerbers hold more than one plot ...`, `the Gerbers have no top copper layer ...`, `the board outline (...) was plotted from another origin than the copper ...` | The Gerber set is not what JLC makes this board from as it is. When its origin cannot be trusted, the rows are checked from `--origin` (default aux). | Plot the Gerbers again from the board as it is, into an empty directory ([prepare-jlc-order](../prepare-jlc-order/SKILL.md)). |
| `gerbers`, yellow: `the Gerbers have no top paste layer ...` | A side the CPL places parts on has no stencil layer. | Plot that side's paste layer. |
| `units`: `the CPL's numbers are in inches (read so because of --units in), but JLC reads a CPL's numbers in mm ...` | `--units` read the numbers as meant, but JLC reads them in mm. | Export the CPL again in mm. |

Without `--gerbers`, a line after the count says `Gerbers not checked: plot them from this CPL's origin (...)`: a
reminder, not a finding.

## Statuses

| status | verdict | meaning | fix |
|---|---|---|---|
| ok | green or verified | Every EasyEDA pad lands on its own pin. | None. |
| ok | yellow | It lands, but look at it: see "Yellow messages" below. | Check the part in JLC's preview. |
| ok | red | It lands as a red correction puts it, and nothing vouches for it: `the polarity needs a decision (...)`, or `the pad numbers do not decide this placement ... nothing vouches for it`. | Settle the part ([write-jlc-cpl](../write-jlc-cpl/SKILL.md), "Resolving red rows"). |
| off-copper | red | `N of M EasyEDA pads land off copper ... (nearest KiCad pad centre up to D mm away)`. The row is shifted or turned. | See "Reading off-copper rows" below. |
| wrong-pad | red | `EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 (USB_DM, not USB_DP), ...`. Every pad is on copper, but on the wrong pins: turned by a multiple of 90 degrees. Also `the pads land by number, but the correction puts them on other nets: ...`: yellow against a fitted correction, red against a verified one, for a polar two-terminal part, or when the numbers do not decide the row. | Rewrite the row from the correction ([write-jlc-cpl](../write-jlc-cpl/SKILL.md)). If the pin numbers themselves disagree, see [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md). |
| reversed | red | `polarity reversed: the row puts the part the other way round than the correction does ...`: a polar two-terminal part (diode, LED, tantalum or electrolytic capacitor, trimmer capacitor, a jumper or switch on a right-angle `_Horizontal` footprint) turned half a turn. | Use the row jlcplace writes. If the preview showed this row right, the correction is wrong: record the row ([review-jlc-preview](../review-jlc-preview/SKILL.md)). |
| renumbered | green | `the two terminals are swapped (non-polar part)`: a resistor or capacitor turned round. | None. |
| renumbered | yellow | `numbering accepted by the correction (manual)`: the EasyEDA and KiCad numbering differ, and a person has decided it. | None, beyond the preview. |
| renumbered | red | `the pads land as the correction (auto) puts them, but it is red, so nothing vouches for this numbering`, with the correction's reason under it: `numbering conflict` (a switch numbered 1-4 in EasyEDA and 1,1,2,2 in KiCad), or `numbering mirrored between the libraries (symmetric land pattern): ... EE 1 -> KiCad 3, EE 3 -> KiCad 1` (a trimmer whose end pins swap). | A person decides from the datasheet, then records it with `jlcplace confirm --source manual` ([write-jlc-cpl](../write-jlc-cpl/SKILL.md)). |
| pinout | red | `pinout differs from the symbol: pad 2 is S on the LCSC part, D on the board; ...`: through the row, the LCSC part's pin names are a permutation of the symbol's pin functions that no placement fixes (a MOSFET whose pins are G, S, D on a G, D, S symbol). No CPL fixes it. | Compare the LCSC part's datasheet with the symbol. Choose a part with the symbol's pinout, or change the symbol and the board. `confirm --source manual` records a datasheet that says the pins are right (yellow then); on an entry JLC's preview verified, only `--source board` (a board that works) does. |
| verified-wrong | red | `verified wrong by JLC's preview on DATE (U3, NAME: note): the row is the stored correction's; fix the correction ...`: `confirm --failed` recorded this row wrong. Red whatever its pads land on, for every part that shares the entry, and `jlcplace cpl --db` writes no CPL with it unless `--force`. | Record the row that is right: the presses that put it right in the preview, or the correction typed in (`--rot --dx --dy`), then confirm it in the preview ([review-jlc-preview](../review-jlc-preview/SKILL.md), "Afterwards"). |
| offset | yellow | `differs from the row jlcplace writes (...): D mm and A deg away, more than ...`: the row is off a known correction by more than 0.15 mm (and a quarter of the smallest pad it lands on) or 2 degrees, but every pad still lands; or `... a quarter turn from where the correction puts the part, though every pad lands as it does` (a one-pad part). | Use the row jlcplace writes, unless the preview showed the row right as it is. |
| not-simulated | yellow | `placement not simulated: no EasyEDA footprint (...); a two-pad non-polar part, and the row point is on it`, or `...; trimmer: which pad is the rotor ...`. | Look at it in the preview; a trimmer: which pad its rotor is on. |
| not-simulated | red | No EasyEDA footprint for a polar or multi-pin part, or a two-pad part whose row point is off its pads. The message ends with where the row point is: `the centre of its courtyard`, or `KiCad's anchor, pin 1, 13.50 mm from the centre of its courtyard: JLC places a part by its centre`. | `no EasyEDA record in the cache`: fetch it. `Component not found`: check the code at lcsc.com; the part can only be checked in JLC's preview. A row at KiCad's pin-1 anchor is far from where JLC puts the part: `jlcplace cpl` writes it at the courtyard's centre. |
| no-row | red | `JLC assembles it (it has an LCSC code) but the CPL has no row`, or `(the BOM lists it) ... (the board excludes it from the position files, so KiCad's position file leaves it out)`. | Add the row (`jlcplace cpl --bom` writes rows for the parts the BOM lists), or take the part out of the BOM and the assembly. |
| not-on-board | red | `R99 is not on the board`. The CPL is from another board or revision. | Use the CPL made from this board. |
| layer-mismatch | red | `the row says Top, the part is on the bottom`. | Fix the Layer column, or re-export. |
| not-placed | red | `the board part is DNP: JLC must not place it` (or board-only). | Remove the row, or clear the flag in KiCad. |
| not-placed | yellow | `the board part is excluded from position files ...: JLC does not place it unless the BOM lists it` (with `--bom`: `... and the BOM does not list it: JLC does not place it`). | Remove the row, or list the part in the BOM if JLC is to assemble it. |
| duplicate | red | `2 rows for C5`. | Keep one row. |
| duplicate-ref | red | `R1 is on the board 2 times: JLC cannot tell them apart`. | Give each part its own reference in KiCad. |

## Reading off-copper rows

* A few parts (connectors, a USB-C receptacle, a switch) off by anything from about 1 mm to over 100 mm. Either the
  tool that wrote the CPL used KiCad's anchor where EasyEDA's footprint origin lies elsewhere, or it applied a wrong
  offset of its own, often from a fit that broke on repeated pad numbers (two `MP` pads, a USB-C shell, a switch
  numbered 1,1,2,2). The old dial-me-spinner CPLs had both kinds of error from their own writer: J1, J20 and J40 were
  1.4 to 4.5 mm off and SW1 was 127 mm off. [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md) says
  how to tell the two apart. Either way, `jlcplace cpl` writes the right row.
* Every row off by about the same vector: an origin mismatch (aux, grid or page origin). With `--gerbers` it is a
  finding; without, a `jlcplace: origin mismatch: ...` hint. See
  [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md).
* Only bottom rows red: bottom X negated (KiCad's "negate X for bottom", kicad-cli's `--bottom-negate-x`), or a tool
  with another bottom-rotation convention.
* `rotation N is not a multiple of 90` alone is not an error: parts on a circle. The preview checklist names one part
  per code to look at.

## Yellow messages

* `the row point is outside the board outline (Edge.Cuts), D mm beyond its edge: look at it in JLC's preview` (the
  Gerbers' outline with `--gerbers`): JLC puts the part's centre off the board. Right for a part that overhangs the
  edge; wrong for a row of another part or origin. On a red row it comes last.
* `lands as the correction puts it, but the fit was unsure of that placement: ...`, then the fit's doubt:
  * `polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it)`;
  * `polarity evidence conflicts: the pad numbers put + on pad 1 ..., the drawn + on pad 2 of the EasyEDA footprint;
    following the drawn + ...`;
  * `... placed by the holes` or `... check the part's pegs against the KiCad footprint`: EasyEDA's unplated holes
    (a connector's peg) against KiCad's NPTH pads decided, or contradict, the orientation;
  * `the pads do not fix the body's orientation`.

  Look at the polarity mark, pin 1 or the pegs in the preview.
* `the pad numbers do not decide this placement ... and no correction is known`. Write the CPL with `jlcplace cpl`,
  which fits a correction, or look at the part in the preview.
* `KiCad pads that no EasyEDA pad lands on: ...`. The EasyEDA land pattern lacks a pad that KiCad has, for example a
  thermal pad or a mounting tab. Check that the part really has it.
* Pin names: `through this row the pin names differ from the symbol (...); another placement puts them on their own
  functions`: use the row jlcplace writes. `N pin names differ from the symbol: ...: check the LCSC part's pinout
  against the symbol`: compare the datasheet with the symbol. `...: accepted by the manual correction`: a person
  decided it.
* `the board excludes it from the position files, but the BOM and the CPL list it: JLC places it (it never sees
  KiCad's attributes); untick 'Exclude from position files' if it is to be assembled, else drop its row and BOM
  line`. The part is checked like any other row.
* `the stored correction was verified wrong by ...; this row is D mm and A deg from its row: look at it in JLC's
  preview, then record it`: not the row that was wrong, but nothing verified vouches for it.
* `yellow because the stored auto correction is yellow: ...`. No EasyEDA record was at hand, so the part was
  simulated through the stored correction. The verdict cannot be better than that correction's.
* `the EasyEDA footprint changed since the stored correction`. EasyEDA edited the footprint, so the stored
  correction is stale. jlcplace refits it, and the part stays yellow until someone checks it again.
* `not in the BOM: JLC does not place it` (with `--bom`): a part without an LCSC code that the BOM does not list.

Messages that do not change the verdict: `bottom reading matters, check it in JLC's preview: ...` (the part is on the
checklist), `differs from the row jlcplace writes (...)` within the offset limits, `simulated with the KiCad pads
through the stored ... correction`, `the board excludes it from the BOM, but the BOM lists it: JLC will place it`.

## BOM lines (with `--bom`)

They are printed as `BOM` lines and make the run at least yellow:

* `R5: JLC places C7519 from the BOM; the board says C2681320 (checked with C7519)`. JLC assembles from the BOM, so
  the BOM's code is the one simulated. Make the board and the BOM agree.
* `R5: the BOM says C1, the board says C2`, `in the BOM but not on the board`, `in the BOM but DNP on the board`
  (or excluded, or board-only), `in the BOM but the CPL has no row for it`, `a CPL row but no BOM line (JLC will not
  place it)`, `JLC should assemble it but the BOM does not list it`, `Qty 3 but 2 designators`, `'C0402' is not an
  LCSC code`. Each one is an inconsistency to fix in the schematic or the BOM export.

## Correction statuses (`--db`, `jlcplace show`)

From strongest to weakest: **board-verified** (right on an assembled board), **preview-verified** (right in JLC's
preview), **manual** (decided or typed in by a person), **auto** (fitted). A row that matches a verified correction is
**verified**. A correction recorded with `confirm --failed` is auto and red (`verification failed (...)`), and its row
is `verified-wrong`, until other numbers replace it or a later verification of it goes right.
