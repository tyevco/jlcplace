# How the check works

For each row of a CPL, jlcplace asks one question: when JLC places its own footprint for the part as this row says,
which of your KiCad copper pads, and which nets, do its pads land on? A CPL (component placement list) is the
pick-and-place file you upload to JLCPCB: one row per part, with its reference, Mid X, Mid Y, Layer and Rotation.

This page explains the coordinate frames involved, the correction that relates JLC's footprint to yours, how a row
is simulated, how corrections are fitted, how the results become verdicts, and how the bottom side is handled. The
examples use the dial-me-spinner files from [Checking a CPL](../guide/check#the-example-files). In the code,
[`convention.py`](../../src/jlcplace/convention.py) holds the formulas, [`check.py`](../../src/jlcplace/check.py) the
simulation and [`fit.py`](../../src/jlcplace/fit.py) the fitting.

## The four frames

| frame | what it describes | origin | y axis |
|---|---|---|---|
| board | KiCad's board coordinates, in mm | KiCad's page origin | down |
| library | a footprint as drawn in its KiCad library | the footprint's anchor | down |
| ee | JLC's EasyEDA footprint as drawn | the EasyEDA footprint's origin | down |
| cpl | the CPL's Mid X and Mid Y, in mm | the CPL origin: the board's drill/place file (aux) origin, unless `--origin` names another | up |

KiCad places a part with its anchor at P and its angle θ:

```text
board = P + R(θ) · D · library
D = diag(1, -1) on the bottom side, the identity on the top
R(a) · (x, y) = (x cos a + y sin a,  -x sin a + y cos a)
```

R(a) is KiCad's rotation: counter-clockwise as seen on screen, with y pointing down. D is the flip to the bottom
side, which mirrors the footprint about its own x axis. A board point q has the CPL coordinates
(q.x - O.x, O.y - q.y), O being the CPL origin. The CPL is not mirrored on the bottom side.

<figure class="figure-scroll">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 360" role="img" aria-label="The board, library, EasyEDA and CPL frames, and where a CPL row puts a part" style="display:block;margin:0 auto;width:100%;max-width:720px;height:auto;font-family:var(--vp-font-family-base);font-size:12px">
<text x="20" y="18" style="fill:var(--vp-c-text-2)">KiCad's page origin (0, 0)</text>
<line x1="20" y1="34" x2="72" y2="34" style="stroke:var(--vp-c-text-2);stroke-width:1.2;fill:none"/><polygon points="72,34 66.5,36.3 66.5,31.7" style="fill:var(--vp-c-text-2)"/>
<line x1="20" y1="34" x2="20" y2="80" style="stroke:var(--vp-c-text-2);stroke-width:1.2;fill:none"/><polygon points="20,80 17.7,74.5 22.3,74.5" style="fill:var(--vp-c-text-2)"/>
<text x="76" y="38" style="fill:var(--vp-c-text-2)">x</text>
<text x="28" y="80" style="fill:var(--vp-c-text-2)">y</text>
<text x="42" y="60" style="fill:var(--vp-c-text-2)">board frame: KiCad's coordinates, y down</text>
<rect x="70" y="96" width="246" height="180" rx="6" style="fill:var(--vp-c-bg-soft);stroke:var(--vp-c-border);stroke-width:1.5"/>
<text x="308" y="114" text-anchor="end" style="fill:var(--vp-c-text-2)">the board</text>
<polygon points="198,146.8 202,146.8 202,153.2 198,153.2" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="206,146.8 210,146.8 210,153.2 206,153.2" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="214,146.8 218,146.8 218,153.2 214,153.2" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="222,146.8 226,146.8 226,153.2 222,153.2" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="190.8,158.4 196.4,158.4 196.4,167.2 190.8,167.2" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="227.6,158.4 233.2,158.4 233.2,167.2 227.6,167.2" style="fill:#d4ad3a;fill-opacity:.55"/>
<rect x="186" y="141" width="56" height="32" style="fill:none;stroke:var(--vp-c-text-3);stroke-width:1;stroke-dasharray:3 3"/>
<line x1="242" y1="141" x2="372" y2="60" style="stroke:var(--vp-c-text-3);stroke-width:1;stroke-dasharray:3 3"/>
<line x1="242" y1="173" x2="372" y2="286" style="stroke:var(--vp-c-text-3);stroke-width:1;stroke-dasharray:3 3"/>
<line x1="70" y1="156.4" x2="212" y2="156.4" style="stroke:var(--vp-c-purple-1);stroke-width:1;stroke-dasharray:5 3;fill:none"/>
<line x1="212" y1="156.4" x2="212" y2="276" style="stroke:var(--vp-c-purple-1);stroke-width:1;stroke-dasharray:5 3;fill:none"/>
<line x1="70" y1="262" x2="212" y2="262" style="stroke:var(--vp-c-purple-1);stroke-width:1.8"/><polygon points="212,262 206.5,264.3 206.5,259.7" style="fill:var(--vp-c-purple-1)"/>
<line x1="84" y1="276" x2="84" y2="156.4" style="stroke:var(--vp-c-purple-1);stroke-width:1.8"/><polygon points="84,156.4 86.3,161.9 81.7,161.9" style="fill:var(--vp-c-purple-1)"/>
<text x="141" y="256" text-anchor="middle" style="fill:var(--vp-c-purple-1)">Mid X</text>
<text x="90" y="220.2" style="fill:var(--vp-c-purple-1)">Mid Y</text>
<line x1="61" y1="276" x2="79" y2="276" style="stroke:var(--vp-c-purple-1);stroke-width:2"/><line x1="70" y1="267" x2="70" y2="285" style="stroke:var(--vp-c-purple-1);stroke-width:2"/>
<line x1="207" y1="156.4" x2="217" y2="156.4" style="stroke:var(--vp-c-purple-1);stroke-width:2"/><line x1="212" y1="151.4" x2="212" y2="161.4" style="stroke:var(--vp-c-purple-1);stroke-width:2"/>
<text x="62" y="298" style="fill:var(--vp-c-purple-1)">aux origin: CPL (0, 0), x right, y up</text>
<text x="62" y="314" style="fill:var(--vp-c-purple-1)">+ on the part: the row's point C</text>
<rect x="372" y="60" width="340" height="226" rx="6" style="fill:none;stroke:var(--vp-c-divider);stroke-width:1"/>
<polygon points="433.5,108 458.5,108 458.5,148 433.5,148" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="483.5,108 508.5,108 508.5,148 483.5,148" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="533.5,108 558.5,108 558.5,148 533.5,148" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="583.5,108 608.5,108 608.5,148 583.5,148" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="388.5,180.5 423.5,180.5 423.5,235.5 388.5,235.5" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="618.5,180.5 653.5,180.5 653.5,235.5 618.5,235.5" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="430.5,105 461.5,105 461.5,151 430.5,151" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<polygon points="480.5,105 511.5,105 511.5,151 480.5,151" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<polygon points="530.5,105 561.5,105 561.5,151 530.5,151" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<polygon points="580.5,105 611.5,105 611.5,151 580.5,151" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<polygon points="385.5,177.5 426.5,177.5 426.5,238.5 385.5,238.5" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<polygon points="615.5,177.5 656.5,177.5 656.5,238.5 615.5,238.5" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<line x1="446" y1="128" x2="486" y2="128" style="stroke:var(--vp-c-text-1);stroke-width:1.6"/><polygon points="486,128 480.5,130.3 480.5,125.7" style="fill:var(--vp-c-text-1)"/>
<line x1="446" y1="128" x2="446" y2="170" style="stroke:var(--vp-c-text-1);stroke-width:1.6"/><polygon points="446,170 443.7,164.5 448.3,164.5" style="fill:var(--vp-c-text-1)"/>
<circle cx="446" cy="128" r="3" style="fill:var(--vp-c-text-1)"/>
<text x="490" y="132" style="fill:var(--vp-c-text-1)">x</text>
<text x="434" y="172" style="fill:var(--vp-c-text-1)">y</text>
<text x="382" y="82" style="fill:var(--vp-c-text-1)">P: KiCad's anchor, origin of the library frame</text>
<line x1="521" y1="168" x2="521" y2="134" style="stroke:var(--vp-c-indigo-1);stroke-width:1.6"/><polygon points="521,134 523.3,139.5 518.7,139.5" style="fill:var(--vp-c-indigo-1)"/>
<line x1="521" y1="168" x2="555" y2="168" style="stroke:var(--vp-c-indigo-1);stroke-width:1.6"/><polygon points="555,168 549.5,170.3 549.5,165.7" style="fill:var(--vp-c-indigo-1)"/>
<text x="509" y="144" style="fill:var(--vp-c-indigo-1)">x</text>
<text x="551" y="183" style="fill:var(--vp-c-indigo-1)">y</text>
<line x1="513" y1="168" x2="529" y2="168" style="stroke:var(--vp-c-purple-1);stroke-width:2"/><line x1="521" y1="160" x2="521" y2="176" style="stroke:var(--vp-c-purple-1);stroke-width:2"/>
<line x1="446" y1="128" x2="515" y2="165" style="stroke:var(--vp-c-text-2);stroke-width:1.2;stroke-dasharray:4 3"/><polygon points="515,165 509,164.4 511.2,160.3" style="fill:var(--vp-c-text-2)"/>
<text x="454" y="190" style="fill:var(--vp-c-text-2)">(dx, dy)</text>
<text x="542" y="250" text-anchor="middle" style="fill:var(--vp-c-purple-1)">C: EasyEDA's origin, where the row puts it</text>
<text x="542" y="266" text-anchor="middle" style="fill:var(--vp-c-indigo-1)">ee frame: turned by rot = 90</text>
<text x="20" y="334" style="fill:var(--vp-c-text-1)">library = R(rot) · ee + (dx, dy)</text>
<text x="20" y="352" style="fill:var(--vp-c-text-1)">Mid X, Mid Y = C from the aux origin;   Rotation = θ + rot (top side)</text>
<text x="712" y="334" text-anchor="end" style="fill:var(--vp-c-text-2)">KiCad pads filled;</text>
<text x="712" y="352" text-anchor="end" style="fill:var(--vp-c-text-2)">EasyEDA pads outlined</text>
</svg>
</figure>

*A made-up connector whose EasyEDA footprint is drawn turned 90 degrees, with its origin at the centre of its pads
instead of at pin 1. The row describes JLC's footprint: its origin C and its angle.*

## The correction

A part's **correction** is the rigid map from JLC's footprint to the KiCad one, in the KiCad footprint's own frame:

```text
library = R(rot) · ee + (dx, dy)
```

`rot` is how far JLC's footprint must turn to sit on the KiCad footprint, and (dx, dy) is where EasyEDA's origin
lies in the KiCad footprint. The correction depends only on the two footprints, not on where the part sits on the
board or on which side. That is why one correction serves every part with the same LCSC code, KiCad footprint and
pin functions, and why the [correction database](../guide/database) can store it per code. (The model also allows a
mirrored correction, library = R(rot) · M · ee + (dx, dy) with M mirroring x. It means the EasyEDA footprint is the
mirror image of KiCad's, or the wrong footprint: such a correction is red and never makes a row.)

`jlcplace show` prints a part's correction and the row it leads to. J1 on the main board is on the bottom side, at
60 degrees:

```sh
jlcplace show dial-me-spinner-main.kicad_pcb --ref J1
```

```text
ref: J1
lcsc: C160411
...
footprint: Connector_JST:JST_SH_SM12B-SRSS-TB_1x12-1MP_P1.00mm_Horizontal
layer: bottom
x: 116.8875
y: 109.75
angle: 60.0
...
correction:
  source: fit
  rot: 0.0
  dx: -0.0002
  dy: -0.0626
...
rows:
  jlc-2022:
    x: 16.9416
    y: -9.7815
    rotation: 120.0
  ccw-from-top:
    x: 16.9416
    y: -9.7815
    rotation: 240.0
  ccw-from-top+180:
    x: 16.9416
    y: -9.7815
    rotation: 60.0
  jlc-2022+180:
    x: 16.9416
    y: -9.7815
    rotation: 300.0
landings: 14
  - ee="1", kicad="1", net="VSYS", dist=0.0003
...
```

The four rows are the same part under the four ways JLC might read a bottom-side rotation; the first is the one
jlcplace writes ([below](#bottom)).

## From a correction to a row

The row puts EasyEDA's origin where the correction says it lies on the board, and turns JLC's footprint by the angle
that makes it sit like the KiCad one:

```text
C = P + R(θ) · D · (dx, dy)        EasyEDA's origin on the board
Mid X = C.x - O.x,  Mid Y = O.y - C.y
Rotation = θ + rot                 top side
Rotation = 180 - θ + rot           bottom side (the jlc-2022 reading)
```

The rotation is brought into [0, 360). For J1: θ = 60, rot = 0, so the rotation is 180 - 60 + 0 = 120.
P = (116.8875, 109.75), and R(60) · D · (-0.0002, -0.0626) = (0.0541, 0.0315), so C = (116.9416, 109.7815). With the
aux origin at (100, 100), Mid X = 16.9416 and Mid Y = 100 - 109.7815 = -9.7815: the row `jlcplace cpl` writes.

Why these formulas: on the top side, board = P + R(θ) · (R(rot) · ee + (dx, dy)) = C + R(θ + rot) · ee, which is JLC
turning its footprint by θ + rot about C. On the bottom side, R(θ) · D · R(rot) = R(-A) · Mx with A = 180 - θ + rot
and Mx = diag(-1, 1): JLC's footprint mirrored and turned, as seen from below. `cpl` writes Mid X and Mid Y to
4 decimals in mm, and the rotation to 0.001 degrees.

## Simulating a row

The check runs the other way: it takes the row as written, whoever wrote it, and places JLC's footprint as JLC
would. For a row (Mid X, Mid Y, Layer, A), with C = (O.x + Mid X, O.y - Mid Y), each EasyEDA pad centre e goes to

```text
q = C + R(A) · e                 top side
q = C + R(-A) · Mx · e           bottom side, jlc-2022 reading
l = D · R(-θ) · (q - P)          the same point in the KiCad footprint's frame
```

and lands on the KiCad copper pad that contains l, or off copper. A point within 0.001 mm of a pad's edge is on it.
Where pads are drawn on top of each other (a USB-C receptacle's A1 and B12), the pad with the EasyEDA pad's own
number wins, else the nearest centre. Only pad centres are placed: a centre on its pad is what a pick-and-place
machine gets right or wrong. Pads on EasyEDA's bottom layer (the far side of a part) are skipped.

The JSON output lists every landing. U2 in KiCad's position file (see
[Why parts end up misaligned](./why#symmetric)), picked out with `jq`, a command-line JSON tool:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb dial-me-spinner-main-pos.csv -q --json pos.json
jq -c '.results[] | select(.ref=="U2") | .landings[]' pos.json
```

```text
{"ee":"3","kicad":"6","net":"VLED","dist":0.2125}
{"ee":"2","kicad":"5","net":"/Power & link/LSW_QOD","dist":0.2125}
{"ee":"1","kicad":"4","net":"/Power & link/LSW_CT","dist":0.2125}
{"ee":"6","kicad":"3","net":"LED_PWR_EN","dist":0.2125}
{"ee":"5","kicad":"2","net":"GND","dist":0.2125}
{"ee":"4","kicad":"1","net":"VSYS","dist":0.2125}
```

`ee` is the EasyEDA pad number, `kicad` the KiCad pad it landed on (`null` off copper), `net` that pad's net, and
`dist` the distance from the EasyEDA pad centre to the nearest KiCad pad centre, in mm.

### Nets, not numbers

What decides a row is the net each EasyEDA pad lands on. Every EasyEDA pad should land on a KiCad pad of the net its
own number has in KiCad (a pad with no net counts as its own net). When an EasyEDA pad lands on a KiCad pad of
another number, that is:

* **harmless** when its number does not exist in KiCad (EasyEDA's mounting pads, or `A1B12` for two pads KiCad draws
  on top of each other), when the pad it lands on has the net its own number has, or when a non-polar two-terminal
  part (a resistor, a capacitor, a switch numbered 1, 1, 2, 2) has its two terminals swapped as a whole;
* **accepted** when the part's correction gives every EasyEDA pad the net the row gives it: the numbering difference
  is then the correction's to vouch for, and the row takes the correction's verdict;
* **wrong** otherwise: red, status `wrong-pad`. U2 above is this case.

The pad numbers *decide* a row when at least 80 % of the EasyEDA pads whose number KiCad also has, and at least two
of them, land on that number or its net. When they do not (KiCad's A and K against EasyEDA's 1 and 2, or BGA balls
lettered on one side and numbered on the other), only the correction can vouch for the row. The correction also
overrules the numbers the other way: a row that lands every pad on its own number, but on other nets than the
correction gives them, is red against a verified correction, for a polar two-terminal part, or when the numbers do
not decide the row; against a fitted correction it is yellow.

Whether a two-terminal part may be turned round is decided by one rule, the same for the fit and the check: its
reference prefix (R, C, L, FB, F, SW, ...), its KiCad footprint name and pin functions, and its EasyEDA record's
title, part categories (diode, LED, tantalum, electrolytic, TVS, ...) and pin names. A part nothing says is non-polar
is treated as polar.

### Without the EasyEDA records

A part with a stored correction can be checked without its EasyEDA record, as a CI job checks from a committed
[correction database](../guide/database). Its KiCad copper pads, taken back through the correction,
e = R(-rot) · ((x, y) - (dx, dy)), stand in for EasyEDA's pads. That simulation is only as good as the stored
correction, so the row's verdict is capped by the correction's, and the row says so (see
[Verdicts and statuses](./verdicts#capped)).

## Fitting a correction {#fitting}

Where the database has no entry for a part, jlcplace fits the correction from the pads of the two footprints, in
the KiCad footprint's frame, using only copper the part is soldered to. Pads that KiCad draws on top of each other
count as one land.

1. **By number.** A least-squares fit (Kabsch) over EasyEDA and KiCad pads of the same number, for the numbers that
   occur once on each side. Numbers repeated equally often on both sides are paired by position once the others have
   fixed the map. A number repeated on one side only (SOT-223's pin 2 and tab 2; two KiCad pads numbered MP) has no
   defined partner and is left out. Repeated numbers also broke the old dial-me-spinner script, in a pad-number fit
   of its own between the library and the placed footprint, and put its rows 1.4 to 127 mm off
   ([Why parts end up misaligned](./why#writer-bugs)).
2. **By geometry.** Ignoring numbers: rotations of 0, 90, 180 and 270 degrees and those that line up the
   farthest-apart pads, normal and mirrored, with translations voted by how many pads they land, then refined. Every
   placement that lands all of JLC's pads on KiCad copper is kept as a candidate.
3. **Names.** EasyEDA's symbol pin names against KiCad's pin functions, normalised (EP, PAD and TAB match GND; A
   matches ANODE; VO matches VOUT; ...). Names count only where they tell candidates apart.
4. **Nets.** Two candidates that land every pad on the same nets are equivalent; so are the two ways round of a
   non-polar two-terminal part.
5. **The "+" mark.** For a polar two-terminal part, the "+" that EasyEDA's footprint draws beside a pad is compared
   with KiCad's positive pad (from the pin functions, else KiCad's library convention: a diode's pad 1 is the
   cathode, a polarised capacitor's pad 1 is "+").

The fit then picks a candidate and gives it a verdict with reasons. Angles within 0.5 degrees of a multiple of 90
are snapped. U2 shows the candidates the fit weighed:

```sh
jlcplace show dial-me-spinner-main.kicad_pcb --ref U2
```

```text
...
correction:
  source: fit
  rot: 180.0
  dx: 0.0
  dy: 0.0
  mirror: false
  method: number
  status: auto
  verdict: yellow
  rms: 0.2125
  matched: 6
  reasons: ["rot 0 also lands every pad, on other nets; ruled out by pad numbers", "rms 0.213 mm: the land patterns differ (every pad lands)"]
  alternatives: 3
    - rot=0.0, dx=0.0, dy=0.0, mirror=true, matched=2, conflicts=4, names=0, equivalent=false, nets_changed=["3", "1", "6", "4"]
    - rot=0.0, dx=0.0, dy=0.0, mirror=false, matched=0, conflicts=6, names=0, equivalent=false, nets_changed=["3", "2", "1", "6", "5", "4"]
    - rot=180.0, dx=0.0, dy=0.0, mirror=true, matched=0, conflicts=6, names=0, equivalent=false, nets_changed=["3", "2", "1", "6", "5", "4"]
...
```

Four placements land all six pads: rot 180 (all six numbers match), rot 0 (every pad on another net), and two mirror
images. The numbers rule out rot 0, and mirror images are never used. The verdict is yellow only because the pad
centres of the two land patterns are 0.21 mm apart; every pad lands, and the check of a row written from this
correction is green.

| fit verdict | when | example reason |
|---|---|---|
| green | the fit by number lands every pad with no numbering conflict, the names agree or say nothing, and every other candidate is equivalent or ruled out by the numbers (for a polar two-terminal part, also by the names or the "+") | `fits by pad numbers: 6/6 matched, rms 0.012 mm` |
| yellow | the names pick another candidate than the numbers; a polarity only the pad numbers decide; the land patterns differ by more than 0.1 mm and a quarter of the smallest pad; under 80 % of the pads matched by number; an angle that is not a multiple of 90; KiCad pads that nothing lands on; or the pads do not fix the body's orientation | `polarity decided by pad numbers only (...)` |
| red | nothing lands every pad; only a mirror image lands; a numbering conflict no pin names vouch for; candidates nothing tells apart; a polarity the names and numbers dispute with no "+" to settle it | `numbering conflict: EE 2 -> KiCad 1 (...)` |

When a yellow fit is about *which* placement was chosen (a polarity, names against numbers, an orientation the pads do
not fix), a row that follows it is yellow too, and the part goes on the preview checklist. A yellow fit that is only
about the land patterns leaves the row green. A red fit needs a person: a look in JLC's preview, or the datasheet,
recorded with [`jlcplace confirm`](../guide/preview).

A database entry is used before any fit. Entries are keyed by the LCSC code, a hash of EasyEDA's pads and a hash of
KiCad's pads and pin functions, so a decision for one footprint never stands for another. When EasyEDA changes the
footprint, the part is fitted again and compared with the stored correction (see
[The correction database](../guide/database)).

## From landings to a verdict

In short:

* a pad off copper, or on another pin's net: **red**;
* every pad on its own pin, harmless differences allowed: **green**;
* every pad on its own pin, and the row matches a correction verified in JLC's preview or on an assembled board:
  **verified**;
* probably right, but look: **yellow**. For example, the row is more than 0.15 mm or 2 degrees from where a known
  correction puts the part, the fit was unsure of its choice, or KiCad pads have nothing on them.

[Verdicts and statuses](./verdicts) lists every case and what to do about it.

## The bottom side {#bottom}

JLC documents the CPL's columns, but not how it reads the rotation of a bottom-side part. A bottom part is mirrored,
and any rigid placement of a mirrored footprint is R(φ) · Mx for some angle φ. So the possible readings of a bottom
rotation A are φ = σ · A + κ, with σ = ±1 and κ = 0 or 180. jlcplace knows all four:

| reading | Rotation written for KiCad angle θ | JLC puts EasyEDA point e at | the part, compared with jlc-2022 |
|---|---|---|---|
| `jlc-2022` (assumed) | 180 - θ + rot | C + R(-A) · Mx · e | as is |
| `ccw-from-top` | θ - rot - 180 | C + R(A) · Mx · e | turned 2A |
| `ccw-from-top+180` | θ - rot | C + R(A + 180) · Mx · e | turned 2A + 180 |
| `jlc-2022+180` | rot - θ | C + R(180 - A) · Mx · e | turned 180 |

In words: under `jlc-2022`, JLC turns its footprint counter-clockwise by A as seen from below; under `ccw-from-top`,
it mirrors the footprint and turns it counter-clockwise by A as seen from the top; the `+180` readings add a half
turn. `jlc-2022` is the reading every open tool uses (kicad-jlcpcb-tools, Fabrication-Toolkit, KiKit, KiBot,
JLCKicadTools), and it matches the JLC preview data recorded in
[the z2amiller fork of kicad-jlcpcb-tools](https://github.com/z2amiller/kicad-jlcpcb-tools). It is still a
well-supported assumption, not a documented fact. J1's four rows above follow this table: 120, 240, 60 and 300
degrees.

<figure class="figure-scroll">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 250" role="img" aria-label="One bottom-side row placed under each of the four bottom-side readings" style="display:block;margin:0 auto;width:100%;max-width:720px;height:auto;font-family:var(--vp-font-family-base);font-size:12px">
<rect x="10" y="22" width="170" height="176" rx="6" style="fill:var(--vp-c-bg-soft);stroke:var(--vp-c-divider);stroke-width:1"/>
<text x="95" y="16" text-anchor="middle" style="fill:var(--vp-c-text-1);font-weight:600">jlc-2022</text>
<polygon points="39.3,92.6 68.6,75.7 76.4,89.2 47.1,106.1" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="64,135.4 93.3,118.5 101.1,132 71.8,148.9" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="101.2,85.4 130.5,68.5 138.3,82 109,98.9" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="41.1,93.1 68.1,77.5 74.6,88.7 47.6,104.3" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<text x="57.9" y="94.9" text-anchor="middle" style="fill:var(--vp-c-indigo-1);font-weight:600;font-size:11px">1</text>
<polygon points="65.8,135.9 92.8,120.3 99.3,131.5 72.3,147.1" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<text x="82.6" y="137.7" text-anchor="middle" style="fill:var(--vp-c-indigo-1);font-weight:600;font-size:11px">2</text>
<polygon points="103,85.9 130,70.3 136.5,81.5 109.5,97.1" style="fill:none;stroke:var(--vp-c-indigo-1);stroke-width:2"/>
<text x="119.8" y="87.7" text-anchor="middle" style="fill:var(--vp-c-indigo-1);font-weight:600;font-size:11px">3</text>
<circle cx="77" cy="79.9" r="3.5" style="fill:var(--vp-c-text-1)"/>
<line x1="89" y1="98" x2="101" y2="98" style="stroke:var(--vp-c-purple-1);stroke-width:2"/><line x1="95" y1="92" x2="95" y2="104" style="stroke:var(--vp-c-purple-1);stroke-width:2"/>
<text x="95" y="174" text-anchor="middle" style="fill:var(--vp-c-text-2)">assumed by jlcplace</text>
<text x="95" y="189" text-anchor="middle" style="fill:var(--vp-c-indigo-1)">every pad on its own pin</text>
<rect x="187" y="22" width="170" height="176" rx="6" style="fill:var(--vp-c-bg-soft);stroke:var(--vp-c-divider);stroke-width:1"/>
<text x="272" y="16" text-anchor="middle" style="fill:var(--vp-c-text-1);font-weight:600">ccw-from-top</text>
<polygon points="216.3,92.6 245.6,75.7 253.4,89.2 224.1,106.1" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="241,135.4 270.3,118.5 278.1,132 248.8,148.9" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="278.2,85.4 307.5,68.5 315.3,82 286,98.9" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="249.3,48.9 276.3,64.5 269.8,75.7 242.8,60.1" style="fill:none;stroke:var(--vp-c-red-1);stroke-width:2;stroke-dasharray:4 3"/>
<text x="259.6" y="66.3" text-anchor="middle" style="fill:var(--vp-c-red-1);font-weight:600;font-size:11px">1</text>
<polygon points="224.6,91.7 251.6,107.3 245.1,118.5 218.1,102.9" style="fill:none;stroke:var(--vp-c-red-1);stroke-width:2;stroke-dasharray:4 3"/>
<text x="234.9" y="109.1" text-anchor="middle" style="fill:var(--vp-c-red-1);font-weight:600;font-size:11px">2</text>
<polygon points="286.5,98.9 313.5,114.5 307,125.7 280,110.1" style="fill:none;stroke:var(--vp-c-red-1);stroke-width:2;stroke-dasharray:4 3"/>
<text x="296.8" y="116.3" text-anchor="middle" style="fill:var(--vp-c-red-1);font-weight:600;font-size:11px">3</text>
<circle cx="278.7" cy="73.4" r="3.5" style="fill:var(--vp-c-text-1)"/>
<line x1="266" y1="98" x2="278" y2="98" style="stroke:var(--vp-c-purple-1);stroke-width:2"/><line x1="272" y1="92" x2="272" y2="104" style="stroke:var(--vp-c-purple-1);stroke-width:2"/>
<text x="272" y="174" text-anchor="middle" style="fill:var(--vp-c-text-2)">turned 2A = 60°</text>
<text x="272" y="189" text-anchor="middle" style="fill:var(--vp-c-red-1)">3 off copper</text>
<rect x="364" y="22" width="170" height="176" rx="6" style="fill:var(--vp-c-bg-soft);stroke:var(--vp-c-divider);stroke-width:1"/>
<text x="449" y="16" text-anchor="middle" style="fill:var(--vp-c-text-1);font-weight:600">ccw-from-top+180</text>
<polygon points="393.3,92.6 422.6,75.7 430.4,89.2 401.1,106.1" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="418,135.4 447.3,118.5 455.1,132 425.8,148.9" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="455.2,85.4 484.5,68.5 492.3,82 463,98.9" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="471.7,147.1 444.7,131.5 451.2,120.3 478.2,135.9" style="fill:none;stroke:var(--vp-c-red-1);stroke-width:2;stroke-dasharray:4 3"/>
<text x="461.4" y="137.7" text-anchor="middle" style="fill:var(--vp-c-red-1);font-weight:600;font-size:11px">1</text>
<polygon points="496.4,104.3 469.4,88.7 475.9,77.5 502.9,93.1" style="fill:none;stroke:var(--vp-c-red-1);stroke-width:2;stroke-dasharray:4 3"/>
<text x="486.1" y="94.9" text-anchor="middle" style="fill:var(--vp-c-red-1);font-weight:600;font-size:11px">2</text>
<polygon points="434.5,97.1 407.5,81.5 414,70.3 441,85.9" style="fill:none;stroke:var(--vp-c-yellow-1);stroke-width:2"/>
<text x="424.2" y="87.7" text-anchor="middle" style="fill:var(--vp-c-yellow-1);font-weight:600;font-size:11px">3</text>
<circle cx="442.3" cy="122.6" r="3.5" style="fill:var(--vp-c-text-1)"/>
<line x1="443" y1="98" x2="455" y2="98" style="stroke:var(--vp-c-purple-1);stroke-width:2"/><line x1="449" y1="92" x2="449" y2="104" style="stroke:var(--vp-c-purple-1);stroke-width:2"/>
<text x="449" y="174" text-anchor="middle" style="fill:var(--vp-c-text-2)">turned 2A + 180 = 240°</text>
<text x="449" y="189" text-anchor="middle" style="fill:var(--vp-c-red-1)">2 off, 1 on another pin</text>
<rect x="541" y="22" width="170" height="176" rx="6" style="fill:var(--vp-c-bg-soft);stroke:var(--vp-c-divider);stroke-width:1"/>
<text x="626" y="16" text-anchor="middle" style="fill:var(--vp-c-text-1);font-weight:600">jlc-2022+180</text>
<polygon points="570.3,92.6 599.6,75.7 607.4,89.2 578.1,106.1" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="595,135.4 624.3,118.5 632.1,132 602.8,148.9" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="632.2,85.4 661.5,68.5 669.3,82 640,98.9" style="fill:#d4ad3a;fill-opacity:.55"/>
<polygon points="679.9,102.9 652.9,118.5 646.4,107.3 673.4,91.7" style="fill:none;stroke:var(--vp-c-red-1);stroke-width:2;stroke-dasharray:4 3"/>
<text x="663.1" y="109.1" text-anchor="middle" style="fill:var(--vp-c-red-1);font-weight:600;font-size:11px">1</text>
<polygon points="655.2,60.1 628.2,75.7 621.7,64.5 648.7,48.9" style="fill:none;stroke:var(--vp-c-red-1);stroke-width:2;stroke-dasharray:4 3"/>
<text x="638.4" y="66.3" text-anchor="middle" style="fill:var(--vp-c-red-1);font-weight:600;font-size:11px">2</text>
<polygon points="618,110.1 591,125.7 584.5,114.5 611.5,98.9" style="fill:none;stroke:var(--vp-c-red-1);stroke-width:2;stroke-dasharray:4 3"/>
<text x="601.2" y="116.3" text-anchor="middle" style="fill:var(--vp-c-red-1);font-weight:600;font-size:11px">3</text>
<circle cx="644" cy="116.1" r="3.5" style="fill:var(--vp-c-text-1)"/>
<line x1="620" y1="98" x2="632" y2="98" style="stroke:var(--vp-c-purple-1);stroke-width:2"/><line x1="626" y1="92" x2="626" y2="104" style="stroke:var(--vp-c-purple-1);stroke-width:2"/>
<text x="626" y="174" text-anchor="middle" style="fill:var(--vp-c-text-2)">turned 180°</text>
<text x="626" y="189" text-anchor="middle" style="fill:var(--vp-c-red-1)">3 off copper</text>
<text x="10" y="222" style="fill:var(--vp-c-text-2)">One bottom-side row, rotation A = 30, seen from below. KiCad pads filled; JLC's pads outlined where each reading</text>
<text x="10" y="240" style="fill:var(--vp-c-text-2)">puts them. + is the row's point; the dot marks pin 1 of the placed part.</text>
</svg>
</figure>

*Relative to `jlc-2022`, the other readings turn a bottom part about its row point by 2A, 2A + 180 and 180 degrees.
At an angle that is not a multiple of 90, each of them moves the part off its pads.*

### The convention-sensitivity check

Nothing offline can prove which reading the factory uses, so every bottom row is placed again under the three other
readings. When a pin would land on another net, or a pad would leave copper, under any of them, the row is
*convention-sensitive*. Its verdict does not change, but the part goes on the preview checklist under "bottom
reading", said in pins:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb released-main-CPL.csv -q --json released.json
jq -c '.checklist[] | select(.category=="bottom reading") | {ref, rotation, what}' released.json
```

```text
{"ref":"J1","rotation":120.0,"what":"look at this one first: it moves under every other reading, so it shows how JLC reads the bottom side; when it sits right, the other bottom parts follow. If JLC read the bottom rotation another way: as ccw-from-top or ccw-from-top+180 or jlc-2022+180, pin 1 would land off copper (14 pins move); also rotation 120 is not a multiple of 90"}
...
{"ref":"U11","rotation":90.0,"what":"if JLC read the bottom rotation another way: as ccw-from-top or jlc-2022+180, pin 1 would sit on pad 5 (8 pins move)"}
...
{"ref":"U40","rotation":180.0,"what":"if JLC read the bottom rotation another way: as ccw-from-top+180 or jlc-2022+180, pin 1 would sit on pad 5 (8 pins move) (2 parts of one code at one rotation: they move alike)"}
```

The table explains the pattern. At a multiple of 90 degrees, 2A is 0 or 180, so each other reading either agrees
with `jlc-2022` or turns the part half a turn: U11 at 90 degrees moves under `ccw-from-top` and `jlc-2022+180`, U40
at 180 under `ccw-from-top+180` and `jlc-2022+180`. A part that looks the same turned half a turn, such as a bottom-side resistor, cannot tell the readings
apart and is not listed. A part at an odd angle, such as J1 at 120 degrees, moves under all three and lands off its
pads, which is the easiest thing to see in JLC's preview. So the checklist puts first a green part that moves under
every other reading, preferring the one with the most pads off copper: if it sits right in the preview, JLC reads
the bottom side as jlcplace assumed, and every other bottom part follows.

If JLC ever changes how it reads bottom rotations, `--reading` makes another reading the one the verdicts use (see
[Checking a CPL](../guide/check#reading-the-bottom-side-rotation)).

## What the check does not see

* Only pad centres are placed. A row a little off, with every centre still on its own pad, passes unless a known
  correction says where the part belongs.
* The check uses the EasyEDA records in your cache. If EasyEDA changes a footprint later, `jlcplace fetch --refresh`
  downloads it again, and database entries made with the old footprint are fitted again.
* Parts with no EasyEDA record and no stored correction are not simulated.

[Limits](../about/limits) lists the rest.
