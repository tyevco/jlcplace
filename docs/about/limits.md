# Limits

jlcplace simulates JLC's placement with JLC's own footprint data. That catches the usual CPL mistakes before you
order, but it is not JLC's machine, and some questions cannot be settled offline. This page lists what jlcplace does
not look at, what it assumes, and where its data comes from. A CPL is the pick-and-place file you upload to JLCPCB
with your BOM: one row per part, with its position, side and rotation.

jlcplace is version 0.1.0, alpha software, and has no release tag yet. Pin the commit you use in CI (see
[Continuous integration](../guide/ci)), so that a newer jlcplace does not change verdicts under you.

## What a green row does and does not say

A green row means that every pad of JLC's EasyEDA footprint, placed where the row says, has its centre in KiCad
copper of its own pin. It does not mean more than that:

* **Only pad centres are placed.** A row a little off, with every pad centre still on its own pad, passes. When
  jlcplace knows the part's correction, a row more than 0.05 mm or 0.5 degrees from the row jlcplace would write
  gets a message, and it turns yellow ("offset") beyond 0.15 mm and a quarter of the smallest pad it lands on, or
  beyond 2 degrees. A row a quarter turn from that row is yellow too, even when every pad still lands as the
  correction puts it: that happens on alike lands, such as a one-pad part whose long pad would then lie across its
  land.
* **Bodies are not looked at.** Part outlines, courtyards, heights, silkscreen and paste are not checked: two parts
  whose bodies collide can both be green.
* **The part itself is not checked.** jlcplace uses the LCSC code's footprint and nothing else about the part. A
  resistor of the wrong value, or a part out of stock, on the right footprint is green.
* **The footprint is the one in your cache.** A record is used until you download it again (`jlcplace fetch
  --refresh`), so JLC's current footprint can differ from the one checked. See [the record
  cache](../reference/environment#the-record-cache).

JLC's placement preview stays the last check. The [preview checklist](../guide/preview) names the few parts there
that need a look.

## Things nothing offline can prove

### How JLC reads a bottom-side rotation

JLC does not document how it turns a part on the bottom side. jlcplace reads a bottom row as `jlc-2022`: the CPL
rotation is 180 - theta + rot, counter-clockwise seen from below, where theta is the part's angle in KiCad and rot
its correction. It is the reading every open tool uses (kicad-jlcpcb-tools,
Fabrication-Toolkit, KiKit, KiBot, JLCKicadTools), and it matches the JLC preview results recorded in
[the z2amiller fork of kicad-jlcpcb-tools](https://github.com/z2amiller/kicad-jlcpcb-tools). That is good evidence, not proof: nothing offline can show which reading the factory
uses.

So every bottom row is also placed under the three other readings a mirrored placement allows. A part whose pins
would move under any of them goes on the preview checklist, and the checklist names the one part to look at first:
when that part sits right in JLC's preview, the reading is settled for the whole board. If JLC ever changes its
reading, `--reading` selects another one. See [How the check works](../concepts/how-it-works).

### Keys pressed in JLC's preview

When you put a part right in JLC's preview, `jlcplace confirm --space N --right N --up N` records the keys you
pressed. jlcplace reads them by Fabrication-Toolkit's convention: one space press turns the part 90 degrees
counter-clockwise (adds 90 to the CPL rotation), one arrow press moves it 0.0635 mm on the screen, and the bottom is
shown seen from below. That convention is derived from Fabrication-Toolkit's documentation. jlcplace's tests replay
JLC preview space presses recorded for seven top-side parts; they have not compared arrow presses, or presses on
bottom-side parts, with JLC's preview. After recording presses, upload the CPL jlcplace writes and look at those
parts in the preview again. See [A part you put right in the
preview](../guide/preview#a-part-you-put-right-in-the-preview).

## Where the footprints come from

### The EasyEDA API is unofficial

jlcplace downloads JLC's footprints from the EasyEDA API (`https://easyeda.com/api/products/CODE/components`). It is
not a documented, stable interface: EasyEDA can change it, rate-limit it or stop answering, and `fetch` then fails.
Only `fetch`, and the other commands with `--online`, use it, and they do so gently (see
[Network](../reference/environment#network)). A committed [correction database](../guide/database) lets a check run
with no EasyEDA records at all: parts with a stored correction are still simulated, with their own KiCad pads put
through that correction, and their verdict is no better than the correction's.

jlcplace reads the records of EasyEDA's classic editor, the ones that API serves.

### Parts with no record

Some LCSC codes have no EasyEDA footprint. On the dial-me-spinner main board, R26 (C4167968) gets "Component not
found". Such a part is not simulated. It is yellow when it is a two-pad part that may be turned round (a resistor, a
ceramic capacitor) and the row's point is on its pads (within 0.1 mm of them), and red otherwise: a polar two-pad
part, a one-pad part, or one with more pads. A stored correction in the database lets it be simulated as above.
Otherwise JLC's preview is the only check.

### Records change

EasyEDA edits footprints now and then. A database entry is stored under a hash of the EasyEDA pads it was made with,
so an edit that moves a pad makes the entry stale: jlcplace fits the part again, and if the new fit differs, the part
is at best yellow until you check it again (see [When EasyEDA changes a
footprint](../guide/database#when-easyeda-changes-a-footprint)).

### What is read from a record

* Pads on EasyEDA's top, bottom and multi-layer copper. Pads on the footprint's own bottom layer are not placed, and
  holed pads EasyEDA marks unplated are dropped. jlcplace keeps no plated flag and does not use slot shapes.
* EasyEDA's pad rotation sense is proven at multiples of 90 degrees, from the order in which the records list a
  rotated pad's corners; every cached pad is at such an angle. A rectangular or oval pad at another angle is checked
  against its own outline, and follows the outline if it shows the other sense.
* Pin names come from the part's EasyEDA symbol, through every unit of a multi-unit symbol. On three real records
  (two 324-ball BGAs and a three-unit module) they named exactly the footprint's pads.

## How a correction is decided

* **Polarity.** For a polar two-terminal part (a diode, an LED, a tantalum or electrolytic capacitor) whose EasyEDA
  pin names and pad numbers disagree, the "+" drawn on the EasyEDA footprint decides. jlcplace finds that "+" as two
  crossing strokes on the silkscreen or document layer, a heuristic. It decided all 6 such cases in the JLC preview
  data, where the names alone were wrong once (D12, C2836396). Where it finds no "+", or the "+" contradicts the only
  other evidence, the part is red until someone decides (see [JLC's preview and confirm](../guide/preview)).
* **KiCad's positive pad** comes from the pins' functions (the schematic's pin names), or from pad numbers that name
  the terminal (`A`, `K`, `+`, `-`). Without either, it follows the convention of KiCad's own diode, LED and
  polarised capacitor footprints: diode and LED pad 1 is the cathode, capacitor pad 1 is `+`. For a footprint from
  another library nothing says which pad is positive, so give its pins functions in the schematic.
* **Pads that do not decide.** Some parts land on every pad in more than one way: a symmetric package whose pads do
  not fix its orientation, or a switch numbered 1 to 4 in EasyEDA and 1, 1, 2, 2 in KiCad (dial-me-spinner's SW1).
  Which way is right is in the datasheet or the preview, not in the records. Such a part is yellow or red until a
  person records the decision with `jlcplace confirm`.
* **Mirror images.** When only a mirror image of the EasyEDA footprint matches KiCad's, no CPL row can place it.
  jlcplace never applies one: the part is red and gets no row.
* **Land patterns that differ.** A fit is yellow when its residual is more than a quarter of the smallest pad its
  pads land on (at least 0.1 mm). That also flags correct fits of SOT-23 (0.28 mm on 0.6 mm pads) and SOT-23-6 (0.21
  mm) parts, whose KiCad and EasyEDA land patterns simply differ. The reason then reads "the land patterns differ
  (every pad lands)". A row that matches such a correction is still green, and the preview checklist leaves it out.

## Copper outlines

jlcplace tests whether a point lies in a KiCad pad with a tolerance of 0.001 mm. A custom pad made of separate
copper islands is held as one outline, with zero-width bridges joining the islands; a point within that tolerance of
such a bridge counts as copper. This matters only for pads whose copper is in several pieces.

## KiCad support

* **Board files.** jlcplace reads `.kicad_pcb` files from KiCad 5 to 10 with its own S-expression reader, so KiCad
  need not be installed. A CPL can come from any tool, but the board must be a KiCad board.
* **pcbnew.** When KiCad's `pcbnew` Python module can be imported, jlcplace uses it instead. It is tested with KiCad
  9; pcbnew from KiCad 6 to 8 is untested (the S-expression reader covers those files). A board that pcbnew cannot
  load, such as a newer file than that pcbnew knows, is read with the S-expression reader, with a warning.
* **KiCad 10 variants.** Variant overrides are ignored: jlcplace uses each footprint's own attributes (DNP,
  exclusions) and fields, not a variant's. A BOM given with `--bom` still supplies the LCSC codes JLC places.

## What it is tested on

The tests run in CI on Linux, macOS and Windows with Python 3.10 to 3.13, without KiCad; the pcbnew tests run in
KiCad's own 9.0 image on Linux. The real-world data behind them is the two dial-me-spinner boards (209 and 25 parts)
with their CPLs, 58 EasyEDA records, the KiCad 5 to 8 test boards of KiKit, and the KiCad 10 boards and recorded JLC
preview results of the z2amiller fork of kicad-jlcpcb-tools. Parts unlike anything there are the likeliest to find a
case jlcplace gets wrong. If you find one, please report it at
[github.com/tyevco/jlcplace/issues](https://github.com/tyevco/jlcplace/issues), with the part's LCSC code and
KiCad footprint.
