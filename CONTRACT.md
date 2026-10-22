# jlcplace module contract (v0.1)

This file fixes the interfaces between modules so they can be built in parallel. `model.py`, `convention.py` and
`geom.py` are written and tested; **do not change them** — if you need a change there, report it instead. Read
their docstrings first: they define the frames (board / library / ee / cpl), `R(a)`, and the correction
`library = R(rot) . M . ee + (dx, dy)`.

Conventions for every module:
* Python >= 3.10, standard library + numpy only at runtime. `pcbnew` is optional: import it lazily, inside
  functions, and only in `board.py` (and tests marked `pcbnew`).
* mm and degrees everywhere. Angles normalised with `convention.norm_angle` before they are written or compared.
* No network access unless a caller passes `offline=False` *and* the code path is in `easyeda.py`.
* Never write EasyEDA geometry anywhere except the user's cache directory (EasyEDA claims copyright over its
  library; the correction database stores derived numbers and hashes only).
* Tests: `tests/test_<module>.py`, pytest, deterministic (seeded random), markers `pcbnew`, `cache`, `network`
  (see pyproject). Tests that need a file that may be absent skip with a reason. Run with
  `/home/user/jlcplace/.venv/bin/python -m pytest -q -p no:cacheprovider`.
* Real data available on this machine (read-only; do not copy EasyEDA records into the repo):
  * KiCad 9.0.9 with `pcbnew`, `kicad-cli`, libraries in `/usr/share/kicad/footprints`, demo boards in
    `/usr/share/kicad/demos/**.kicad_pcb`.
  * The dial-me-spinner boards (public repo tyevco/dial-me-spinner; may be used as fixtures, snapshots allowed):
    `/home/user/dial-me-spinner/hardware/kicad/{main,base}/dial-me-spinner-{main,base}.kicad_pcb`, aux origin
    (100, 100). Their correct CPLs: `/home/user/dial-me-spinner/hardware/fab/{main,base}/*-CPL-JLCPCB.csv`.
    The old buggy CPLs (7 rows wrong: main J1 J20 J40, base J1 J2 J3 SW1):
    `/tmp/claude-0/-home-user-dial-me-spinner/3708159a-bc3b-5167-8507-f5b08e1ce6c9/scratchpad/jlc/fab-before/`.
    Per-code corrections already fitted and cross-checked: `/home/user/dial-me-spinner/hardware/design/jlc_offsets.json`
    (`rot`, `dx`, `dy` in the library frame; `ee` = EasyEDA pad centres `[number, x, y]` in the ee frame).
  * Raw EasyEDA API responses for 58 codes: `.../scratchpad/jlc/ee_raw/C*.json` and `.../scratchpad/jlc/dl/*.json`
    (same scratchpad). Format: `{"success": true, "result": {...}}`, or `{"success": false, "code": 404,
    "message": "Component not found"}`.
  * Reference implementations (read for knowledge, do not copy code; licences vary):
    `.../scratchpad/jlc/src/{Bouni_kicad-jlcpcb-tools, bennymeg_Fabrication-Toolkit, yaqwsx_KiKit,
    matthewlai_JLCKicadTools, uPesy_easyeda2kicad, TousstNicolas_JLC2KiCad_lib, z2amiller_jlccheck}`.
    The z2amiller fork has JLC-preview ground truth CSVs.

---------------------------------------------------------------------------------------------------------------------

## board.py — read a KiCad board into `model.Board`

```python
def load_board(path, *, origin="aux", lcsc_fields=(), reader="auto") -> Board
def load_board_pcbnew(path, *, origin="aux", lcsc_fields=()) -> Board
def load_board_sexpr(path, *, origin="aux", lcsc_fields=()) -> Board     # no KiCad needed
def save_snapshot(board, path) / load_snapshot(path) -> Board            # JSON (Board.to_dict / from_dict)
```
* `reader`: "auto" (a `.json` path -> snapshot; else pcbnew if importable, else sexpr), "pcbnew", "sexpr".
* `origin`: "aux" (setup `aux_axis_origin`; (0, 0) if absent), "grid" (`grid_origin`), "absolute" (0, 0), an
  "X,Y" string or a tuple in mm. Store the kind in `Board.origin_kind`.
* Parts: every footprint. `ref`, `value`, `footprint` ("Lib:Name"), `layer` (bottom when on B.Cu), `x, y`,
  `angle` (= `GetOrientationDegrees()`, normalised), `fields` (all footprint fields/properties, name -> text),
  `lcsc`/`lcsc_field` from `lcsc.find_lcsc(fields, lcsc_fields)`, flags `dnp`, `exclude_pos`, `exclude_bom`,
  `board_only`, `tht` (footprint type through-hole).
* Pads in the **library frame**. KiCad files and `pad.GetFPRelativePosition()` give the *as-placed* local frame,
  in which a bottom part's y is already mirrored; a pad's `at` angle in the file is board-absolute (it includes the
  footprint angle). So for a bottom part `library = (x, -y)` and the pad angle must be mirrored too — derive it and
  prove it: for every pad, `geom.pad_outline` mapped with `convention.library_to_board` must match pcbnew's
  `pad.GetEffectivePolygon(...)`/`HitTest` on the dial-me-spinner boards and the KiCad demos.
* `Pad.copper`: True for SMD pads with copper on the part's side, plated through-hole pads, and "connect" pads;
  False for NPTH, paste-only/aperture pads with no copper layer, and pads only on the far side. `kind` per model.
  `net`, `pin_name` (pinfunction), `pin_type`, `rr` (roundrect ratio), `poly` for custom / chamfered / trapezoid
  pads (outline in the library frame; from pcbnew's effective polygon, or computed from the primitives in sexpr).
* The sexpr reader must handle KiCad 6–9 files (fields as `fp_text reference/value` + `property`, or all
  `property`; `(attr smd|through_hole [board_only] [exclude_from_pos_files] [exclude_from_bom] [dnp])`, KiCad 9
  `(dnp yes)` style if present; `(net N "name")` and newer forms). Quoted strings with escapes.
* Required test: on both dial-me-spinner boards and every KiCad demo board, `load_board_sexpr` and
  `load_board_pcbnew` agree (refs, layers, positions, angles, pad numbers/positions/sizes/angles within 1e-4 mm /
  1e-3 deg, flags, lcsc), and every pad centre mapped with `library_to_board` equals pcbnew's `pad.GetPosition()`.

## lcsc.py — find the LCSC code and other tools' correction fields

```python
LCSC_FIELDS: tuple[str, ...]      # searched in order, case-insensitive
def normalize(value) -> str        # "c2040" / " C2040 " -> "C2040"; anything not ^C\d+$ after cleanup -> ""
def find_lcsc(fields: dict, extra=()) -> tuple[str, str]   # (code, field name); extra names searched first
def correction_fields(fields: dict) -> dict[str, str]      # fields from other tools that carry offsets
```
* Names: extra first, then "LCSC", "LCSC Part", "LCSC Part #", "LCSC#", "LCSC_PN", "LCSC PN", "JLCPCB Part",
  "JLCPCB Part #", "JLC", "JLC_PN", "JLCPCB", "Part #", "PN", "P/N", "Part No.", "Part Number", "MPN" (MPN only if
  the value is a valid code).
* Correction fields (warn about double application): "FT Rotation Offset", "FT Position Offset",
  "JLCPCB Rotation Offset", "JLCPCB Position Offset", "JLCRotOffset", "JLCPosOffset", "JLCPCB_CORRECTION",
  "JLCPCB Correction", case-insensitive.

## easyeda.py — fetch, cache and parse EasyEDA footprints

```python
class EEError(Exception)
def default_cache_dir() -> pathlib.Path        # $JLCPLACE_CACHE, else $XDG_CACHE_HOME/jlcplace, else ~/.cache/jlcplace
def parse_record(code: str, result: dict) -> EEFootprint
class EEClient:
    def __init__(self, cache_dir=None, *, offline=False, min_interval=0.7, timeout=30.0, max_failures=5,
                 notfound_ttl_days=7, opener=None)   # opener: injectable urlopen for tests
    def get(self, code) -> EEFootprint | NotFound    # raises EEError on network failure / offline cache miss
    def raw(self, code) -> dict | None               # cached raw response
```
* API: `https://easyeda.com/api/products/{code}/components?version=6.4.19.5`. HTTP 403/429/5xx: back off
  3, 6, 12, 24, 48 s (+ jitter, honour Retry-After); after `max_failures` consecutive failures stop calling the
  network for this client (raise EEError). At least `min_interval` s between requests. A 200 with
  `success: false, code: 404` is `NotFound` (cache it with a timestamp; refetch after `notfound_ttl_days`).
* Cache: `cache_dir/<code>.json` holds the raw response verbatim; parsing always happens from the cache file.
* Parse `result.packageDetail.dataStr`: origin `head.x/head.y`; units 10 mil = 0.254 mm; `PAD~` fields
  `[1] shape, [2] x, [3] y, [4] w, [5] h, [6] layer, [7] net, [8] number, [9] hole radius, [10] polygon points,
  [11] rotation, [12] id, [13] hole length, [14] hole points, [15] plated ...`. Layers: 1 top, 2 bottom,
  11 multi (through-hole). Shapes RECT, OVAL, ELLIPSE, POLYGON. Convert positions, sizes and polygon points to the
  ee frame (mm, y down, relative to the origin). **Determine EasyEDA's rotation sense** (from easyeda2kicad's
  converter and by checking real records whose polygon points show the true orientation) and convert it to
  model's sense; prove it in a test. Hole diameter = 2 * radius * 0.254.
* `marks`: `CIRCLE` shapes on layer 101 (centre, ee frame). `pin_names`: from the symbol `result.dataStr`
  (including `subparts`): `P~` records, pin number -> pin name (see `.../scratchpad/jlc/eelib.py` for a working
  parse; re-implement, don't import). `tokens`: title split on `-`/`_`, keep TL TR BL BR RD FD CW. `uuid`, `utime`
  from `packageDetail`. `title` from `packageDetail.title` (or `dataStr.head.c_para.package`).
* Tests: parse every record in ee_raw/dl when present (marker `cache`, skip if the directory is absent); the
  parsed pads, rotated into the library frame with the dial-me-spinner `jlc_offsets.json` corrections, land on the
  KiCad pads; synthetic records (built in the test) for every shape/layer/rotation; the client with a fake opener:
  cache hit, not found + TTL, 403 back-off with a patched sleep, Retry-After, max_failures, offline miss.

## fit.py — work out the correction for one part

```python
def fit_part(part: Part, ee: EEFootprint, *, snap=0.5) -> Correction
def ee_landings(part: Part, ee: EEFootprint, corr: Correction) -> list[PadLanding]   # library frame
def net_map(part, ee, corr) -> dict[int, str | None]      # ee pad index -> KiCad net it lands on (None = off)
```
Algorithm (work in the library frame; use only `part.copper_pads()`; merge coincident KiCad pads):
1. **By number**: centroid per pad number on both sides (repeated numbers are safe). Kabsch normal and mirrored
   over common numbers (>= 2), and also try rot in {0, 90, 180, 270} with the mean translation. Candidates.
2. **By geometry** (ignoring numbers): candidate angles 0/90/180/270, the angle aligning the farthest-apart pad
   pairs, normal and mirrored; refine 3x with `geom.assign` (cost: centre distance + size-mismatch penalty) then
   Kabsch. Keep every candidate under which all EE pads land on copper (`geom.pad_contains`), merging duplicates
   within 0.5 deg / 0.05 mm: the set S.
3. **Names**: EE pin names (`ee.pin_names`) vs KiCad `pin_name` of the landed pads, normalised (upper case, strip
   `~{}`, `#`, `/`, `*`, spaces; synonyms: EP/PAD/EPAD/TAB/THERMAL ~ GND, DP ~ D+, DM ~ D-, SHIELD/SH/EH/MH ~ shell
   pads, A ~ ANODE, K/C ~ CATHODE). Score agreement per candidate.
4. **Net equivalence**: two candidates are equivalent if every EE pad lands on pads of the same nets.
5. **Decide** (`Correction.verdict`, `reasons`, `alternatives`, `score`, `matched`, `rms`, `method`):
   green = the number fit is in S and names agree or are uninformative and every other member of S is net-equivalent;
   yellow = numbering differs but names pick exactly one member of S (e.g. a switch numbered 1,1,2,2 vs 1-4), or
   rms > 0.1 mm while every pad lands, or < 80 % of EE pads matched by number, or a non-90 angle (kept unsnapped),
   or polarity decided by one source only;
   red = no candidate lands every pad, several non-equivalent members of S that nothing separates ("ambiguous"),
   or the mirrored fit beats the normal one ("mirrored footprint or wrong footprint") — `mirror=True`.
   Angles within `snap` deg of a multiple of 90 are snapped. `dx, dy` = EE origin in the library frame.
   `ee_key = ee.key()`, `kicad_hash = geom.pad_hash(part.copper_pads())`.
* Tests: synthetic EE footprints made from KiCad library footprints (`pcbnew.FootprintLoad` from
  `/usr/share/kicad/footprints`, test marker `pcbnew`) and from hand-built pad sets: known alpha (incl. 37 deg),
  offsets, mirroring, renumbering (1,1,2,2 vs 1-4; letters vs digits), repeated numbers (MP x2, S1 x4), missing
  pads, extra tabs; recover alpha within 0.01 deg and offsets within 1e-3 mm with the right verdicts. On the
  dial-me-spinner parts (marker `cache`, raw records in the scratchpad) the fits must reproduce `jlc_offsets.json`
  rot/dx/dy (within 0.01 mm) for every code.

## check.py — simulate any CPL against the board

```python
def place_row(part, row: CplRow, ee: EEFootprint, origin, reading="jlc-2022") -> list[PadLanding]
def check_row(part, row, ee, origin, *, corr=None, reading="jlc-2022", alt_readings=True) -> RowResult
def check_cpl(board, rows, ee_lookup, *, corrections=None, reading="jlc-2022") -> list[RowResult]
def check_bom(board, bom_rows, cpl_rows) -> list[str]
```
* `place_row`: EE pad centre e -> board via `convention.ee_to_board(e, cpl_to_board(row), row.rotation,
  row.layer, reading)` -> library via `board_to_library` -> the copper pad containing it (prefer a pad with the
  same number when several overlap, e.g. USB-C A1/B12) else None; `dist` to the nearest copper pad centre.
* `check_row` statuses and verdicts: any pad off copper -> "off-copper", red. An EE pad on a different-numbered
  KiCad pad where that EE number also exists on the KiCad side: red "wrong-pad" if the nets differ from what
  same-number placement would give, unless `corr` (from fit/db) already accepts this numbering (verdict from
  corr). Numbering differences on names one side lacks (MP, S1, A1B12) are fine (message only). KiCad soldered
  pads no EE pad lands in -> `uncovered`, yellow. `ee` is NotFound/None -> "not-simulated": yellow for 2-pad
  parts with no polarity (R, C, L, FB, by pad count 2 and ref prefix R/C/L/FB/RN), red otherwise. Bottom rows:
  re-place under every other `convention.BOTTOM_READINGS`; if the pin->net mapping changes or a pad leaves copper,
  `convention_sensitive=True` (a preview-checklist item; verdict unchanged). If `corr` is known, also compute
  the row jlcplace would write (`convention.cpl_row_for`) into `expected` and add a message when the given row
  differs by > 0.05 mm or > 0.5 deg.
* `check_cpl`: one RowResult per CPL row and per placed board part with an LCSC code but no row ("no-row", red);
  rows whose ref is not on the board ("not-on-board", red), on the wrong layer ("layer-mismatch", red),
  duplicate refs (red), rows for parts that are DNP/excluded ("not-placed", red). Parts with no LCSC code and no
  row are not reported. `ee_lookup(code) -> EEFootprint | NotFound | None`.
* `check_bom`: every CPL ref in the BOM and vice versa; BOM LCSC == board field; DNP/excluded agree; Qty ==
  number of designators; no duplicate designators; messages as strings.

## cpl.py — read and write CPL / BOM files

```python
def read_cpl(path) -> list[CplRow]
def write_cpl(path, rows: list[CplRow])          # JLC sample header: Designator, Mid X, Mid Y, Layer, Rotation
def read_bom(path) -> list[dict]                 # keys: designators (list), lcsc, comment, footprint, qty
def rows_for_board(board, corrections: dict[str, Correction], reading="jlc-2022") -> tuple[list[CplRow], list[str]]
```
* `read_cpl` accepts CSV (and TSV), header variants case-insensitive: Designator/Ref/Reference;
  Mid X/PosX/Center-X(mm)/X; Mid Y/PosY/Center-Y(mm)/Y; Layer/Side (Top/Bottom/T/B/top/bottom/F/B/Front/Back);
  Rotation/Rot/Angle. Values with or without "mm" (a value ending "mil"/"in" is converted). KiCad's own
  `.pos` ASCII format too (`# Ref Val Package PosX PosY Rot Side`). Unknown columns -> `extra`.
* `write_cpl`: 4 decimals with "mm", "Top"/"Bottom", rotation `:g` after norm_angle, never "-0.0000".
* `rows_for_board`: rows for placed parts with an LCSC code, using `convention.cpl_row_for`; returns (rows,
  refs with no correction). Sorted by ref naturally (R2 before R10).

## db.py — the shared correction database (one JSON file per code, safe to keep in git)

```python
class CorrectionDB:
    def __init__(self, path)                       # a directory
    def lookup(self, code, *, ee_key="", kicad_hash="") -> Correction | None
    def upsert(self, code, corr: Correction, *, footprint="", ee_title="", tool="jlcplace 0.1.0") -> None
    def confirm(self, code, *, ee_key, kicad_hash, by, source, board="", ref="", layer="", ok=True,
                presses=None, note="") -> Correction
    def entries(self, code) -> list[dict]
def presses_to_correction(part, base: Correction, *, space=0, right=0, up=0) -> Correction
```
* File `<dir>/<code>.json`: `{"lcsc": code, "entries": [ {ee: {fp_uuid_utime key, title}, kicad: {fpid, pad_hash},
  correction: {rot, dx, dy, mirror}, fit: {method, rms, matched, ...}, verdict, reasons, status,
  verifications: [{by, date, source, board, ref, layer, presses, note}], tool, fitted} ] }` — derived numbers
  and hashes only, never pad geometry. Keys: (ee_key, kicad_hash); the same code on two KiCad footprints is two
  entries. Writes are atomic (temp file + rename), sorted keys, 1-space indent, trailing newline.
* `lookup` precedence: board-verified > preview-verified > manual > auto. An entry whose ee_key differs from the
  given one is stale: return it with `status="stale"` and verdict red, reason "footprint changed since verified"
  (the caller refits and compares). Empty ee_key/kicad_hash = match any.
* `confirm`: records a verification; with `presses`, the correction is adjusted (`presses_to_correction`: one
  space press = 90 deg counter-clockwise; one arrow press = 0.0635 mm, converted into the library frame through
  diag(1, s) . R(-theta) with Y negated; on the bottom the X direction is mirrored, following Fabrication-Toolkit;
  record "derived from presses (FT convention)").

## report.py and cli.py — built after the modules above (second stage)

* report: self-contained HTML (inline SVG: per part KiCad pads filled with numbers/nets, EE pads outlined as JLC
  would place them from the row; whole-board top and bottom overlays, bottom drawn mirrored as JLC's preview shows
  it), JSON, preview checklist (md + csv), JUnit XML, GitHub annotations.
* cli: `jlcplace check BOARD CPL [--bom] [--db] [--offline] [--cache] [--origin] [--reading] [--fail-on red|yellow]
  [--report] [--json] [--junit] [--github-annotations] [--lcsc-field]`, `jlcplace cpl BOARD -o CPL ...`
  (fits as needed, writes, then checks its own output), `fit`, `fetch`, `confirm`, `show CODE|--ref`,
  `snapshot BOARD -o board.json`. Exit codes: 0 clean, 1 yellow with --fail-on yellow, 2 red, 3 bad input.
