# Continuous integration

A CPL is the pick-and-place file you upload to JLCPCB with your BOM: one row per part, giving its reference,
position, side and rotation. A CPL that was right goes wrong when someone moves a part on the board, edits the CPL
by hand, or exports it with other settings. Checking it on every push catches that before an order does. This page
sets up the check in GitHub Actions.

## The workflow

This is the recipe from jlcplace's README. jlcplace's own tests read it as a workflow and check its steps
([`tests/test_packaging.py`](../../tests/test_packaging.py)), so it stays in step with the tool:

```yaml
# .github/workflows/cpl.yml
name: CPL
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    env:
      JLCPLACE_CACHE: ~/.cache/jlcplace       # the EasyEDA record cache (jlcplace expands the ~)
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "jlcplace @ git+https://github.com/tyevco/jlcplace@975d8dc4358587dd88aff66d35695356e27b2db8"   # a commit SHA
      # EasyEDA records: never upload them, or an HTML report (it draws EasyEDA pad outlines), as an artifact of a
      # public repository; the JUnit and JSON outputs hold derived data only.
      - uses: actions/cache@v4
        with:
          path: ~/.cache/jlcplace
          key: easyeda-${{ hashFiles('hardware/board.kicad_pcb') }}
          restore-keys: easyeda-
      - run: jlcplace fetch hardware/board.kicad_pcb
        continue-on-error: true               # EasyEDA down or rate-limiting: the check says which records it lacks
      - run: >
          jlcplace check hardware/board.kicad_pcb fab/board-CPL.csv --bom fab/board-BOM.csv
          --db jlcplace-db --github-annotations --junit cpl.xml
```

To use it, change the paths to your board (in the cache key, the fetch step and the check step), your CPL and your
BOM, and commit the [correction database](./database) as `jlcplace-db` (or change `--db`). jlcplace has no
release tag yet, so the recipe pins a commit of its `main` branch. Replace it with the commit SHA of jlcplace that you
tested with (or, once there are releases, a release tag): a newer jlcplace may give other verdicts, and a pinned
version means they change only when you change the pin.

What each step does:

| step | what it does |
|---|---|
| `JLCPLACE_CACHE` | where jlcplace keeps EasyEDA records; the same directory as the cache step's `path` |
| `pip install` | installs jlcplace and numpy. No KiCad is needed (see [Without KiCad](#without-kicad)) |
| `actions/cache` | restores the EasyEDA records of an earlier run (see [Caching the EasyEDA records](#caching-the-easyeda-records)) |
| `jlcplace fetch` | downloads the records the cache lacks. It is the only step that talks to EasyEDA |
| `jlcplace check` | checks the CPL against the board, the BOM and the database, prints annotations and writes JUnit. It downloads nothing |

`check` exits with code 2 when any row is red, which fails the job. Yellow rows do not fail it unless you ask (see
[Failing on yellow](#failing-on-yellow)). Exit code 3 means bad input, such as a missing file or a database file
with merge conflict markers in it. All the codes are in [Exit codes and environment](../reference/environment).

## What a failing run shows

The commands below are the recipe's steps, run in a checkout laid out as the recipe expects, with
dial-me-spinner's main board as `hardware/board.kicad_pcb`, its old CPL with the three misplaced connectors as
`fab/board-CPL.csv`, and a database made with `jlcplace fit`. The fetch step finds every record in the cache:

```sh
jlcplace fetch hardware/board.kicad_pcb
```

```text
...
C4167968: not found (Component not found; cached)
C55109522: LED-SMD_4P-L2.2-W2.0-P1.00_WS2815C-2020-4P, 4 pads (cached)
49 codes: 48 footprints, 1 not found, 0 failed; cache .../.cache/jlcplace
```

The check step prints the summary, then one line per red or yellow row for GitHub:

```sh
jlcplace check hardware/board.kicad_pcb fab/board-CPL.csv --bom fab/board-BOM.csv --db jlcplace-db --github-annotations --junit cpl.xml
```

```text
jlcplace 0.2.1: board.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs board-CPL.csv (184 rows), bottom reading jlc-2022
RED     J1   C160411    bottom  14 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 3.40 mm away)
RED     J20  C262671    top     11 of 32 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 4.48 mm away)
RED     J40  C160404    bottom  6 of 6 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6 (nearest KiCad pad centre up to 1.40 mm away)
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only ...
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 3 red, 2 yellow, 179 green, 0 verified
Preview checklist (12 items, 99 parts): look at these in JLC's placement preview
...
::warning file=fab/board-CPL.csv,line=2,title=jlcplace BZ1 ok::lands as the correction puts it, but the fit was unsure of that placement: ...
::error file=fab/board-CPL.csv,line=141,title=jlcplace J1 off-copper::14 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 3.40 mm away); KiCad pads that no EasyEDA pad lands on: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, MP; differs from the row jlcplace writes (16.9416, -9.7815, bottom, 120): 3.399 mm and 0 deg away; ...
::error file=fab/board-CPL.csv,line=142,title=jlcplace J20 off-copper::11 of 32 EasyEDA pads land off copper: ...
::error file=fab/board-CPL.csv,line=143,title=jlcplace J40 off-copper::6 of 6 EasyEDA pads land off copper: ...
::warning file=fab/board-CPL.csv,line=153,title=jlcplace R26 not-simulated::placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
```

The exit code is 2, and the job fails.

### Annotations

`--github-annotations` prints a `::error` line for every row that fails the job and a `::warning` line for every
other yellow row and every BOM issue. GitHub turns these lines into annotations (the step's log shows them as
`Error:` and `Warning:` lines): on the run's summary page and, in a pull request that changes the CPL, on the CPL's row in the diff: `file=` is the
CPL's path as given to `check` and `line=` is the row's line in it. So run `check` from the repository root, with
paths relative to it, as the recipe does. Every message from `check` is written so that it cannot start a workflow
command of its own. GitHub shows only a limited number of annotations per step; the log and the JUnit file always
have every row.

The annotations go to standard output, so they cannot be combined with `--json -`, which writes JSON there; give
`--json` a file name instead.

### JUnit

`--junit cpl.xml` writes one test case per CPL row, named by its reference, plus one for BOM issues when there are
any. A red row is a failure. A yellow row is a failure only with `--fail-on yellow`; otherwise its messages go to the
test case's output. From the run above:

```text
<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="jlcplace" tests="184" failures="3">
  <testsuite name="jlcplace board-CPL.csv" tests="184" failures="3" errors="0" skipped="0" timestamp="2026-10-22T14:16:04">
    <testcase classname="jlcplace.board" name="BZ1">
      <system-out>yellow ok
lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only (rot 180 also lands both pads, and no pin names or drawn + confirm it): check the polarity mark in JLC's preview
...</system-out>
    </testcase>
    <testcase classname="jlcplace.board" name="C1" />
...
    <testcase classname="jlcplace.board" name="J1">
      <failure type="red" message="14 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 3.40 mm away)">14 of 14 EasyEDA pads land off copper: 1, 2, 3, 4, 5, 6, 7, 8 ... (nearest KiCad pad centre up to 3.40 mm away)
KiCad pads that no EasyEDA pad lands on: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, MP
differs from the row jlcplace writes (16.9416, -9.7815, bottom, 120): 3.399 mm and 0 deg away
...</failure>
    </testcase>
...
```

GitHub does not display JUnit files by itself. A test-report action can show them, or you can keep the file with
the run. The JUnit file holds derived data only, so uploading it is fine:

```yaml
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: cpl-junit
          path: cpl.xml
```

### The preview checklist in the job summary

The preview checklist (see [JLC's preview and confirm](./preview)) can go on the run's summary page as tick boxes.
Add `--checklist checklist.md` to the check step, and a step after it:

```yaml
      - if: always()
        run: cat checklist.md >> "$GITHUB_STEP_SUMMARY"
```

`if: always()` posts it also when the check fails. The Markdown checklist escapes everything it quotes from your
files, so a reference or a file name cannot add links or formatting to the page.

## Caching the EasyEDA records

JLC places every part with the footprint that its EasyEDA library holds for the part's LCSC code. jlcplace needs
those footprints, the EasyEDA records, to simulate the placement. Fetching them takes a while: jlcplace waits at
least 0.7 s between requests to the EasyEDA API, and backs off when EasyEDA asks it to. The recipe keeps them in the
Actions cache:

* The cache `key` is a hash of the board file, so it changes whenever the board does. `restore-keys: easyeda-`
  restores the most recent earlier cache when there is no cache for this exact board, which holds the records of
  most of its parts.
* `jlcplace fetch` downloads only the records the cache lacks: those of parts added since. When nothing is missing,
  it makes no request at all, except to ask again about a code EasyEDA did not know (a cached "not found" answer)
  once that answer is more than 7 days old.
* When the key was new and the job succeeds, `actions/cache` saves the directory under that key at the end of the
  job, records included.

`fetch` exits with code 2 when it could not get some records, for instance because EasyEDA is down or rate-limits
the runner. `continue-on-error: true` lets the job go on to the check, which says which records it lacked. Parts
with a stored correction are still checked from the database; the others are not simulated.

::: tip Saving the records even when the check fails
Because the check fails the job when a row is red, a red run does not save the records it fetched, and the next run
fetches them again. jlcplace's own workflow
([`.github/workflows/ci.yml`](../../.github/workflows/ci.yml), job `e2e`) splits the cache into
`actions/cache/restore` and `actions/cache/save` steps and saves right after a successful fetch.
:::

### Checking from the database alone

The check itself downloads nothing. Drop the cache and fetch steps, and it runs from the committed database with no
network access:

```yaml
# .github/workflows/cpl.yml, from the committed database alone
name: CPL
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "jlcplace @ git+https://github.com/tyevco/jlcplace@975d8dc4358587dd88aff66d35695356e27b2db8"   # a commit SHA
      - run: >
          jlcplace check hardware/board.kicad_pcb fab/board-CPL.csv --bom fab/board-BOM.csv
          --db jlcplace-db --github-annotations --junit cpl.xml
```

Each part with a stored correction is then simulated with its own KiCad pads, taken back through the correction.
That still catches a part that moved and a row that was edited: on the old CPL above it finds the same three red
connectors. A row's verdict is then never better than its stored correction's, so the routine yellow fits of the
database (EasyEDA's land pattern a little different from KiCad's) make their rows yellow too, and a part with no
stored correction is not simulated. [The correction database](./database#use-it-without-the-easyeda-records) shows
the output. A run with the records also notices when EasyEDA changes a footprint, which the database alone cannot.

## Failing on yellow

By default only red rows fail the job. Yellow rows are probably right but need a look in JLC's preview. To make the
job fail until every one of them has been looked at and recorded, add `--fail-on yellow`: the check then exits with
code 1 when something is yellow and nothing is red, and annotates yellow rows as errors. On dial-me-spinner's main
board with a correct CPL:

```sh
jlcplace check hardware/board.kicad_pcb fab/board-CPL.csv --bom fab/board-BOM.csv --db jlcplace-db --github-annotations --junit cpl.xml --fail-on yellow
```

```text
jlcplace 0.2.1: board.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs board-CPL.csv (184 rows), bottom reading jlc-2022
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only ...
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 0 red, 2 yellow, 182 green, 0 verified
...
::error file=fab/board-CPL.csv,line=2,title=jlcplace BZ1 ok::lands as the correction puts it, but the fit was unsure of that placement: ...
::error file=fab/board-CPL.csv,line=155,title=jlcplace R26 not-simulated::placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
```

The exit code is 1. Once BZ1's polarity and R26 have been seen right in JLC's preview, record them and commit the
database:

```sh
jlcplace confirm hardware/board.kicad_pcb --ref BZ1 --ref R26 --db jlcplace-db --by you --source preview --cpl fab/board-CPL.csv
```

```text
BZ1 C910763: preview-verified, verified: rot 0, dx -0.0001, dy -0.0001; row X 20.3979 Y 8.2412 rot 158 (jlcplace-db/C910763.json)
R26 C4167968: preview-verified, verified: rot 0, dx 0.0000, dy 0.0000; row X 7.9170 Y -20.5264 rot 0 (jlcplace-db/C4167968.json)
```

Now the check passes. With `-q` it prints nothing and exits with code 0:

```sh
jlcplace check hardware/board.kicad_pcb fab/board-CPL.csv --bom fab/board-BOM.csv --db jlcplace-db --fail-on yellow -q
```

Without the records, the same check is still yellow: the five parts whose stored fits are yellow cap their rows
(see [Checking from the database alone](#checking-from-the-database-alone)). `--cache no-records` points jlcplace at
an empty record cache, as on a runner without one:

```sh
jlcplace check hardware/board.kicad_pcb fab/board-CPL.csv --bom fab/board-BOM.csv --db jlcplace-db --fail-on yellow --cache no-records
```

```text
jlcplace 0.2.1: board.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs board-CPL.csv (184 rows), bottom reading jlc-2022
jlcplace: no EasyEDA record in the cache no-records for 49 of the 49 codes JLC places (C1523, C1525, C1547, C1562, C1779, C2500, C7189, C7519 and 41 more), so the rows below are checked without JLC's footprints (red where a part needs one): `jlcplace fetch hardware/board.kicad_pcb --bom fab/board-BOM.csv --cache no-records` downloads them (rate-limited), or pass --online
YELLOW  D20  C2500      top     ok: yellow because the stored auto correction is yellow: rms 0.281 mm: the land patterns differ (every pad lands)
YELLOW  U2   C2681320   top     ok: yellow because the stored auto correction is yellow: rms 0.213 mm: the land patterns differ (every pad lands)
YELLOW  U21  C2681320   top     ok: yellow because the stored auto correction is yellow: rms 0.213 mm: the land patterns differ (every pad lands)
YELLOW  U30  C3715882   bottom  ok: yellow because the stored auto correction is yellow: rms 0.213 mm: the land patterns differ (every pad lands)
YELLOW  U31  C189624    top     ok: yellow because the stored auto correction is yellow: rms 0.113 mm: the land patterns differ (every pad lands)
184 results: 0 red, 5 yellow, 177 green, 2 verified
...
```

So with `--fail-on yellow` and no records, those parts need confirming in the preview as well. The same goes for a
run whose fetch failed: every part that could not be simulated is yellow or red.

## Without KiCad

GitHub's runners have no KiCad, and jlcplace does not need it. It reads `.kicad_pcb` files from KiCad 5 to 10 with
its own S-expression reader, and the first line of the summary says so: `board.kicad_pcb (sexpr, ...)`.

On a machine where KiCad's `pcbnew` Python module can be imported, jlcplace reads boards with it instead. On
dial-me-spinner's main board the two readers give the same result. If you want CI to check exactly what pcbnew read,
or the board file is not in the repository the job checks out, save a snapshot of the board on a machine with KiCad
and commit it:

```sh
jlcplace snapshot hardware/board.kicad_pcb -o hardware/board.json
```

```text
jlcplace: wrote hardware/board.json: 209 parts (pcbnew), origin aux (100, 100)
```

A snapshot is a JSON file with the board's parts, pads and nets as jlcplace reads them: your own design data, and
nothing from EasyEDA. Every command that takes a board takes the snapshot in its place, on any machine:

```sh
jlcplace fetch hardware/board.json
jlcplace check hardware/board.json fab/board-CPL.csv --bom fab/board-BOM.csv
```

```text
49 codes: 48 footprints, 1 not found, 0 failed; cache .../.cache/jlcplace
jlcplace 0.2.1: board.kicad_pcb (pcbnew, 209 parts, origin aux 100, 100) vs board-CPL.csv (184 rows), bottom reading jlc-2022
YELLOW  BZ1  C910763    bottom  ok: lands as the correction puts it, but the fit was unsure of that placement: polarity decided by pad numbers only ...
YELLOW  R26  C4167968   top     placement not simulated: no EasyEDA footprint (Component not found); a two-pad non-polar part, and the row point is on it
184 results: 0 red, 2 yellow, 182 green, 0 verified
...
```

The summary names the board the snapshot was made from and the reader that made it. In the workflow, use the
snapshot's path in the fetch and check steps and in the cache key.

::: warning Remake the snapshot when the board changes
jlcplace does not compare a snapshot with the board it came from. A snapshot that is older than the board checks
the CPL against the old board. Make it again, and commit it, every time you change the board.
:::

jlcplace's own CI also runs its tests with pcbnew, in KiCad's Docker image `kicad/kicad:9.0` with a virtual
environment made with `--system-site-packages`; the `kicad` job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) shows how.

## What never to publish

::: warning EasyEDA records and the HTML report stay private
The EasyEDA records belong to the JLCEDA/EasyEDA Official Library. Keep them on the runner and in the repository's
Actions cache. Never upload the record cache as an artifact, never commit it, and never publish an HTML report
(`--report`), for example as an artifact or on GitHub Pages: the report draws EasyEDA's pad outlines. The recipe
writes no report.

The JUnit file, the JSON results (`--json`), the checklist and the annotations hold derived data only: verdicts,
messages, pad numbers, distances and package names. They are fine to upload or post. So is the correction database,
which holds derived numbers and hashes. See [Data and licensing](../about/licensing).
:::
