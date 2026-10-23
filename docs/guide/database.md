# The correction database

For each part, jlcplace works out how JLC's EasyEDA footprint must be turned and shifted to sit on your KiCad
footprint: the part's correction. Without a database it fits the corrections again on every run and forgets what
anyone checked. The correction database keeps them, together with what people confirmed in JLC's placement preview
or on assembled boards, so that later runs, other boards and other people get the same answers.

The database is a directory of JSON files, one per LCSC code, made to be committed to git. Every command that takes
`--db DIR` uses it:

| command | what it does with the database |
|---|---|
| `jlcplace fit --db` | stores the corrections it fits (status `auto`); creates the directory |
| `jlcplace confirm` | records a verification; fits and stores the correction first when there is none; creates the directory |
| `jlcplace check --db`, `jlcplace cpl --db` | read it: a stored correction is used instead of a fit, and a verified one makes its rows `verified` |
| `jlcplace show --db` | prints a code's entries, or the correction a part gets |

`check`, `cpl` and `show` only read. They warn when the `--db` directory does not exist, since a typo would
otherwise drop every stored correction without a word.

The examples run in the `dms` directory of [Getting started](./getting-started), with the EasyEDA records fetched.
One example also uses dial-me-spinner's second board, whose files are in the same directory; fetch its records with
`jlcplace fetch dial-me-spinner-base.kicad_pcb`.

## Start one

`fit` fits every part JLC assembles and stores the corrections:

```sh
jlcplace fit dial-me-spinner-main.kicad_pcb --db jlcplace-db
```

```text
YELLOW  BZ1   C910763    bottom  rot 0, dx -0.0001, dy -0.0001 (number, rms 0.350, 2 matched)  polarity decided by pad numbers only ... [stored]
GREEN   C1    C45783     bottom  rot 0, dx 0.0000, dy 0.0000 (number, rms 0.050, 2 matched)  fits by pad numbers: 2/2 matched, rms 0.050 mm  [stored]
GREEN   C2    C96446     top     rot 0, dx 0.0000, dy 0.0000 (number, rms 0.075, 2 matched)  fits by pad numbers: 2/2 matched, rms 0.075 mm  [stored]
...
YELLOW  R26   C4167968   top     not fitted  no EasyEDA footprint (Component not found): the row is KiCad's own position and angle
...
YELLOW  U2    C2681320   top     rot 180, dx 0.0000, dy 0.0000 (number, rms 0.212, 6 matched)  rms 0.213 mm: the land patterns differ (every pad lands)  [stored]
...
GREEN   Y1    C97603     bottom  rot 0, dx 0.0000, dy 0.0000 (number, rms 0.000, 2 matched)  fits by pad numbers: 2/2 matched, rms 0.000 mm  [stored]
184 parts: 0 red, 7 yellow, 177 green; database jlcplace-db
```

The board has 49 LCSC codes, and `jlcplace-db` now holds 48 files. R26's code has no EasyEDA footprint, so there was
nothing to fit. A fit's verdict is not a row's verdict: U2's fit is yellow only because EasyEDA's land pattern
differs a little from KiCad's (every pad still lands on its own pin), and its row in the CPL that `jlcplace cpl`
writes is green.

If you followed [JLC's preview and confirm](./preview), `jlcplace-db` already exists. `fit` adds the parts it lacks.
Where it fits the same numbers as a verified entry, the entry keeps its status, verdict and verifications. The
outputs on this page start from an empty database, so yours may show verified rows where these do not.

## What a file holds

U2 and U21 are SOT-23-6 load switches. EasyEDA draws this package half a turn from KiCad, so a CPL that uses KiCad's
angle puts every pin on the wrong pad while all six pads still land on copper. Their file:

```sh
cat jlcplace-db/C2681320.json
```

```json
{
 "entries": [
  {
   "correction": {
    "dx": 0.0,
    "dy": 0.0,
    "mirror": false,
    "rot": 180.0
   },
   "ee": {
    "key": "ee-sha1:1f1f56c450245cb8",
    "title": "SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR"
   },
   "fit": {
    "alternatives": [
     {
      "conflicts": 4,
      "dx": 0.0,
      "dy": 0.0,
      "equivalent": false,
      "matched": 2,
      "mirror": true,
      "names": 0,
      "rot": 0.0
     },
     ...
    ],
    "matched": 6,
    "method": "number",
    "rms": 0.2125,
    "score": 49
   },
   "fitted": "2026-10-22",
   "kicad": {
    "fpid": "Package_TO_SOT_SMD:SOT-23-6",
    "pad_hash": "sha1:9578321e9ecef718"
   },
   "reasons": [
    "rot 0 also lands every pad, on other nets; ruled out by pad numbers",
    "rms 0.213 mm: the land patterns differ (every pad lands)"
   ],
   "status": "auto",
   "tool": "jlcplace 0.2.0",
   "verdict": "yellow",
   "verifications": []
  }
 ],
 "lcsc": "C2681320"
}
```

| field | what it is |
|---|---|
| `correction` | the correction: `rot` in degrees and `dx`, `dy` in mm, the EasyEDA footprint's origin in the KiCad footprint's own frame. `mirror` is true only when EasyEDA's footprint is a mirror image of KiCad's, which no CPL row can place |
| `ee` | the key of the EasyEDA footprint the entry was made with, and its title (the package name, for people reading the file; without the record, the check reads in it what made the fit judge a two-terminal part non-polar: BI, a crystal's title) |
| `kicad` | the KiCad footprint's library name, and the key of its pads |
| `fit` | how the fit went: method, residual (`rms`, mm), pads matched, and the other placements it considered, as numbers |
| `verdict`, `reasons` | the correction's verdict and why: what the fit found, and later what people recorded |
| `status` | how far it is trusted: `auto`, `manual`, `preview-verified` or `board-verified` (below) |
| `verifications` | every `confirm`: who, when, source, board, reference, side, keys pressed, note, and whether it was right |
| `tool`, `fitted` | the jlcplace version and the date of the fit |

The full format is in [File formats](../reference/files).

::: tip No EasyEDA data in the files
A file holds derived numbers, two hashes and the EasyEDA package name. It holds no pad positions, sizes or outlines
from EasyEDA's library, so it is safe to commit to a public repository. The EasyEDA records themselves stay in your
record cache. See [Data and licensing](../about/licensing).
:::

## Keys: which parts share an entry

An entry is keyed by both footprints:

* **The EasyEDA key** (`ee.key`) is a hash of the EasyEDA footprint's pads: their numbers, positions, sizes and
  angles. Any change that moves a pad changes it. A change to EasyEDA's record that moves no pad does not.
* **The KiCad key** (`kicad.pad_hash`) is a hash of the KiCad footprint's copper pads and, when they say something,
  their pin functions. A diode footprint whose pad 1 is the cathode and the same footprint with pad 1 the anode get
  different keys, so a verification of one never vouches for the other.

So one LCSC code on one KiCad footprint is one entry, on every board that uses it. One code on two KiCad footprints
is two entries in the same file.

`show` prints a part's keys:

```sh
jlcplace show dial-me-spinner-main.kicad_pcb --ref U2 --db jlcplace-db
```

```text
ref: U2
lcsc: C2681320
lcsc_field: LCSC
value: TPS22917DBVR
footprint: Package_TO_SOT_SMD:SOT-23-6
...
pad_hash: sha1:9578321e9ecef718
kicad_hash: sha1:9578321e9ecef718
easyeda:
  title: SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR
  key: f3d2a2ecf658447dac746eb15158a989:1690330044
  db_key: ee-sha1:1f1f56c450245cb8
  pads: 6
correction:
  source: db
  rot: 180.0
...
```

`kicad_hash` is the KiCad key the entry is stored under. Here it equals `pad_hash`, the hash of the pads alone,
because dial-me-spinner's pads carry no pin functions. `db_key` is the EasyEDA key. `key` is EasyEDA's own record id
and update time. jlcplace does not key entries by it, because it also changes when EasyEDA edits only a record's
text, which would make every verification stale for nothing.

The shared key is why a verification on one board counts on another. dial-me-spinner's main board has the 12-pin JST
connector C160411 as J1, and its other board has the same connector on the same KiCad footprint as J3. After J1 was
confirmed in the preview (`jlcplace confirm dial-me-spinner-main.kicad_pcb --ref J1 ...`), the other board's check
has one verified row, and it is J3:

```sh
jlcplace check dial-me-spinner-base.kicad_pcb released-base-CPL.csv --db jlcplace-db
```

```text
jlcplace 0.2.0: dial-me-spinner-base.kicad_pcb (sexpr, 25 parts, origin aux 100, 100) vs released-base-CPL.csv (17 rows), bottom reading jlc-2022
RED     SW1  C318884    top     renumbered: the pads land as the correction (auto) puts them, but it is red, so nothing vouches for this numbering: 2->1
             correction (fit, auto, red): numbering conflict: EE 2 -> KiCad 1 (pads on another terminal's net) and no pin names confirm it; check the pinout, or confirm the placement in JLC's preview
17 results: 1 red, 0 yellow, 15 green, 1 verified
...
```

(SW1 is red until someone records how its pins are numbered: see
[A decision of your own](./preview#a-decision-of-your-own).)

## Status and precedence

Each entry has a status, strongest first:

| status | set by | the correction's verdict |
|---|---|---|
| `board-verified` | `confirm --source board`: right on assembled boards | `verified` |
| `preview-verified` | `confirm --source preview`: right in JLC's placement preview | `verified` |
| `manual` | `confirm --source manual`: a person's decision, about the stored numbers or typed in with `--rot`, `--dx`, `--dy` | as fitted; a red one becomes yellow ("accepted manually") |
| `auto` | `fit`; `confirm` fitting a part the database lacked; `confirm --failed`, which also makes it red | as fitted; red after `--failed`, through refits of the same numbers or of numbers within 0.15 mm and 2 degrees of them, and its rows red (`verified-wrong`), until other numbers replace it or a later verification of it goes right |

A row that matches a `verified` correction is itself `verified`. The status decides two things.

**Which entry is used.** When several entries match a part's keys (an entry written by hand without keys matches
any part with its code), the strongest status wins, and among equals the later entry in the file.

**What may replace what.** `fit` never replaces a stronger entry with other numbers: it prints `not stored` and the
reason instead. `confirm` records the row you uploaded, and when that row's correction differs from the stored one it
replaces it, except where the stored entry is preview- or board-verified and the new source is no stronger. Here
the FPC connector J20 was confirmed in the preview from the released CPL, as J1 was, and the old CPL's J20 row,
4.5 mm off, is refused against its preview-verified entry:

```sh
jlcplace confirm dial-me-spinner-main.kicad_pcb --ref J20 --db jlcplace-db --by you --source preview --cpl old-main-CPL.csv
```

```text
jlcplace: J20: the stored correction is preview-verified with other numbers (rot 0, dx -0.0009, dy 1.3405; the row in old-main-CPL.csv gives rot 0, dx -4.4760, dy 1.3400): if that verification was wrong, record it with --failed and its own row first
```

Nothing is written, and the exit code is 3. A verification from the assembled boards (`--source board`) would replace
it, since it is stronger.

::: warning Refitting a manual decision
`fit --db` keeps a `manual` entry's status and verifications when it fits the same numbers, but puts the fit's own
verdict and reasons back. For SW1 on dial-me-spinner's second board that means red again, with the "accepted
manually" reason gone. After `fit --db` on a board with manual decisions, record them again with `confirm`, or
better, confirm them in JLC's preview.
:::

## When EasyEDA changes a footprint

When EasyEDA moves a pad of a footprint, the record you fetch next has a new EasyEDA key, and the entries made with
the old footprint no longer match. jlcplace calls them stale. For a part whose only entries are stale, `check` and
`cpl` fit the correction again from the new footprint and compare:

* The refit gives the same placement: it is used, with the reason "the EasyEDA footprint changed since the stored
  correction (...); the refit gives the same placement".
* The refit differs: it is used, at best yellow, with the reason "the EasyEDA footprint changed since the stored
  correction (...); the refit differs: check it in JLC's preview, then confirm it again".

Either way a verification made with the old footprint no longer counts: JLC now places the new one. Look at the part
in the preview and confirm it again, which stores an entry under the new key. The old entry stays in the file.

jlcplace notices a change only when the record cache holds the new footprint. The cache keeps a record until you
fetch it again, with `fetch --refresh` (see the [command reference](../reference/cli#jlcplace-fetch)).

A changed KiCad footprint (pads moved, renumbered, or given other pin functions) has a new KiCad key. No entry
matches it, so the part is fitted as if it were new.

## Keep it in git

Commit the database next to the board. jlcplace writes each file with sorted keys and a one-space indent, so a change
shows up in a diff as exactly what changed. After J1's preview check:

```sh
git diff jlcplace-db/C160411.json
```

```text
diff --git a/jlcplace-db/C160411.json b/jlcplace-db/C160411.json
index 3d0a54d..c44d9ee 100644
--- a/jlcplace-db/C160411.json
+++ b/jlcplace-db/C160411.json
@@ -37,10 +37,22 @@
    "reasons": [
     "fits by pad numbers: 12/12 matched (2 more with numbers KiCad does not have), rms 0.001 mm"
    ],
-   "status": "auto",
+   "status": "preview-verified",
    "tool": "jlcplace 0.2.0",
-   "verdict": "green",
-   "verifications": []
+   "verdict": "verified",
+   "verifications": [
+    {
+     "board": "dial-me-spinner-main.kicad_pcb",
+     "by": "you",
+     "date": "2026-10-22",
+     "layer": "bottom",
+     "note": "",
+     "ok": true,
+     "presses": null,
+     "ref": "J1",
+     "source": "preview"
+    }
+   ]
   }
  ],
  "lcsc": "C160411"
```

Because an entry belongs to a code on a footprint, not to a board, one database can serve several boards and several
projects, as J1 and J3 above show. To share one across repositories, keep it in a repository of its own and point
`--db` at its checkout. With several people recording verifications, `--by` says who checked what.

## Use it without the EasyEDA records

A part with a stored correction can be checked without its EasyEDA record: its own KiCad pads, taken back through the
stored correction, stand in for the EasyEDA pads. That needs no network and no record cache, which is what
[continuous integration](./ci) wants. Here `--cache no-records` points jlcplace at an empty record cache, as on a CI
runner that has none, and the old CPL is checked from the database alone:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb old-main-CPL.csv --db jlcplace-db --cache no-records
```

```text
jlcplace 0.2.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs old-main-CPL.csv (184 rows), bottom reading jlc-2022
jlcplace: no EasyEDA record in the cache no-records for 49 of the 49 codes JLC places (C1523, C1525, C1547, C1562, C1779, C2500, C7189, C7519 and 41 more), so the rows below are checked without JLC's footprints (red where a part needs one): `jlcplace fetch dial-me-spinner-main.kicad_pcb --cache no-records` downloads them (rate-limited), or pass --online
RED     J1   C160411    bottom  14 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 3.40 mm away)
RED     J20  C262671    top     11 of 32 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 4.48 mm away)
RED     J40  C160404    bottom  6 of 6 EasyEDA pads land off copper: 1, 2, 3, 4, MP, MP (nearest KiCad pad centre up to 1.40 mm away)
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only ...
YELLOW  D20  C2500      top     ok: yellow because the stored auto correction is yellow: rms 0.281 mm: the land patterns differ (every pad lands)
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (no EasyEDA record in the cache (`jlcplace fetch` or --online downloads it)); a two-pad non-polar part, and the row point is on it
YELLOW  U2   C2681320   top     ok: yellow because the stored auto correction is yellow: rms 0.213 mm: the land patterns differ (every pad lands)
YELLOW  U21  C2681320   top     ok: yellow because the stored auto correction is yellow: rms 0.213 mm: the land patterns differ (every pad lands)
YELLOW  U30  C3715882   bottom  ok: yellow because the stored auto correction is yellow: rms 0.213 mm: the land patterns differ (every pad lands)
YELLOW  U31  C189624    top     ok: yellow because the stored auto correction is yellow: rms 0.113 mm: the land patterns differ (every pad lands)
184 results: 3 red, 7 yellow, 174 green, 0 verified
...
```

The three misplaced connectors are found as they are with the records. (J40's pads are named by KiCad's numbers
here, MP for its mounting pads, because KiCad's pads stand in for EasyEDA's.) Two things differ:

* **A row is never better than its stored correction.** The simulation is only as good as the correction, so a row
  whose correction is yellow is yellow, and the first message says so. With the records, D20, U2, U21, U30 and U31
  are green; from the database alone they are yellow. Confirming them in JLC's preview makes their corrections
  verified, and their rows with them.
* **A part with no stored correction is not simulated.** R26 has none (EasyEDA has no footprint to fit), so it stays
  yellow until it is confirmed, which stores its uploaded row as its correction. A part without a stored correction
  or a record is red unless it is a two-pad non-polar part whose row point lies on its pads.

The CPL that `jlcplace cpl` wrote gives `0 red, 7 yellow, 176 green, 1 verified` the same way (J1 is the verified
row). Staleness cannot be seen without the records, so a run that has them, on your machine or in CI with the
[record cache](./ci#caching-the-easyeda-records), is still worth having.

## Merge conflicts

Two people who confirm parts of one code on two branches change the same file. Here you confirmed U41 on `main`
while Sam confirmed U40 on a branch named `teammate`. U40 and U41 are the same motor driver on the same footprint,
so they share one entry:

```sh
git merge teammate
```

```text
Auto-merging jlcplace-db/C39159.json
CONFLICT (content): Merge conflict in jlcplace-db/C39159.json
Automatic merge failed; fix conflicts and then commit the result.
```

A file with conflict markers is not JSON, and jlcplace refuses to read it rather than guess:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb dial-me-spinner-main-CPL-JLCPCB.csv --db jlcplace-db
```

```text
jlcplace: jlcplace-db/C39159.json: not a valid jlcplace correction file (Expecting property name enclosed in double quotes: line 67 column 1 (char 1345))
```

The exit code is 3, bad input, so a CI job fails too. The conflict is in the list of verifications:

```text
   "verifications": [
    {
     "board": "dial-me-spinner-main.kicad_pcb",
<<<<<<< HEAD
     "by": "you",
=======
     "by": "sam",
>>>>>>> teammate
     "date": "2026-10-22",
     "layer": "bottom",
     "note": "",
     "ok": true,
     "presses": null,
<<<<<<< HEAD
     "ref": "U41",
=======
     "ref": "U40",
>>>>>>> teammate
     "source": "preview"
    }
   ]
```

Both checks happened, so keep both:

```json
   "verifications": [
    {
     "board": "dial-me-spinner-main.kicad_pcb",
     "by": "you",
     "date": "2026-10-22",
     "layer": "bottom",
     "note": "",
     "ok": true,
     "presses": null,
     "ref": "U41",
     "source": "preview"
    },
    {
     "board": "dial-me-spinner-main.kicad_pcb",
     "by": "sam",
     "date": "2026-10-22",
     "layer": "bottom",
     "note": "",
     "ok": true,
     "presses": null,
     "ref": "U40",
     "source": "preview"
    }
   ]
```

The rules for other conflicts:

* **Both sides added verifications**: keep them all.
* **Both sides added entries** (other footprints): keep both entries in the `entries` list.
* **Both sides changed one entry's `correction`**: keep the side with the stronger status, with its `verdict`,
  `reasons` and `verifications`. When in doubt, take one side's file whole and run the other side's `confirm` again
  with the CPL it recorded; `confirm` applies the precedence rules for you.
* **`fitted` or `tool` differ**: either side will do.

Then make sure jlcplace can read the file before you commit the merge. `show` with the code reads it:

```sh
jlcplace show C39159 --db jlcplace-db
```

```text
lcsc: C39159
easyeda:
  title: WSON-8_L2.0-W2.0-P0.50-TL-EP
...
db:
  file: jlcplace-db/C39159.json
  entries: 1
    - correction={"dx": 0.0, "dy": 0.0, "mirror": false, "rot": 0.0}, ... status="preview-verified", tool="jlcplace 0.2.0", verdict="verified", verifications=[{"board": "dial-me-spinner-main.kicad_pcb", "by": "you", ... "ref": "U41", "source": "preview"}, {"board": "dial-me-spinner-main.kicad_pcb", "by": "sam", ... "ref": "U40", "source": "preview"}]
```

And the check runs again, with both drivers verified:

```sh
jlcplace check dial-me-spinner-main.kicad_pcb dial-me-spinner-main-CPL-JLCPCB.csv --db jlcplace-db
```

```text
jlcplace 0.2.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs dial-me-spinner-main-CPL-JLCPCB.csv (184 rows), bottom reading jlc-2022
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only ...
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 0 red, 2 yellow, 179 green, 3 verified
...
```

The three verified rows are J1, U40 and U41.
