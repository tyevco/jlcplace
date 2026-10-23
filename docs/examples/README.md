# The documentation's worked example

`make_examples.py` builds a small made-up board, checks two CPLs (pick-and-place files) against it with
`jlcplace check`, and takes the screenshots of jlcplace's HTML report that the documentation shows. Run it again
whenever the report's layout or jlcplace's messages change, and commit the new images and transcript.

## Run it

From the repository root, with jlcplace installed in `.venv`:

```sh
source .venv/bin/activate
python -m pip install playwright
python -m playwright install chromium
python docs/examples/make_examples.py
```

The screenshots are taken with Playwright's Chromium, which `playwright install chromium` downloads (it does nothing
when that Chromium is already there). Set `PLAYWRIGHT_BROWSERS_PATH` if yours is installed somewhere other than
Playwright's default. To run only the checks, with no browser and no change to the images (the transcript is still
written again):

```sh
python docs/examples/make_examples.py --no-images
```

The script writes:

| file | what it holds |
|---|---|
| `docs/examples/transcript.txt` | the two `jlcplace check` commands and their exact output, with exit codes |
| `docs/public/img/report-overview.png` | the top of the report for the bad CPL: counts, then the top and bottom board views |
| `docs/public/img/card-offset.png` | J1's card from the bad CPL: the row is 1.3 mm off, every pad lands off copper |
| `docs/public/img/card-rotated.png` | U1's card from the bad CPL: turned 180 degrees, every pad on copper but on the wrong pin |
| `docs/public/img/card-ok.png` | U1's card from the good CPL: green, every pad on its own pin |

Pages use the images as `![...](/img/card-ok.png)`.

The output does not change from run to run: two runs on one machine give byte-identical files. Another machine
may draw the text with other fonts. The script stops with an error, and writes no images, if jlcplace no longer
gives the verdicts the pages describe: exit code 0 and no red row for the good CPL, exit code 2 and exactly J1 and U1
red for the bad one.

## The board

Everything on it is made up. The KiCad footprints (library `Example:`), their pads, and the EasyEDA-format records
(titles `EXAMPLE_...`, LCSC codes C1001 to C1005) were written for this example. No EasyEDA data is used. The script
writes the records into a temporary record cache, as `tests/test_cli.py` does, and nothing goes on the network.

| part | what it is | side | what makes it interesting | good row | bad row |
|---|---|---|---|---|---|
| U1 | SOT-23-6 | top | its EasyEDA footprint is drawn turned 180 degrees | rotation 180 | rotation 0, KiCad's angle |
| J1 | 6-pin FPC connector, 1.0 mm pitch | top | its EasyEDA origin is 1.3 mm from KiCad's anchor | Mid Y 13.5 | Mid Y 14.8, KiCad's anchor |
| Q1 | SOT-23 | bottom | its EasyEDA footprint is drawn turned 90 degrees | rotation 90 | same |
| R1, R2 | 0603 resistors | top | | | same |
| C1, C2 | 0402 capacitors | top | | | same |
| H1 to H4, TP1 to TP3 | mounting holes (top), test pads (bottom) | | no LCSC code, so JLC does not place them | no row | no row |

The board is 26 by 18 mm. Its aux origin, the point the CPL's coordinates are measured from, is the lower-left
corner, at (100, 118) in KiCad's coordinates.

The good CPL holds the rows the known corrections give. The bad CPL is the same file with two rows replaced by
KiCad's own position and angle for those parts: U1 at KiCad's angle, J1 at KiCad's footprint anchor.

## Expected output

The good CPL: every row is green. Q1 is listed for a look in JLC's preview because it is on the bottom, and its pins
would move if JLC read a bottom-side rotation another way.

```text
$ jlcplace check example.json example-CPL-good.csv --cache cache --report report-good.html
jlcplace 0.1.0: example.json (json, 14 parts, origin aux 100, 118) vs example-CPL-good.csv (7 rows), bottom reading jlc-2022
7 results: 0 red, 0 yellow, 7 green, 0 verified
Preview checklist (1 item): look at these in JLC's placement preview
  bottom reading  Q1
[exit code 0]
```

The bad CPL: J1 is off its pads, and U1 sits on copper with every pin on the wrong net.

```text
$ jlcplace check example.json example-CPL-bad.csv --cache cache --report report-bad.html
jlcplace 0.1.0: example.json (json, 14 parts, origin aux 100, 118) vs example-CPL-bad.csv (7 rows), bottom reading jlc-2022
RED     J1  C1004      top     8 of 8 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 (nearest KiCad pad centre up to 1.30 mm away)
RED     U1  C1001      top     wrong-pad: EasyEDA pads land on other-numbered KiCad pads of other nets: 1->4 (EN, not BST), 2->5 (VIN, not GND), 3->6 (SW, not FB), 4->1 (BST, not EN), 5->2 (GND, not VIN), 6->3 (FB, not SW)
7 results: 2 red, 0 yellow, 5 green, 0 verified
Preview checklist (1 item): look at these in JLC's placement preview
  bottom reading  Q1
[exit code 2]
```

The `[exit code N]` lines are added by the script; jlcplace does not print them.

## Look at the files yourself

`--keep DIR` writes everything into DIR and leaves it there: the board snapshot `example.json`, both CPLs, the
record cache `cache/` and both reports. Then you can run the checks by hand (`out/` is ignored by git):

```sh
python docs/examples/make_examples.py --keep out/example
cd out/example
jlcplace check example.json example-CPL-bad.csv --cache cache --report report-bad.html
```

Two things in the reports differ from a real board's:

* The script sets each report's time stamp to 2026-01-01 12:00, so that the images do not change from run to run. A
  report you make by hand carries the current time.
* Every jlcplace report says its pad outlines come from the JLCEDA/EasyEDA Official Library. In this example they
  come from the made-up records.

## How the images are cut

The reports are opened in a 720 px wide window, about the width of a documentation page's text column, drawn at 5/3
scale in the light theme, so each image is 1173 px wide. Each image is one part of the page with 8 px of margin:
`report-overview.png` runs from the title to the note under the board views, and each card image is the whole card.
Green cards start closed, so the script clicks U1's card open before taking `card-ok.png`.
