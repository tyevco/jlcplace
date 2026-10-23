# Data and licensing

jlcplace's own code is MIT licensed. The footprints it checks CPLs against are not jlcplace's: they belong to the
JLCEDA/EasyEDA Official Library, and their records carry a notice that asks for their source to be declared. (A CPL
is the pick-and-place file you upload to JLCPCB with your BOM: one row per part, with its position, side and
rotation.) This page quotes that notice, says what jlcplace keeps where, and what you can share.

## jlcplace

jlcplace is released under the [MIT licence](https://github.com/tyevco/jlcplace/blob/main/LICENSE), copyright (c)
2026 tyevco. That covers the code in `src/`, the tests, and this documentation, which live in the same repository.
The repository holds no EasyEDA data: its tests build their EasyEDA records by hand, or read a local cache when one
is present, and the screenshots on this site are of a made-up example board with made-up records.

## The EasyEDA records

JLC places each part with the footprint its EasyEDA library holds for the part's LCSC code. jlcplace downloads those
records from the EasyEDA API (`jlcplace fetch`, or `--online`) into your record cache, and simulates the placement
with them. Each record carries a licence notice in its `license` field, in Chinese and in English:

```sh
python3 -c "import json, sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['result']['packageDetail']['dataStr']['head']['license'])" ~/.cache/jlcplace/C7519.json
```

```text
【版权归属/Copyright】嘉立创EDA/EasyEDA官方库（包括但不限于器件参数、原理图符号、PCB封装、3D模型、模块及相关数据）的版权及相关权利归嘉立创EDA所有。The copyright and related rights of the JLCEDA/EasyEDA Official Library, including but not limited to device parameters, schematic symbols, PCB footprints, 3D models, modules and related data, b...
```

The English text of the notice, section by section, as the records hold it:

| section | text |
|---|---|
| Copyright | The copyright and related rights of the JLCEDA/EasyEDA Official Library, including but not limited to device parameters, schematic symbols, PCB footprints, 3D models, modules and related data, belong to JLCEDA/EasyEDA. |
| Scope | Any AI tool. |
| Trigger | When citing, calling, training, fine-tuning, retrieving, generating, reproducing, outputting or distributing all or part of the JLCEDA/EasyEDA Official Library. |
| Requirement | Must prominently declare the source as “JLCEDA/EasyEDA Official Library”. |
| Link | And provide the official JLCEDA homepage link: https://lceda.cn/ and the official EasyEDA homepage link: https://easyeda.com. |
| Prohibited | Do not delete, alter, hide or obscure the above copyright and source information. |
| Commercial Use Statement | The JLCEDA/EasyEDA official library or modules may be used in any commercial circuit design when used in JLCEDA/EasyEDA. |

The notice is in both the symbol's and the footprint's data (`result.dataStr.head.license` and
`result.packageDetail.dataStr.head.license`). EasyEDA's "Component not found" answers carry none.

Where jlcplace draws EasyEDA footprints, in its HTML report, it declares their source as the notice asks:
**JLCEDA/EasyEDA Official Library**, with links to [lceda.cn](https://lceda.cn/) and
[easyeda.com](https://easyeda.com). This site declares it in its footer, since some pages show EasyEDA package
titles such as `SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL` in jlcplace's output.

## What jlcplace keeps where

| what | where | what it holds from EasyEDA | share it? |
|---|---|---|---|
| Record cache | `~/.cache/jlcplace`, or `$JLCPLACE_CACHE`, `$XDG_CACHE_HOME/jlcplace`, `--cache DIR` | the records, verbatim, licence notice included | No. Keep it on your machine, or in a CI cache only your repository can read. Never commit it or upload it as an artifact. |
| Correction database | `--db DIR`, one JSON file per LCSC code | derived numbers (`rot`, `dx`, `dy`, the fit's residual and score, the other placements as numbers), a hash of the EasyEDA pads, and the EasyEDA footprint's title (its package name). No pad positions, sizes or outlines. | Yes: it is made to be committed and shared. |
| JSON results, `fit --json`, `show --json` | where you write them | verdicts, messages, pad numbers, distances, the footprint's title and its record key. No geometry. | Yes |
| JUnit XML, checklists, GitHub annotations | where you write them | verdicts, messages and pad numbers | Yes |
| HTML report | `--report FILE.html` | the EasyEDA pads' outlines, drawn where JLC will put them | No: for your own review |
| Board snapshot | `snapshot -o FILE.json` | nothing: only your board | Yours to decide |

The two hashes in a database entry identify the footprints the correction was made with, one EasyEDA's and one
KiCad's. Each is the first 16 hex digits of a SHA-1 over the pads' numbers, positions, sizes and angles (the KiCad
one also over the pins' functions): enough to notice when a footprint changes, not enough to rebuild it. The
title, such as `BUZ-SMD_L9.0-W9.0_PKMCS0909E4000-R1`, is EasyEDA's name for the package, kept for people reading
the file (and for the check without the record: a BI in it marks a bidirectional TVS). See
[File formats](../reference/files#correction-database).

## The HTML report

The report draws the EasyEDA pads as outlines over your KiCad copper. That is what makes it useful, and it is also
why the records' notice applies to it. So:

* It declares the source above the board views, again above the part cards, and in its footer: the JLCEDA/EasyEDA
  Official Library, with links to https://lceda.cn/ and https://easyeda.com, and the note that the copyright and
  related rights in that library belong to JLCEDA/EasyEDA.
* It asks search engines not to index it.
* It is meant for your own review. Do not publish it: not as a CI artifact of a public repository, not on a public
  website, not attached to a public issue. If you share one with a colleague, keep the source notice in it.

The screenshots of the report on this site are of the documentation's example board, whose footprints and records
were made up for it; no EasyEDA geometry is shown here.

## Attribution

When you publish something made with jlcplace:

* The CPL jlcplace writes holds your parts' positions and rotations, and the database holds numbers derived from the
  records, not the records. The database's titles are EasyEDA's names for its packages, so when you publish a
  database, say where they come from, for example in a README next to it: "Corrections fitted with jlcplace against
  footprints of the JLCEDA/EasyEDA Official Library (https://lceda.cn/, https://easyeda.com); the `ee.title` fields
  are that library's package names."
* Anything that shows EasyEDA footprint geometry, such as a report or a screenshot of one, falls under the notice:
  it asks for the source to be declared prominently, with both links, and for the notice not to be removed or
  hidden.
* The dial-me-spinner boards used as examples on this site are a public KiCad project at
  [github.com/tyevco/dial-me-spinner](https://github.com/tyevco/dial-me-spinner).
