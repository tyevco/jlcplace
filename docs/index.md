---
layout: home
hero:
  name: jlcplace
  text: Check a JLCPCB pick-and-place file against your KiCad board
  tagline: jlcplace puts JLC's own EasyEDA footprint down exactly where each CPL row tells JLC to put it, and tells you which of your pads, and which net, each of its pads lands on.
  actions:
    - theme: brand
      text: Getting started
      link: /guide/getting-started
    - theme: alt
      text: Why parts end up misaligned
      link: /concepts/why
features:
  - title: Parts shifted off their pads
    details: JLC places the footprint from its EasyEDA library, and its origin is often not KiCad's. On the dial-me-spinner board, KiCad's position file used as a CPL puts a 30-pin FPC connector 1.34 mm off its pads. jlcplace names every pad that lands off copper and how far off it is.
    link: /concepts/why
    linkText: Why this happens
  - title: Parts turned onto the wrong pins
    details: A SOT-23-6 turned 180 degrees still puts all six pads on copper. jlcplace compares the net under each pad, not only whether there is copper, so it reports EasyEDA pad 1 on KiCad pad 4, pad 2 on pad 5, and so on, each on another net.
    link: /concepts/how-it-works
    linkText: How the check works
  - title: The bottom side
    details: JLC does not document how it reads the rotation of a bottom-side part. jlcplace places every bottom row under the reading open tools use and under the three others, and names the one part to look at in JLC's preview to settle it for the whole board.
    link: /concepts/how-it-works
    linkText: The bottom-side reading
  - title: Rows that do not match the board
    details: A DNP part in the CPL, a reference that is not on the board or appears twice, a row on the wrong side, an assembled part with no row and, given your BOM, a BOM line whose LCSC code differs from the board's.
    link: /concepts/verdicts
    linkText: Verdicts
  - title: A short list for JLC's preview
    details: What only JLC's placement preview can settle (a polarity that only pad numbers decide, the bottom-side reading, parts at odd angles) comes as a checklist, with alike parts in one item. On a 184-row board that is 12 items for 99 parts.
    link: /guide/preview
    linkText: Using the checklist
  - title: Any tool's CPL, or its own
    details: It checks a CPL from kicad-jlcpcb-tools, Fabrication-Toolkit, KiKit, KiCad's position file or a spreadsheet. jlcplace cpl writes the CPL itself and keeps no file when a row is red. In CI it runs without network access from a committed correction database.
    link: /guide/cpl
    linkText: Writing a CPL
---

## In one minute

A CPL is the pick-and-place file you upload to JLCPCB: one row per part, with its position, side and rotation.
jlcplace needs Python 3.10 or newer and your `.kicad_pcb`; KiCad itself does not have to be installed. Here it
checks the first CPL of the [dial-me-spinner](https://github.com/tyevco/dial-me-spinner) main board:

```sh
pip install git+https://github.com/tyevco/jlcplace
jlcplace fetch dial-me-spinner-main.kicad_pcb
jlcplace check dial-me-spinner-main.kicad_pcb old-CPL.csv
```

`fetch` downloads JLC's EasyEDA footprint for every LCSC code on the board into a local cache. It is the only
command that uses the network. `check` then places those footprints at the CPL's rows:

```text
jlcplace 0.1.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs old-CPL.csv (184 rows), bottom reading jlc-2022
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

[Getting started](./guide/getting-started) walks through this check line by line and writes a corrected CPL.
[Why parts end up misaligned](./concepts/why) explains where the errors come from.
