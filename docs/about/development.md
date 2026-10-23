# Development

This page is for working on jlcplace itself: where the code is, how to run the tests and what they need, what CI
runs, and how this site is built. A CPL is the pick-and-place file you upload to JLCPCB with your BOM: one row per
part, with its position, side and rotation.

## Set up

Clone the repository and install it in a virtual environment, with the test dependencies (pytest, hypothesis and
PyYAML):

```sh
git clone https://github.com/tyevco/jlcplace
cd jlcplace
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

```text
Cloning into 'jlcplace'...
...
Successfully installed hypothesis-6.170.5 iniconfig-2.3.1 jlcplace-0.2.0 numpy-2.5.4 packaging-26.3 pluggy-1.7.0 pygments-2.21.0 pytest-9.1.1 pyyaml-6.0.3 sortedcontainers-2.4.0
```

At run time jlcplace needs Python 3.10 or newer and numpy, nothing else. To run the tests that use KiCad's `pcbnew`
module, make the virtual environment on a machine with KiCad and let it see KiCad's Python packages:
`python3 -m venv --system-site-packages .venv`.

## Repository layout

| path | what it holds |
|---|---|
| `src/jlcplace/` | the package (below) |
| `tests/` | the tests, a `test_<module>.py` for most modules, plus `test_cli.py`, `test_e2e.py`, `test_docs.py`, `test_packaging.py` and `test_fixture_paths.py` |
| [`CONTRACT.md`](https://github.com/tyevco/jlcplace/blob/main/CONTRACT.md) | the design document: every module's interface and rules, and the open questions |
| [`README.md`](https://github.com/tyevco/jlcplace/blob/main/README.md) | the overview shown on GitHub |
| [`pyproject.toml`](../../pyproject.toml) | package metadata, the test extras, pytest's markers and warning filters |
| [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) | the tests in CI |
| [`.github/workflows/docs.yml`](../../.github/workflows/docs.yml) | this site: build for pull requests and pushes to `main`, deploy from `main` |
| [`.github/skip_gate.py`](../../.github/skip_gate.py) | fails a CI job on a test skipped for a reason the job does not allow |
| [`.github/dogfood.sh`](../../.github/dogfood.sh) | jlcplace on dial-me-spinner's shipped and old CPLs |
| `docs/` | this site (VitePress): pages, [`docs/gen_cli.py`](../gen_cli.py), the example board in `docs/examples/` |

### Modules

| module | what it does |
|---|---|
| [`model.py`](../../src/jlcplace/model.py) | the data model shared by every module, and the four frames: board (KiCad's coordinates), library (a footprint as drawn in its KiCad library), ee (an EasyEDA footprint) and cpl (JLC's coordinates) |
| [`convention.py`](../../src/jlcplace/convention.py) | how a CPL row maps to where JLC puts the part, and back: the CPL rotation for each side and the four bottom-side readings |
| [`geom.py`](../../src/jlcplace/geom.py) | geometry: rigid fits, assignment, pad outlines, point-in-pad tests and pad hashes |
| [`board.py`](../../src/jlcplace/board.py) | reads a board with pcbnew, with the S-expression reader, or from a JSON snapshot; pads come out in each footprint's library frame |
| [`lcsc.py`](../../src/jlcplace/lcsc.py) | finds a part's LCSC code in its footprint fields |
| [`easyeda.py`](../../src/jlcplace/easyeda.py) | fetches, caches and parses EasyEDA records; the only module that uses the network |
| [`parts.py`](../../src/jlcplace/parts.py) | rules fit and check share: which two-terminal parts may be turned round, pin-name tokens, the database keys |
| [`fit.py`](../../src/jlcplace/fit.py) | works out one part's correction (EasyEDA footprint to KiCad footprint) and how far to trust it |
| [`check.py`](../../src/jlcplace/check.py) | simulates a CPL: puts JLC's pads where each row says and finds what they land on |
| [`cpl.py`](../../src/jlcplace/cpl.py) | reads and writes CPL and BOM files, and makes the rows jlcplace writes |
| [`db.py`](../../src/jlcplace/db.py) | the correction database and the JLC-preview key-press convention |
| [`pipeline.py`](../../src/jlcplace/pipeline.py) | puts the modules together for one board: corrections from the database or fitted, the check of a CPL, the preview checklist. Usable from a KiCad plugin |
| [`report.py`](../../src/jlcplace/report.py) | the terminal summary, JSON, checklists, JUnit, GitHub annotations and the HTML report |
| [`cli.py`](../../src/jlcplace/cli.py) | the `jlcplace` command line and its exit codes |

## Run the tests

The tests use pytest and run from the repository root. Some of them need data that is not in the repository. They
carry a pytest marker, or skip with a reason that says what is missing:

| marker | needs | how to get it |
|---|---|---|
| `pcbnew` | KiCad's `pcbnew` Python module | a virtual environment with `--system-site-packages` on a machine with KiCad (CI uses KiCad 9) |
| `cache` | real EasyEDA records in `$JLCPLACE_TEST_EASYEDA/ee_raw` | `jlcplace fetch --cache DIR BOARD`, with that directory as `DIR` and the dial-me-spinner boards as `BOARD` |
| `network` | the EasyEDA API | `JLCPLACE_NETWORK_TESTS=1` |

The tests find their real data through environment variables (see [Variables the tests
read](../reference/environment#variables-the-tests-read)):

| variable | what it points at |
|---|---|
| `JLCPLACE_DIAL_ME_SPINNER` | a checkout of [dial-me-spinner](https://github.com/tyevco/dial-me-spinner); its `hardware/` holds the boards, CPLs and BOMs the golden tests check |
| `JLCPLACE_TEST_EASYEDA` | a directory with `ee_raw/` (EasyEDA records), `dl/` (more records), `fab-before/main` and `fab-before/base` (dial-me-spinner's old CPLs, 7 rows wrong) and `src/` (KiKit's KiCad 5 to 8 test boards in `yaqwsx_KiKit`, and the KiCad 10 boards and JLC preview results of the z2amiller fork of kicad-jlcpcb-tools in `z2amiller_jlccheck`) |
| `KICAD_FOOTPRINT_DIR` | KiCad's footprint libraries |

The `kicad` and `e2e` jobs in [`ci.yml`](../../.github/workflows/ci.yml) build this layout from public repositories
at pinned commits (their checkout steps and their "fixture locations" step), and `e2e` fills `ee_raw/` with
`jlcplace fetch`. Those steps are the recipe.

Without that data, as on a fresh clone, select the tests that need nothing, as CI's `unit` job does. The job points
the fixture variables at a directory that does not exist, and runs the tests under `python -X warn_default_encoding`,
which turns text I/O that depends on the system's encoding into an error on every system:

```sh
export JLCPLACE_DIAL_ME_SPINNER=$PWD/absent JLCPLACE_TEST_EASYEDA=$PWD/absent
python -X warn_default_encoding -m pytest -q -p no:cacheprovider -rs -m "not pcbnew and not cache and not network" --junitxml=junit-unit.xml
```

```text
sssssss..........s...................................................... [ 16%]
...
=========================== short test summary info ============================
SKIPPED [1] tests/test_board.py:49: .../absent/src/z2amiller_jlccheck/tests/fixtures/jlcfootprint/boards/variant_board/variant_board.kicad_pcb is not on this machine (JLCPLACE_DIAL_ME_SPINNER / JLCPLACE_TEST_EASYEDA say where)
...
SKIPPED [1] tests/test_cpl.py:217: needs the dial-me-spinner fab outputs in .../absent/hardware (set JLCPLACE_DIAL_ME_SPINNER)
415 passed, 9 skipped, 152 deselected in 20.95s
```

With KiCad's pcbnew and all the data in place, the whole suite runs. Two tests still skip here: one needs three
multi-unit EasyEDA records in the cache, the other the network:

```sh
python -m pytest -q -p no:cacheprovider -rs
```

```text
...
SKIPPED [1] tests/test_easyeda.py:300: no multi-unit EasyEDA record cached (`jlcplace fetch C20916 C111548 C108023` into .../jlc/ee_raw)
SKIPPED [1] tests/test_easyeda.py:508: set JLCPLACE_NETWORK_TESTS=1
574 passed, 2 skipped in 104.36s (0:01:44)
```

### The skip gate

A test that skips because its data moved would pass silently. So every CI job writes JUnit and runs
[`.github/skip_gate.py`](../../.github/skip_gate.py) on it: a skip whose reason does not match the job's `ALLOW_SKIPS`
regular expression fails the job. On the run above:

```sh
python .github/skip_gate.py junit-unit.xml
ALLOW_SKIPS="is not on this machine|needs the dial-me-spinner fab outputs" python .github/skip_gate.py junit-unit.xml
```

```text
...
::error title=unexpected skip::tests.test_cpl::test_read_real_bom: needs the dial-me-spinner fab outputs in .../absent/hardware (set JLCPLACE_DIAL_ME_SPINNER)
9 skipped, 9 not allowed
9 skipped, 0 not allowed
```

The first exits with 1, the second, with the `unit` job's allowed reasons, with 0. In GitHub Actions the gate also
lists every skip in the job summary.

### Dogfood

[`.github/dogfood.sh`](../../.github/dogfood.sh) runs jlcplace on the board it was made for. It records SW1's
switch numbering, checks dial-me-spinner's shipped CPLs, and checks that the old CPLs are red on exactly the 7 rows
that were wrong. It writes its results into the current directory, here `out/`, which git ignores:

```sh
mkdir -p out && cd out
bash ../.github/dogfood.sh "$JLCPLACE_DIAL_ME_SPINNER/hardware" "$JLCPLACE_TEST_EASYEDA/fab-before" "$JLCPLACE_TEST_EASYEDA/ee_raw"
```

```text
SW1 C318884: manual, yellow: rot 0, dx 0.0000, dy 0.0000; row X 0.0000 Y 0.0000 rot 0 (/tmp/tmp.z4eGSUuAha/C318884.json)
jlcplace 0.2.0: dial-me-spinner-main.kicad_pcb (sexpr, 209 parts, origin aux 100, 100) vs dial-me-spinner-main-CPL-JLCPCB.csv (184 rows), bottom reading jlc-2022
...
old CPLs red on exactly {'main': ['J1', 'J20', 'J40'], 'base': ['J1', 'J2', 'J3', 'SW1']}
```

## CI

[`ci.yml`](../../.github/workflows/ci.yml) runs on pushes to `main`, on pull requests, and weekly:

| job | what it runs |
|---|---|
| `unit` | the tests that need no KiCad, EasyEDA data or network, on Linux, macOS and Windows with Python 3.10 to 3.13 (the versions `pyproject.toml` claims), under `python -X warn_default_encoding`. The proxy variables point at a closed port, so a stray request fails at once. |
| `package` | builds the sdist and the wheel (a build warning fails the job), runs `twine check`, and installs and runs the wheel on its own |
| `kicad` | in KiCad's own image (`kicad/kicad:9.0`): the pcbnew tests and every other test but the EasyEDA-record and network ones, on the public dial-me-spinner boards and the other tools' KiCad 5 to 10 test boards. Nothing may skip. |
| `e2e` | the EasyEDA-record tests and the dogfood script, with the boards' records fetched by `jlcplace fetch` into an Actions cache. When the fetch fails (EasyEDA down, or rate-limiting the runner), these steps report but do not fail the job. The weekly run fetches every record again and runs the network test. |

The EasyEDA records stay on the runner and in the repository's Actions cache. The jobs upload derived results only
(JUnit and JSON), never a record or an HTML report. [`tests/test_packaging.py`](../../tests/test_packaging.py) checks
`pyproject.toml`, this workflow and the README's CI recipe as data, so that a workflow GitHub would reject fails in
the tests first.

[`docs.yml`](../../.github/workflows/docs.yml) builds this site for every pull request and every push to `main`,
after checking that the command reference is up to date, and deploys it to GitHub Pages from `main`.

## The documentation

### The command reference

[Commands](../reference/cli) is generated from jlcplace's own argument parser by
[`docs/gen_cli.py`](../gen_cli.py); do not edit it by hand. After you change an option or its help text, write the
page again:

```sh
python docs/gen_cli.py
```

```text
wrote .../docs/reference/cli.md
```

`python docs/gen_cli.py --check` exits with 1 when the page is out of date, and
[`tests/test_docs.py`](../../tests/test_docs.py) runs it. Here the help of `-q` was changed and the page not
written again:

```sh
python docs/gen_cli.py --check
```

```text
.../docs/reference/cli.md is out of date: run python docs/gen_cli.py
```

`tests/test_docs.py` also parses every `jlcplace ...` command shown in a page's shell code blocks with the real
argument parser, and checks that every sidebar link has a page. A command that is meant to fail (an option jlcplace
does not take) goes in a `text` block, which the test does not read.

### The example board

The screenshots of the HTML report come from a made-up board with made-up EasyEDA records, so that this site shows
no EasyEDA data. [`docs/examples/make_examples.py`](../examples/make_examples.py) builds it, checks two CPLs against
it, and takes the screenshots with Playwright. Its README in `docs/examples/` says how to run it.

### Build the site

The site is [VitePress](https://vitepress.dev/), in `docs/`, with its configuration in `docs/.vitepress/config.mts`.
To work on it, install its dependencies once and start the development server, which shows each page as you save it:

```sh
cd docs
npm ci
npm run docs:dev
```

```text
added 127 packages, and audited 128 packages in 2s
...
  vitepress v1.6.4

  ➜  Local:   http://localhost:5173/jlcplace/
  ➜  Network: use --host to expose
```

The site is served under `/jlcplace/`, as on GitHub Pages. A relative link to a file that is not a page (a module,
a test, the workflow) is turned into a link to that file on GitHub, so the same link works in the repository and on
the site. The production build fails on a dead link.

## The design document

[`CONTRACT.md`](https://github.com/tyevco/jlcplace/blob/main/CONTRACT.md) fixes the interfaces between the modules:
the frames and the correction, each module's functions and rules, the database format, the checklist, and the
command line. It was written before the modules were built and has been kept up to date with what was built and
proven; its last section lists what is still open. Read `model.py`, `convention.py` and `geom.py` first: they define
the frames and the correction everything else uses.

Its conventions for every module:

* Python 3.10 or newer, the standard library and numpy only at run time. `pcbnew` is imported lazily, only in
  `board.py` (and in tests marked `pcbnew`).
* Millimetres and degrees everywhere; angles normalised before they are written or compared.
* No network access except in `easyeda.py`, and only when the caller asks for it: the command line asks only for
  `fetch` and with `--online`.
* EasyEDA geometry is written nowhere but the user's cache, and the HTML report, which states its source. The
  database stores derived numbers and hashes.
* Text from boards, CPLs, BOMs, the database and EasyEDA goes to terminals and logs with its control characters
  escaped, and references that could start a workflow command or a spreadsheet formula are refused.

## Contributing

Issues and pull requests are welcome at [github.com/tyevco/jlcplace](https://github.com/tyevco/jlcplace). A part
that jlcplace gets wrong is the most useful report: give its LCSC code, its KiCad footprint, the row, and what JLC's
preview or the assembled board showed.

For a pull request:

* Add or change a test in `tests/test_<module>.py` for every change in behaviour. Tests are deterministic: seed any
  randomness. A test that needs optional data skips with a reason that says what is missing.
* Never add EasyEDA records or data drawn from them to the repository. Build the records a test needs by hand, as
  [`tests/test_cli.py`](../../tests/test_cli.py) does, or read them from the cache at `$JLCPLACE_TEST_EASYEDA`.
* Open every text file with `encoding="utf-8"`, and assume nothing POSIX in the tests that need no data (file modes,
  line ends, `HOME`, `/tmp`): they run on Windows too.
* When an interface or a rule changes, change `CONTRACT.md` with it.
* When an option or its help text changes, run `python docs/gen_cli.py`.
* Run the tests that need nothing, as CI's `unit` job does, before you push.

jlcplace is MIT licensed; see [Data and licensing](./licensing), which also says what may and may not go into the
repository from EasyEDA.
