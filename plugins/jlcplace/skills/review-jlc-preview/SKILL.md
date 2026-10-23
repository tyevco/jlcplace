---
name: review-jlc-preview
description: Walk JLCPCB's placement preview item by item from jlcplace's checklist and record each result, or what an assembled board showed right, with jlcplace confirm. Use when the user is going through JLC's placement preview or wants preview or board results recorded; for parts that came out wrong, use diagnose-jlc-misplacement.
argument-hint: "[board.kicad_pcb] [uploaded-CPL.csv]"
allowed-tools: Bash(jlcplace check *) Bash(jlcplace show *) Bash(jlcplace --version) Read Glob
---

# Walk JLC's placement preview and record it

JLC's placement preview (on the order page, after the BOM and the CPL are uploaded) shows every part where JLC will
put it. It is the last check before paying, and the only one of what jlcplace cannot prove offline: the bottom-side
reading, parts without an EasyEDA footprint, and choices the records leave open. `jlcplace confirm` stores what the
preview proved, so that the next `jlcplace cpl` writes those rows as verified.

## What you need

* The board (`*.kicad_pcb`) and **the CPL exactly as uploaded**. What the preview shows is that file's rows, which
  need not be jlcplace's.
* The checklist: `checklist.md`, from `--checklist` on `jlcplace check` or `jlcplace cpl`. If it is missing, make it
  from the uploaded files (leave out `--gerbers` when the zip is not at hand). `--overlay` draws each side as the
  preview shows it, the parts to look at circled (`overlay/top.svg`, `overlay/bottom.svg`), to hold beside the
  preview; like `--report`, it draws EasyEDA pad outlines, so keep it local:

  ```sh
  jlcplace check board.kicad_pcb uploaded-CPL.csv --bom uploaded-BOM.csv --gerbers uploaded-gerbers.zip --db jlcplace-db --checklist checklist.md --overlay overlay
  ```

* The correction database directory (`jlcplace-db/` unless the project has another; `confirm` creates it) and the
  name of the person looking (`--by`). Run `jlcplace --version` first. If it is missing, follow
  [setup-jlcplace](../setup-jlcplace/SKILL.md).

## Walk the list

Go through the checklist in its order, one item at a time. For each item, tell the user which part to look at and
what to look for. Then wait for their answer. Record only what the user says they saw. Never record a result for a
part nobody looked at.

1. **Red rows** (flagged `red: fix the row first`): do not verify them. Fix the row
   ([write-jlc-cpl](../write-jlc-cpl/SKILL.md)), upload the new CPL, and start the preview again.
2. **pinout**: not for the preview, which shows where pads land, not what the pins are. Compare the LCSC part's
   datasheet with the symbol ([diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md), "Wrong pins"). A
   datasheet answer the user accepts is recorded with `--source manual`, never `--source preview`.
3. **uncertain correction**: the item's text says what is in doubt: the polarity mark, a numbering, the orientation,
   the pegs. Ask whether the part sits on its pads with pin 1, or its polarity mark, where the board's silkscreen
   wants it.
   A part with no polarity whose numbering a person decided from the datasheet (`numbering accepted by the correction
   (manual)`, a 4-pad switch) is different: ask only whether it sits square on all its pads, with its pad pattern's
   long axis as on the board. The preview cannot show which pins are joined inside the part, so a preview record
   proves the landing only; say so in `--note` ("landing only; numbering from the datasheet").
4. **not simulated**: no EasyEDA footprint, so jlcplace could not place it. Ask whether it sits on its pads, the right
   way round (a trimmer: its rotor on the pad the board wants).
5. **outside the outline**: the row point lies off the board. Ask whether the part sits where the board wants it: a
   connector overhanging the edge may; a part off the board is a wrong row.
6. **bottom reading**: start with the part marked `look at this one first`. Its pins move under every other reading,
   so it shows how JLC reads bottom-side rotations. If it sits right, record it: that rules out the readings under
   which it would have moved, and on the next check the bottom parts that only those readings moved have no item
   left (the others say which readings it ruled out). If it sits wrong, stop and use
   [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md) ("Only bottom-side parts turned").
7. **odd angle**: one named part stands for all parts of that code and side. Ask whether it sits square on its pads.

An item that stands for several parts names the one to look at. Record that one part. Parts with the same code on the
same footprint share one database entry, so recording one verifies the others.

## Record each answer

Pass the uploaded CPL every time. Add `--note` with what was seen when it helps the next person.

* **Right as shown:**

  ```sh
  jlcplace confirm board.kicad_pcb --ref U10 --db jlcplace-db --by NAME --source preview --cpl uploaded-CPL.csv
  ```

* **Right after moving it in the preview**: give the keys that were pressed, counted from the row as uploaded. Each
  space press turns the part 90 degrees counter-clockwise as the preview shows it. Each arrow press moves it 0.0635 mm
  on screen. `--right` and `--up` take negative numbers for left and down. The bottom view is seen from below.

  ```sh
  jlcplace confirm board.kicad_pcb --ref D21 --db jlcplace-db --by NAME --source preview --cpl uploaded-CPL.csv --space 2
  ```

* **Wrong, and not put right in the preview**: record the failure. Presses cannot go with `--failed`. From then on
  `check --db` holds the row red (`verified-wrong`), for every part that shares the entry, and `cpl --db` writes no
  CPL with it unless `--force`, until the correction is fixed: see "Afterwards".

  ```sh
  jlcplace confirm board.kicad_pcb --ref U3 --db jlcplace-db --by NAME --source preview --cpl uploaded-CPL.csv --failed --note "pin 1 at the wrong corner"
  ```

* **Several parts that were all right**: repeat `--ref` (`--ref U11 --ref U12`). Parts that share an entry must agree,
  and jlcplace refuses the whole command if their rows differ.

`confirm` checks everything before it writes anything. It refuses (exit 3, nothing recorded for any `--ref` of the
command) when:

* the uploaded CPL has no row for the part;
* the part's EasyEDA record is not in the cache (ask before running `jlcplace fetch`; never add `--online`);
* a verified entry would get other numbers before that entry's own row is recorded as failed;
* `--failed` is given for a row that is not jlcplace's correction: `the row in ... is not the stored correction's
  (...): its failure says nothing about that correction; nothing recorded`. That row is red or off its correction
  already, and jlcplace writes another one, so there is nothing to record. Leave the part out, or fix its row.

Read its message and tell the user. Each successful line says the new status (`preview-verified, verified`), the
stored correction, the row it gives, and whether it replaced an older correction.

After an assembled board has been inspected, record the parts that came out right the same way, with
`--source board`, the strongest status. For parts that came out wrong, use
[diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md) first.

## Afterwards

1. Check again with the database:

   ```sh
   jlcplace check board.kicad_pcb uploaded-CPL.csv --db jlcplace-db
   ```

   Rows the preview proved right are now `verified`. A part moved with presses now has the moved row as its
   verified correction, and its uploaded row checks against that: green after a small move, a yellow `offset` after
   a larger one (more than 0.15 mm and a quarter of the smallest pad), red after a turn that puts pins on other
   pads. A part recorded as failed is red, `verified-wrong` (`verified wrong by JLC's preview on DATE (U3, NAME:
   note): the row is the stored correction's; fix the correction ...`), whatever its pads land on, and so is every
   part that shares its entry. `jlcplace cpl --db` refuses to write it (`... the stored correction of U3 was verified
   wrong: fix it and confirm again`); a refit does not clear it.
2. Settle every failed part before uploading again. Do one of: move it right in the preview and record the presses
   (`--source preview --cpl uploaded-CPL.csv` with `--space`, `--right`, `--up`); type the correction in with
   `jlcplace confirm --rot --dx --dy --source manual` (the user's decision; confirm it in the next preview); choose
   another part; or fix the pinout or footprint ([diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md)).
   Then write a new CPL from the database ([write-jlc-cpl](../write-jlc-cpl/SKILL.md)). Check that it is written and
   that its checklist no longer says `verification failed` for the part, upload it, and look at the moved and failed
   parts once more.
3. Tell the user to commit `jlcplace-db/`. It holds derived numbers and the verification history, no EasyEDA data.
4. Summarise for the user: what was verified, what was moved (and how), what failed, and what is left to look at.
