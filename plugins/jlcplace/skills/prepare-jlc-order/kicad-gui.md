# JLC fab outputs from KiCad's dialogs (no kicad-cli)

Use these steps when kicad-cli is not installed (KiCad 7 and earlier lack some of its commands). The menu names are
KiCad 8 and 9's. KiCad 7's are close. Every dialog that has an origin option must use the **drill/place file
origin**.

## Gerbers (PCB Editor)

1. File > Fabrication Outputs > Gerbers (.gbr)...
2. Plot format Gerber. Output directory `fab/gerbers/`.
3. Layers: F.Cu, the inner copper layers (In1.Cu ...), B.Cu, F.Paste, B.Paste, F.Silkscreen, B.Silkscreen, F.Mask,
   B.Mask, Edge.Cuts.
4. Tick **Use drill/place file origin**. Leave "Use Protel filename extensions" as it is (JLC reads both).
5. Plot.

## Drill files (same dialog)

1. Generate Drill Files... (the button in the Plot dialog, or File > Fabrication Outputs > Drill Files (.drl)...).
2. Format Excellon. Units millimeters. Zeros format decimal.
3. Drill origin: **Drill/place file origin**.
4. Leave **Mirror Y axis** unticked.
5. Untick **PTH and NPTH in single file**, so the plated and the non-plated holes go to separate files and the
   non-plated ones (mounting holes, connector pegs) are not told apart by per-tool attributes alone.
6. Output directory `fab/gerbers/`. Generate Drill File.

Zip the contents of `fab/gerbers/` (the files, not the folder) into `fab/board-gerbers.zip`.

## BOM (Schematic Editor)

1. Tools > Generate Bill of Materials... (KiCad 8 and 9 open the Symbol Fields Table's Export tab).
2. Show and order the columns Value, Reference, Footprint and the LCSC field (for example `LCSC` or
   `JLCPCB Part #`). Group by Value, Footprint and the LCSC field. Exclude DNP symbols.
3. Export as CSV with the headers `Comment`, `Designator`, `Footprint`, `LCSC Part #`. Each designator cell must
   list every reference (`R1,R2,R3`), with no ranges (`R1-R3`).
4. Save as `fab/board-BOM.csv`.

## Position file (only to check another tool's CPL)

`jlcplace cpl` writes the CPL JLC needs, so you do not need KiCad's position file for the order. To check one anyway:

1. File > Fabrication Outputs > Component Placement (.pos, .gbr)... (KiCad 7: Footprint Position (.pos) File...).
2. Format CSV. Units **Millimeters**. Files: single file for board.
3. Tick **Use drill/place file origin**. Leave **Use negative X coordinates for footprints on bottom layer**
   unticked.
4. Generate, then check the file with [check-jlc-placement](../check-jlc-placement/SKILL.md). Expect red rows wherever
   EasyEDA's footprint angle or origin differs from KiCad's. That is why `jlcplace cpl` writes the CPL.
