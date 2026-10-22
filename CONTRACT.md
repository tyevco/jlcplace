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
* No network access unless a caller passes `offline=False` *and* the code path is in `easyeda.py`. The command line
  passes it only for `fetch` and with `--online`.
* References from boards, CPLs and BOMs that `model.ref_problem` refuses (a control character; a leading = + - @, a
  spreadsheet formula) are bad input: the readers raise ValueError naming them escaped. Text printed to a terminal or
  log goes through `model.printable` (control characters as \xNN): a newline in it would start a GitHub workflow
  command.
* Never write EasyEDA geometry anywhere except the user's cache directory (EasyEDA claims copyright over its
  library; the correction database stores derived numbers and hashes only). The one exception is the HTML report,
  a local review file that draws EasyEDA pad outlines and states their source (the JLCEDA/EasyEDA Official Library).
* Tests: `tests/test_<module>.py`, pytest, deterministic (seeded random), markers `pcbnew`, `cache`, `network`
  (see pyproject). Tests that need a file that may be absent skip with a reason. Run with
  `/home/user/jlcplace/.venv/bin/python -m pytest -q -p no:cacheprovider`.
* Fixture locations can be overridden, and every test module reads them: `JLCPLACE_DIAL_ME_SPINNER` (the
  dial-me-spinner checkout), `JLCPLACE_TEST_EASYEDA` (the directory holding `ee_raw/`, `dl/`, `fab-before/`, `src/`),
  `KICAD_FOOTPRINT_DIR` (KiCad's footprint libraries). Network tests run only with `JLCPLACE_NETWORK_TESTS=1`.
  `.github/workflows/ci.yml` runs the unmarked tests without KiCad on Linux, macOS and Windows with Python 3.10 to
  3.13 (the classifiers' versions), under `python -X warn_default_encoding` so that text I/O without an explicit
  encoding fails on every OS (pyproject's `filterwarnings`); builds and checks the sdist and wheel; runs the pcbnew
  tests in KiCad's image (kicad/kicad:9.0, its Python 3.11 in a `--system-site-packages` venv); and, optionally, the
  cache tests with records `jlcplace fetch` puts in an Actions cache. `.github/skip_gate.py` fails a job on any skip
  whose reason it does not allow. The unmarked tests assume nothing POSIX (file modes, line ends, `HOME`, `/tmp`)
  and open every text file with `encoding="utf-8"`; the fixture defaults above are the development machine's paths.
  `tests/test_packaging.py` checks pyproject, that workflow and the README's CI recipe as data.
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
  such as a KiCad 10 file for which `LoadBoard` returns None, falls back to sexpr with a warning naming the file
  once), "pcbnew",
  "sexpr", "json".
* `origin`: "aux" (setup `aux_axis_origin`; (0, 0) if absent), "grid", "absolute" (0, 0), an "X,Y" string (also
  "(x;y)", "10 20", and lengths with a unit as `cpl.parse_length` reads them: "10mm,20mm", "4in,-500mil") or a
  tuple in mm; `Board.origin_kind` is aux | grid | absolute | user. A snapshot stores
  only the origin it was made with: "aux" or its own kind keeps it, an explicit absolute/"X,Y"/tuple overrides it,
  another named origin raises ValueError, and so does a coordinate that is not a finite number ("nan,nan"). A
  snapshot keeps `source` = the reader that made it; a file that is not JSON or not a snapshot (`{}`, `[]`) raises
  ValueError, and a code in it is normalised as the readers do (`lcsc.normalize`: "C0402" is no code).
* `Board.kicad_version` is the file-format version (e.g. "20241229"). Malformed or truncated files raise ValueError,
  and so does a reference `model.ref_problem` refuses (every reader, snapshots included).
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
def check_code(code) -> str                    # " c2040 " -> "C2040"; anything not C[1-9][0-9]* raises EEError
def parse_record(code: str, result: dict) -> EEFootprint
def from_response(code, response) -> EEFootprint | NotFound    # a whole API response (the entry point for caches)
def pin_names(result) -> dict[str, str]; def title_tokens(title) -> list[str]
class EEClient:
    def __init__(self, cache_dir=None, *, offline=False, min_interval=0.7, timeout=30.0, max_failures=5,
                 notfound_ttl_days=7, opener=None, sleep=None, clock=None, now=None, rng=None, max_wait=300.0,
                 max_total_wait=600.0, user_agent=USER_AGENT)
    def get(self, code, *, refresh=False) -> EEFootprint | NotFound   # EEError: network failure, offline miss
    def raw(self, code) -> dict | None; def cache_path(self, code) -> Path
    failures, requests, disabled, waited (s slept before retries), misses (codes an offline client lacked)
```
* API: `https://easyeda.com/api/products/{code}/components?version=6.4.19.5`. Retried with BACKOFF 3, 6, 12, 24,
  48 s (the last repeats) plus up to 25 % jitter, or Retry-After when longer: HTTP 403, 408, 425, 429, 5xx, network
  errors, 2xx non-JSON bodies (a CDN's HTML page), 2xx JSON errors 403/429/5xx. Other failures (400, an HTML 404,
  JSON without a success flag) fail at once but count. A Retry-After above `max_wait` gives up, and so does a wait
  that would bring the client's retry waits above `max_total_wait` (a success resets the failure count, not this
  sum). After `max_failures` consecutive failures, or either of those, the client stops using the network for the
  run (EEError); cached records are still served. `success: false, code: 404` (and a record without a footprint)
  is NotFound, cached and trusted for `notfound_ttl_days` by file mtime; a failed refetch returns the stale NotFound.
  An offline cache miss is an EEError, and its code is added to `misses`.
* Cache: `cache_dir/<code>.json` holds the raw response verbatim (atomic write); parsing always reads that file.
* Frame: `e = 0.254 * (p - head)` (mm, y down). `EEPad.angle = r`: EasyEDA's rotation has R's sense, proven from the
  corner order of every rotated RECT pad in the cached records; RECT/OVAL pads at other angles are checked against
  their own point lists. w, h are along the pad's own axes. Pads on layers 1 (top), 2 (bottom), 11 (multi); other
  layers and unplated holed pads are dropped. Hole = 2 * radius * 0.254. POLYGON pads: poly = the point list.
* `marks`: CIRCLE on layer 101 (a pin-1 dot: it sits by pad 1 whatever its function, so it says nothing of
  polarity). `plus`: the "+" signs drawn on layers 3 / 12 (two crossing strokes, TRACKs or thin SOLIDREGIONs, alike
  in length, crossing near both middles, no other stroke near), as (x, y, bar length). `tags`: `result.tags`, the
  part categories ("Tantalum Capacitors", "TVS", ...). `pin_names` from `result.dataStr` and both multi-unit
  layouts, the first non-empty name winning, the units' before the main symbol's (C111548's main symbol repeats a
  unit with the '/' between functions lost); checked on three real multi-unit records (C20916, C111548, C108023:
  every pad named, no extra names; the test reads them from a local cache, never from this repository). `tokens`:
  TL TR BL BR RD FD CW BI. `title` = packageDetail.title (else c_para.package). `uuid` =
  packageDetail.uuid, `utime` = packageDetail.updateTime (else head.utime).

## parts.py — shared by fit, check and the pipeline

```python
NONPOLAR_REFS          # R RN RT RV C L FB F NTC Y XTAL SW JP: two terminals that may be swapped (X only for a crystal)
POLAR_FOOTPRINT        # footprint names that make such a part polar (diode, LED, tant, Elec, CP_, battery ...)
def nonpolar(part, ee=None) -> bool          # the one polarity rule fit and check use (below)
def name_tokens(name, number="") -> frozenset[str]; def compare_names(a, b) -> int
def kicad_positive_pad(part) -> (number, how) | None; def ee_positive_pad(ee) -> str | None
def uncovered_exemption(pad, covered_pads) -> str   # "inside" | "via" | "same pin" | ""
def pad_size(pad) -> float     # the smaller dimension of a KiCad pad's copper along its own axes
def norm_number(s) -> str      # upper case, '01' -> '1'
def ref_prefix(ref) -> str
def ee_key(ee) -> str          # "ee-" + geom.pad_hash(ee.pads): the database key of an EasyEDA footprint
def kicad_hash(part) -> str    # geom.pad_hash of the copper pads, with the pin functions' name tokens when informative
```
* `nonpolar`: two KiCad terminals, and either alike (pin functions equal up to an index, A1 / A2; EasyEDA token BI)
  or a non-polar reference prefix (X only when the footprint or EasyEDA title says crystal / xtal / osc: IEC 81346
  designates connectors X) with nothing that says polar: the KiCad footprint name (polar words as words, not
  "Angled" or "KOHERelec"; a right-angle body), anode / cathode pin functions, and with `ee` its title (LED, SOD,
  diode, tant, elec; a polarised capacitor's CAP-SMD_ / CASE-x_ / CP- unless its categories name a non-polar class),
  its categories (tantalum, electrolytic, polymer, diode, LED, zener, schottky, TVS, rectifier) and its symbol's
  anode / cathode pin names. Unknown parts are polar.
* `name_tokens`: as fit.py describes; a name that only repeats its pad number is uninformative unless it means
  something of its own (K on a pad numbered K). Synonyms include VO ~ VOUT, VI ~ VIN, IN ~ VIN, OUT ~ VOUT.
* `kicad_positive_pad`: from the pin functions or pad numbers (A, K, +, -), else KiCad's library convention for its
  own diode / LED (pad 1 = cathode) and polarised capacitor (pad 1 = +) libraries. `ee_positive_pad`: the pad a
  two-terminal footprint draws its one `plus` beside (not one near the middle, not marks on both sides).
* `uncovered_exemption`: KiCad copper no EasyEDA pad lands on that belongs to a covered pin (thermal vias inside a
  covered pad, a via-sized plated hole numbered like a covered SMD pad, a much smaller pad of a covered pad's number
  and net such as a DPAK's cut lead): fit and check both use it. Two equal pads of a number are never exempt.
* `ee_key` replaces `EEFootprint.key()` (uuid:updateTime) as the key corrections are stored and looked up under:
  updateTime also moves when EasyEDA edits only a record's metadata, which would make every verified correction
  stale. `kicad_hash` keys the KiCad side: the fit decides polarity and orientation by pin names too, so parts whose
  pin functions differ need entries of their own (uninformative names leave it the plain pad hash).

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
3. **Names** (`parts.name_tokens`): split at '/', ',' and parentheses, upper case, synonyms (EP/PAD/TAB ~ GND, DP ~
   D+, A ~ ANODE, K ~ CATHODE, VO ~ VOUT, ...); names equal up to a trailing index (CC1/CC2) compare as unknown. Names count only where they
   separate members of S.
4. **Equivalence**: same nets on every pad, or a terminal swap of a non-polar two-terminal part (`parts.nonpolar`,
   the rule check uses too; unknown parts are polar).
5. **+ mark** (polar two-terminal parts): `parts.ee_positive_pad` against `parts.kicad_positive_pad`.
6. **Verdict**:
   * green = the number fit lands every pad with no numbering conflict, names agree or are uninformative, and every
     other member of S is equivalent or ruled out by the pad numbers (for a polar two-terminal part, by the names
     or the + mark as well; otherwise yellow "polarity decided by pad numbers only");
   * yellow = names pick a member the numbers do not (the names win; for a polar two-terminal part only when the +
     mark sides with them, and the numbers win when it sides with them); numbering differs but the members are
     equivalent or the names (or the + mark) pick one; rms above max(0.1 mm, a quarter of the smallest KiCad pad
     the pads land on); < 80 % of the EasyEDA pads *whose number KiCad has* matched; a non-90 angle (kept
     unsnapped); KiCad lands no EasyEDA pad lands on (`parts.uncovered_exemption` exempt; each label named once);
     another placement as good on the same numbers and nets (the pads do not orient the body); all pads on one land
     (their shape picks the rotation up to 180 when they are not square or round);
   * red = nothing lands every pad (the best partial placement at a multiple of 90 is reported, the tilted best in
     the reason only); a numbering conflict (an EasyEDA pad on a land of another number and another net while its
     own number exists in KiCad) that pin names do not vouch for; several non-equivalent members that nothing
     separates ("ambiguous"); a polar two-terminal part whose names and numbers disagree and no + mark settles it,
     or whose + mark contradicts the numbers (or the names) that are its only other evidence; the mirror wins (only
     a mirror lands, or it is better supported and no normal placement gives the same nets, or as well supported and
     far more exact) -> `mirror=True`, never placed.
   Angles within `snap` of a multiple of 90 are snapped and the translation refitted. `dx, dy` (4 decimals) = the
   EasyEDA origin in the library frame. Pairs for the number fit and translation hypotheses use copper centres
   (a custom / polygon KiCad pad's outline box centre, an EasyEDA polygon pad's point-list box centre: SOT-89's tab
   is numbered with its middle lead); landing uses the pad centres, as check does; translations are anchored on at
   least two EasyEDA pads. `ee_key = parts.ee_key(ee)`, `kicad_hash = parts.kicad_hash(part)`. `doubt` =
   "polarity" | "names" | "orientation" | "" (why the choice among placements may be wrong), `polar` = whether a
   two-terminal part was judged polar. `alternatives` = [{rot, dx, dy, mirror, matched, conflicts, names,
   equivalent, nets_changed}].

## check.py — simulate any CPL against the board

```python
def place_row(part, row: CplRow, ee: EEFootprint, origin, reading="jlc-2022") -> list[PadLanding]
def check_row(part, row, ee, origin, *, corr=None, reading="jlc-2022", alt_readings=True) -> RowResult
def check_cpl(board, rows, ee_lookup, *, corrections=None, reading="jlc-2022", bom_refs=None) -> list[RowResult]
def check_bom(board, bom_rows, cpl_rows) -> list[str]
def nonpolar(part, ee=None, corr=None) -> bool     # parts.nonpolar; a correction the fit judged polar keeps it so
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
  for the numbering); else "wrong-pad", red. The numbers *decide* a row when at least EVIDENCE (80 %, and 2) of
  the EasyEDA pads whose number KiCad has land on that number or its net. Pads landing by number (or where the
  numbers decide nothing) on other nets than `corr` gives them: red against a verified correction, for a polar
  two-terminal part, or when the numbers do not decide the row; yellow against a fitted one otherwise. Against a
  red correction: a polar two-terminal part is red unless the row differs from it only harmlessly and the
  correction's doubt is not its polarity ("the polarity needs a decision"); any other row is red when the numbers
  do not decide it ("nothing vouches for it"). With no correction a row the numbers do not decide is yellow. A row
  that lands as a fitted ("auto") yellow correction does whose `doubt` is set is yellow. KiCad soldered pads no
  EasyEDA pad lands on: yellow, listed in `uncovered` (status stays "ok"; `parts.uncovered_exemption` pads are a
  message). No EasyEDA footprint: "not-simulated", yellow for a non-polar part with two copper pads whose row point
  is within 0.1 mm of its pads' box (red when it is not), red otherwise, with the reason (a polar two-pad part, a
  one-pad part, N copper pads). Bottom rows are re-placed under the other readings (`alt_readings`: True, False, or
  names): a changed pin -> net mapping or a pad leaving copper sets `convention_sensitive` (a checklist item,
  verdict unchanged) and, per such reading, `reading_moves[name]` = the moved pads as (EasyEDA number, KiCad pad
  or None off copper), natural order. With `corr`, `expected` = `cpl_row_for(...)`; a row further than 0.05 mm /
  0.5 deg gets a message; further than max(0.15 mm, a quarter of the smallest pad it lands on) or 2 deg (up to 45)
  from a correction that is not red, with every pad on copper, it is yellow, status "offset", the message first; a
  row matching a verified correction's expected row is VERIFIED.
* `check_cpl`: one result per row in order, then "no-row" (red) for parts JLC assembles with a code and no row.
  JLC assembles a part when `cpl.assembled(part)` = placed and not exclude_bom. A reference that more than one part
  JLC places carries is "duplicate-ref" (red, "R1 is on the board 2 times: JLC cannot tell them apart"): every row of
  it, or one result when it has none, and never simulated against either part (a row of a reference whose other
  parts JLC does not place is checked against the placed one). Rows for refs not on the board
  ("not-on-board"), on the wrong layer ("layer-mismatch"), repeated ("duplicate", every copy) are red; "not-placed"
  is red for DNP/exclude_pos/board_only parts and yellow when the only flag is exclude_bom, unless `bom_refs` (the
  designators a JLC BOM lists) lists it: then it is simulated like any other row, with a message. A row for a part
  with no code is checked without a footprint. `corrections` is keyed by ref or by code (the ref wins). Lookups
  happen once per code; an exception from `ee_lookup` becomes NotFound("lookup failed (...)").
* Statuses: ok | offset | off-copper | wrong-pad | renumbered | not-simulated | no-row | not-on-board |
  layer-mismatch | not-placed | duplicate | duplicate-ref.

## cpl.py — read and write CPL / BOM files

```python
def read_cpl(path, *, units="mm") -> list[CplRow]
def write_cpl(path, rows: list[CplRow])
def read_bom(path) -> list[dict]       # designators, lcsc (normalised when valid, else raw), comment, footprint, qty
def rows_for_board(board, corrections, reading="jlc-2022") -> tuple[list[CplRow], list[str]]
def assembled(part) -> bool; def correction_for(corrections, part) -> Correction | None; def natural_key(ref)
def atomic_write(path, text)           # temp file + rename; keeps an existing file's mode, else 0666 less the umask
```
* `read_cpl`: CSV, TSV or semicolons (the first delimiter under which a header names all five columns), header
  variants case-insensitive, rows in file order with duplicates kept, KiCad's ASCII `.pos` too. A value with a unit
  is converted; a bare number takes its column header's unit, KiCad's "## Unit =", or `units` (KiCad's CSV position
  file records none). ValueError with the line for a missing column or a bad value (a number that is not finite:
  "nan", "inf", "1e400"). UTF-8 (BOM or not), cp1252, and UTF-16 / UTF-32 with a byte order mark (Excel's
  "Unicode Text"); LF, CRLF or CR-only line ends; text csv cannot read is a ValueError naming the file (`read_bom`
  alike).
* `write_cpl`: JLC's header, CRLF, `%.4fmm`, Top/Bottom, rotation `norm_angle(round(norm_angle(a), 3))` with `:g`.
* `atomic_write` creates its temporary file with mode 0666 (the kernel applies the umask; the umask is never read,
  since reading it means setting it, process-wide) and gives it an existing file's mode before the rename.
* `read_cpl` and `read_bom` raise ValueError (with the line) for a designator `model.ref_problem` refuses.
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
                presses=None, note="", part=None, correction=None) -> Correction
def presses_to_row(row: CplRow, *, space=0, right=0, up=0) -> CplRow
def presses_to_correction(part, base: Correction, *, space=0, right=0, up=0) -> Correction
```
* File `<dir>/<code>.json`: `{"lcsc", "entries": [{ee: {key, title}, kicad: {fpid, pad_hash}, correction: {rot, dx,
  dy, mirror}, fit: {method, rms, matched, score, alternatives (scalars only), doubt?, polar?}, verdict, reasons,
  status, verifications: [{by, date, source, ok, board, ref, layer, presses, note}], tool, fitted}]}`; `ee.key` is
  `parts.ee_key`, `kicad.pad_hash` is `parts.kicad_hash` (pads and pin functions), `ee.title` the EasyEDA
  footprint's title (a package name, for people reading the file). Atomic writes, sorted keys, 1-space indent,
  trailing newline. `jlcplace fit --db` does not let a second part with the same keys and other numbers replace the
  entry a first part stored in the same run. A file that is not JSON (git conflict markers), whose entries do not
  hold a correction (`"rot": "x"`), or that cannot be written raises `DBError` (a ValueError) naming the file; the
  command line reports it as bad input (exit 3).
* `lookup`: board-verified > preview-verified > manual > auto, then the later entry; "" (in the call or the entry)
  matches any. Entries for the pad hash but none for the ee key: the strongest is returned with status "stale",
  verdict red, reason "footprint changed since verified"; the caller refits (pipeline: same numbers -> the refit
  stands, noted; different -> at best yellow). The returned Correction carries the entry's own keys.
* `upsert`: the same numbers keep the stored status, verifications and verified verdict; different numbers never
  replace a stronger status (ValueError).
* `confirm`: ok=True raises the status to the source's; board/preview make the verdict VERIFIED; a red entry
  confirmed manually becomes yellow with the reason "accepted manually (by): note". ok=False: status auto, verdict
  red. A verification is of the row that was uploaded: `correction` is that row's (`convention.row_correction`),
  and when its numbers differ from the entry's it replaces them (with its reasons and verdict; the status starts
  again from auto), except over a preview- or board-verified entry no weaker than the source (ValueError). Presses
  are recorded with the verification and need `correction`: they are never added to the stored numbers.
  `presses_to_row` moves the uploaded row as the preview shows it (FT convention: one space = +90 deg on the CPL
  rotation, one arrow = 0.0635 mm on the preview screen: the cpl frame on the top, x mirrored on the bottom);
  `presses_to_correction` is the same under jlc-2022 for a base correction whose row was uploaded (library delta =
  D . R(-theta) . (sigma * right, -up), sigma = -1 on the bottom).

## pipeline.py — the modules put together (used by cli.py and report.py; usable from a KiCad plugin)

```python
def correction(part, ee, db=None, *, snap=0.5) -> (Correction | None, source)     # db | fit | ""
def fallback(part, ee) -> Correction          # KiCad's own row (rot 0); yellow for a non-polar 2-pad part, else red
def resolve(board, client, db=None, *, refs=None, snap=0.5, use_fallback=False, bom_rows=None) -> dict[ref, Resolved]
def check(board, rows, resolved, *, reading=DEFAULT, bom_rows=None, cpl_path="") -> Run
def proxy_footprint(part, corr) -> EEFootprint   # KiCad pads through the inverse correction
def apply_bom_codes(board, bom_rows) -> list[(ref, board code, BOM code)]; def bom_overrides(changed) -> list[str]
def bom_listed(part, bom_rows) -> bool; def lookup_ee(client, code); def key_reasons(corr, n=2)
def needs_preview(corr) -> bool; def moves_text(res) -> str
class Resolved(part, ee, corr, source, proxy); class Run(board, rows, results, resolved, reading, cpl_path,
    bom_messages, row_lines): counts(), worst(), exit_code(fail_on), checklist() -> list[CheckItem]
class CheckItem(category, ref, lcsc, layer, rotation, what, verdict, refs, also, first)
```
* A part with no EasyEDA record (offline miss or not found) but a database correction is simulated with
  `proxy_footprint`; its verdict is capped by the correction's, and when the cap lowers it the first message says
  so ("yellow because the stored auto correction is yellow: <its key reason>"); the note on how it was simulated
  comes last. With a BOM, a code-less part not in the BOM is yellow ("JLC does not place it"), and every part takes
  its BOM line's code (JLC assembles from the BOM): `apply_bom_codes` returns the parts whose board code it
  replaced, `bom_overrides` makes their BOM messages. `resolve(bom_rows=...)` also resolves the parts the board
  only excludes from its BOM but the BOM lists (`bom_listed`), and `check` simulates their rows.
* `key_reasons`: what a person or a later refit added (latest first), then the fit's own reasons, those that make
  it yellow or red before those that only inform ("rot 0 also lands every pad, on other nets; ruled out by pad
  numbers", "N pin names agree"). `needs_preview`: the correction is red, its `doubt` is set, or a reason other than
  an rms residual ("rms 0.213 mm: the land patterns differ") or a non-90 angle makes it yellow.
* Checklist, in this order of categories: uncertain correction (correction yellow or red, not a fallback, on a
  simulated row; left out when the row is green or verified and not `needs_preview`; its reasons the preview can
  settle shown first), not simulated, bottom reading (convention-sensitive, said in pins with `moves_text`: "as
  ccw-from-top or ccw-from-top+180, pin 1 would land off copper; as jlc-2022+180, pins 1 and 2 would swap"), odd
  angle (row rotation not a multiple of 90). A part is one item, under its first category; its other categories
  are `also` and their notes follow "; also " in `what`. Alike parts are one item, `refs` naming them all and `ref`
  the one to look at: odd angle per (code, side, row verdict), `ref` the part furthest from a multiple of 90,
  `rotation` None; bottom reading per (code, side, verdict, rotation, what); uncertain correction per (code, side,
  verdict, what); never a not-simulated row or a part without a code. `first`: the bottom-reading item of a green
  row whose pins move under every other reading (the most of its pads off copper under each, then the most pads),
  `what` starting "look at this one first": it settles the reading for every bottom part. Sorted by category, then
  `first`, row verdict (red first), the correction's doubt (polarity, names, orientation), natural ref.

## report.py and cli.py

* report: `summary(run, color=False)` (one line per red or yellow result: `headline(res)`, "status: first message",
  the status left out when the message says it; text through `model.printable`; with no CPL file, "vs the rows
  jlcplace would write"; the checklist as "N items, M parts" by category, groups as ranges "C102..C112"),
  `plural(n, word)`, `ref_ranges(refs, dash)`, `to_dict(run)` (strict JSON: a landing with no copper to measure to
  has dist null; no EasyEDA geometry; checklist items with refs, also, first), `checklist_markdown` (references, file
  names and messages Markdown-escaped; red rows flagged "fix the row first"), `checklist_csv` (columns category, ref,
  lcsc, layer, rotation, row verdict, what, refs, checked; a cell starting with = + - @, tab or CR gets a leading
  apostrophe), `junit(run, fail_on=)` (characters XML 1.0 forbids become U+FFFD), `github_annotations(run,
  fail_on=)`, `html(run)` (one page, inline CSS and SVG, no external resources, noindex; file names, never paths;
  bottom drawn seen from below with x mirrored; board views in verdict colours with their own legend (the "+" is
  the CPL origin) and red and yellow parts labelled above their copper and EasyEDA pads, with a halo; the cards'
  legend above the cards; the checklist table with the row's verdict, stacked on narrow screens; the EasyEDA
  source, the JLCEDA/EasyEDA Official Library with links to https://lceda.cn/ and https://easyeda.com, declared
  above the board views and above the part cards, and in the footer).
* cli (`jlcplace`, also `python -m jlcplace`):
  `check BOARD CPL [--bom] [--db] [--online|--offline] [--cache] [--origin] [--reading] [--fail-on red|yellow]
  [--report] [--json PATH|-] [--junit] [--checklist .md|.csv] [--github-annotations] [--lcsc-field]... [--reader]
  [--units] [-q]`; `cpl BOARD -o CPL [...] [--force]` (writes, checks the written file, keeps no file when red unless
  --force: an existing CPL is left unchanged and the note says so, under -q naming the red refs; the reports of a
  run that wrote nothing name no CPL, "the rows jlcplace would write"; `-o -` is bad input; parts without a footprint
  or entry get the fallback row); `fit BOARD [--ref]... [--db] [--json]`;
  `fetch BOARD|CODE...` [--refresh]; `confirm BOARD --ref REF --db DIR --by NAME --source manual|preview|board
  [--cpl UPLOADED [--units]] [--failed] [--note] [--space/--right/--up N] [--rot --dx --dy]` (preview and board need
  --cpl or --rot, presses need --cpl; each ref's row, moved by the presses, gives the correction recorded, refs of one
  entry must agree on it, a reference two placed parts carry is refused, and everything is checked before anything
  is written); `show CODE | BOARD --ref REF [--json]`;
  `snapshot BOARD -o board.json`. `check`, `cpl`, `fit`, `show` and `confirm` read only the record cache (the
  default, also `--offline`) unless `--online` is given; `check`, `cpl` and `fit` then name the codes the cache lacked
  and say how to fetch them; `check` also says `--units in` (or mil) when most rows are red and would sit within 2
  mm of their parts in that unit. `-v` is taken before or after the command; `--origin -5,3` is read as
  `--origin=-5,3`. `check`, `cpl` and `show` warn on stderr when `--db` does not exist (`fit` and `confirm` create
  it); a `--db` that exists and is not a directory is bad input. Exit codes: 0 clean, 1 yellow (rows or BOM issues)
  with --fail-on yellow, 2 red (fetch: records that could not be fetched), 3 bad input (argparse errors, unreadable or
  unwritable files and database files included; when one output cannot be written the others still are), 4 an
  internal error (the traceback is printed): no crash exits 1; 130 interrupted (ctrl-C, "jlcplace: interrupted").

## Open

* `Pad.poly` is one ring with zero-width bridges: points within `tol` of a bridge to an island count as inside
  (`geom.pad_contains` with tol > 0; check uses 1e-3 mm). A multi-ring `Pad.polys` or bridge-aware distance in
  geom would remove it.
* Fit's rms yellow is relative to the smallest pad the pads land on (a quarter of it, at least 0.1 mm); it still
  flags correct SOT-23 (0.28 mm on 0.6 mm pads) and SOT-23-6 (0.21) fits whose land patterns differ. A per-pad,
  per-axis criterion would cut that too.
* The "+" mark reader is a heuristic on EasyEDA's drawings (two crossing strokes beside a pad). It settled all 6
  name-against-number cases in JLC's preview data; where it reads nothing, such a part is red until confirmed.
* `EEPad` has no plated flag (unplated holed pads are dropped); slot geometry is parsed but not exposed.
* pcbnew of KiCad 6-8 (calls without a layer argument) is untested; KiCad 10 variant overrides are ignored.
