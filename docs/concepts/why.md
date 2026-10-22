# Why parts end up misaligned

If a JLCPCB assembly order came back with parts turned (rotated), shifted or the wrong way round, look at the CPL
first. The CPL (component placement list) is the pick-and-place file you upload with the BOM: one row per part,
giving its reference, a position (Mid X, Mid Y), the side (Layer) and a rotation. This page goes through the ways a
CPL ends up wrong. Each comes with a real example, what JLC's placement preview shows, and what jlcplace reports.

| what you see on the boards or in JLC's preview | the likely cause |
|---|---|
| a part turned by 90, 180 or 270 degrees on the top side | [1. another angle](#zero-angle) |
| parts turned on the bottom side only | [5. the bottom side](#bottom) |
| a part shifted by about 0.5 to 5 mm, or far from its pads | [2. another origin](#origin), [7. guesses](#guesses), [8. tool bugs](#writer-bugs) |
| a part on its pads, but pin 1 or the polarity mark at the wrong end | [3. symmetric parts](#symmetric), [4. polarity](#polarity) |
| every part off by the same distance, or crowded towards one corner | [6. origin or units](#origin-units) |

A CPL mistake is the same on every board of an order. Small shifts that differ from board to board come from
assembly, not from the CPL.

::: tip What to do
Check the CPL you uploaded with that order: `jlcplace fetch BOARD`, then `jlcplace check BOARD CPL --bom BOM`. Its
red rows are the parts JLC places wrong from that CPL. `jlcplace cpl BOARD -o NEW-CPL --bom BOM` writes a corrected
one. [Getting started](../guide/getting-started) walks through it.
:::

## JLC places its own footprints, not yours

JLC never sees your KiCad footprints. For each CPL row it takes the footprint that its EasyEDA library holds for
the part's LCSC code, puts that footprint's origin at the row's Mid X, Mid Y, and turns it by the row's Rotation.
So a row is right only when it describes JLC's footprint: where its origin falls on your board, and how far it must
turn from the way EasyEDA drew it.

KiCad's footprint and JLC's footprint for the same part often disagree on both. They put the origin in different
places, and they draw the part at different angles. Most of the failures below come from that disagreement. The
rest are mistakes in the numbers themselves.

jlcplace checks a CPL by doing what JLC does. For each row it puts JLC's footprint down where the row says, and finds
the KiCad copper pad, and the net, that each of JLC's pads lands on. A pad off copper, or on another pin's net,
makes the row red. [How the check works](./how-it-works) has the details.

## How this came up

A coworker had parts come out misaligned going from KiCad to JLCPCB. Looking into how that happens led to a
check, by simulating JLC's placement, of the CPLs of [dial-me-spinner](https://github.com/tyevco/dial-me-spinner), a public KiCad project whose CPLs were
written by its own script. Seven rows were wrong: the connectors J1, J20 and J40 on the main board, and J1, J2, J3
and the switch SW1 on the base board. The connectors were 1.4 to 4.5 mm off their pads, and SW1 was 127 mm away.
The script itself had a bug ([section 8](#writer-bugs)). jlcplace grew out of that check.

The same main board shows most of the other failures too, if you upload KiCad's own position file as the CPL. It
has the right columns, and 17 of its 184 rows are wrong for JLC:

```sh
kicad-cli pcb export pos --format csv --units mm --side both --use-drill-file-origin --exclude-dnp -o dial-me-spinner-main-pos.csv dial-me-spinner-main.kicad_pcb
jlcplace check dial-me-spinner-main.kicad_pcb dial-me-spinner-main-pos.csv --bom dial-me-spinner-main-BOM-JLCPCB.csv
```

```text
Wrote position data to 'dial-me-spinner-main-pos.csv'.
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs dial-me-spinner-main-pos.csv (184 rows), bottom reading jlc-2022
RED     BZ1  C910763    bottom  2 of 2 EasyEDA pads land off copper: 1, 2 (nearest KiCad pad centre up to 3.14 mm away)
RED     D20  C2500      top     3 of 3 EasyEDA pads land off copper: 1, 2, 3 (nearest KiCad pad centre up to 1.00 mm away)
...
RED     J20  C262671    top     32 of 32 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 1.34 mm away)
...
RED     U2   C2681320   top     wrong-pad: EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 (/Power & link/LSW_CT, not VSYS), 2->5 (/Power & link/LSW_QOD, not GND), 3->6 (VLED, not LED_PWR_EN), 4->1 (VSYS, not /Power & link/LSW_CT), 5->2 (GND, not /Power & link/LSW_QOD), 6->3 (LED_PWR_EN, not VLED)
...
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 17 red, 1 yellow, 166 green, 0 verified
...
```

The examples on this page use the files from [Checking a CPL](../guide/check#the-example-files), with the EasyEDA
records fetched. The 17 red rows fall into four of the cases below:

| rows | side | what differs between the position file and JLC's footprint | see |
|---|---|---|---|
| D20, U42 (SOT-23); U1, U20 (SOT-23-5) | top | EasyEDA draws them turned 180 or 270 degrees | [1](#zero-angle) |
| U2, U21 (SOT-23-6); U31 (LGA-12) | top | turned 180 or 270 degrees, but every pad still lands on copper | [1](#zero-angle), [3](#symmetric) |
| J20 (FPC connector) | top | EasyEDA's origin is 1.34 mm from KiCad's anchor | [2](#origin) |
| BZ1, J1, J40 | bottom | KiCad's angle, where JLC reads a bottom rotation as 180 degrees minus it | [5](#bottom) |
| U40, U41 (WSON-8) | bottom | the same, and every pad still lands on copper | [3](#symmetric), [5](#bottom) |
| U11, U12, U32 | bottom | both: EasyEDA draws them turned 270 degrees, and the bottom side | [1](#zero-angle), [5](#bottom) |
| U10 (radio module) | bottom | both: EasyEDA's origin is 1.70 mm away, and the bottom side | [2](#origin), [5](#bottom) |

## 1. JLC's footprint is drawn at another angle {#zero-angle}

EasyEDA and KiCad often draw the same package turned by 90, 180 or 270 degrees. The SOT-23 family is the usual
case. For each part, jlcplace works out how far JLC's footprint must turn to sit on yours, from the pads of both
footprints: `rot` in the output of `fit`.

```sh
jlcplace fit dial-me-spinner-main.kicad_pcb --ref D20 --ref U42 --ref U1 --ref U20 --ref U2 --ref U21 --ref U12 --ref U30
```

```text
YELLOW  D20  C2500      top     rot 180, dx 0.0992, dy 0.0000 (number, rms 0.281, 3 matched)  rms 0.281 mm: the land patterns differ (every pad lands)
GREEN   U1   C841192    top     rot 270, dx 0.0020, dy 0.0000 (number, rms 0.010, 5 matched)  fits by pad numbers: 5/5 matched, rms 0.010 mm
YELLOW  U2   C2681320   top     rot 180, dx 0.0000, dy 0.0000 (number, rms 0.212, 6 matched)  rms 0.213 mm: the land patterns differ (every pad lands)
GREEN   U12  C7519      bottom  rot 270, dx 0.0000, dy 0.0000 (number, rms 0.012, 6 matched)  fits by pad numbers: 6/6 matched, rms 0.012 mm
GREEN   U20  C7722      top     rot 270, dx 0.0032, dy 0.0000 (number, rms 0.016, 5 matched)  fits by pad numbers: 5/5 matched, rms 0.016 mm
YELLOW  U21  C2681320   top     rot 180, dx 0.0000, dy 0.0000 (number, rms 0.212, 6 matched)  rms 0.213 mm: the land patterns differ (every pad lands)
YELLOW  U30  C3715882   bottom  rot 180, dx 0.0000, dy 0.0000 (number, rms 0.212, 6 matched)  rms 0.213 mm: the land patterns differ (every pad lands)
GREEN   U42  C140921    top     rot 180, dx 0.0208, dy 0.0000 (number, rms 0.059, 3 matched)  fits by pad numbers: 3/3 matched, rms 0.059 mm
8 parts: 0 red, 4 yellow, 4 green
```

On the top side, the CPL rotation must be KiCad's angle plus `rot` (the bottom side is [case 5](#bottom)). A CPL
that carries KiCad's angle leaves every one of these parts turned. The yellow fits only say that the two land
patterns differ a little, their pad centres 0.2 to 0.3 mm apart: every pad still lands on its own pin, and the rows
written from these corrections are green.

* **In JLC's preview:** the part sits turned on its pads. At 90 or 270 degrees its leads point across the pads; a
  SOT-23 at 180 degrees puts its single lead where its two leads should be.
* **jlcplace reports:** red, `N of M EasyEDA pads land off copper`, as for D20 above. A part that still lands on
  copper is [case 3](#symmetric).

## 2. JLC's footprint has its origin elsewhere {#origin}

Mid X and Mid Y say where JLC puts its footprint's origin. KiCad's anchor is wherever the footprint's author put
it: often pin 1, or the middle of the body rather than of the pads. On all six connectors of these two boards,
EasyEDA's origin is the centre of the box around its pads. Where the two points differ, a row at KiCad's anchor puts
the part beside its pads. On dial-me-spinner:

| part | package | EasyEDA's origin from KiCad's anchor |
|---|---|---|
| main J20 | 30-pin FPC connector, 0.5 mm pitch | 1.34 mm |
| base J1 | USB-C receptacle | 1.57 mm |
| main U10 | Raytac MDBT50Q radio module | 1.70 mm |
| main J1, J40; base J2, J3 | JST SH connectors | 0.06 mm (harmless) |

`jlcplace show` gives the offset as `dx, dy`, in the KiCad footprint's own frame, and the row it leads to:

```sh
jlcplace show dial-me-spinner-main.kicad_pcb --ref J20
```

```text
ref: J20
lcsc: C262671
...
correction:
  source: fit
  rot: 0.0
  dx: -0.0009
  dy: 1.3405
...
rows:
  jlc-2022:
    x: -0.0009
    y: -10.8405
    rotation: 0.0
...
```

The position file put J20 at KiCad's anchor, (0.0000, -9.5000). JLC needs its origin at (-0.0009, -10.8405).

* **In JLC's preview:** the part sits shifted, its contacts beside the pads.
* **jlcplace reports:** red, `32 of 32 EasyEDA pads land off copper ... up to 1.34 mm away`. A shift small enough
  to keep every pad on copper is yellow, `offset`, once the row is more than 0.15 mm from where the part's correction
  puts it (or a quarter of its smallest pad, when that is larger).

![The card of a connector whose row is 1.3 mm off: every EasyEDA pad outlined red and dashed beside the KiCad pads it should cover](/img/card-offset.png)

*A connector placed at KiCad's anchor, as it looks in jlcplace's HTML report: every EasyEDA pad, outlined red and
dashed, lands beside its KiCad pad. From the documentation's synthetic example board, not dial-me-spinner.*

## 3. A symmetric part turned 180 degrees still lands on copper {#symmetric}

A SOT-23-6 has the same land pattern turned 180 degrees. Placed half a turn wrong, every one of its six pads lands on
copper, each on the pad opposite its own. A check that asks only "is every pad on copper?" passes it. The part
is still wrong: every pin is on another pin's net.

<figure class="figure-scroll">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 250" role="img" aria-label="A SOT-23-6 whose EasyEDA footprint is drawn turned 180 degrees, placed at rotation 180 and at rotation 0" style="display:block;margin:0 auto;width:100%;max-width:720px;height:auto;font-family:var(--vp-font-family-base);font-size:12px">
<rect x="10" y="26" width="345" height="176" rx="6" style="fill:var(--vp-c-bg-soft);stroke:var(--vp-c-divider);stroke-width:1"/>
<text x="182" y="18" text-anchor="middle" style="fill:var(--vp-c-text-1);font-weight:600">Rotation 180: the row jlcplace writes</text>
<polygon points="115.5,64.5 164.9,64.5 164.9,87.3 115.5,87.3" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="107.5" y="79.9" text-anchor="end" style="fill:var(--vp-c-text-2)">VSYS  1</text>
<polygon points="115.5,100.6 164.9,100.6 164.9,123.4 115.5,123.4" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="107.5" y="116" text-anchor="end" style="fill:var(--vp-c-text-2)">GND  2</text>
<polygon points="115.5,136.7 164.9,136.7 164.9,159.5 115.5,159.5" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="107.5" y="152.1" text-anchor="end" style="fill:var(--vp-c-text-2)">LED_PWR_EN  3</text>
<polygon points="199.1,136.7 248.5,136.7 248.5,159.5 199.1,159.5" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="256.5" y="152.1" style="fill:var(--vp-c-text-2)">4  LSW_CT</text>
<polygon points="199.1,100.6 248.5,100.6 248.5,123.4 199.1,123.4" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="256.5" y="116" style="fill:var(--vp-c-text-2)">5  LSW_QOD</text>
<polygon points="199.1,64.5 248.5,64.5 248.5,87.3 199.1,87.3" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="256.5" y="79.9" style="fill:var(--vp-c-text-2)">6  VLED</text>
<polygon points="112,61 168.4,61 168.4,90.8 112,90.8" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<text x="140.2" y="79.9" text-anchor="middle" style="fill:var(--vp-c-indigo-1);font-weight:700">1</text>
<polygon points="112,97.1 168.4,97.1 168.4,126.9 112,126.9" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<text x="140.2" y="116" text-anchor="middle" style="fill:var(--vp-c-indigo-1);font-weight:700">2</text>
<polygon points="112,133.2 168.4,133.2 168.4,163 112,163" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<text x="140.2" y="152.1" text-anchor="middle" style="fill:var(--vp-c-indigo-1);font-weight:700">3</text>
<polygon points="195.6,133.2 252,133.2 252,163 195.6,163" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<text x="223.8" y="152.1" text-anchor="middle" style="fill:var(--vp-c-indigo-1);font-weight:700">4</text>
<polygon points="195.6,97.1 252,97.1 252,126.9 195.6,126.9" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<text x="223.8" y="116" text-anchor="middle" style="fill:var(--vp-c-indigo-1);font-weight:700">5</text>
<polygon points="195.6,61 252,61 252,90.8 195.6,90.8" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<text x="223.8" y="79.9" text-anchor="middle" style="fill:var(--vp-c-indigo-1);font-weight:700">6</text>
<polygon points="212.4,167.1 151.6,167.1 151.6,56.9 212.4,56.9" style="fill:none;stroke:var(--vp-c-text-2);stroke-width:1.2;stroke-dasharray:5 3"/>
<circle cx="172.5" cy="68.3" r="4.5" style="fill:var(--vp-c-text-1)"/>
<text x="182" y="192" text-anchor="middle" style="fill:var(--vp-c-indigo-1)">every EasyEDA pad on its own pin: green</text>
<rect x="365" y="26" width="345" height="176" rx="6" style="fill:var(--vp-c-bg-soft);stroke:var(--vp-c-divider);stroke-width:1"/>
<text x="537" y="18" text-anchor="middle" style="fill:var(--vp-c-text-1);font-weight:600">Rotation 0: KiCad's angle</text>
<polygon points="470.5,64.5 519.9,64.5 519.9,87.3 470.5,87.3" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="462.5" y="79.9" text-anchor="end" style="fill:var(--vp-c-text-2)">VSYS  1</text>
<polygon points="470.5,100.6 519.9,100.6 519.9,123.4 470.5,123.4" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="462.5" y="116" text-anchor="end" style="fill:var(--vp-c-text-2)">GND  2</text>
<polygon points="470.5,136.7 519.9,136.7 519.9,159.5 470.5,159.5" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="462.5" y="152.1" text-anchor="end" style="fill:var(--vp-c-text-2)">LED_PWR_EN  3</text>
<polygon points="554.1,136.7 603.5,136.7 603.5,159.5 554.1,159.5" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="611.5" y="152.1" style="fill:var(--vp-c-text-2)">4  LSW_CT</text>
<polygon points="554.1,100.6 603.5,100.6 603.5,123.4 554.1,123.4" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="611.5" y="116" style="fill:var(--vp-c-text-2)">5  LSW_QOD</text>
<polygon points="554.1,64.5 603.5,64.5 603.5,87.3 554.1,87.3" style="fill:#d4ad3a;fill-opacity:.55"/>
<text x="611.5" y="79.9" style="fill:var(--vp-c-text-2)">6  VLED</text>
<polygon points="550.6,133.2 607,133.2 607,163 550.6,163" style="fill:none;stroke:var(--vp-c-yellow-1);stroke-width:2"/>
<text x="578.8" y="152.1" text-anchor="middle" style="fill:var(--vp-c-yellow-1);font-weight:700">1</text>
<polygon points="550.6,97.1 607,97.1 607,126.9 550.6,126.9" style="fill:none;stroke:var(--vp-c-yellow-1);stroke-width:2"/>
<text x="578.8" y="116" text-anchor="middle" style="fill:var(--vp-c-yellow-1);font-weight:700">2</text>
<polygon points="550.6,61 607,61 607,90.8 550.6,90.8" style="fill:none;stroke:var(--vp-c-yellow-1);stroke-width:2"/>
<text x="578.8" y="79.9" text-anchor="middle" style="fill:var(--vp-c-yellow-1);font-weight:700">3</text>
<polygon points="467,61 523.4,61 523.4,90.8 467,90.8" style="fill:none;stroke:var(--vp-c-yellow-1);stroke-width:2"/>
<text x="495.2" y="79.9" text-anchor="middle" style="fill:var(--vp-c-yellow-1);font-weight:700">4</text>
<polygon points="467,97.1 523.4,97.1 523.4,126.9 467,126.9" style="fill:none;stroke:var(--vp-c-yellow-1);stroke-width:2"/>
<text x="495.2" y="116" text-anchor="middle" style="fill:var(--vp-c-yellow-1);font-weight:700">5</text>
<polygon points="467,133.2 523.4,133.2 523.4,163 467,163" style="fill:none;stroke:var(--vp-c-yellow-1);stroke-width:2"/>
<text x="495.2" y="152.1" text-anchor="middle" style="fill:var(--vp-c-yellow-1);font-weight:700">6</text>
<polygon points="506.6,56.9 567.4,56.9 567.4,167.1 506.6,167.1" style="fill:none;stroke:var(--vp-c-text-2);stroke-width:1.2;stroke-dasharray:5 3"/>
<circle cx="546.5" cy="155.7" r="4.5" style="fill:var(--vp-c-text-1)"/>
<text x="537" y="192" text-anchor="middle" style="fill:var(--vp-c-red-1)">every pad on copper, each on another pin's net: red</text>
<text x="10" y="226" style="fill:var(--vp-c-text-2)">KiCad pads filled, with their numbers and nets; JLC's pads outlined, with EasyEDA's numbers; the dot is pin 1</text>
<text x="10" y="244" style="fill:var(--vp-c-text-2)">of the placed part. A made-up land pattern, with the nets of U2 on the dial-me-spinner main board.</text>
</svg>
</figure>

*U2 on the dial-me-spinner main board. JLC's footprint for its LCSC code, C2681320, is drawn turned 180 degrees from
KiCad's. KiCad's position file gives rotation 0, which lands EasyEDA's pad 1 on KiCad's pad 4.*

That is why jlcplace compares nets, not just copper. Each EasyEDA pad must land on a KiCad pad of the net that its
own pin number has in KiCad. In the position-file check above, U2's line names every pin and the net it would
land on:

```text
RED     U2   C2681320   top     wrong-pad: EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 (/Power & link/LSW_CT, not VSYS), 2->5 (/Power & link/LSW_QOD, not GND), 3->6 (VLED, not LED_PWR_EN), 4->1 (VSYS, not /Power & link/LSW_CT), 5->2 (GND, not /Power & link/LSW_QOD), 6->3 (LED_PWR_EN, not VLED)
```

The same happens to any package that looks alike when turned: SOT-23-6 and SOT-363 at 180 degrees, SOIC and WSON at
180 (U40 and U41 on the bottom side here), square QFN, LGA and BGA packages at 90, 180 or 270 (U31, an LGA-12, here).

* **In JLC's preview:** nothing looks wrong at first. Only the pin-1 mark (a dot, or a chamfered corner of the body)
  sits in the wrong corner. Look at the pin-1 mark of every part with more than two pins.
* **jlcplace reports:** red, `wrong-pad`, with every pin and the net it would land on.

![The card of a SOT-23-6 turned 180 degrees: every EasyEDA pad on copper, outlined amber, each on the pad opposite its own number](/img/card-rotated.png)

*The same mistake in jlcplace's HTML report (synthetic example): every pad on copper, outlined amber because each
is on a KiCad pad of another number.*

## 4. A two-pad polar part is the wrong way round {#polarity}

Diodes, LEDs, tantalum and electrolytic capacitors, and buzzers have two pads, and both land on copper either way
round. Here the released main CPL is edited: the Schottky diode D21, the tantalum capacitor C32 and the resistor R1
are each turned 180 degrees.

```sh
sed -e 's/^D21,.*/D21,3.5212mm,-17.3167mm,Top,180/' -e 's/^C32,.*/C32,-3.9689mm,-17.1679mm,Top,180/' -e 's/^R1,.*/R1,-14.9087mm,-4.0490mm,Top,180/' released-main-CPL.csv > reversed-CPL.csv
jlcplace check dial-me-spinner-main.kicad_pcb reversed-CPL.csv --json reversed.json
```

```text
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs reversed-CPL.csv (184 rows), bottom reading jlc-2022
RED     C32  C7189      top     wrong-pad: EasyEDA pads land on other-numbered KiCad pads of other nets: 1->2 (GND, not /OLED display & 13 V boost/OLED_VCOMH), 2->1 (/OLED display & 13 V boost/OLED_VCOMH, not GND)
RED     D21  C8598      top     wrong-pad: EasyEDA pads land on other-numbered KiCad pads of other nets: 1->2 (/OLED display & 13 V boost/OLED_BST_SW, not +13V_OLED), 2->1 (+13V_OLED, not /OLED display & 13 V boost/OLED_BST_SW)
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 2 red, 2 yellow, 180 green, 0 verified
...
```

The diode and the capacitor are red. The resistor is green, since its two terminals are interchangeable (`jq`, a
command-line JSON tool, picks R1's result out of the JSON output):

```sh
jq -c '.results[] | select(.ref=="R1") | {ref, status, verdict, messages}' reversed.json
```

```text
{"ref":"R1","status":"renumbered","verdict":"green","messages":["the two terminals are swapped (non-polar part): 1->2, 2->1","differs from the row jlcplace writes (-14.9087, -4.0490, top, 0): 0.000 mm and 180 deg away"]}
```

Which way round is right comes from the part's correction. jlcplace looks at three things: the pad numbers, the pin
names in EasyEDA's symbol, and the "+" that EasyEDA's footprint may draw beside its positive pad. The "+" is
compared with KiCad's positive pad, which comes from the pin functions or, for KiCad's own libraries, their
convention (a diode's pad 1 is the cathode, a polarised capacitor's pad 1 is "+"). For D21 and C32 the numbers and
the "+" agree, so their corrections are green. For BZ1, the buzzer, nothing in the record (pin names, a drawn "+")
confirms the pad numbers, so its row is yellow and goes on the preview checklist. When the names and the numbers
disagree and no "+" settles it, the part is red until someone decides (see
[JLC's preview and confirm](../guide/preview)).

* **In JLC's preview:** the cathode band, or the "+" on the body, is at the wrong end. Compare it with the board's
  silkscreen.
* **jlcplace reports:** red, `wrong-pad`, with both pins swapped. Yellow, with `polarity decided by pad numbers
  only`, when nothing but the pad numbers decides it.

## 5. The bottom side reads rotation differently {#bottom}

Two separate problems meet on the bottom side.

**KiCad's angle is not JLC's.** KiCad's position file gives a bottom part's KiCad angle. The reading every open
tool uses wants 180 degrees minus KiCad's angle, plus `rot`, measured as seen from below. The position file's bottom
rows are off by the difference: BZ1 at 22 degrees needs 158, J1 at 60 needs 120, U40 at 0 needs 180. Most bottom
parts on this board are resistors and capacitors; the ones at 0 degrees come out turned 180 degrees, which does no
harm to a part whose two terminals are interchangeable. So only 9 of the 33 bottom rows are red, and the mistake is
easy to miss on a board with few bottom-side ICs.

**JLC does not document how it reads a bottom rotation.** jlcplace assumes the reading every open tool uses,
`jlc-2022`, which also matches the JLC preview data recorded in
[the z2amiller fork of kicad-jlcpcb-tools](https://github.com/z2amiller/kicad-jlcpcb-tools). Nothing offline can
prove it, so jlcplace also places every bottom row under the three other possible readings. Parts whose pins would
move under one of them go on the preview checklist, said in pins, with the most telling part first. For the released
main CPL:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb released-main-CPL.csv
```

```text
...
184 results: 0 red, 2 yellow, 182 green, 0 verified
Preview checklist (12 items, 99 parts): look at these in JLC's placement preview
...
  bottom reading        J1 (look at it first: it settles the bottom reading), J40, U10, U11, U12, U30, U32, U40 U41
...
```

J1 sits at 120 degrees and would land off its pads under every other reading: if it sits right in the preview, the
reading is the one jlcplace assumed, and every other bottom part follows. [How the check works](./how-it-works#bottom)
explains the four readings.

* **In JLC's preview:** the bottom side is shown seen from below. A part read the wrong way is turned on its pads: by
  a half turn at multiples of 90 degrees, by other angles otherwise.
* **jlcplace reports:** red rows for KiCad's angles, as above, and a `bottom reading` item on the preview checklist
  for each bottom part whose pins would move under another reading.

## 6. The CPL's origin or units are wrong {#origin-units}

Mid X and Mid Y are measured in mm from an origin: for jlcplace, the board's drill/place file origin (KiCad's aux
origin) unless you say otherwise. KiCad's position file measures from the page origin unless "Use drill/place file
origin" is ticked, and it can be written in inches without saying so. Either mistake moves every row, and jlcplace
says which one it is:

```sh
kicad-cli pcb export pos --format csv --units mm --side both --exclude-dnp -o page-origin-pos.csv dial-me-spinner-main.kicad_pcb
jlcplace check dial-me-spinner-main.kicad_pcb page-origin-pos.csv
```

```text
Wrote position data to 'page-origin-pos.csv'.
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs page-origin-pos.csv (184 rows), bottom reading jlc-2022
RED     BZ1   C910763    bottom  2 of 2 EasyEDA pads land off copper: 1, 2 (nearest KiCad pad centre up to 143.42 mm away)
...
184 results: 184 red, 0 yellow, 0 green, 0 verified
...
jlcplace: 184 of 184 rows would sit on their parts if the CPL were relative to the page origin, not the board's aux origin: pass --origin absolute (and check that the Gerbers use the same origin)
```

```sh
kicad-cli pcb export pos --format csv --units in --side both --use-drill-file-origin --exclude-dnp -o inch-pos.csv dial-me-spinner-main.kicad_pcb
jlcplace check dial-me-spinner-main.kicad_pcb inch-pos.csv
```

```text
Wrote position data to 'inch-pos.csv'.
...
184 results: 184 red, 0 yellow, 0 green, 0 verified
...
jlcplace: 184 of 184 rows would sit on their parts if the CPL's numbers were in inches: pass --units in
```

`--origin` and `--units` tell jlcplace how to read the file; they change nothing in the file JLC gets. The safer fix
is to export the CPL again in mm from the drill/place file origin, the origin your Gerbers use. See
[Troubleshooting](../guide/troubleshooting#every-row-is-red-by-about-the-same-distance).

* **In JLC's preview:** every part is off the board by the same distance, or the parts crowd towards the origin
  (inches read as mm: every coordinate 25.4 times too small).
* **jlcplace reports:** every row red, and a last line that names the fix.

## 7. Corrections guessed from names or pad boxes {#guesses}

Tools that correct a CPL without looking at JLC's footprint have to guess. Two common guesses fail on these boards.

**A rotation table keyed by footprint name.** The main board's U2 (C2681320) and the base board's U2 (C7519) both
use KiCad's `Package_TO_SOT_SMD:SOT-23-6` footprint, with identical pads. JLC's footprints for the two codes need
different turns:

```sh
jlcplace show dial-me-spinner-main.kicad_pcb --ref U2
jlcplace show dial-me-spinner-base.kicad_pcb --ref U2
```

```text
ref: U2
lcsc: C2681320
...
footprint: Package_TO_SOT_SMD:SOT-23-6
...
easyeda:
  title: SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR
...
correction:
  source: fit
  rot: 180.0
...
ref: U2
lcsc: C7519
...
footprint: Package_TO_SOT_SMD:SOT-23-6
...
easyeda:
  title: SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL
...
correction:
  source: fit
  rot: 270.0
...
```

A table keyed by "SOT-23-6" gives both parts the same rotation, so one of them is wrong. jlcplace fits each LCSC
code's own footprint and gets both.

**The centre of the pads' box.** Placing a part at the centre of the box around its pads assumes that this is where
JLC's origin is. For the JST SH connectors on these boards, all at angles that are not multiples of 90 degrees, the
centre of the box around the pads, measured along the board's axes, is 0.83 mm from EasyEDA's origin. For main J1
that centre is at (16.5058, -9.0755), for J40 at (-17.6367, 19.4649). Rows there:

```sh
sed -e 's/^J1,.*/J1,16.5058mm,-9.0755mm,Bottom,120/' -e 's/^J40,.*/J40,-17.6367mm,19.4649mm,Bottom,138/' released-main-CPL.csv > padbox-CPL.csv
jlcplace check dial-me-spinner-main.kicad_pcb padbox-CPL.csv
```

```text
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs padbox-CPL.csv (184 rows), bottom reading jlc-2022
RED     J1   C160411    bottom  12 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 0.83 mm away)
RED     J40  C160404    bottom  4 of 6 EasyEDA pads land off copper: 1, 2, 3, 4 (nearest KiCad pad centre up to 0.83 mm away)
...
184 results: 2 red, 2 yellow, 180 green, 0 verified
...
```

A guess cannot tell when it is wrong, and a tool that only guesses cannot check a CPL. Whatever tool wrote yours,
`jlcplace check` it.

* **In JLC's preview:** the same as cases 1 and 2: parts turned or shifted on their pads.
* **jlcplace reports:** the same as cases 1 and 2.

## 8. Bugs in the tool that wrote the CPL {#writer-bugs}

This is the dial-me-spinner story. Its first fab release shipped CPLs written by the project's own script, and
seven rows were wrong:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb old-main-CPL.csv
```

```text
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs old-main-CPL.csv (184 rows), bottom reading jlc-2022
RED     J1   C160411    bottom  14 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 3.40 mm away)
RED     J20  C262671    top     11 of 32 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 4.48 mm away)
RED     J40  C160404    bottom  6 of 6 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6 (nearest KiCad pad centre up to 1.40 mm away)
...
184 results: 3 red, 2 yellow, 179 green, 0 verified
...
```

```sh
jlcplace check dial-me-spinner-base.kicad_pcb old-base-CPL.csv
```

```text
jlcplace 0.1.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs old-base-CPL.csv (17 rows), bottom reading jlc-2022
RED     J1   C165948    top     15 of 16 EasyEDA pads land off copper: 2, 3, 4, A1B12, A4B9, A5, A6, A7 ... (nearest KiCad pad centre up to 2.40 mm away)
RED     J2   C160404    top     6 of 6 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6 (nearest KiCad pad centre up to 1.40 mm away)
RED     J3   C160411    top     14 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 3.40 mm away)
RED     SW1  C318884    top     4 of 4 EasyEDA pads land off copper: 1, 2, 3, 4 (nearest KiCad pad centre up to 127.26 mm away)
17 results: 4 red, 0 yellow, 13 green, 0 verified
...
```

The script did look at JLC's footprints, and it got each part's correction right: where JLC's origin lies on the
KiCad footprint as drawn in its library, and how far JLC's footprint must turn. The mistake was in the next step,
carrying that point from the library footprint onto the part placed on the board. The script did that with a
least-squares fit between the library footprint's pads and the placed footprint's pads, matched by pad number, and
it kept only one library pad per number. That works until a number repeats. Each of these connectors has two
mounting or shield pads that share one number in KiCad (MP, or S1 on the USB-C receptacle), and the tactile switch
SW1 is numbered 1, 1, 2, 2 in KiCad. The repeated numbers paired pads wrongly, and the fit moved the origin: by 1.4
to 4.5 mm on the connectors, and for SW1 to a row at (-90, 90) instead of (0, 0). Every rotation was right, and so
was every other row.

A fit by number is a reasonable method, and it failed only on parts with repeated numbers. A check that puts JLC's
pads down and looks at what they land on catches such a bug, whatever caused it. jlcplace carries a point onto the
board with the footprint's own position, angle and side, with no fit. Its fit between EasyEDA and KiCad pads does
not pair pads by a number that repeats on one side only, and also fits by geometry alone. And `jlcplace cpl` checks
the rows it writes and writes no file when one is red ([How the check works](./how-it-works#fitting)).

* **In JLC's preview:** the affected parts sit shifted on their pads, or, like SW1, somewhere else entirely.
* **jlcplace reports:** red, pads off copper, with the distance.

## What to do

1. Check the CPL you upload, whatever wrote it: `jlcplace check BOARD CPL --bom BOM`
   ([Getting started](../guide/getting-started)).
2. Fix the red rows, or let [`jlcplace cpl`](../guide/cpl) write the CPL.
3. Before you pay, work through the preview checklist in JLC's placement preview
   ([JLC's preview and confirm](../guide/preview)).
