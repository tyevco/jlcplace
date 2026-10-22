# jlcplace module contract (v0.1, as built)

This file fixes the interfaces between modules. It was written before the modules were built in parallel and has been
brought up to date with what was built and proven (the change requests accepted at integration are folded in;
"open" items are listed at the end). `model.py`, `convention.py` and `geom.py` are the foundation: read their
docstrings first. They define the frames (board / library / ee / cpl), `R(a)`, and the correction
`library = R(rot) . M . ee + (dx, dy)`.

Conventions for every module:
* Python >= 3.10, standard library + numpy only at runtime. `pcbnew` is optional: import it lazily, inside
  functions, and only in `board.py` (and tests marked `pcbnew`).
* mm and degrees everywhere. Angles normalised with `convention.norm_angle` before they are written or compared.
* No network access unless a caller passes `offline=False` *and* the code path is in `easyeda.py`.
* Never write EasyEDA geometry anywhere except the user's cache directory (EasyEDA claims copyright over its
  library; the correction database stores derived numbers and hashes only). The one exception is the HTML report,
  a local review file that draws EasyEDA pad outlines and states their source (the JLCEDA/EasyEDA Official Library).
* Tests: `tests/test_<module>.py`, pytest, deterministic (seeded random), markers `pcbnew`, `cache`, `network`
  (see pyproject). Tests that need a file that may be absent skip with a reason. Run with
  `/home/user/jlcplace/.venv/bin/python -m pytest -q -p no:cacheprovider`.
* Fixture locations can be overridden: `JLCPLACE_DIAL_ME_SPINNER` (the dial-me-spinner checkout),
  `JLCPLACE_TEST_EASYEDA` (the directory holding `ee_raw/`, `dl/`, `fab-before/`, `src/`). Network tests run only
  with `JLCPLACE_NETWORK_TESTS=1`.
* Real data used by the tests on the development machine (read-only; EasyEDA records are never copied into the repo):
  KiCad 9.0.9 (`pcbnew`, `kicad-cli`, `/usr/share/kicad/footprints`, `/usr/share/kicad/demos`); the dial-me-spinner
  boards, CPLs, BOMs and `jlc_offsets.json`; the old buggy CPLs (7 rows wrong: main J1 J20 J40, base J1 J2 J3 SW1);
  raw EasyEDA responses for 58 codes; reference tools (read for knowledge only) including the z2amiller fork with
  JLC-preview ground truth.

---------------------------------------------------------------------------------------------------------------------

## board.py — read a KiCad board into `model.Board`

```python
def load_board(path, *, origin="aux", lcsc_fields=(), reader="auto") -> Board
def load_board_pcbnew(path, *, origin="aux", lcsc_fields=()) -> Board
def load_board_sexpr(path, *, origin="aux", lcsc_fields=()) -> Board     # no KiCad needed
def board_from_pcbnew(kb, path="", *, origin="aux", lcsc_fields=()) -> Board   # a pcbnew.BOARD in memory (plugins)
def save_snapshot(board, path) / load_snapshot(path) -> Board            # JSON (Board.to_dict / from_dict)
def resolve_origin(origin, aux, grid) -> ((x, y), kind)
```
* `reader`: "auto" (a `.json` path -> snapshot; else pcbnew if importable, else sexpr; a file pcbnew cannot load,
  such as a KiCad 10 file for which `LoadBoard` returns None, falls back to sexpr with a warning), "pcbnew",
  "sexpr", "json".
* `origin`: "aux" (setup `aux_axis_origin`; (0, 0) if absent), "grid", "absolute" (0, 0), an "X,Y" string (also
  "(x;y)", "10mm,20mm") or a tuple in mm; `Board.origin_kind` is aux | grid | absolute | user. A snapshot stores
  only the origin it was made with: "aux" or its own kind keeps it, an explicit absolute/"X,Y"/tuple overrides it,
  another named origin raises ValueError. A snapshot keeps `source` = the reader that made it.
* `Board.kicad_version` is the file-format version (e.g. "20241229"). Malformed or truncated files raise ValueError.
* Parts: every footprint, as in model.Part. `fields` always holds Reference, Value, Datasheet and Description (as
  pcbnew 9 does). KiCad 5 `(attr virtual)` sets exclude_pos and exclude_bom.
* Pads in the **library frame**: `library = (x, -y)` on the bottom, and the library pad angle is psi = a - theta on
  the top and theta - a on the bottom (a = the file's board-absolute pad angle), proven against pcbnew's
  `GetPosition()` and `HitTest` on 30 boards. `Pad.x, y` is the pad's anchor (pcbnew `GetPosition()`, the hole for
  THT); when the copper is offset from it, or the pad is custom, chamfered or trapezoid, `Pad.poly` holds the exact
  outline (one even-odd ring; holes and islands joined by zero-width bridges). Consumers test landing with
  `geom.pad_contains`, never by assuming the copper is centred on (x, y).
* `Pad.copper`: True for SMD pads with copper on the part's side, plated through-hole pads and "connect" pads; False
  for NPTH, aperture pads (number "" as in pcbnew) and pads only on the far side. For front/inner/back padstacks the
  entry of the part's own side is used.

## lcsc.py — find the LCSC code and other tools' correction fields

```python
LCSC_FIELDS: tuple[str, ...]
CORRECTION_FIELDS: tuple[str, ...]
def normalize(value) -> str        # "c2040" / " C2040 " / "LCSC: C2040" -> "C2040"; else ""
def find_lcsc(fields: dict, extra=()) -> tuple[str, str]   # (code, field name); extra names searched first
def correction_fields(fields: dict) -> dict[str, str]
```
* A code is `^C[1-9][0-9]*$` after cleanup (Unicode compatibility folding, invisible characters, quotes, one leading
  "LCSC:"-style label): LCSC codes have no leading zero, so "C0402" typed into the field is a package, not a code.
* Names match ignoring case, spaces, "_" and "-", the exact spelling first. Order: extra, then "LCSC", "LCSC Part",
  "LCSC Part #", "LCSC#", "LCSC_PN", "LCSC PN", "JLCPCB Part", "JLCPCB Part #", "JLC", "JLC_PN", "JLCPCB", then the
  supplier-specific spellings other tools read ("LCSC P/N", "LCSC Part No.", "LCSC Part Number", "JLCPCB PN",
  "JLCPCB P/N", "JLCPCB Part No.", "JLCPCB Part Number", "JLC Part", "JLC Part #", "JLC Part Number"), then "Part #",
  "PN", "P/N", "Part No.", "Part Number", "MPN". A field whose value is not a code is skipped (so MPN counts only
  when it holds one).
* Correction fields: "FT Rotation Offset", "FT Position Offset", "JLCPCB Rotation Offset", "JLCPCB Position Offset",
  "JLCRotOffset", "JLCPosOffset", "JLCPCB_CORRECTION", "JLCPCB Correction", and Fabrication-Toolkit's fallbacks
  "Rotation Offset", "RotOffset", "Position Offset", "PosOffset".

## easyeda.py — fetch, cache and parse EasyEDA footprints

```python
class EEError(Exception)
def default_cache_dir() -> pathlib.Path        # $JLCPLACE_CACHE, else $XDG_CACHE_HOME/jlcplace, else ~/.cache/jlcplace
def check_code(code) -> str                    # " c2040 " -> "C2040"; anything not ^C\d+$ raises EEError
def parse_record(code: str, result: dict) -> EEFootprint
def from_response(code, response) -> EEFootprint | NotFound    # a whole API response (the entry point for caches)
def pin_names(result) -> dict[str, str]; def title_tokens(title) -> list[str]
class EEClient:
    def __init__(self, cache_dir=None, *, offline=False, min_interval=0.7, timeout=30.0, max_failures=5,
                 notfound_ttl_days=7, opener=None, sleep=None, clock=None, now=None, rng=None, max_wait=300.0,
                 user_agent=USER_AGENT)
    def get(self, code, *, refresh=False) -> EEFootprint | NotFound   # EEError: network failure, offline miss
    def raw(self, code) -> dict | None; def cache_path(self, code) -> Path
    failures, requests, disabled
```
* API: `https://easyeda.com/api/products/{code}/components?version=6.4.19.5`. Retried with BACKOFF 3, 6, 12, 24,
  48 s (the last repeats) plus up to 25 % jitter, or Retry-After when longer: HTTP 403, 408, 425, 429, 5xx, network
  errors, 2xx non-JSON bodies (a CDN's HTML page), 2xx JSON errors 403/429/5xx. Other failures (400, an HTML 404,
  JSON without a success flag) fail at once but count. A Retry-After above `max_wait` gives up. After
  `max_failures` consecutive failures the client stops using the network (EEError); cached records are still
  served. `success: false, code: 404` (and a record without a footprint) is NotFound, cached and trusted for
  `notfound_ttl_days` by file mtime; a failed refetch returns the stale NotFound.
* Cache: `cache_dir/<code>.json` holds the raw response verbatim (atomic write); parsing always reads that file.
* Frame: `e = 0.254 * (p - head)` (mm, y down). `EEPad.angle = r`: EasyEDA's rotation has R's sense, proven from the
  corner order of every rotated RECT pad in the cached records; RECT/OVAL pads at other angles are checked against
  their own point lists. w, h are along the pad's own axes. Pads on layers 1 (top), 2 (bottom), 11 (multi); other
  layers and unplated holed pads are dropped. Hole = 2 * radius * 0.254. POLYGON pads: poly = the point list.
* `marks`: CIRCLE on layer 101. `pin_names` from `result.dataStr` and both multi-unit layouts. `tokens`: TL TR BL BR
  RD FD CW. `title` = packageDetail.title (else c_para.package). `uuid` = packageDetail.uuid, `utime` =
  packageDetail.updateTime (else head.utime).

## parts.py — shared by fit, check and the pipeline

```python
NONPOLAR_REFS          # R RN RT RV C L FB F NTC Y X XTAL SW JP: two terminals that may be swapped
POLAR_FOOTPRINT        # footprint names that make such a part polar (diode, LED, tantalum, electrolytic, battery ...)
def norm_number(s) -> str      # upper case, '01' -> '1'
def ref_prefix(ref) -> str
def ee_key(ee) -> str          # "ee-" + geom.pad_hash(ee.pads): the database key of an EasyEDA footprint
```
`ee_key` replaces `EEFootprint.key()` (uuid:updateTime) as the key corrections are stored and looked up under:
updateTime also moves when EasyEDA edits only a record's metadata, which would make every verified correction stale.
`Correction.ee_key` holds `parts.ee_key` (model.py's comment still says `EEFootprint.key()`).

## fit.py — work out the correction for one part

```python
def fit_part(part: Part, ee: EEFootprint, *, snap=0.5) -> Correction
def ee_landings(part, ee, corr) -> list[PadLanding]                 # library frame
def net_map(part, ee, corr) -> dict[int, str | None]                 # ee pad index -> net ("" no net, None off)
def name_tokens(name, number="") -> frozenset[str]; def compare_names(a, b) -> int
```
Work in the library frame on `part.copper_pads()` and the EasyEDA pads not on its bottom layer; coincident KiCad
pads are merged into lands. An EasyEDA number that concatenates a land's numbers ("A1B12" on A1 + B12) names it.
1. **By number**: Kabsch, normal and mirrored, over numbers that occur once on each side; numbers repeated equally
   often on both sides are paired by position after a first fit; numbers repeated on one side only (SOT-223 pin 2 +
   tab 2) are left out; centroids per common number only as a fallback; when even that is degenerate, the unique
   placement putting every numbered pad on its own number. Also rot 0/90/180/270 with the mean translation.
2. **By geometry**: angles 0/90/180/270 and those aligning the farthest-apart pairs, normal and mirrored;
   translations voted by landed pads, refined 3x with assignment + Kabsch. Every transform landing all EasyEDA
   pads is a member of S; members landing every pad on the same lands are merged.
3. **Names**: split at '/', ',' and parentheses, upper case, synonyms (EP/PAD/TAB ~ GND, DP ~ D+, A ~ ANODE, K ~
   CATHODE, ...); names equal up to a trailing index (CC1/CC2) compare as unknown. Names count only where they
   separate members of S.
4. **Equivalence**: same nets on every pad, or a terminal swap of a non-polar two-terminal part (NONPOLAR_REFS
   unless the footprint, the EasyEDA title or anode/cathode pin names say polar; unknown parts are polar).
5. **Verdict**:
   * green = the number fit lands every pad with no numbering conflict, names agree or are uninformative, and every
     other member of S is equivalent or ruled out by the pad numbers (for a polar two-terminal part, by the names
     as well; otherwise yellow "polarity decided by pad numbers only");
   * yellow = names pick a member the numbers do not (the names win); numbering differs but the members are
     equivalent or the names pick one; rms > 0.1 mm; < 80 % of the *numbered* EasyEDA pads matched; a non-90 angle
     (kept unsnapped); KiCad lands no EasyEDA pad lands on (thermal vias inside a covered pad exempt); another
     placement as good on the same numbers and nets (the pads do not orient the body);
   * red = nothing lands every pad; a numbering conflict (an EasyEDA pad on a land of another number and another
     net while its own number exists in KiCad) that pin names do not vouch for; several non-equivalent members that
     nothing separates ("ambiguous"); the mirror wins (only a mirror lands, or it is better supported and no normal
     placement gives the same nets, or as well supported and far more exact) -> `mirror=True`, never placed.
   Angles within `snap` of a multiple of 90 are snapped and the translation refitted. `dx, dy` (4 decimals) = the
   EasyEDA origin in the library frame. `ee_key = parts.ee_key(ee)`, `kicad_hash = geom.pad_hash(copper pads)`.
   `alternatives` = [{rot, dx, dy, mirror, matched, conflicts, names, equivalent, nets_changed}].

## check.py — simulate any CPL against the board

```python
def place_row(part, row: CplRow, ee: EEFootprint, origin, reading="jlc-2022") -> list[PadLanding]
def check_row(part, row, ee, origin, *, corr=None, reading="jlc-2022", alt_readings=True) -> RowResult
def check_cpl(board, rows, ee_lookup, *, corrections=None, reading="jlc-2022") -> list[RowResult]
def check_bom(board, bom_rows, cpl_rows) -> list[str]
def nonpolar(part) -> bool
```
* `place_row`: each EasyEDA pad centre (pads on EasyEDA's bottom layer skipped) goes to the board via
  `convention.ee_to_board`, then to the library frame, then onto the copper pad containing it (`LAND_TOL` 1e-3
  mm; overlapping pads: the one with its own number, else the nearest centre) or off copper. A row on the wrong
  layer reaches only through-hole copper.
* `check_row`, compared by **nets** (a pad without a net is its own net):
  off-copper -> red. An EasyEDA pad on another-numbered pad is harmless when its own number does not exist in KiCad
  (MP, S1, A1B12; message), when the pad has the net its own number has, or when a non-polar two-terminal part
  has its terminals swapped (status "renumbered", green). Otherwise, when `corr` gives every EasyEDA pad the net the
  row gives it, status "renumbered" with the correction's verdict (red if the correction is red: nothing vouches
  for the numbering); else "wrong-pad", red. Pads landing by number on other nets than `corr` gives them: red
  against a verified correction, yellow against a fitted one, a message against a red one. KiCad soldered pads no
  EasyEDA pad lands on: yellow, listed in `uncovered` (status stays "ok"). No EasyEDA footprint: "not-simulated",
  yellow for a non-polar part with two copper pads, red otherwise. Bottom rows are re-placed under the other
  readings (`alt_readings`: True, False, or names): a changed pin -> net mapping or a pad leaving copper sets
  `convention_sensitive` (a checklist item, verdict unchanged). With `corr`, `expected` = `cpl_row_for(...)`; a row
  further than 0.05 mm / 0.5 deg gets a message; a row matching a verified correction's expected row is VERIFIED.
* `check_cpl`: one result per row in order, then "no-row" (red) for parts JLC assembles with a code and no row.
  JLC assembles a part when `cpl.assembled(part)` = placed and not exclude_bom. Rows for refs not on the board
  ("not-on-board"), on the wrong layer ("layer-mismatch"), repeated ("duplicate", every copy) are red; "not-placed"
  is red for DNP/exclude_pos/board_only parts and yellow when the only flag is exclude_bom. A row for a part with no
  code is checked without a footprint. `corrections` is keyed by ref or by code (the ref wins). Lookups happen once
  per code; an exception from `ee_lookup` becomes NotFound("lookup failed (...)").
* Statuses: ok | off-copper | wrong-pad | renumbered | not-simulated | no-row | not-on-board | layer-mismatch |
  not-placed | duplicate (model.py's comment lacks "duplicate").

## cpl.py — read and write CPL / BOM files

```python
def read_cpl(path, *, units="mm") -> list[CplRow]
def write_cpl(path, rows: list[CplRow])
def read_bom(path) -> list[dict]       # designators, lcsc (normalised when valid, else raw), comment, footprint, qty
def rows_for_board(board, corrections, reading="jlc-2022") -> tuple[list[CplRow], list[str]]
def assembled(part) -> bool; def correction_for(corrections, part) -> Correction | None; def natural_key(ref)
def atomic_write(path, text)           # temp file + rename; keeps an existing file's mode, else the umask's
```
* `read_cpl`: CSV, TSV or semicolons (the first delimiter under which a header names all five columns), header
  variants case-insensitive, rows in file order with duplicates kept, KiCad's ASCII `.pos` too. A value with a unit
  is converted; a bare number takes its column header's unit, KiCad's "## Unit =", or `units` (KiCad's CSV position
  file records none). ValueError with the line for a missing column or a bad value.
* `write_cpl`: JLC's header, CRLF, `%.4fmm`, Top/Bottom, rotation `norm_angle(round(norm_angle(a), 3))` with `:g`.
* `rows_for_board`: rows for assembled parts with a code; refs without a correction or with a mirrored one are
  returned as missing. Both sorted naturally.

## db.py — the shared correction database (one JSON file per code, safe to keep in git)

```python
class CorrectionDB:
    def __init__(self, path)
    def file(self, code) -> Path; def entries(self, code) -> list[dict]
    def lookup(self, code, *, ee_key="", kicad_hash="") -> Correction | None
    def upsert(self, code, corr: Correction, *, footprint="", ee_title="", tool="jlcplace 0.1.0") -> None
    def confirm(self, code, *, ee_key, kicad_hash, by, source, board="", ref="", layer="", ok=True,
                presses=None, note="", part=None) -> Correction
def presses_to_correction(part, base: Correction, *, space=0, right=0, up=0) -> Correction
```
* File `<dir>/<code>.json`: `{"lcsc", "entries": [{ee: {key, title}, kicad: {fpid, pad_hash}, correction: {rot, dx,
  dy, mirror}, fit: {method, rms, matched, score, alternatives (scalars only)}, verdict, reasons, status,
  verifications: [{by, date, source, ok, board, ref, layer, presses, note}], tool, fitted}]}`; `ee.key` is
  `parts.ee_key`. Atomic writes, sorted keys, 1-space indent, trailing newline.
* `lookup`: board-verified > preview-verified > manual > auto, then the later entry; "" (in the call or the entry)
  matches any. Entries for the pad hash but none for the ee key: the strongest is returned with status "stale",
  verdict red, reason "footprint changed since verified"; the caller refits (pipeline: same numbers -> the refit
  stands, noted; different -> at best yellow). The returned Correction carries the entry's own keys.
* `upsert`: the same numbers keep the stored status, verifications and verified verdict; different numbers never
  replace a stronger status (ValueError).
* `confirm`: ok=True raises the status to the source's; board/preview make the verdict VERIFIED; a red entry
  confirmed manually becomes yellow with the reason "accepted manually (by): note". ok=False: status auto, verdict
  red. Presses (need `part`) replace the correction (method manual, FT convention: one space = +90 deg in the
  preview, one arrow = 0.0635 mm, library delta = D . R(-theta) . (sigma * right, -up), sigma = -1 on the bottom).

## pipeline.py — the modules put together (used by cli.py and report.py; usable from a KiCad plugin)

```python
def resolve(board, client, db=None, *, refs=None, snap=0.5, use_fallback=False) -> dict[ref, Resolved]
def correction(part, ee, db=None, *, snap=0.5) -> (Correction | None, source)     # db | fit | ""
def fallback(part, ee) -> Correction          # KiCad's own row (rot 0); yellow for a non-polar 2-pad part, else red
def check(board, rows, resolved, *, reading=DEFAULT, bom_rows=None, cpl_path="") -> Run
def proxy_footprint(part, corr) -> EEFootprint   # KiCad pads through the inverse correction
def apply_bom_codes(board, bom_rows) -> list[str]; def lookup_ee(client, code); def key_reasons(corr, n=2)
class Resolved(part, ee, corr, source, proxy); class Run(board, rows, results, resolved, reading, cpl_path,
    bom_messages, row_lines): counts(), worst(), exit_code(fail_on), checklist() -> list[CheckItem]
```
* A part with no EasyEDA record (offline miss or not found) but a database correction is simulated with
  `proxy_footprint`; its verdict is capped by the correction's. With a BOM, a code-less part not in the BOM is yellow
  ("JLC does not place it"), and code-less parts take their BOM codes.
* Checklist: bottom reading (convention-sensitive), not simulated, odd angle (row rotation not a multiple of 90),
  uncertain correction (correction yellow or red and not a fallback).

## report.py and cli.py

* report: `summary(run, color=False)`, `to_dict(run)` (JSON; no EasyEDA geometry), `checklist_markdown`,
  `checklist_csv`, `junit(run, fail_on=)`, `github_annotations(run, fail_on=)`, `html(run)` (one page, inline CSS
  and SVG, no external resources; bottom drawn seen from below with x mirrored; footer carries the EasyEDA source
  notice).
* cli (`jlcplace`, also `python -m jlcplace`):
  `check BOARD CPL [--bom] [--db] [--offline] [--cache] [--origin] [--reading] [--fail-on red|yellow] [--report]
  [--json PATH|-] [--junit] [--checklist .md|.csv] [--github-annotations] [--lcsc-field]... [--reader] [--units]
  [-q]`; `cpl BOARD -o CPL [...] [--force]` (writes, checks the written file, keeps no file when red unless
  --force; parts without a footprint or entry get the fallback row); `fit BOARD [--ref]... [--db] [--json]`;
  `fetch BOARD|CODE...` [--refresh]; `confirm BOARD --ref REF --db DIR --by NAME --source manual|preview|board
  [--failed] [--note] [--space/--right/--up N] [--rot --dx --dy]`; `show CODE | BOARD --ref REF [--json]`;
  `snapshot BOARD -o board.json`. Exit codes: 0 clean, 1 yellow (rows or BOM issues) with --fail-on yellow, 2 red
  (fetch: records that could not be fetched), 3 bad input (argparse errors included).

## Open

* `Pad.poly` is one ring with zero-width bridges: points within `tol` of a bridge to an island count as inside
  (`geom.pad_contains` with tol > 0; check uses 1e-3 mm). A multi-ring `Pad.polys` or bridge-aware distance in
  geom would remove it.
* Fit's "rms > 0.1 mm" yellow flags many correct parts whose land patterns differ (SOT-23 0.28 mm, SOT-23-6 0.21);
  a size-relative criterion would cut the noise.
* `EEPad` has no plated flag (unplated holed pads are dropped); slot geometry is parsed but not exposed.
* Multi-unit EasyEDA symbols are handled in both known layouts but unverified on a real record.
* pcbnew of KiCad 6-8 (calls without a layer argument) is untested; KiCad 10 variant overrides are ignored.
