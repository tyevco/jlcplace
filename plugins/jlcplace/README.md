# jlcplace for Claude Code

A Claude Code plugin for handing KiCad boards to JLCPCB assembly. Its skills make Claude use
[jlcplace](https://github.com/tyevco/jlcplace) to check and correct pick-and-place (CPL) files, prepare a complete
JLC order, explain why an order came back with parts shifted, turned or reversed, and walk JLC's placement preview.

jlcplace puts JLC's own EasyEDA footprint down at each CPL row and reports which KiCad pad and net every EasyEDA pad
lands on. A part that is turned, shifted, reversed or on the wrong pins shows up before you pay for it.

## Install

In Claude Code:

```text
/plugin marketplace add tyevco/jlcplace
/plugin install jlcplace@jlcplace
```

Or in one step (Claude Code 2.1.275 or later):

```text
/plugin install jlcplace --marketplace tyevco/jlcplace
```

From a shell: `claude plugin marketplace add tyevco/jlcplace`, then
`claude plugin install jlcplace@jlcplace --scope user`.

Third-party marketplaces do not update on their own. To get a newer version, run
`claude plugin marketplace update jlcplace` and then `claude plugin update jlcplace@jlcplace`, and restart Claude
Code. Or turn on auto-update for the marketplace under `/plugin` (Marketplaces).

The plugin does not install the jlcplace CLI. The first time a skill needs it, Claude looks for it and, if you agree,
installs it from GitHub with `uv tool install`, `pipx install`, or a virtual environment of its own. The skills
need jlcplace 0.2.1 or later, and Claude offers to update an older one. It needs Python 3.10 or later, and git (or
GitHub's zip download of the repository). It does not need KiCad: a built-in reader handles `.kicad_pcb` files from
KiCad 5 to 10.
KiCad's `kicad-cli` is used only to export Gerbers, drill files and the BOM, and the order skill falls back to
KiCad's dialogs without it.

**Permission prompts.** Each skill pre-approves the jlcplace commands it runs by name (`jlcplace check`, `cpl`,
`show`, `--version`, and the order skill's `kicad-cli`), so those run without a prompt. Recording
results (`jlcplace confirm`), downloading EasyEDA records (`jlcplace fetch`) and installing anything always ask. If
uv or pipx installs jlcplace into a directory that is not on your `PATH`, Claude calls it by its full path, which
the pre-approvals do not cover, so every call asks; run `uv tool update-shell` (or `pipx ensurepath`) and start a new
session to avoid that.

## Skills

Claude picks the skill from what you ask. You can also run one as `/jlcplace:<name>`.

| skill | what it does |
|---|---|
| `check-jlc-placement` | Checks a CPL from any tool (KiCad, kicad-jlcpcb-tools, Fabrication-Toolkit, KiKit, hand-made) against the board with `jlcplace check`, and explains each red, yellow, green or verified row and what to fix. |
| `write-jlc-cpl` | Writes a corrected CPL with `jlcplace cpl`, resolves the rows it refuses, and records numbering or polarity decisions with `jlcplace confirm --source manual`. |
| `prepare-jlc-order` | The whole hand-off: Gerbers, drill files and CPL from one origin, a BOM with LCSC codes, the jlcplace check of the CPL against the Gerber zip (`--gerbers`: the Gerbers' origin, and whether they are this board's), and a pre-upload checklist. |
| `diagnose-jlc-misplacement` | An order or a preview showed parts shifted, turned, reversed or on the wrong pins. It separates origin, units and Y-axis errors from EasyEDA angle and origin differences, offsets the CPL writer added itself, the bottom-side reading, polarity conventions and pinout mismatches, gives the fix for each, and says which files to upload again. |
| `review-jlc-preview` | Walks jlcplace's preview checklist item by item in JLC's placement preview, and records each result with `jlcplace confirm --source preview`, including key presses and failures. |
| `setup-jlcplace` | Finds, installs or updates the jlcplace CLI. The other skills use it. |

Examples:

* "Is `fab/board-CPL.csv` right for JLC?"
* "Prepare my board for a JLCPCB assembly order."
* "My JLC boards came back with U3 rotated and the USB connector shifted. Here is what I uploaded."
* "I'm in JLC's preview now. What should I look at?"

## Privacy and data

* **Network.** Only `jlcplace fetch` and the `--online` option download anything: EasyEDA footprint records, from the
  EasyEDA API, rate-limited. The skills ask before running `jlcplace fetch` and never add `--online` (the
  pre-approved `jlcplace check` and `cpl` would accept it without a prompt). Installing jlcplace downloads it from
  GitHub, again only when you agree. Nothing else uses the network.
* **EasyEDA data stays on your machine.** The footprint records belong to the JLCEDA/EasyEDA Official Library.
  jlcplace keeps them in its local cache (`$JLCPLACE_CACHE`, else `$XDG_CACHE_HOME/jlcplace`, else
  `~/.cache/jlcplace`). The HTML report (`--report`) and the overlay images (`--overlay`) draw EasyEDA pad outlines
  and are for your own review. Never commit or publish the cache, the report or the images. The CPL, the checklist,
  the JSON and JUnit outputs and the correction database hold derived numbers only, and can be committed.
* **The bottom side.** JLC does not document how it reads a bottom-side rotation. jlcplace uses the reading every
  open tool uses and lists the parts that depend on it. JLC's placement preview stays the final check.

## License

MIT, like jlcplace. See the repository's [LICENSE](https://github.com/tyevco/jlcplace/blob/main/LICENSE).
