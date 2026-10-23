---
layout: home
hero:
  name: jlcplace
  text: Check a JLCPCB pick-and-place file against your KiCad board
  tagline: jlcplace puts JLC's own EasyEDA footprint down exactly where each row of your CPL (the pick-and-place file) tells JLC to put it, and tells you which of your pads, and which net, each of its pads lands on.
  actions:
    - theme: brand
      text: Getting started
      link: /guide/getting-started
    - theme: alt
      text: Why parts end up misaligned
      link: /concepts/why
features:
  - title: Parts shifted off their pads
    details: JLC places the footprint from its own EasyEDA library, and its origin is often not KiCad's. jlcplace names every pad that lands off copper, and how far off it is.
    link: /concepts/why#origin
    linkText: Why this happens
  - title: Parts turned onto the wrong pins
    details: A SOT-23-6 turned 180 degrees still lands all six pads on copper. jlcplace checks the net under each pad, so it catches that too.
    link: /concepts/why#symmetric
    linkText: Symmetric parts
  - title: The bottom side
    details: JLC does not say how it reads the rotation of a bottom-side part. jlcplace tries every way, and names the one part to look at in JLC's placement preview.
    link: /concepts/why#bottom
    linkText: Bottom-side rotation
  - title: Any tool's CPL, or its own
    details: It checks a CPL from any tool, or KiCad's position file. jlcplace cpl writes a corrected CPL, and writes no file while a row is red.
    link: /guide/cpl
    linkText: Writing a CPL
---

## In one minute

A CPL is the pick-and-place file you upload to JLCPCB with your BOM: one row per part, with its position, side and
rotation. jlcplace needs Python 3.10 or newer and your `.kicad_pcb`; KiCad itself does not have to be installed.
Here it checks the CPL of the first fab release of the [dial-me-spinner](https://github.com/tyevco/dial-me-spinner)
main board ([Getting started](./guide/getting-started) has the files):

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install git+https://github.com/tyevco/jlcplace
jlcplace fetch dial-me-spinner-main.kicad_pcb
jlcplace check dial-me-spinner-main.kicad_pcb old-main-CPL.csv
```

`fetch` downloads JLC's EasyEDA footprint for every LCSC code on the board into a local cache. It is the only
command that uses the network. `check` then places those footprints at the CPL's rows:

```text
jlcplace 0.2.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs old-main-CPL.csv (184 rows), bottom reading jlc-2022
RED     J1   C160411    bottom  14 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 3.40 mm away)
RED     J20  C262671    top     11 of 32 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 4.48 mm away)
RED     J40  C160404    bottom  6 of 6 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6 (nearest KiCad pad centre up to 1.40 mm away)
...
184 results: 3 red, 2 yellow, 179 green, 0 verified
...
```

**Red means the CPL is wrong for that part.** At least one pad of JLC's footprint lands off copper or on a pin of
another net, or the row should not be there at all, or an assembled part has no row. Fix every red row before you
order. Here JLC would have put three connectors 1.4 to 4.5 mm off their pads, and that CPL was part of the
project's first fab release. Yellow means probably right, but look at it in JLC's placement preview. Green means
every pad lands on its own pin.

On your own board, run the same two commands on your `.kicad_pcb` and the CPL you upload (or uploaded) to JLC, and
add `--bom` with your BOM. [Getting started](./guide/getting-started) walks through this check line by line and
writes a corrected CPL. [Why parts end up misaligned](./concepts/why) explains where the errors come from.
