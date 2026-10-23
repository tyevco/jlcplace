# Writing a CPL

`jlcplace cpl` writes the CPL for a KiCad board: the pick-and-place file you upload to JLCPCB with the BOM, one row
per part with its reference, the X and Y of its centre, its side and its rotation. It works out each row from JLC's
own footprint for the part, checks the file it wrote exactly as [`jlcplace check`](./check) would, and keeps the file
only when no row is red.

The examples use the two dial-me-spinner boards and their BOMs; [Checking a CPL](./check#the-example-files) shows how
to download them and the EasyEDA records.

## Write it

```sh
jlcplace cpl dial-me-spinner-main.kicad_pcb -o dial-me-spinner-main-CPL-JLCPCB.csv
```

```text
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs dial-me-spinner-main-CPL-JLCPCB.csv (184 rows), bottom reading jlc-2022
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 0 red, 2 yellow, 182 green, 0 verified
Preview checklist (12 items, 99 parts): look at these in JLC's placement preview
...
jlcplace: wrote dial-me-spinner-main-CPL-JLCPCB.csv (184 rows)
```

The summary is the summary of a check of the file just written; [Checking a CPL](./check#reading-the-summary)
explains every line. Nothing is red, so the file was kept. The two yellow rows are not mistakes in the file: BZ1's
polarity and R26's footprint are questions for JLC's placement preview (see [JLC's preview and confirm](./preview)).

jlcplace writes the CPL only. It checks a BOM (`--bom`) but does not write one, and it does not touch the Gerbers:
keep making those with the tool you use now, and pass the BOM to `--bom`.

`cpl` takes the same options as `check` for the board (`--origin`, `--lcsc-field`, `--reader`), the records
(`--cache`, `--online`), the database (`--db`), the bottom reading (`--reading`) and the outputs (`--report`, `--json`,
`--junit`, `--checklist`, `--github-annotations`, `--fail-on`, `-q`). `-o` must name a file:

```sh
jlcplace cpl dial-me-spinner-main.kicad_pcb -o -
```

```text
jlcplace: -o -: cpl writes a file; give it a name (--json - prints the results)
```

## Where each row comes from

For each part, jlcplace needs its **correction**: how JLC's footprint (the EasyEDA footprint for the part's LCSC code)
has to be turned and shifted to sit on the KiCad footprint. It takes the first of these that exists:

1. **An entry in the correction database** (`--db DIR`) for the part's LCSC code, its EasyEDA footprint and its KiCad
   pads. Entries are stored by `jlcplace fit --db` and `jlcplace confirm`; a correction someone verified in JLC's
   preview or on an assembled board, or a decision someone recorded, comes from here (see
   [The correction database](./database)).
2. **A fit** from the EasyEDA record in the cache. jlcplace matches the pads of the two footprints, by pad number and
   by geometry, with pin names, nets and the "+" EasyEDA draws beside a positive pad as further evidence, and gives
   the fit its own verdict (see [How the check works](../concepts/how-it-works)).
3. **KiCad's angle, at the centre of the part's body**, when there is neither: the part has no EasyEDA footprint
   (EasyEDA does not know the code, like R26's C4167968) or its record is not in the cache. JLC places a part by its
   centre, so the row point is the centre of the footprint's courtyard (else of its fabrication-layer drawing). For a
   centred part such as a resistor that is KiCad's own position; KiCad's anchor of a through-hole connector is usually
   pin 1, and on a right-angle coaxial connector the courtyard's centre can be over 10 mm from it. Without either drawing, the row is KiCad's own
   position, and the correction's reason says when that anchor is a pin. The check says "not simulated": yellow for a
   two-pad non-polar part whose row point is on its pads, red otherwise, with where the row point lies ("the row point
   is the centre of its courtyard"). Look at such a part in JLC's preview.

From the correction, the row is:

* **Mid X, Mid Y**: where the EasyEDA footprint's origin lands on the board, measured from the CPL origin with y up.
* **Rotation**: KiCad's angle plus the correction's rotation on the top side; 180 minus KiCad's angle, plus the
  correction's rotation, on the bottom side (the `jlc-2022` reading, the one every open tool uses; `--reading`
  writes the rows for another one).

Three rows of the main board, as KiCad's position file has them and as `cpl` writes them (the position file is the
one exported in [Checking a CPL](./check#a-cpl-from-kicad-s-position-file)):

```sh
grep -E '^"?(J20|R26|U2)"?,' dial-me-spinner-main-pos.csv dial-me-spinner-main-CPL-JLCPCB.csv
```

```text
dial-me-spinner-main-pos.csv:"J20","OLED_ZIF30","JUSHUO_AFC01-S30FCA-00_1x30-1MP_P0.50mm_Horizontal",0.000000,-9.500000,0.000000,top
dial-me-spinner-main-pos.csv:"R26","499k 0.5%","R_0402_1005Metric",7.917000,-20.526400,0.000000,top
dial-me-spinner-main-pos.csv:"U2","TPS22917DBVR","SOT-23-6",14.605900,4.070900,0.000000,top
dial-me-spinner-main-CPL-JLCPCB.csv:J20,-0.0009mm,-10.8405mm,Top,0
dial-me-spinner-main-CPL-JLCPCB.csv:R26,7.9170mm,-20.5264mm,Top,0
dial-me-spinner-main-CPL-JLCPCB.csv:U2,14.6059mm,4.0709mm,Top,180
```

The EasyEDA origin of the FPC connector J20 is 1.34 mm below KiCad's anchor. The SOT-23-6 U2 is drawn turned 180
degrees in EasyEDA. R26 has no EasyEDA footprint, so its row is KiCad's own.

## What is written

`cat -A` (GNU cat; `cat -e` on macOS) shows the line ends:

```sh
head -3 dial-me-spinner-main-CPL-JLCPCB.csv | cat -A
```

```text
Designator,Mid X,Mid Y,Layer,Rotation^M$
BZ1,20.3979mm,8.2412mm,Bottom,158^M$
C1,16.9889mm,-18.7280mm,Bottom,180^M$
```

* **Format**: JLC's own column names, comma-separated, with Windows line ends (the `^M$` above). Positions in mm with
  four decimals and the unit written out, the side as `Top` or `Bottom`, the rotation in degrees from 0 to 360 (up to
  three decimals). Rows are sorted by reference, R2 before R10.
* **Rows**: one for every part JLC assembles that has an LCSC code: placed (not DNP, not excluded from position files,
  not board-only, KiCad's "Not in schematic" attribute) and not excluded from the BOM. On this board that is 184 of its 209 footprints. With `--bom`, the
  BOM's code decides the correction, since JLC places the part the BOM names, and a part the BOM lists gets its row
  though the board excludes it from its position files or its BOM (not a DNP or board-only part).
* **Origin**: the board's drill/place file origin (KiCad's auxiliary origin, at (100, 100) mm on this board), unless
  `--origin` names another one. The CPL's coordinates have to match your Gerbers', so plot the Gerbers from the same
  origin (KiCad's "Use drill/place file origin" plot option).
* **Bottom side**: C1 sits on the bottom at KiCad angle 0, and its correction does not turn it, so its rotation is
  180 - 0 + 0 = 180.

The file is written to a temporary name and renamed into place, so a failed write never leaves half a CPL.

## When a row is red, no file is written

`cpl` checks the rows before it keeps them. If any is red, it writes nothing, exits with code 2, and says why. On the
base board:

```sh
jlcplace cpl dial-me-spinner-base.kicad_pcb -o dial-me-spinner-base-CPL-JLCPCB.csv
```

```text
jlcplace 0.1.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs the rows jlcplace would write (17 rows), bottom reading jlc-2022
RED     SW1  C318884    top     renumbered: the pads land as the correction (auto) puts them, but it is red, so nothing vouches for this numbering: 2->1
             correction (fit, auto, red): numbering conflict: EE 2 -> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview
17 results: 1 red, 0 yellow, 16 green, 0 verified
Preview checklist (3 items): look at these in JLC's placement preview
  uncertain correction  SW1: red fit correction (auto): numbering conflict: EE 2 -> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview; only 1 of 2 EasyEDA pads with a number KiCad has matched by number
  odd angle             J2 (310), J3 (30)
jlcplace: dial-me-spinner-base-CPL-JLCPCB.csv not written: the rows above are red (--force writes it anyway)
```

Since no file was written, the first line says "the rows jlcplace would write", and so do the reports of the run.
`(auto)` and `fit, auto, red` say that the correction was fitted by jlcplace and nobody has confirmed it. With `-q`,
the note names the red parts:

```sh
jlcplace cpl dial-me-spinner-base.kicad_pcb -o dial-me-spinner-base-CPL-JLCPCB.csv -q
echo "exit code $?"
```

```text
jlcplace: dial-me-spinner-base-CPL-JLCPCB.csv not written: SW1 is red (--force writes it anyway)
exit code 2
```

When a CPL already exists at that path, it is left as it was, and the note ends with `; the existing
dial-me-spinner-base-CPL-JLCPCB.csv is left unchanged`.

`--force` writes the file anyway, red rows included. The exit code stays 2:

```sh
jlcplace cpl dial-me-spinner-base.kicad_pcb -o forced-base-CPL.csv --force -q
echo "exit code $?"
grep SW1 forced-base-CPL.csv
```

```text
jlcplace: wrote forced-base-CPL.csv (17 rows), red rows included (--force)
exit code 2
SW1,0.0000mm,0.0000mm,Top,0
```

::: warning
A red row is one jlcplace cannot vouch for. Upload a forced CPL only once you know why each row is red and have
decided it is right; then record that decision, as below, so the next run is not red.
:::

## SW1: a part the records cannot decide {#sw1-a-part-the-records-cannot-decide}

SW1 is a TS-1187A tactile switch (LCSC C318884). Its KiCad footprint numbers the four pads 1, 1, 2, 2: the two pads
numbered 1 are one terminal of the switch, the two numbered 2 the other. Its EasyEDA footprint numbers them 1, 2, 3, 4.
`show` lists where each EasyEDA pad lands under the fitted correction:

```sh
jlcplace show dial-me-spinner-base.kicad_pcb --ref SW1
```

```text
...
easyeda:
  title: SW-SMD_4P-L5.1-W5.1-P3.70-LS6.5-TL_H1.5
...
correction:
  source: fit
  rot: 0.0
  dx: 0.0
  dy: 0.0
  mirror: false
  method: geometry
  status: auto
  verdict: red
...
landings: 4
  - ee="1", kicad="1", net="/Base board/SW_HI", dist=0.0251
  - ee="2", kicad="1", net="/Base board/SW_HI", dist=0.0251
  - ee="3", kicad="2", net="/Base board/SW_GATE", dist=0.0251
  - ee="4", kicad="2", net="/Base board/SW_GATE", dist=0.0251
```

The pads fit at rotation 0 with no shift, and EasyEDA's pads 1 and 2 land on KiCad's two pads numbered 1. That is
right only if EasyEDA's pins 1 and 2 are joined inside the switch. Nothing in the EasyEDA record says so, and
EasyEDA's pad 2 on KiCad's pad 1, while KiCad has a pad 2 on another net, looks exactly like a pin on the wrong net. So
the fit is red: "numbering conflict ... and no pin names confirm it".

Which pins the switch joins inside is in its datasheet. Turning the part does not help: the only other placement that
lands every pad is rotation 180, which pairs the pads the same way. If the datasheet joined other pins, no CPL row
could fix it; the KiCad footprint itself would pair the wrong pads.

For dial-me-spinner the datasheet answer was that EasyEDA's pins 1-2 and 3-4 are each one terminal. Record that with
`confirm`, with `--source manual` (a decision from the datasheet, not from JLC's preview):

```sh
jlcplace confirm dial-me-spinner-base.kicad_pcb --ref SW1 --db jlcplace-db --by you --source manual --note "TS-1187A datasheet: EasyEDA pins 1-2 and 3-4 are each one terminal"
```

```text
SW1 C318884: manual, yellow: rot 0, dx 0.0000, dy 0.0000; row X 0.0000 Y 0.0000 rot 0 (jlcplace-db/C318884.json)
```

This creates the database directory `jlcplace-db` and stores the correction under SW1's code, its EasyEDA footprint
and its KiCad pads, with the decision (`jq`, a command-line JSON tool, picks out the fields):

```sh
jq '.entries[0] | {ee, correction, status, verdict, verifications}' jlcplace-db/C318884.json
```

```text
{
  "ee": {
    "key": "ee-sha1:e3bedbb303c206d2",
    "title": "SW-SMD_4P-L5.1-W5.1-P3.70-LS6.5-TL_H1.5"
  },
  "correction": {
    "dx": 0.0,
    "dy": 0.0,
    "mirror": false,
    "rot": 0.0
  },
  "status": "manual",
  "verdict": "yellow",
  "verifications": [
    {
      "board": "dial-me-spinner-base.kicad_pcb",
      "by": "you",
      "date": "2026-10-22",
      "layer": "top",
      "note": "TS-1187A datasheet: EasyEDA pins 1-2 and 3-4 are each one terminal",
      "ok": true,
      "presses": null,
      "ref": "SW1",
      "source": "manual"
    }
  ]
}
```

A manual decision lifts a red correction to yellow, not green: it is a person's word, and it is worth one look in
JLC's preview. Write the CPL with the database:

```sh
jlcplace cpl dial-me-spinner-base.kicad_pcb -o dial-me-spinner-base-CPL-JLCPCB.csv --db jlcplace-db
```

```text
jlcplace 0.1.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs dial-me-spinner-base-CPL-JLCPCB.csv (17 rows), bottom reading jlc-2022
YELLOW  SW1  C318884    top     renumbered: numbering accepted by the correction (manual): 2->1
             correction (db, manual, yellow): accepted manually (you): TS-1187A datasheet: EasyEDA pins 1-2 and 3-4 are each one terminal
17 results: 0 red, 1 yellow, 16 green, 0 verified
Preview checklist (3 items): look at these in JLC's placement preview
  uncertain correction  SW1: yellow db correction (manual): accepted manually (you): TS-1187A datasheet: EasyEDA pins 1-2 and 3-4 are each one terminal; numbering conflict: EE 2 -> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview
  odd angle             J2 (310), J3 (30)
jlcplace: wrote dial-me-spinner-base-CPL-JLCPCB.csv (17 rows)
```

Commit `jlcplace-db` with the board, and pass `--db jlcplace-db` to every `cpl` and `check` of it, so the decision
is made once. When SW1 has been seen right in JLC's preview, record that too (`--source preview` with the CPL you
uploaded); its row then becomes verified. [JLC's preview and confirm](./preview) and
[The correction database](./database) go further.

::: tip The same applies to polarity
A polar two-terminal part (a diode, a tantalum capacitor, a buzzer) is red in the same way when EasyEDA's pin names
and pad numbers disagree and no "+" drawn on its EasyEDA footprint settles which way round it goes. Look at its
datasheet or JLC's preview and record the decision with `confirm`.
:::

## Reproducing the dial-me-spinner CPLs

dial-me-spinner's corrected CPLs (`released-main-CPL.csv` and `released-base-CPL.csv`, from commit a5f70dc) were
written by the project's own script from a table of corrections, once the bad release was caught and the script
fixed. From the board files and the EasyEDA records alone, plus SW1's decision, `cpl` writes the same rows. The base
board, against its corrected CPL:

```sh
diff --strip-trailing-cr released-base-CPL.csv dial-me-spinner-base-CPL-JLCPCB.csv
```

```text
7,9c7,9
< J1,29.7790mm,0.0000mm,Top,90
< J2,-12.5860mm,-11.3353mm,Top,310
< J3,-7.7815mm,13.4780mm,Top,30
---
> J1,29.7791mm,0.0000mm,Top,90
> J2,-12.5860mm,-11.3350mm,Top,310
> J3,-7.7815mm,13.4775mm,Top,30
```

The main board (the corrected file sorts C10 before C2, so both are sorted first):

```sh
diff --strip-trailing-cr <(sort released-main-CPL.csv) <(sort dial-me-spinner-main-CPL-JLCPCB.csv)
```

```text
1c1
< BZ1,20.3980mm,8.2413mm,Bottom,158
---
> BZ1,20.3979mm,8.2412mm,Bottom,158
138c138
< D20,10.1421mm,-5.3522mm,Top,270
---
> D20,10.1421mm,-5.3520mm,Top,270
141,143c141,143
< J1,16.9421mm,-9.7815mm,Bottom,120
< J20,-0.0010mm,-10.8400mm,Top,0
< J40,-17.0213mm,18.9041mm,Bottom,138
---
> J1,16.9416mm,-9.7815mm,Bottom,120
> J20,-0.0009mm,-10.8405mm,Top,0
> J40,-17.0214mm,18.9038mm,Bottom,138
...
```

Every reference, side and rotation is the same, and no coordinate differs by more than 0.5 µm (in the fourth
decimal place). The script's corrections were rounded to three decimal places; `cpl` keeps four.

The CPLs of the first release are another matter: their rows for main J1, J20 and J40 and base J1, J2 and J3 were
1.4 to 4.5 mm off, and base SW1 was 127 mm off. The script that wrote them had each part's correction right, but
carried JLC's origin onto the board with a fit between the KiCad library footprint's pads and the placed
footprint's pads, matched by number. These parts repeat pad numbers (the connectors' mounting and shield pads, the
switch's 1, 1, 2, 2), which threw that fit off (see [Why parts end up misaligned](../concepts/why#writer-bugs)). [Checking a CPL](./check#reading-the-summary) shows `check` finding the four
on the base board.

## Next

* Upload the CPL with the BOM, and go through the preview checklist in JLC's placement preview:
  [JLC's preview and confirm](./preview).
* Keep the corrections and decisions with the board: [The correction database](./database).
* Check the CPL on every push: [Continuous integration](./ci).
* Every option: [`jlcplace cpl`](../reference/cli#jlcplace-cpl) in the command reference.
