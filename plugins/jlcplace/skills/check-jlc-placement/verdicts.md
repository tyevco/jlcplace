# jlcplace verdicts and messages

Every CPL row gets one line in the summary:

```text
RED     J20  C262671    top     11 of 32 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 4.48 mm away)
```

The columns are the verdict, the reference, the LCSC code, the side, then the status and its first message. The status
is left out when the message already says it. Green and verified rows are only counted. In the JSON output
(`--json`), each result has `status`, `verdict`, `messages`, the `row` it read, the `expected` row (where a known
correction puts the part) and the `landings` (EasyEDA pad, the KiCad pad it lands on, the net, the distance).

## Statuses

| status | verdict | meaning | fix |
|---|---|---|---|
| ok | green or verified | Every EasyEDA pad lands on its own pin. | None. |
| ok | yellow | It lands, but look at it: see "Yellow messages" below. | Check the part in JLC's preview. |
| off-copper | red | `N of M EasyEDA pads land off copper ... (nearest KiCad pad centre up to D mm away)`. The row is shifted or turned. | See "Reading off-copper rows" below. |
| wrong-pad | red | `EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 (USB_DM, not USB_DP), ...`. Every pad is on copper, but on the wrong pins: turned by a multiple of 90 degrees, or reversed. | Rewrite the row from the correction ([write-jlc-cpl](../write-jlc-cpl/SKILL.md)). If the pin numbers themselves disagree, see [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md). |
| renumbered | green | `the two terminals are swapped (non-polar part)`: a resistor or capacitor turned round. | None. |
| renumbered | yellow | `numbering accepted by the correction (manual)`: the EasyEDA and KiCad numbering differ, and a person has decided it. | None, beyond the preview. |
| renumbered | red | `the pads land as the correction (auto) puts them, but it is red, so nothing vouches for this numbering`. The pad numbers disagree, for example a switch numbered 1-4 in EasyEDA and 1,1,2,2 in KiCad. | A person decides from the datasheet, then records it with `jlcplace confirm --source manual` ([write-jlc-cpl](../write-jlc-cpl/SKILL.md)). |
| offset | yellow | `differs from the row jlcplace writes (...): D mm and A deg away, more than ...`: the row is off a known correction by more than 0.15 mm (and a quarter of the smallest pad it lands on) or 2 degrees, but every pad still lands. | Use the row jlcplace writes, unless the preview showed the row right as it is. |
| not-simulated | yellow | `placement not simulated: no EasyEDA footprint (...); a two-pad non-polar part, and the row point is on it`. | Look at it in the preview. |
| not-simulated | red | No EasyEDA footprint for a polar or multi-pin part, or a two-pad part whose row point is off its pads. | Check the code at lcsc.com. Look at the part in the preview, or pick a part that has a footprint. |
| no-row | red | `JLC assembles it (it has an LCSC code) but the CPL has no row`. | Add the row, or mark the part DNP or "exclude from position files" in KiCad. |
| not-on-board | red | `R99 is not on the board`. The CPL is from another board or revision. | Use the CPL made from this board. |
| layer-mismatch | red | `the row says Top, the part is on the bottom`. | Fix the Layer column, or re-export. |
| not-placed | red | `the board part is DNP` (or excluded from position files, or board-only): JLC must not place it. | Remove the row, or clear the flag in KiCad. |
| not-placed | yellow | The part is only excluded from the BOM. | Remove the row, or pass `--bom` when the BOM lists it. |
| duplicate | red | `2 rows for C5`. | Keep one row. |
| duplicate-ref | red | `R1 is on the board 2 times: JLC cannot tell them apart`. | Give each part its own reference in KiCad. |

## Reading off-copper rows

* A few parts (connectors, a USB-C receptacle, a switch) off by anything from about 1 mm to over 100 mm. Either the
  tool that wrote the CPL used KiCad's anchor where EasyEDA's footprint origin lies elsewhere, or it applied a wrong
  offset of its own, often from a fit that broke on repeated pad numbers (two `MP` pads, a USB-C shell, a switch
  numbered 1,1,2,2). The old dial-me-spinner CPLs had both kinds of error from their own writer: J1, J20 and J40 were
  1.4 to 4.5 mm off and SW1 was 127 mm off. [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md) says
  how to tell the two apart. Either way, `jlcplace cpl` writes the right row.
* Every row off by about the same vector: an origin mismatch (aux, grid or page origin). See
  [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md).
* Only bottom rows red: bottom X negated (KiCad's "negate X for bottom", kicad-cli's `--bottom-negate-x`), or a tool
  with another bottom-rotation convention.
* `rotation N is not a multiple of 90` alone is not an error: parts on a circle. The preview checklist names one part
  per code to look at.

## Yellow messages

* `lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only
  (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview`.
  Look at the polarity mark in the preview.
* `the pad numbers do not decide this placement ... and no correction is known`. Write the CPL with `jlcplace cpl`,
  which fits a correction, or look at the part in the preview.
* `KiCad pads that no EasyEDA pad lands on: ...`. The EasyEDA land pattern lacks a pad that KiCad has, for example a
  thermal pad or a mounting tab. Check that the part really has it.
* `yellow because the stored auto correction is yellow: ...`. No EasyEDA record was at hand, so the part was
  simulated through the stored correction. The verdict cannot be better than that correction's.
* `bottom reading matters, check it in JLC's preview: ...`. The verdict is unchanged. The part is on the checklist.
* `the EasyEDA footprint changed since the stored correction`. EasyEDA edited the footprint, so the stored
  correction is stale. jlcplace refits it, and the part stays yellow until someone checks it again.

## BOM lines (with `--bom`)

They are printed as `BOM` lines and make the run at least yellow:

* `R5: JLC places C7519 from the BOM; the board says C2681320 (checked with C7519)`. JLC assembles from the BOM, so
  the BOM's code is the one simulated. Make the board and the BOM agree.
* `R5: the BOM says C1, the board says C2`, `in the BOM but not on the board`, `in the BOM but DNP on the board`,
  `in the BOM but the CPL has no row for it`, `a CPL row but no BOM line (JLC will not place it)`, `Qty 3 but 2
  designators`, `'C0402' is not an LCSC code`. Each one is an inconsistency to fix in the schematic or the BOM
  export.

## Correction statuses (`--db`, `jlcplace show`)

From strongest to weakest: **board-verified** (right on an assembled board), **preview-verified** (right in JLC's
preview), **manual** (decided or typed in by a person), **auto** (fitted). A row that matches a verified correction is
**verified**.
