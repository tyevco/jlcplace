# Getting started

This page installs jlcplace, checks an existing CPL, goes through the result line by line, and writes a corrected
CPL. A CPL is the pick-and-place file you upload to JLCPCB with your BOM: one row per part, giving its reference,
position, side and rotation.

The examples use the main board of [dial-me-spinner](https://github.com/tyevco/dial-me-spinner), a public KiCad
project, and two of its CPLs. `old-main-CPL.csv` is the CPL from its first fab release, written by the project's own
script. Three of its rows would have put connectors 1.4 to 4.5 mm off their pads, and four more rows were wrong on
the project's second board, one of them by 127 mm. The mistake came to light when the project's CPLs were checked by
simulating JLC's placement, as jlcplace does, after a coworker had parts come out misaligned on another board.
`released-main-CPL.csv` is the corrected CPL, from the dial-me-spinner commit that fixed those rows.

## What you need

* Python 3.10 or newer.
* The board as a `.kicad_pcb` file, from KiCad 5 to 10. KiCad does not have to be installed.
* An LCSC code for each part JLC assembles, in a footprint field (`LCSC`, `LCSC Part #`, `JLCPCB Part #` and other
  common spellings) or in the BOM you upload to JLC (see [below](#download-jlc-s-footprints) when they are only in
  the BOM).
* Network access the first time, to download JLC's footprints.

## Install

Install jlcplace from GitHub. Make a virtual environment, activate it, and install into it (on Windows, run the
`activate` script in `.venv\Scripts` instead of `source`):

::: code-group

```sh [From GitHub]
python3 -m venv .venv
source .venv/bin/activate
pip install git+https://github.com/tyevco/jlcplace
jlcplace --version
```

```sh [From a clone]
python3 -m venv .venv
source .venv/bin/activate
git clone https://github.com/tyevco/jlcplace
cd jlcplace
pip install -e .
jlcplace --version
```

:::

Either way, the output ends like this:

```text
...
Successfully installed jlcplace-0.1.0 numpy-2.5.4
...
jlcplace 0.1.0
```

numpy is the only dependency. Install from a clone when you want to run the tests or change the code (see
[Development](../about/development)).

::: tip KiCad's pcbnew is optional
jlcplace reads `.kicad_pcb` files with a built-in S-expression reader, so it needs no KiCad. When KiCad's `pcbnew`
Python module can be imported, jlcplace uses it instead. To get that, make the virtual environment on a machine with
KiCad installed and let it see KiCad's Python packages:

```sh
python3 -m venv --system-site-packages .venv
```

That works where KiCad's Python packages are installed into the system Python, as on Linux. On Windows and macOS,
KiCad brings its own Python, and only that Python can import `pcbnew`: install jlcplace into KiCad's Python instead.

The first line of every summary says which reader was used: `sexpr` or `pcbnew`. On the dial-me-spinner board the
two give the same results; only that word differs.
:::

::: details Get the example files
Both dial-me-spinner boards and their BOMs come from commit a5f70dc, which corrected the seven rows; its CPLs are
saved as `released-main-CPL.csv` and `released-base-CPL.csv`. The CPLs of the first fab release (commit 8816f0b)
are saved as `old-main-CPL.csv` and `old-base-CPL.csv`. This page and the rest of the guide run in the `dms`
directory these commands make:

```sh
mkdir dms && cd dms
B=https://raw.githubusercontent.com/tyevco/dial-me-spinner
NEW=a5f70dcb261cfebc490fd5aa2956613b593ebfa3   # the fixed CPLs
OLD=8816f0b662914a1e1aea03265aba6b4ec9f2c327   # the first fab release, 7 rows wrong
for b in main base; do
  curl -sSfLO $B/$NEW/hardware/kicad/$b/dial-me-spinner-$b.kicad_pcb
  curl -sSfLO $B/$NEW/hardware/fab/$b/dial-me-spinner-$b-BOM-JLCPCB.csv
  curl -sSfL -o released-$b-CPL.csv $B/$NEW/hardware/fab/$b/dial-me-spinner-$b-CPL-JLCPCB.csv
  curl -sSfL -o old-$b-CPL.csv $B/$OLD/hardware/fab/$b/dial-me-spinner-$b-CPL-JLCPCB.csv
done
```

This page uses only the main board.
:::

## Download JLC's footprints

JLC does not place parts with your KiCad footprints. It uses the footprint that its EasyEDA library holds for each
LCSC code, and that footprint's origin and zero angle often differ from KiCad's. jlcplace needs those footprints
(the EasyEDA records) to simulate the placement. `fetch` downloads one for every LCSC code on the board:

```sh
jlcplace fetch dial-me-spinner-main.kicad_pcb
```

```text
C1523: C0402, 2 pads (cached)
C1525: C0402, 2 pads (cached)
...
C160404: CONN-SMD_4P-P1.00_SM04B-SRSS-TB-LF-SN, 6 pads (cached)
C160411: CONN-TH_SM12B-SRSS-TB-LF-SN, 14 pads (cached)
...
C262671: FPC-SMD_AFC01-S30FCA-00, 32 pads (cached)
...
C4167968: not found (Component not found; cached)
C55109522: LED-SMD_4P-L2.2-W2.0-P1.00_WS2815C-2020-4P, 4 pads (cached)
49 codes: 48 footprints, 1 not found, 0 failed; cache .../.cache/jlcplace
```

Each line is an LCSC code, the EasyEDA package name and its number of pads. In this run every record was already in
the cache, so each line ends in `cached`. The first time, they end in `fetched`, and the download takes a while:
jlcplace waits at least 0.7 s between requests to the EasyEDA API. One code, C4167968 (the resistor R26), has no
EasyEDA footprint; the check will say what that means for R26.

The cache is `~/.cache/jlcplace` unless `JLCPLACE_CACHE` or `XDG_CACHE_HOME` says otherwise (see
[Exit codes and environment](../reference/environment)). `fetch` is the only command that uses the network. The
others read the cache and download nothing unless you pass `--online`. The records belong to the JLCEDA/EasyEDA
Official Library: jlcplace keeps them in your cache, as downloaded, and never redistributes them (see
[Data and licensing](../about/licensing)).

`fetch` takes the LCSC codes from the board's footprint fields, and with `--bom` the BOM's too: JLC places the BOM's
codes. If your codes are only in the BOM, fetch them with `jlcplace fetch board.kicad_pcb --bom board-BOM.csv`, and
pass the same BOM to `check` with `--bom` (see
[Troubleshooting](./troubleshooting#every-row-says-the-board-part-has-no-lcsc-code)).

## Check a CPL

`check` takes the board and a CPL from any tool: kicad-jlcpcb-tools, Fabrication-Toolkit, KiKit, KiCad's own
position file, or one typed by hand. Here it is the old dial-me-spinner CPL:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb old-main-CPL.csv
```

```text
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs old-main-CPL.csv (184 rows), bottom reading jlc-2022
RED     J1   C160411    bottom  14 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 3.40 mm away)
RED     J20  C262671    top     11 of 32 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 4.48 mm away)
RED     J40  C160404    bottom  6 of 6 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6 (nearest KiCad pad centre up to 1.40 mm away)
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 3 red, 2 yellow, 179 green, 0 verified
Preview checklist (12 items, 99 parts): look at these in JLC's placement preview
  uncertain correction  BZ1: yellow fit correction (auto): polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview; also the bottom reading matters (as ccw-from-top or ccw-from-top+180, pin 1 would land off copper (2 pins move); as jlc-2022+180, pins 1 and 2 would swap); rotation 158 is not a multiple of 90
  not simulated         R26
  bottom reading        U10 (look at it first: it settles the bottom reading), J1, J40, U11, U12, U30, U32, U40 U41
  odd angle             C107 of C102..C112 C114..C124 C126..C136 C138..C148 (C1525, 44 parts at 8 to 353 deg: look at C107)
                        D107 of D102..D112 D114..D124 D126..D136 D138..D148 (C55109522, 44 parts at 8 to 353 deg: look at D107)
```

Only red and yellow rows are listed. The 179 green rows are counted, not printed.

::: warning If you skip `fetch`
Without the records, nothing can be simulated. Every row is red or yellow, and the line under the header says what
is missing:

```text
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs old-main-CPL.csv (184 rows), bottom reading jlc-2022
jlcplace: no EasyEDA record in the cache .../.cache/jlcplace for 49 of the 49 codes JLC places (C1523, C1525, C1547, C1562, C1779, C2500, C7189, C7519 and 41 more), so the rows below are checked without JLC's footprints (red where a part needs one): `jlcplace fetch dial-me-spinner-main.kicad_pcb` downloads them (rate-limited), or pass --online
...
RED     BZ1   C910763    bottom  placement not simulated: no EasyEDA footprint (no EasyEDA record in the cache (`jlcplace fetch` or --online downloads it)); a polar two-pad part; the row point is the centre of its courtyard
...
184 results: 68 red, 116 yellow, 0 green, 0 verified
...
```
:::

## Read the summary

**The first line** says what was compared with what.

* `dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100)`: the board, read with the built-in
  reader. 209 parts are all its footprints, including the test points, fiducials, mounting holes and DNP parts that
  JLC does not place. `origin aux 100, 100` means the CPL's coordinates are measured from the board's drill/place file
  origin (KiCad's auxiliary origin), which sits at (100, 100) mm on this board. A CPL made from another origin
  makes almost every row red; `--origin` sets another one, and `check` says when `--origin absolute` would fit
  (see [Troubleshooting](./troubleshooting)).
* `vs old-main-CPL.csv (184 rows)`: the CPL, one result per row. 184 is also the number of parts JLC assembles here.
* `bottom reading jlc-2022`: how JLC is assumed to read the rotation of a bottom-side part. JLC does not document
  it; `jlc-2022` is the reading the open tools use. jlcplace also tries the other three (see the checklist below,
  and [How the check works](../concepts/how-it-works)).

**Each red or yellow line** has five columns: the verdict, the reference, the LCSC code, the side, and what is wrong.

* `RED J1 ... 14 of 14 EasyEDA pads land off copper`: jlcplace put the 14 pads of JLC's footprint for C160411 where
  this row tells JLC to put them, and none of them lands on a KiCad copper pad. The numbers are the EasyEDA pad
  numbers (the first eight), and the farthest of them is 3.40 mm from the nearest KiCad pad centre. J20 and J40 are
  the same mistake: the part would be placed off its pads.
* `YELLOW BZ1 ... polarity decided by pad numbers only`: for each part, jlcplace works out how JLC's footprint has to
  be turned and shifted to sit on your KiCad footprint. That is the part's **correction**, fitted from the pads of
  both footprints. The buzzer's row puts it exactly where the correction does. But the buzzer turned 180 degrees
  would land on both pads too, and nothing in EasyEDA's record (pin names, a drawn "+") says which way round is
  right, only the pad numbers. The row is probably right; look at the buzzer's polarity mark in JLC's preview.
* `YELLOW R26 ... placement not simulated`: EasyEDA has no footprint for C4167968 (the `not found` line of
  `fetch`). R26 is a resistor, a two-pad part that may be turned either way, and its row point (the Mid X, Mid Y
  the row gives) lies on its pads, so it is yellow. A polar or multi-pin part with no footprint and no stored
  correction would be red.

**The count line** adds up the verdicts:

| verdict | what it means | what to do |
|---|---|---|
| red | A pad of JLC's footprint lands off copper or on another pin's net; or the row should not be there (a DNP part, a reference not on the board, a duplicate, the wrong side); or an assembled part has no row | Fix the row before ordering |
| yellow | Probably right, but something is uncertain: no footprint to simulate, a correction the fit was unsure of, a row a little off from where the correction puts the part | Look at it in JLC's preview |
| green | Every pad lands on its own pin | Nothing |
| verified | Green, and the row matches a correction someone confirmed in JLC's preview or on an assembled board | Nothing |

`0 verified` is expected on a first run: verifications live in a correction database (`--db`, see
[The correction database](./database)). [Verdicts and statuses](../concepts/verdicts) lists every case.

**The preview checklist** lists what the simulation cannot settle offline, for you to look at in JLC's placement
preview before you order. Alike parts share one item, so 12 items cover 99 parts:

* `uncertain correction`: BZ1, for the reason above (`auto` means jlcplace fitted the correction and nobody has
  confirmed it yet). Its other reasons are folded into the same item.
* `not simulated`: R26, which has no EasyEDA footprint.
* `bottom reading`: bottom-side parts whose pins would move if JLC read bottom rotations another way. U10 comes first
  because it moves under every other reading: when it sits right in the preview, the reading is the one jlcplace
  assumed, and the other bottom parts follow. Parts written with a space between them, like `U40 U41`, share one
  item: the same part at the same rotation, so they move alike.
* `odd angle`: rows at angles that are not multiples of 90 degrees. This board has a ring of 44 capacitors and 44
  LEDs; each group is one item, naming the part to look at (C107, D107).

[JLC's preview and confirm](./preview) shows how to work through the list and record what you saw.

The exit code says the same thing as the count line: 0 when nothing is red, 2 when something is (here, 2).
[Checking a CPL](./check#exit-codes-and-fail-on) covers `--fail-on yellow` and `-q` for scripts.

## Look at the report

JLC assembles from the LCSC codes in your BOM, not the board's fields, so pass the BOM too when you have it.
`--report` writes an HTML page with a drawing of every part:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb old-main-CPL.csv \
  --bom dial-me-spinner-main-BOM-JLCPCB.csv --report report.html
```

The summary is the same as above: this BOM agrees with the board and the CPL. Where it does not, `BOM` lines say so.

Open `report.html` in a browser. The top shows the counts and both sides of the board, with JLC's pads outlined in
the colour of their row's verdict: blue for a green row, amber for a yellow one, red for a red one. The bottom is
drawn seen from below, as JLC's preview shows it. Below that come the checklist and a card for every part, where
KiCad's copper pads are filled and numbered and JLC's pads are outlined: blue on a KiCad pad of the same number,
amber on a pad of another number, red and dashed off copper.

![The top of an HTML report: the verdict counts and both sides of the board, each part's EasyEDA pads outlined in its row's colour](/img/report-overview.png)

*The top of a report, from the synthetic example board that
[`docs/examples/make_examples.py`](../examples/make_examples.py) builds, not from dial-me-spinner.*

![A part card for a red FPC connector: the EasyEDA pads, outlined in red, sit 1.3 mm from the KiCad pads they should cover](/img/card-offset.png)

*A card for the example board's connector J1 (synthetic, not dial-me-spinner's J1), whose row is shifted off its
pads: the same kind of error as dial-me-spinner's J1, J20 and J40 above. Next to the drawing, the card gives the CPL
row, the row jlcplace would write, and the correction.*

::: warning Keep the report to yourself
The report draws EasyEDA's pad outlines, which belong to the JLCEDA/EasyEDA Official Library. It is for your own
review: do not publish it, for example as a CI artifact of a public repository. The JSON, JUnit and checklist
outputs hold derived data only. See [Data and licensing](../about/licensing).
:::

## Write a CPL

`cpl` writes the CPL itself. For every part JLC assembles, it works out the correction (from a correction database
with `--db`, otherwise by fitting it), writes the rows, then reads the file back and checks it:

```sh
jlcplace cpl dial-me-spinner-main.kicad_pcb -o dial-me-spinner-main-CPL-JLCPCB.csv
```

```text
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs dial-me-spinner-main-CPL-JLCPCB.csv (184 rows), bottom reading jlc-2022
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 0 red, 2 yellow, 182 green, 0 verified
Preview checklist (12 items, 99 parts): look at these in JLC's placement preview
  uncertain correction  BZ1: yellow fit correction (auto): polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview; also the bottom reading matters (as ccw-from-top or ccw-from-top+180, pin 1 would land off copper (2 pins move); as jlc-2022+180, pins 1 and 2 would swap); rotation 158 is not a multiple of 90
  not simulated         R26
  bottom reading        J1 (look at it first: it settles the bottom reading), J40, U10, U11, U12, U30, U32, U40 U41
  odd angle             C107 of C102..C112 C114..C124 C126..C136 C138..C148 (C1525, 44 parts at 8 to 353 deg: look at C107)
                        D107 of D102..D112 D114..D124 D126..D136 D138..D148 (C55109522, 44 parts at 8 to 353 deg: look at D107)
jlcplace: wrote dial-me-spinner-main-CPL-JLCPCB.csv (184 rows)
```

The three connectors are green now. The same two yellow rows remain: BZ1's polarity and R26's missing footprint
are questions for JLC's preview, not mistakes in the rows. J1, green now, replaces U10 as the bottom part to look at
first: under any other reading, a larger share of its pads would land off copper, which is the easiest thing to
spot in the preview.

Only the three connector rows moved; every other row agrees with the old CPL to within 0.2 µm. The connectors'
rotations were right and their positions 1.4 to 4.5 mm off. The old script had each part's correction right, but
carried JLC's origin onto the board with a fit between the KiCad library footprint's pads and the placed
footprint's pads, matched by number, and each of these connectors has two mounting pads numbered MP (see
[Why parts end up misaligned](../concepts/why#writer-bugs)). The corrected CPL, `released-main-CPL.csv`, agrees with
the file `cpl` wrote to within a micron.

jlcplace writes the CPL only. Keep making the BOM and the Gerbers with the tool you use now, and pass the BOM to
`--bom`. If any row of the CPL `cpl` would write is red, it writes no file and exits with code 2 (see
[Writing a CPL](./cpl#when-a-row-is-red-no-file-is-written)).

## On your own board

Run the same commands on your own files, with your board, the CPL you upload to JLC (or uploaded with an order that
came back wrong), and the BOM that goes with it:

```sh
jlcplace fetch BOARD.kicad_pcb
jlcplace check BOARD.kicad_pcb CPL.csv --bom BOM.csv
jlcplace cpl BOARD.kicad_pcb -o NEW-CPL.csv --bom BOM.csv
```

The red rows of `check` are the parts JLC places wrong from that CPL. A CPL mistake is the same on every board of
the order: a part turned by a quarter or half turn, or shifted by about a millimetre or more, the same way each
time. Small shifts that differ from board to board come from assembly, not from the CPL, and jlcplace cannot see
them.

## Where to go next

* [Checking a CPL](./check): the BOM, other origins and units, and the report and output files.
* [Writing a CPL](./cpl): `cpl` with a correction database, and what to do when it refuses to write.
* [JLC's preview and confirm](./preview): working through the checklist and recording what you saw.
* [The correction database](./database): keep corrections and verifications in your repository.
* [Continuous integration](./ci): run the check on every push.
* [Troubleshooting](./troubleshooting): when the results do not look right.
* [Why parts end up misaligned](../concepts/why) and [How the check works](../concepts/how-it-works): the
  background.
* [Command reference](../reference/cli#jlcplace-check): every option of every command.
