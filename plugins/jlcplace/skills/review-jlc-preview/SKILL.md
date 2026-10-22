---
name: review-jlc-preview
description: Walk JLCPCB's placement preview item by item from jlcplace's checklist, and record each result with jlcplace confirm --source preview, key presses and failures included. Use when the user is looking at or reporting on JLC's placement preview, or an assembled board, after uploading a CPL.
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
  from the uploaded files:

  ```sh
  jlcplace check board.kicad_pcb uploaded-CPL.csv --bom uploaded-BOM.csv --db jlcplace-db --checklist checklist.md
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
2. **uncertain correction**: the item's text says what is in doubt: the polarity mark, a numbering, the orientation.
   Ask whether the part sits on its pads with pin 1, or its polarity mark, where the board's silkscreen wants it.
3. **not simulated**: no EasyEDA footprint, so jlcplace could not place it. Ask whether it sits on its pads, the right
   way round.
4. **bottom reading**: start with the part marked `look at this one first`. Its pins move under every other reading,
   so it shows how JLC reads bottom-side rotations. If it sits right, the other bottom parts follow. If it sits
   wrong, stop and use [diagnose-jlc-misplacement](../diagnose-jlc-misplacement/SKILL.md) ("Only bottom-side parts
   turned").
5. **odd angle**: one named part stands for all parts of that code and side. Ask whether it sits square on its pads.

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

* **Wrong, and not put right in the preview**: record the failure. Presses cannot go with `--failed`.

  ```sh
  jlcplace confirm board.kicad_pcb --ref U3 --db jlcplace-db --by NAME --source preview --cpl uploaded-CPL.csv --failed --note "pin 1 at the wrong corner"
  ```

* **Several parts that were all right**: repeat `--ref` (`--ref U11 --ref U12`). Parts that share an entry must agree,
  and jlcplace refuses the whole command if their rows differ.

`confirm` checks everything before it writes anything. It refuses (exit 3, nothing recorded) when the uploaded CPL has
no row for the part, or when the part's EasyEDA record is not in the cache (ask before running `jlcplace fetch`). It
also refuses to give a verified entry other numbers until that entry's own row is recorded as failed. Read its
message and tell the user. Each successful line says the new status (`preview-verified, verified`), the stored
correction, the row it gives, and whether it replaced an older correction.

After an assembled board has been inspected, record it the same way with `--source board`, the strongest status.

## Afterwards

1. Check again with the database:

   ```sh
   jlcplace check board.kicad_pcb uploaded-CPL.csv --db jlcplace-db
   ```

   Rows the preview proved right are now `verified`. A row the user had to move, or recorded as failed, is now red
   against the verified correction. That is expected: the uploaded row was wrong.
2. If any part needed presses or failed, write a new CPL from the database
   ([write-jlc-cpl](../write-jlc-cpl/SKILL.md)), upload it, and look at those parts once more.
3. Tell the user to commit `jlcplace-db/`. It holds derived numbers and the verification history, no EasyEDA data.
4. Summarise for the user: what was verified, what was moved (and how), what failed, and what is left to look at.
