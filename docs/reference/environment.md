# Exit codes and environment

This page lists what jlcplace's exit codes mean, the environment variables it reads, where it keeps JLC's EasyEDA
footprint records, and exactly when and how it uses the network. A CPL is the pick-and-place file you upload to
JLCPCB with your BOM: one row per part, with its position, side and rotation.

## Exit codes

| code | meaning |
|---|---|
| 0 | Nothing to stop for. `check` and `cpl`: no red row, and nothing yellow when `--fail-on yellow` is given. `fit`: no red fit (and no yellow one with `--fail-on yellow`). `fetch`: every record was downloaded or already cached, including codes EasyEDA answered "not found" for. `confirm`, `show`, `snapshot`: done. |
| 1 | Something is yellow and you passed `--fail-on yellow`: a yellow row or a BOM issue (`check`, `cpl`), or a yellow fit (`fit`). Nothing else exits with 1; a crash does not. |
| 2 | Something is red: a red row (`check`, `cpl`) or a red fit (`fit`). `cpl` then writes no file, unless you pass `--force`; with `--force` it writes the file and still exits with 2. `fetch`: at least one record could not be downloaded. |
| 3 | Bad input: a file that does not exist or cannot be read or written (board, CPL, BOM, database file, an output), a file jlcplace cannot parse, a database file that is not valid, a `--db` that is a file, a reference that cannot be a designator, a part `confirm` cannot record, or a bad option or value. When one output cannot be written, the others are still written, and the run exits with 3. |
| 4 | An internal error: a bug in jlcplace. It prints the traceback and `jlcplace: internal error: ... (a bug: please report it with the traceback above)`. Please report it at [github.com/tyevco/jlcplace/issues](https://github.com/tyevco/jlcplace/issues). |
| 130 | Interrupted with ctrl-C. jlcplace prints `jlcplace: interrupted`. |
| 141 | The program reading jlcplace's standard output went away (`jlcplace ... \| head`). jlcplace stops and prints nothing. |

The verdict codes on the example board (see [File formats](./files) for where it comes from):

```sh
jlcplace check example.json example-CPL-good.csv --cache cache -q
echo "exit $?"
jlcplace check example.json example-CPL-bad.csv --cache cache -q
echo "exit $?"
jlcplace check example.json example-CPL-good.csv --bom example-BOM.csv --cache cache -q --fail-on yellow
echo "exit $?"
```

```text
exit 0
exit 2
exit 1
```

The good CPL has no red or yellow row. The bad one has two red rows. The third run checks the good CPL against a BOM
that lacks R2's line: the BOM issues are yellow, and `--fail-on yellow` makes them exit with 1.

Bad input prints one line that starts with `jlcplace:` and names the file, and exits with 3:

```sh
jlcplace check example.json missing.csv --cache cache
echo "exit $?"
jlcplace check example.json example-CPL-good.csv --cache cache --db example-CPL-bad.csv
echo "exit $?"
```

```text
jlcplace: missing.csv: no such file
exit 3
jlcplace: --db example-CPL-bad.csv: not a directory (the database is a directory of JSON files)
exit 3
```

An option jlcplace does not take also exits with 3, after the usage text. (Python's argument parser would exit with
2, which would read as "red".)

```text
$ jlcplace check example.json example-CPL-good.csv --units cm
usage: jlcplace check [-h] [-v] [--origin ORIGIN] [--lcsc-field NAME]
...
jlcplace check: error: argument --units: invalid choice: 'cm' (choose from 'mm', 'in', 'mil')
$ echo $?
3
```

[Troubleshooting](../guide/troubleshooting#exit-code-3) lists more of these messages and their fixes.

Exit codes 130 and 141 come from outside jlcplace. Here `timeout` sends the interrupt that ctrl-C would, and `head`
stops reading after two lines:

```sh
timeout --preserve-status -s INT 1 jlcplace check dial-me-spinner-main.kicad_pcb old-main-CPL.csv
echo "exit $?"
jlcplace fetch dial-me-spinner-main.kicad_pcb | head -2
echo "exit ${PIPESTATUS[0]}"
```

```text
jlcplace: interrupted
exit 130
C1523: C0402, 2 pads (cached)
C1525: C0402, 2 pads (cached)
exit 141
```

141 is 128 plus the number of SIGPIPE, the code a shell reports for a program stopped this way; jlcplace returns it
on Windows too. A pipeline loses jlcplace's verdict: when a script needs it, take jlcplace's own exit code
(`${PIPESTATUS[0]}` in bash) or write the results to a file with `--json FILE`. See [Exit code
141](../guide/troubleshooting#exit-code-141).

## Environment variables

| variable | what it does |
|---|---|
| `JLCPLACE_CACHE` | the EasyEDA record cache directory. A leading `~` is expanded. `--cache DIR` on the command line wins over it. |
| `XDG_CACHE_HOME` | when `JLCPLACE_CACHE` is not set: the cache is `$XDG_CACHE_HOME/jlcplace`. When neither is set, it is `~/.cache/jlcplace`, on every system (on Windows, `.cache\jlcplace` in your user folder). |
| `HTTPS_PROXY`, `https_proxy`, `NO_PROXY`, `no_proxy` | the proxy for EasyEDA requests, read by Python's standard library (`urllib`). When the upper- and lower-case names are both set and differ, the lower-case one wins. |
| `SSL_CERT_FILE`, `SSL_CERT_DIR` | the certificates Python trusts for HTTPS, for example a company proxy's certificate bundle. |
| `NO_COLOR` | set to any value but the empty one: no colours in the terminal summary. jlcplace colours the verdicts only when its output is a terminal. |
| `PYTHONIOENCODING` | the encoding of jlcplace's terminal output, where the system's default cannot hold every character (see [Windows console](../guide/troubleshooting#windows-console)). |

The default cache, and `JLCPLACE_CACHE` pointing at another directory that holds no records yet:

```sh
jlcplace fetch C7519 C4167968
JLCPLACE_CACHE=~/other-cache jlcplace check example.json example-CPL-good.csv -q
```

```text
C7519: SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL, 6 pads (cached)
C4167968: not found (Component not found; cached)
2 codes: 1 footprint, 1 not found, 0 failed; cache .../.cache/jlcplace
jlcplace: no EasyEDA record in the cache .../other-cache for 5 of the 5 codes JLC places (C1001, C1002, C1003, C1004, C1005), so the rows below are checked without JLC's footprints (red where a part needs one): `jlcplace fetch example.json` downloads them (rate-limited), or pass --online
```

### Variables the tests read

These matter only when you run jlcplace's own tests (see [Development](../about/development)):

| variable | what it does |
|---|---|
| `JLCPLACE_NETWORK_TESTS` | `1` runs the test that talks to the EasyEDA API (marker `network`). Without it, that test skips. |
| `JLCPLACE_DIAL_ME_SPINNER` | a checkout of [dial-me-spinner](https://github.com/tyevco/dial-me-spinner): its boards, CPLs and BOMs. |
| `JLCPLACE_TEST_EASYEDA` | a directory with `ee_raw/` (cached EasyEDA records), `dl/` (more records), `fab-before/` (dial-me-spinner's old CPLs) and `src/` (other tools' test boards and JLC preview data). |
| `KICAD_FOOTPRINT_DIR` | KiCad's footprint libraries (default `/usr/share/kicad/footprints`). |
| `ALLOW_SKIPS` | for `.github/skip_gate.py`: a regular expression for the skip reasons a CI job allows. |

The defaults of `JLCPLACE_DIAL_ME_SPINNER` and `JLCPLACE_TEST_EASYEDA` are paths on the development machine;
`KICAD_FOOTPRINT_DIR` defaults to KiCad's install path on Linux. A test whose data is missing skips and says which
variable to set.

## The record cache

The cache holds JLC's footprints: one file per LCSC code, named after the code (`C7519.json`), holding the EasyEDA
API's answer byte for byte, with the licence notice EasyEDA puts in each record. jlcplace writes a file to a temporary
name and renames it, so a reader never sees half a record, and it parses records from these files every time.

```sh
ls ~/.cache/jlcplace | head -3
cat ~/.cache/jlcplace/C4167968.json
```

```text
C11490.json
C11702.json
C132339.json
{"success": false, "code": 404, "message": "Component not found"}
```

* **A record never expires.** Once cached, a footprint is used until you download it again with `jlcplace fetch
  --refresh BOARD`. Do that when you want EasyEDA's current footprints; parts whose footprint changed then get fitted
  again (see [When EasyEDA changes a footprint](../guide/database#when-easyeda-changes-a-footprint)).
* **"Not found" answers are cached too**, like C4167968 above. A command that may use the network (`fetch`, or
  another command with `--online`) asks EasyEDA again when the cached answer is more than 7 days old, going by the
  file's modification time. If asking fails, it keeps the old answer and logs a warning. Without the network,
  jlcplace trusts a cached "not found" however old it is.
* A cached file that is not valid JSON counts as missing.
* You can delete the cache at any time; `jlcplace fetch` fills it again.
* Every command but `snapshot` takes `--cache DIR` for another cache.

The records belong to the JLCEDA/EasyEDA Official Library. Keep the cache on your machine, or in a CI cache that only
your repository reads, and do not commit or publish it. See [Data and licensing](../about/licensing).

## Network

jlcplace uses the network for one thing: downloading EasyEDA records that the cache lacks.

* `fetch` always uses it, for the codes it is given or that are on the board, and only for those not in the cache
  (all of them with `--refresh`).
* `check`, `cpl`, `fit`, `show` and `confirm` use it only with `--online`, and then only for records the cache lacks.
  Without `--online` (or with `--offline`, the default) they read the cache and download nothing. `check`, `cpl` and
  `fit` then name the codes the cache lacked and how to get them, as in the run above; `confirm` refuses a part whose
  record is missing (exit code 3).
* `snapshot` never uses it.

Each record is one HTTPS request to `https://easyeda.com/api/products/CODE/components?version=6.4.19.5`, sent with
the User-Agent `jlcplace/0.2.1 (+https://github.com/tyevco/jlcplace)` and a 30 s timeout. This API is not a
documented, stable interface (see [Limits](../about/limits)), so jlcplace treats it gently:

* **Rate.** At least 0.7 s between requests. `fetch --min-interval S` sets another interval.
* **Retries.** These are retried: HTTP 403, 408, 425, 429 and 5xx, network errors and timeouts, a successful answer
  whose body is not JSON (a proxy's or CDN's page), and a successful answer holding an EasyEDA error 403, 429 or 5xx.
  The wait after the first, second, third and fourth failure in a row is 3, 6, 12 and 24 s, plus up to a quarter
  more at random. When the server sends a `Retry-After` (seconds or a date) that is longer, jlcplace waits that long.
  Other failures, such as HTTP 400 or a 404 page, fail that code at once and count as a failure.
* **Stopping.** jlcplace stops using the API for the rest of the run at the fifth failure in a row, when the server
  asks for a wait longer than 300 s, or when its retry waits would add up to more than 600 s in the run. The codes
  left then fail at once, and records already in the cache are still used. An answer resets the count of failures in
  a row, but not the total wait. EasyEDA's "not found" answer counts as an answer.

Each retry is logged on standard error. Here no proxy listens at the address given, so every request fails:

```sh
export https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9
jlcplace fetch C2040 C7519 --cache new-cache
echo "exit $?"
```

```text
jlcplace: WARNING: EasyEDA C2040: network error: [Errno 111] Connection refused; retrying in 3.7 s (failure 1 of 5)
jlcplace: WARNING: EasyEDA C2040: network error: [Errno 111] Connection refused; retrying in 6.9 s (failure 2 of 5)
jlcplace: WARNING: EasyEDA C2040: network error: [Errno 111] Connection refused; retrying in 13.2 s (failure 3 of 5)
jlcplace: WARNING: EasyEDA C2040: network error: [Errno 111] Connection refused; retrying in 24.5 s (failure 4 of 5)
C2040: failed: C2040: network error: [Errno 111] Connection refused; 5 consecutive failures, EasyEDA API disabled for this run
C7519: failed: C7519: EasyEDA API disabled for this run after 5 consecutive failures
2 codes: 0 footprints, 0 not found, 2 failed; cache new-cache
exit 2
```

The fifth failure ends the run's use of the API after about 48 s, so C7519 fails without a request. `fetch` exits
with 2 when any code failed. In CI, let the fetch step fail without failing the job; the check that follows says
which records it lacks (see [Continuous integration](../guide/ci)).
