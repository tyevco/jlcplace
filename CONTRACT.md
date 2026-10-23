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
  command. Text read from Gerber files (layer functions, attribute values, net and reference names) is untrusted
  alike: it goes through `model.printable` before it is printed, and is escaped in SVG and HTML.
* Never write EasyEDA geometry anywhere except the user's cache directory (EasyEDA claims copyright over its
  library; the correction database stores derived numbers and hashes only). The exceptions are the HTML report and
  the overlay images (`--overlay`), local review files that draw EasyEDA pad outlines and state their source (the
  JLCEDA/EasyEDA Official Library).
* Tests: `tests/test_<module>.py`, pytest, deterministic (seeded random), markers `pcbnew`, `cache`, `network`,
  `claude` (see pyproject). Tests that need a file that may be absent skip with a reason. Run with
  `/home/user/jlcplace/.venv/bin/python -m pytest -q -p no:cacheprovider`.
* Fixture locations can be overridden, and every test module reads them: `JLCPLACE_DIAL_ME_SPINNER` (the
  dial-me-spinner checkout), `JLCPLACE_TEST_EASYEDA` (the directory holding `ee_raw/`, `dl/`, `fab-before/`, `src/`),
  `KICAD_FOOTPRINT_DIR` (KiCad's footprint libraries). Network tests run only with `JLCPLACE_NETWORK_TESTS=1`.
  `.github/workflows/ci.yml` runs the unmarked tests without KiCad on Linux, macOS and Windows with Python 3.10 to
  3.13 (the classifiers' versions), under `python -X warn_default_encoding` so that text I/O without an explicit
  encoding fails on every OS (pyproject's `filterwarnings`); builds and checks the sdist and wheel; runs the pcbnew
  tests in KiCad's image (kicad/kicad:9.0, its Python 3.11 in a `--system-site-packages` venv); validates the Claude
  Code marketplace and plugin (`.claude-plugin/`, `plugins/jlcplace/`, `tests/test_claude_plugin.py`) with a pinned
  `claude` CLI, the only job that runs the `claude` tests, and fails a pull request that changes the plugin without
  raising its version (`.github/plugin_version_gate.py`: installed copies update only then; the version is the
  package's); and, optionally, the cache tests with records
  `jlcplace fetch` puts in an Actions cache. `.github/skip_gate.py` fails a job on any skip
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
def load_board_pcbnew(path, *, origin="aux", lcsc_fields=()) -> Board   # NewerFormat (OSError) for a newer file
def file_version(path) -> int | None                                       # the file's (version N)
def load_board_sexpr(path, *, origin="aux", lcsc_fields=()) -> Board     # no KiCad needed
def board_from_pcbnew(kb, path="", *, origin="aux", lcsc_fields=()) -> Board   # a pcbnew.BOARD in memory (plugins)
def save_snapshot(board, path) / load_snapshot(path) -> Board            # JSON (Board.to_dict / from_dict)
def resolve_origin(origin, aux, grid) -> ((x, y), kind)
```
* `reader`: "auto" (a `.json` path -> snapshot; else pcbnew if importable, else sexpr; a file pcbnew cannot load,
  such as a KiCad 10 file for which `LoadBoard` returns None, falls back to sexpr with a warning naming the file
  once: "PATH: written by a newer KiCad (version 20260206) than pcbnew 9.0.9 reads; reading it with the S-expression
  reader"), "pcbnew", "sexpr", "json". When `LoadBoard` returns None, `load_board_pcbnew` raises `NewerFormat` (an
  OSError) if the file's `(version N)` (`file_version`) is past pcbnew's `SEXPR_BOARD_FILE_VERSION` ("written by a
  newer KiCad (version 20260206); pcbnew 9.0.9 cannot read it (it reads up to version 20241229); use --reader sexpr
  or that KiCad's Python"), else OSError ("pcbnew 9.0.9 could not load it (file format version N)"): neither names
  the path, which the command line says once.
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
  entry of the part's own side is used. `Pad.cu`: the board's outer copper the pad has as placed (top | bottom | both;
  "" for NPTH and paste apertures, and in snapshots made before it was recorded), from its layers in either reader:
  an edge-mount connector's pads on the far side of its part are plotted on that side (gerber.board_pads).
* A footprint's filled graphics on its own paste layer (fp_poly, fp_rect, fp_circle on F.Paste, B.Paste when
  flipped: stencil openings, as KiCad's ICS-43434 or SMA footprints draw them) follow its pads as paste apertures in
  either reader: number "", kind smd, copper False, shape custom, `poly` the outline, (x, y) its box centre. KiCad
  plots them on the paste layer, so gerber.board_pads(apertures=True) has them; board-level paste graphics are not
  read.
* `Part.body` = (x0, y0, x1, y1), library frame: the box of the footprint's drawing on its own side's courtyard layer
  (F.CrtYd, B.CrtYd when flipped), else on its fabrication layer (text left out), from the centre lines of lines,
  rectangles, circles, arcs (ends and axis extremes), polygons and curves, in either reader (within the board's max
  error of each other); `Part.body_layer` = courtyard | fab | "" (neither drawn: body None, also in older snapshots).
  JLC places a part by its centre; KiCad's anchor is pin 1 on most through-hole connectors.

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
  layers are dropped, and unplated holed pads are holes. Hole = 2 * radius * 0.254. POLYGON pads: poly = the point
  list.
* `holes`: unplated holes as (x, y, diameter), ee frame: `HOLE~x~y~radius~id~locked` (a connector's peg, a mounting
  hole) and pads whose drill is unplated (`plated` N); a malformed one is skipped (holes are evidence only).
* `marks`: CIRCLE on layer 101 (a pin-1 dot: it sits by pad 1 whatever its function, so it says nothing of
  polarity). `plus`: the "+" signs drawn on layers 3 / 12 (two crossing strokes, TRACKs or thin SOLIDREGIONs, alike
  in length, crossing near both middles, no other stroke near), as (x, y, bar length). `tags`: `result.tags`, the
  part categories ("Tantalum Capacitors", "TVS", ...). `pin_names` from `result.dataStr` and both multi-unit
  layouts, the first non-empty name winning, the units' before the main symbol's (C111548's main symbol repeats a
  unit with the '/' between functions lost); checked on three real multi-unit records (C20916, C111548, C108023:
  every pad named, no extra names; the test reads them from a local cache, never from this repository). `tokens`:
  TL TR BL BR RD FD CW BI. `title` = packageDetail.title (else c_para.package). `uuid` =
  packageDetail.uuid, `utime` = packageDetail.updateTime (else head.utime).

## gerber.py — read Gerber sets and find where their origin lies on the board

```python
class GerberError(ValueError)                # "file: block N: why"
def read_gerbers(path) -> GerberSet          # a directory (its files), a .zip (every member, <= 512 MB), or one file
def parse_gerber(text, name="") -> GerberLayer; def read_gerber_text({name: text}) -> GerberSet
def classify(name) -> str; def function_kind(function) -> (kind, side)
class GerberSet(path, layers, skipped): find(kind, side=None), pads(side, kinds=("copper", "paste")), outline,
    extents, x2_pins
class GerberLayer(name, function, kind, side, source, polarity, units, software, attributes, flashes, regions, draws,
    notes): pads(), bbox()
class GPad(x, y, w, h, poly, kind, aperture, params, at, dark, function, ref, pin, pin_name, net, shapes): contains(q)
class GDraw(x1, y1, x2, y2, width, arc, dark, function, ref, pin, net): points(most=MAX_PRIMITIVE), extent()
def board_pads(board, *, apertures=False) -> list[BoardPad]      # board frame, copper box centres, DNP parts too
def board_origins(board) -> {kind: (x, y)}  # its CPL origin under its kind, "absolute", "aux" / "grid" from the file
def gerber_origin(board, gerbers, cpl_origin=None, *, cpl_origin_kind=None, tol=MATCH_TOL, edge=None) -> OriginCheck
def gerber_to_board(g, origin, flip="none", scale=1.0); def board_to_gerber(b, origin, flip="none", scale=1.0)
def shifted_rows(rows, check) -> list[CplRow]                     # ValueError for a mirrored or scaled set
MATCH_TOL = 0.02; ORIGIN_TOL = 0.01; MIN_FRACTION = 0.3; MIN_MATCHED = 3; BELONGS = 0.5; OUTLINE_TOL = 0.1; FLIPS
SCALES; MAX_ZIP; MAX_OBJECTS = 1_000_000; MAX_VERTICES = 10_000_000; MAX_PRIMITIVE = 4096; MAX_APERTURE = 65536
```
* Frame **gerber**: a file's coordinates in mm (inch files converted), y UP. KiCad plots board point b with plot
  origin o at g = (bx - ox, oy - by) = `convention.board_to_cpl(b, o)`, proven pad by pad on kicad-cli sets of the
  dial-me-spinner boards and five KiCad demos, from the drill/place and the page origin, with and without X2. JLC
  lays a CPL over the Gerbers from their (0, 0): a CPL is right only when written from that origin.
* Reader: FS (L/T zero omission, absolute or incremental, explicit decimals), MO / G70 / G71, AD (C, R, O, P with holes,
  macros), AM (primitives 1, 2/20, 21, 22, 4, 5, 6, 7, variables, arithmetic), D01-D03, G01-G03 with G74 / G75, regions,
  LP, LM / LR / LS, SR, AB, OF / SF, X2 TF / TA / TO / TD and KiCad's `G04 #@!` X1 comments. Layer function from
  TF.FileFunction, else a .gbrjob, else the file name (Protel, Eagle, KiCad names: KiCad's drawing layers, Fab,
  Courtyard, Adhesive, User_n, Margin, are drawings whatever the project's name holds; generic words count only as
  words, "cu" not in "circuit"). Excellon, job files (a .gbrjob whose JSON has Header or FilesAttributes; any other is
  "not a Gerber job file"), documents, nested zips and macOS metadata (members under __MACOSX/, ._name AppleDouble
  twins, .DS_Store) go to `skipped`; so do layers the check does not use (inner copper, mask, legend, drawings: their
  function read from the header's TF.FileFunction or G04 #@! comment, the job file or the name, before parsing), unread.
  Only outer copper, paste, the profile and files nothing names are parsed. A directory's files and a zip's members
  total at most MAX_ZIP; a zip member that cannot be read (encrypted, Deflate64, corrupt) is a GerberError naming it.
  Gerber text is untrusted: the files of a set that would expand together (step and repeat, blocks, macros, apertures,
  the samples of arcs in regions; a stroke that is a pad counts as its outline) past MAX_OBJECTS objects or MAX_VERTICES
  vertices (one budget for the set, spent as objects are made), a macro primitive past MAX_PRIMITIVE vertices (or moire
  rings), an outline naming more points than it gives, an extended block never closed, a command at the end of the file
  without its '*' (truncated: read in one pass, never in time quadratic in it), or an aperture past MAX_APERTURE
  vertices is a GerberError; `check --gerbers` reports a MemoryError while reading as bad input too. Extents and
  bounding boxes use an arc's ends and axis extremes, sampling nothing. A directory with no Gerber files names its
  subdirectories that hold some ("a directory of Gerber files: pass it as the path"); with nothing read, `gerber_origin`
  says so, naming such a directory, a zip among the files ("gerbers.zip is a zip (pass it as the path)"), or the layers
  it holds instead (mask, silk, inner copper). A pad is what TO.P or a *Pad AperFunction (not ViaPad) says; without
  attributes every flash, and a region on paste, with TO.C or at most 10 mm across. Pads compare by the centre of their
  outline's bounding box (it commutes with every flip).
* `gerber_origin` fits g = s . F . b + t over F in FLIPS (none = KiCad's diag(1, -1), y, x, xy) and s in {1, 25.4,
  1/25.4}: from (ref, pin, side) pairs when the copper carries TO.P and at least half agree (method x2-pins; when they
  agree only at another scale, the message says it: an FS that does not match the data), else by translation voting with
  48 anchors per side, cells scored by their 3 x 3 neighbourhood (a round origin splits its votes over up to four cells)
  and one candidate per peak, plus the board's named origins, each refined by median, the most pads matched winning
  (pad-voting); the sound map wins ties. `origin` = the board point that is Gerber (0, 0) (None when fewer than
  MIN_FRACTION of the board's pads, and MIN_MATCHED, match), `named` the board origin it is (aux | absolute | grid |
  user | ""). Against `cpl_origin` (default board.origin): `verdict` matches | does-not-match (any flip or scale too) |
  unknown, `shift` = (cx - ox, oy - cy) to add to every row. Coverage (`compared`, `coverage`, `missing`): the board's
  pads on each side the set has layers for, against that side's Gerber pads (SMD pads against copper and paste, plated
  and connect pads against copper; a plated pad on both sides, counted once); `belongs` = some compared and coverage >=
  BELONGS; `extra`: paste pads on no board pad (`extra_origin` / `extra_layers`: whole paste layers that are a shifted
  copy of the board's pads, plotted from another origin than the copper); `mismatched`: with TO.P, copper pads whose
  (ref, pin) is no board pad's at their place, or whose TO.N is another net (parts swapped, nets changed: "R1:1 is where
  the Gerbers have R2:1"); `stale` = belongs and anything missing, extra or mismatched. `problems` [(verdict, side,
  message)] about the set as a whole: red for two plots in one set (more than one outer copper or paste layer of a side,
  profile by attribute, or job file), a side the board has SMD pads on and the set has no copper for, an outline
  (profile) that through the copper's map is not the board's `edge` (Edge.Cuts polylines, board frame: moved by one
  vector, plotted from another origin; or another size) or, without `edge`, holds under half of the board's pads; yellow
  for a side with copper but no paste; red for a set with no outline (no profile layer, or an empty one) when `edge` is
  given. `messages` start with the verdict; a side without layers is a note, not stale; a set whose pads cannot be
  compared says which files were taken for which layer and names the files nothing names that hold flashes.

## parts.py — shared by fit, check and the pipeline

```python
NONPOLAR_REFS          # R RN RT RV C L FB F NTC Y XTAL SW JP: two terminals that may be swapped (X only for a crystal)
POLAR_FOOTPRINT        # footprint names that make such a part polar (diode, LED, tant, Elec, CP_, battery ...)
def nonpolar(part, ee=None) -> bool          # the one polarity rule fit and check use (below)
def trimmer(part, ee=None) -> bool           # a two-terminal trimmer capacitor: its rotor is a terminal of its own
def name_tokens(name, number="") -> frozenset[str]; def compare_names(a, b) -> int
def kicad_positive_pad(part) -> (number, how) | None; def ee_positive_pad(ee) -> str | None
def uncovered_exemption(pad, covered_pads) -> str   # "inside" | "via" | "same pin" | ""
def body_centre(part) -> (x, y) | None   # the centre of Part.body, library frame (KiCad's anchor is (0, 0))
def anchor_pin(part) -> str    # the copper pad centred on KiCad's anchor ("1" on a THT connector), else ""
BODY_TOL = 0.1                 # mm: a body centre this close to the anchor is the anchor
def pad_size(pad) -> float     # the smaller dimension of a KiCad pad's copper along its own axes
def norm_number(s) -> str      # upper case, '01' -> '1'
def ref_prefix(ref) -> str
def ee_key(ee) -> str          # "ee-" + geom.pad_hash(ee.pads): the database key of an EasyEDA footprint
def kicad_hash(part) -> str    # geom.pad_hash of the copper pads, with the pin functions' name tokens when informative
class Pin(NamedTuple): number, name, tokens, net=None   # name as written (messages); net: the KiCad side's net key
class PinDiff: ee_number, ee_name, kicad_number, kicad_name, kind     # kind: swapped | same net | differs
def pin(number, name, net=None) -> Pin
def pin_diffs(pairs, ee_pins, kicad_pins) -> list[PinDiff]  # pairs [(EasyEDA Pin, KiCad Pin)] of the pads on copper
def pinout_text(diffs, n=4, board="on the board") -> str   # "pad 2 is S on the LCSC part, D on the board; ..."
```
* `nonpolar`: two KiCad terminals, and either alike (pin functions equal up to an index, A1 / A2; EasyEDA token BI) or
  a non-polar reference prefix (X only when the footprint or EasyEDA title says crystal / xtal / osc: IEC 81346
  designates connectors X) with nothing that says polar: the KiCad footprint name (polar words as words, not "Angled"
  or "KOHERelec"; a right-angle body, `ORIENTED_FOOTPRINT`: Angled, right angle, or "Horizontal" in KiCad's
  Connector*, TerminalBlock*, Button_Switch*, Rotary_Encoder, Potentiometer* and Crystal libraries or on a footprint
  named SW_, PinHeader_, PinSocket_, Conn_, Crystal_, ...: not an axial passive lying flat, nor fuse clips), a trimmer
  (`trimmer`: C_Trim / C_Trimmer / Trimmer in the footprint name, or "trimmer" in the EasyEDA title or a part
  category: turned round its rotor moves to the other pad), anode / cathode pin functions, and with `ee` its title
  (LED, SOD, diode, tant, elec; a polarised capacitor's CAP-SMD_ / CASE-x_ / CP- unless its categories name a
  non-polar class), its categories (tantalum, electrolytic, polymer, diode, LED, zener, schottky, TVS, rectifier) and
  its symbol's anode / cathode pin names. Unknown parts are polar.
* `name_tokens`: as fit.py describes; a name that only repeats its pad number is uninformative unless it means
  something of its own (K on a pad numbered K), and so are LOCATION_NAMES (EP, PAD, EPAD, TAB, THERMAL, THERMALPAD:
  where a pad is, not what it is; a regulator's tab is often its output). A name split at '/' also gives its parts
  joined (P0.00/XL1 is P0.00_XL1). Synonyms include VO ~ VOUT, VI ~ VIN, IN ~ VIN, OUT ~ VOUT, Input ~ VIN, Output ~
  VOUT, VSS ~ GND. `compare_names`, for comparing only: ANODE (+) ~ IN+, CATHODE (-) ~ IN-, V+ ~ VDD, V- / VEE ~ GND,
  R- ~ RK (an RGB LED's cathodes); an op-amp's signed inputs VIN+, +IN, IN1+, +IN1, INA+ (units A-D are 1-4) read as
  1IN+ (IN+ without a unit), split supplies VCC+ / VS+ / +VS as VDD and VCC- / VS- / -VS as GND (a plain VIN stays a
  regulator's input); names equal up to an index agree when only one side numbers it (DP1 / D+, 1IN+ / +) and are
  unknown when both do, differently (CC1 / CC2, 1IN+ / 2IN+); G against GND is unknown. `kicad_hash`
  keeps the tokens the database keys were made with (location names as GND, no joined parts), so they do not move.
* `kicad_positive_pad`: from the pin functions ("pin names") or pad numbers (A, K, +, -: "pad numbers"), else
  KiCad's library convention for its own diode / LED (pad 1 = cathode) and polarised capacitor (pad 1 = +)
  libraries. `ee_positive_pad`: the pad a two-terminal footprint draws its one `plus` beside (not one near the
  middle, not marks on both sides).
* `uncovered_exemption`: KiCad copper no EasyEDA pad lands on that belongs to a covered pin (thermal vias inside a
  covered pad, a via-sized plated hole numbered like a covered SMD pad, a much smaller pad of a covered pad's number
  and net such as a DPAK's cut lead): fit and check both use it. Two equal pads of a number are never exempt.
* `pin_diffs`: the pads where `compare_names` < 0, in the order of `pairs`. "swapped": the EasyEDA name is the
  function of another KiCad pad on another net and the KiCad function the name of another EasyEDA pad (the names are
  permuted: a pin sits on the net meant for another); "same net": a KiCad pad with the EasyEDA name is on the net the
  pad lands on (harmless); "differs": everything else (B/E/C against G/D/S). Also "swapped": a named EasyEDA pin on a
  pad whose function says nothing (NC, ~) while the KiCad pads of its function are on other nets that no EasyEDA pin
  of that function (equal up to an index: unit 2's input beside unit 1's) reaches (a dual LM358's VCC on a single
  op-amp's NC pad). Names equal up to a trailing index compare as unknown, so they are never diffs.
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
   D+, A ~ ANODE, K ~ CATHODE, VO ~ VOUT, ...); names equal up to a trailing index (CC1/CC2) compare as unknown. Names
   count only where they separate members of S. A polar two-terminal part whose lands have no name tokens gets ANODE
   / CATHODE on them from `parts.kicad_positive_pad` (its library convention, or pads numbered A / K), so that
   EasyEDA's + / -, A / K / C count as names against it; never a part with more terminals (an op-amp's + / -, a
   transistor's C), and the lands only (`kicad_hash` and the check's pinout read the pads' own pin functions); a
   reason naming such a land's name says where it comes from ("+ by KiCad's library convention (pad 1 = cathode)",
   "- by its pad number"), not "on the board".
4. **Equivalence**: same nets on every pad, or a terminal swap of a non-polar two-terminal part (`parts.nonpolar`,
   the rule check uses too; unknown parts are polar).
5. **+ mark** (polar two-terminal parts): `parts.ee_positive_pad` against `parts.kicad_positive_pad`. Pad numbers,
   names and the + mark are the polarity's evidence: the correction never takes the side most of it rejects.
   **Holes**: `EEFootprint.holes` against the KiCad part's NPTH pads (kind "npth"): the one member, of those in
   question, under which every EasyEDA hole lands in an NPTH (a peg goes in one way only) is told apart. A polar
   two-terminal part's alternative half turn is ruled out by the pad numbers and the holes when they agree (green,
   "rot A also lands both pads; ruled out by the pad numbers and the holes: the EasyEDA footprint's hole lands in
   KiCad's unplated holes only at rot B"); the holes and the numbers outvote a drawn + against them (yellow,
   "polarity evidence conflicts"); holes that fit only the other way round than the numbers place it are red
   ("polarity undecided", the correction follows the holes). Holes also pick among members on the same numbers and
   nets (the body's orientation: no doubt) and among members that numbers and names leave equal (yellow, "placed by
   the holes", doubt "orientation"); holes that land under another member than the chosen one and not under it make
   the fit yellow ("check the part's pegs", doubt "orientation"). Either side without holes: nothing.
6. **Verdict**:
   * green = the number fit lands every pad with no numbering conflict, names agree or are uninformative, and every
     other member of S is equivalent or ruled out by the pad numbers (for a polar two-terminal part, by the names,
     the + mark or the holes as well, none against them; otherwise yellow "polarity decided by pad numbers only"); the
     first reason "fits by pad numbers: N/M matched" ("fits by pad numbers and pin names" when names agree, none
     differ);
   * yellow = pin names that differ otherwise (the reason names the pads: "N pin name(s) differ (M agree): ...");
     names pick a member the numbers do not (the names win; for a polar two-terminal part only when the +
     mark sides with them, "following the pin names and the drawn +", and the numbers win when it sides with them,
     "following the pad numbers and the drawn +"); a polar two-terminal part whose + mark is against the pad numbers
     alone (the + is followed) or against pad numbers and names that agree (they are followed): doubt "polarity",
     reason "polarity evidence conflicts: the pad numbers [and the pin names] put + on pad X (KiCad's positive pad,
     by ...), the drawn + on pad Y of the EasyEDA footprint; following ... (rot A, not rot B): ..."; where the
     polarity evidence takes the placement the numbers do not, no "matched by number" reason repeats it; numbering
     differs but the members are
     equivalent or the names (or the + mark) pick one; rms above max(0.1 mm, a quarter of the smallest KiCad pad
     the pads land on); < 80 % of the EasyEDA pads *whose number KiCad has* matched; a non-90 angle (kept
     unsnapped); KiCad lands no EasyEDA pad lands on (`parts.uncovered_exemption` exempt; each label named once);
     another placement as good on the same numbers and nets (the pads do not orient the body); all pads on one land
     (their shape picks the rotation up to 180 when they are not square or round);
   * red = nothing lands every pad (the best partial placement at a multiple of 90 is reported, the tilted best in
     the reason only); a numbering conflict (an EasyEDA pad on a land of another number and another net while its
     own number exists in KiCad) that pin names do not vouch for; several non-equivalent members that nothing
     separates ("ambiguous"); a polar two-terminal part whose names and numbers disagree and no + mark settles it
     (the correction follows the names; with KiCad's side named by its library convention the reason says so,
     "KiCad's positive pad 2 by KiCad's library convention (pad 1 = cathode)"), or whose + mark contradicts the names
     where the pad numbers say nothing; the mirror wins (only
     a mirror lands -> `mirror=True`, never placed; or it is better supported, by numbers that match more pads than
     they conflict on, and no normal placement gives the same nets, or as well supported and far more exact, while a
     normal member lands every pad -> that member, refitted over every pad, is the correction, so `cpl --force` and
     `confirm` can write its row: "numbering mirrored between the libraries (symmetric land pattern): rot R lands every
     pad on the lands the mirror image of the EasyEDA footprint lands them on by number (N matched, no conflict), but
     renumbered: EE 1 -> KiCad 3, ...; which numbering is the part's is in its datasheet" when it lands the pads on
     the lands the mirror image does, within TWIN_RMS of its residual, else "the mirrored fit beats every normal one:
     mirrored footprint or wrong footprint; the correction is rot R"; a mirror image with numbering conflicts of its
     own on those lands is no evidence, and the normal members are judged as usual); a pinout that differs: through
     the chosen member the EasyEDA names
     are a permutation of the KiCad pin functions (`parts.pin_diffs` "swapped") and no member of S wires it better
     (permutes no names and agrees with more of them, as check's `_pinout` asks; not for a non-polar two-terminal part,
     nor a polar one whose drawn + the member puts on KiCad's positive pad): reason "pinout differs from the symbol: pad
     N is X on the LCSC part, Y on the board; ...: no placement that lands every pad puts the pins on their own
     functions, so the part is wired wrong whatever the rotation; check the LCSC part's pinout against the symbol".
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
def jlc_part(board, ref) -> Part | None   # the part JLC places for a ref: first assembled, else placed, else first
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
  for the numbering); else "wrong-pad", red. A polar two-terminal part whose row exchanges its two terminals
  against a correction that is not red, by its numbers or not: "reversed", red ("polarity reversed: the row puts the
  part the other way round than the [verified] correction does (the pads land by number): 1 on X, not Y, ..."; the
  summary adds the correction's key reason). The numbers *decide* a row when at least EVIDENCE (80 %, and 2) of
  the EasyEDA pads whose number KiCad has land on that number or its net. Pads landing by number (or where the
  numbers decide nothing) on other nets than `corr` gives them: red against a verified correction, for a polar
  two-terminal part, or when the numbers do not decide the row; yellow against a fitted one otherwise. Against a
  red correction: a polar two-terminal part is red unless the row differs from it only harmlessly and the
  correction's doubt is not its polarity ("the polarity needs a decision"); any other row is red when the numbers
  do not decide it ("nothing vouches for it"). With no correction a row the numbers do not decide is yellow. A row
  that lands as a fitted ("auto") yellow correction does whose `doubt` is set is yellow. KiCad soldered pads no
  EasyEDA pad lands on: yellow, listed in `uncovered` (status stays "ok"; `parts.uncovered_exemption` pads are a
  message). No EasyEDA footprint: "not-simulated", yellow for a non-polar part with two copper pads whose row point
  is within 0.1 mm of its pads' box (red when it is not), and so for a two-pad trimmer ("trimmer: which pad is the
  rotor", the checklist item it makes), red otherwise, with the reason (a polar two-pad part, a one-pad part, N copper
  pads) and, for such a red one, where its row point lies: "; the row point is the centre of its courtyard" (within
  BODY_TOL), "; the row point is KiCad's anchor, pin 1, 13.50 mm from the centre of its courtyard: JLC places a part
  by its centre", or "...is D mm from the centre of its courtyard" ("fabrication-layer drawing" for a fab body).
  Bottom rows are re-placed under the other readings (`alt_readings`: True, False, or
  names): a changed pin -> net mapping or a pad leaving copper sets `convention_sensitive` (a checklist item,
  verdict unchanged) and, per such reading, `reading_moves[name]` = the moved pads as (EasyEDA number, KiCad pad
  or None off copper), natural order. With `corr`, `expected` = `cpl_row_for(...)`; a row further than 0.05 mm /
  0.5 deg gets a message; further than max(0.15 mm, a quarter of the smallest pad it lands on) or 2 deg (up to 45)
  from a correction that is not red, with every pad on copper, it is yellow, status "offset", the message first, and
  so is a row a quarter turn (45 to 135 deg) from it whose pads all land as the correction puts them (alike lands:
  one land, whose long pad would lie across it); a row matching a verified correction's expected row is VERIFIED.
  A correction verified wrong (`corr.failure`): a row within those offset limits of its expected row is red, status
  "verified-wrong" (an ok or renumbered row; another status keeps its own), whatever its pads land on, the message
  first: "verified wrong by JLC's preview on 2026-10-22 (U3, ann: pin 1 at the wrong corner): the row is the stored
  correction's; fix the correction (...) and confirm it again" (`failure_text`: by JLC's preview, the assembled board
  or a manual check); a row further off is yellow ("the stored correction was verified wrong by ...; this row is D mm
  and A deg from its row: look at it in JLC's preview, then record it").
* Pin names (`_pinout`): with the EasyEDA record (not a proxy) and every pad on copper, the symbol's pin names
  through the row are compared with the KiCad pin functions of the pads they land on (`parts.pin_diffs`), whatever
  the correction says. Red, status "pinout" (the message first): names permuted ("swapped") with no placement known
  to wire the part better (the correction's own or a non-mirror alternative, re-placed: one that lands every pad,
  permutes no names and agrees with more of them than the row) on a part with more than two terminals (a
  two-terminal part's half turn always lands it the other way round: its names are its polarity). Yellow: names that
  a known placement would put right ("through this row the pin names differ from the symbol (...); another placement
  puts them on their own functions"), or more names that differ otherwise than agree ("N pin names differ from the
  symbol: ...: check the LCSC part's pinout against the symbol"); fewer: a message ("N pin names differ from the
  symbol (M agree): ..."). Same-net differences and a polar two-terminal
  part whose drawn + the row puts on KiCad's positive pad: a message. A row landing as a manual correction: yellow,
  "accepted by the manual correction"; as a board-verified one: a message; a preview verification settles only a
  two-terminal part (JLC's preview does not show pin functions).
* `check_cpl`: one result per row in order, then "no-row" (red) for parts JLC places with a code and no row ("JLC
  assembles it (the BOM lists it) but the CPL has no row (the board excludes it from the position files, so KiCad's
  position file leaves it out)"). JLC never sees KiCad's attributes and places what its BOM and CPL list:
  `cpl.jlc_places(part, bom_refs, cpl_refs)` = not DNP, not board-only, and `cpl.assembled(part)` (placed and not
  exclude_bom), or listed by `bom_refs` (the designators a JLC BOM lists; None: no BOM), or, with no BOM, only
  exclude_pos and listed by the CPL's rows. A reference that more than one part
  JLC places carries is "duplicate-ref" (red, "R1 is on the board 2 times: JLC cannot tell them apart"): every row of
  it, or one result when it has none, and never simulated against either part (a row of a reference whose other
  parts JLC does not place is checked against the placed one). Rows for refs not on the board
  ("not-on-board"), on the wrong layer ("layer-mismatch"), repeated ("duplicate", every copy) are red; "not-placed"
  is red for DNP and board-only parts ("JLC must not place it") and yellow for a part the board excludes from its BOM
  or position files that nothing else lists ("... and the BOM does not list it: JLC does not place it"). A part JLC
  places that is not assembled is simulated like any other row, with a message: for an exclude_pos part, first when
  the row is green, and the row is yellow at least ("the board excludes it from the position files, but the BOM and
  the CPL list it: JLC places it (it never sees KiCad's attributes); untick ..."). A row for a part with no code is
  checked without a footprint. `corrections` is keyed by ref or by code (the ref wins). Lookups
  happen once per code; an exception from `ee_lookup` becomes NotFound("lookup failed (...)").
* Statuses: ok | offset | off-copper | wrong-pad | reversed | renumbered | pinout | verified-wrong | not-simulated |
  no-row | not-on-board | layer-mismatch | not-placed | duplicate | duplicate-ref.

## cpl.py — read and write CPL / BOM files

```python
def read_cpl(path, *, units="mm") -> list[CplRow]
def write_cpl(path, rows: list[CplRow])
def read_bom(path) -> list[dict]       # designators, lcsc (normalised when valid, else raw), comment, footprint, qty
def rows_for_board(board, corrections, reading="jlc-2022", *, bom_refs=None) -> tuple[list[CplRow], list[str]]
def assembled(part) -> bool; def jlc_places(part, bom_refs=None, cpl_refs=None) -> bool
def correction_for(corrections, part) -> Correction | None; def natural_key(ref)
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
* `rows_for_board`: rows for the parts JLC places (`jlc_places(part, bom_refs)`: assembled, or listed by the BOM)
  with a code; refs without a correction or with a mirrored one are returned as missing. Both sorted naturally.

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
  status, verifications: [{by, date, source, ok, board, ref, layer, presses, note, correction}], tool, fitted}]}`
  (a verification's `correction`: the numbers it was of, rot, dx, dy, mirror; older records lack it); `ee.key` is
  `parts.ee_key`, `kicad.pad_hash` is `parts.kicad_hash` (pads and pin functions), `ee.title` the EasyEDA
  footprint's title (a package name, for people reading the file). Atomic writes, sorted keys, 1-space indent,
  trailing newline. `jlcplace fit --db` does not let a second part with the same keys and other numbers replace the
  entry a first part stored in the same run. A file that is not JSON (git conflict markers), whose entries do not
  hold a correction (`"rot": "x"`), or that cannot be written raises `DBError` (a ValueError) naming the file; the
  command line reports it as bad input (exit 3).
* `lookup`: board-verified > preview-verified > manual > auto, then the later entry; "" (in the call or the entry)
  matches any. Entries for the pad hash but none for the ee key: the strongest is returned with status "stale",
  verdict red, reason "footprint changed since verified"; the caller refits (pipeline: same numbers -> the refit
  stands, noted; different -> at best yellow). The returned Correction carries the entry's own keys and
  `verifications`, those of its numbers (a record's `correction` equal to them; a record without one counts, a failed
  one only while the entry still gives its "verification failed (...)" reason); `Correction.failure` = the latest of
  them when it failed (verified wrong), and then the verdict is red.
* `upsert`: the same numbers keep the stored status, verifications and verified verdict, and a failure of those
  numbers (verdict red, its "verification failed" reason): a refit does not make them right; different numbers never
  replace a stronger status (ValueError).
* `confirm`: ok=True raises the status to the source's; board/preview make the verdict VERIFIED; a red entry
  confirmed manually becomes yellow with the reason "accepted manually (by): note". ok=False: status auto, verdict
  red, verified wrong (`failure`) until other numbers replace the entry's or a later verification of them is ok;
  every verification records the entry's numbers after it (`correction`). A verification is of the row that was
  uploaded: `correction` is that row's (`convention.row_correction`),
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
def fallback(part, ee) -> Correction          # rot 0 at the body's centre (else KiCad's anchor); yellow for a
                                              # non-polar 2-pad part or a two-pad trimmer, else red
def resolve(board, client, db=None, *, refs=None, snap=0.5, use_fallback=False, bom_rows=None, cpl_refs=None)
    -> dict[ref, Resolved]
def check(board, rows, resolved, *, reading=DEFAULT, bom_rows=None, cpl_path="") -> Run
def off_board(run, outline, what="Edge.Cuts") -> list[str]   # rows outside the board outline: yellow
def proxy_footprint(part, corr) -> EEFootprint   # KiCad pads through the inverse correction
def apply_bom_codes(board, bom_rows) -> list[(ref, board code, BOM code)]; def bom_overrides(changed) -> list[str]
def bom_listed(part, bom_rows) -> bool; def bom_refs(bom_rows) -> set | None; def lookup_ee(client, code)
def key_reasons(corr, n=2)
def needs_preview(corr) -> bool; def moves_text(res) -> str
class Resolved(part, ee, corr, source, proxy); class Run(board, rows, results, resolved, reading, cpl_path,
    bom_messages, row_lines, findings, gerbers, gerbers_path, offset): counts(), worst(), exit_code(fail_on),
    checklist() -> list[CheckItem]
class CheckItem(category, ref, lcsc, layer, rotation, what, verdict, refs, also, first)
class Finding(status, verdict, message)       # about the whole run: origin | gerbers | units
OFFSET_TOL = 0.25; OFFSET_TOL_GERBERS = 0.05; OFFSET_SHARE = 0.6; OFFSET_MIN = 3; OFFSET_IN_PLACE = 0.1
class Offset(dx, dy, rows, total, origin, origin_kind, scale, unit, y_up, refs, in_place): fix(row) -> CplRow,
    origin_error
def offset_tol(board) -> float
def common_offset(run, *, units="mm", origins=None) -> Offset | None
def offset_hints(run, *, units="mm", origins=None, off=<computed>) -> list[str]
def jlc_origin(check) -> (x, y) | None; def gerber_notes(check) -> list[str]
def gerber_findings(check, rows=None) -> list[Finding]
def origin_findings(run, *, given=None, given_kind="", units="mm", origins=None) -> list[Finding]
def origin_lines(run) -> list[(verdict, text)]
```
* `fallback`: no EasyEDA footprint and no entry (`cpl`): rot 0, the row at KiCad's angle and at `parts.body_centre`
  when that is more than BODY_TOL from KiCad's anchor (reason "the row is the centre of its courtyard, 13.50 mm from
  KiCad's anchor (pin 1), at KiCad's angle"), else at the anchor ("the row is KiCad's own position and angle", adding,
  for a part of several pads whose anchor is a pin and that draws no body, "that anchor is pin 1, not the part's
  centre, and the board draws no courtyard to take the centre from: check the row in JLC's preview").
* `off_board(run, outline, what="Edge.Cuts") -> refs`: the outline (board-frame polylines) chained into closed rings
  (`geom.rings`; none closed: nothing checked), read even-odd (a cutout is outside); a row of a part on the board whose
  point lies outside is yellow at least, "the row point is outside the board outline (Edge.Cuts), D mm beyond its edge:
  look at it in JLC's preview" first; a red or verified row gets it last, its verdict kept.
* A part with no EasyEDA record (offline miss or not found) but a database correction is simulated with
  `proxy_footprint`; its verdict is capped by the correction's, and when the cap lowers it the first message says
  so ("yellow because the stored auto correction is yellow: <its key reason>"); the note on how it was simulated
  comes last. With a BOM, a code-less part not in the BOM is yellow ("JLC does not place it"), and every part takes
  its BOM line's code (JLC assembles from the BOM; every part with the reference, so a DNP alternate footprint
  listed first does not keep it from the placed one): `apply_bom_codes` returns the references whose board code it
  replaced (the code of the part JLC places, `check.jlc_part`), `bom_overrides` makes their BOM messages.
  `resolve` resolves the parts `cpl.jlc_places` names: with `bom_rows`, also the parts the board excludes from its
  BOM or its position files that the BOM lists (`bom_listed`; `bom_refs(bom_rows)` = the BOM's designators), with
  `cpl_refs` (the CPL's references, `check`) the parts only excluded from the position files that the CPL lists;
  `check` simulates their rows. `cpl --bom` resolves and writes (`cpl.rows_for_board(..., bom_refs=)`) the parts the
  BOM lists; `fetch BOARD` and `fit BOARD` take the parts a CPL can get placed (assembled, or only exclude_pos).
* Common offset. Where a row belongs (cpl frame): `cpl_row_for(part, corr)` with a non-mirror correction, else the
  part's anchor (not-on-board and duplicate-ref results skipped). Hypotheses, simplest first: the rows as read, Mid Y
  not negated, then each other unit in `cpl.UNITS` than `units`, Y negated or not; for each, the densest cluster of
  (s x, f s y) - belongs within `offset_tol` (OFFSET_TOL; OFFSET_TOL_GERBERS on a run from the Gerbers' origin,
  known to a micron) (the point with the most neighbours, then the median twice). It needs max(OFFSET_MIN,
  ceil(OFFSET_SHARE * rows with a known part)) rows; the rows as read within the tolerance of where they belong are
  in place (None); otherwise the hypothesis with the most rows wins, ties to the simpler. `Offset.dx, dy` =
  displacement (mm, cpl frame), `origin` = the writer's origin (O.x - dx, O.y + dy) in the board frame, named by
  `origins` after the board's own CPL origin and "absolute" (then exactly the named point); `in_place` = the other
  rows that sit where they belong as read; `origin_error` = at most OFFSET_IN_PLACE of the rows in place (an
  origin, unit or y error moves every row); `fix(row)` = (s x - dx, f s y - dy), positions only. `offset_hints`:
  one "jlcplace: " line ("origin mismatch: N of M rows would sit on their parts if shifted by (-dx, -dy) mm (K rows
  sit on their parts already: ...): the CPL was written from ..., not the board's ...: ... check with --origin
  absolute"; or "... if the CPL's numbers were in inches: JLC reads a CPL's numbers in mm: write it again in mm"
  (mm read with --units in: "check without --units in"); or "... with Mid Y negated"); without `origin_error`, "N
  of M rows are displaced alike from where they belong, by (dx, dy) mm (refs), while K sit on their parts (...):
  look at those N rows", with no origin and no fix for every row; or [].
* Gerbers (with `--gerbers`). JLC reads the rows from the Gerbers' (0, 0): `jlc_origin(check)` is `check.origin` when
  the set is this board's (`belongs`) and a plain shift (flip none, scale 1), else None; the command line then checks
  the rows from a copy of the board with that origin and `origin_kind` "gerber". `gerber_findings`: a red "gerbers"
  finding when there is no such origin or the set is `stale` (its messages about the set itself, `gerber_notes`: not its
  verdict against the CPL origin it was asked about), and one "gerbers" finding per `check.problems` (a yellow one about
  a side without paste only when `rows`, the CPL's, place parts on that side or are not given). `origin_findings` (sets
  `run.offset`), on a run checked from the Gerbers' origin: a red "origin" finding when the common offset is an
  `origin_error` and puts the writer's origin more than OFFSET_TOL_GERBERS from it ("origin mismatch: JLC lays the CPL
  over the Gerbers from their (0, 0), board (0, 0) (the page origin), but the CPL was written from the aux (drill/place
  file) origin (100, 100) (184 of 184 rows): JLC would put every part 141.42 mm off, displaced by (-100.000, +100.000)
  mm; fix: plot the Gerbers and drill files from ..., or write the CPL from the Gerbers' origin (...), or shift every
  row by (+100.000, -100.000) mm (`check --fixed-cpl FILE` writes it)"; "N of the M parts" when not every row is
  displaced, and the shift leaves out the rows in place; for a unit or Y mix-up "write the CPL's numbers in mm" or
  "negate Mid Y", joined to the origin fixes with "and either"), else when `given` (an explicit --origin) is not the
  Gerbers' origin. `Run.worst()` and `exit_code` count findings. `origin_lines`: the findings; else the Gerbers' check
  ("the Gerbers' (0, 0) is this CPL's origin, ..."); else a reminder that the Gerbers were not checked and must be
  plotted from the CPL's origin.
* `key_reasons`: what a person or a later refit added (latest first), then the fit's own reasons, those that make
  it yellow or red before those that only inform ("rot 0 also lands every pad, on other nets; ruled out by pad
  numbers", "N pin names agree"). `needs_preview`: the correction is red, its `doubt` is set, or a reason other than
  an rms residual ("rms 0.213 mm: the land patterns differ") or a non-90 angle makes it yellow.
* Checklist, in this order of categories: pinout (a row of status "pinout", or with a message asking for the LCSC
  part's pinout to be checked that no manual or verified correction accepted; the datasheet settles it, not the
  preview; an uncertain correction of that part keeps only its other reasons the preview can settle), uncertain
  correction (correction yellow or red, not a fallback, on a simulated
  row; left out when the row is green or verified and not `needs_preview`; its reasons the preview can settle shown
  first), not simulated (a part with a code: JLC places no other), bottom reading (convention-sensitive, said in pins
  with `moves_text`: "as ccw-from-top or ccw-from-top+180, pin 1 would land off copper; as jlc-2022+180, pins 1 and 2
  would swap"; `ruled_out(run)`: a reading under which a bottom row verified itself (`_seen`: verdict VERIFIED and an ok
  preview or board verification of its numbers for this ref, side and board file stem) would move is ruled out for
  every part: a part that moves only under ruled-out readings has no item, the others leave them out of the pins and
  say "ccw-from-top+180 and jlc-2022+180 are ruled out by U4, verified on the assembled board"), odd angle (row rotation
  not a multiple of 90; not for a row verified itself). A part is one item, under its first category; its other
  categories are `also` and their notes follow "; also " in `what`. Alike parts are one item, `refs` naming them all and
  `ref` the one to look at: odd angle per (code, side, row verdict), `ref` the part furthest from a multiple of 90,
  `rotation` None; bottom reading per (code, side, verdict, rotation, what); uncertain correction per (code, side,
  verdict, what); never a not-simulated row or a part without a code. `first`: the bottom-reading item of a green row
  whose pins move under every other reading not ruled out (the most of its pads off copper under each, then the most
  pads), `what` starting "look at this one first" and saying that recording it (`confirm --source preview` or
  `--source board`) takes the other bottom parts' items away: it settles the reading for every bottom part. Sorted
  by category, then `first`, row verdict (red first), the correction's doubt (polarity, names, orientation), natural
  ref.

## report.py and cli.py

* report: `summary(run, color=False)` (`run.lead` right after the header, then one line per red or yellow result:
  `headline(res)`, "status: first message",
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
* Findings and the origin in the reports: the summary's header names the CPL origin ("origin aux 100, 100", or "origin
  100, 100 from the Gerbers (aux)") and the Gerber set ("over the Gerbers gerbers.zip"); each finding is the first line
  after it (`headline`), the count line ends "; origin red"; without --gerbers and without findings one line reminds
  that the Gerbers were not checked. `to_dict` adds `findings` [{status, verdict, message}], `summary.findings`, and
  `origin` {x, y, kind, from_gerbers, verdict (green | red | unchecked), note, gerbers (the OriginCheck's derived
  numbers: origin, named, method, flip, scale, residual, matched, board_pads, gerber_pads, pin_pairs, compared,
  coverage, missing (<= 50), extra (a count), mismatched (<= 50), belongs, stale, notes) | null, offset (Offset's
  fields, in_place and origin_error among them) | null}. `junit`: one test case per finding, or a passing "origin" case
  when the Gerbers were checked; `github_annotations`: one per finding, on the CPL file; `html`: a banner per finding
  and the origin line above the checklist; the Markdown checklist an "**Origin:**" paragraph before the items, the CSV a
  last row of category `origin`. A pinout item is flagged "(red: the LCSC part or the symbol is wrong)".
* Overlays: `overlay_svg(run, side, *, outline=None, gerber_origin=None, notes=(), width=OVERLAY_WIDTH)` one standalone
  SVG (px) of a side as JLC's preview shows it (the bottom seen from below, x mirrored): board outline (board-frame
  polylines, closed rings filled), KiCad copper, the EasyEDA pads where JLC puts them per row in the row's verdict
  colour, red and yellow parts circled (yellow dashed) and labelled, a second circle round pads put far from their part,
  none past CIRCLES (40) on a side, the circles clipped to the drawing (a view at least OVERLAY_MIN_VIEW, 20 mm, round a
  tiny board); rows whose EasyEDA pads all land beyond the view's reach get an arrow from the middle of the drawing
  toward them and a header note ("the EasyEDA pads of N rows land D mm from their parts, off this view"; then the header
  never claims they are outlined); the CPL origin (+), the Gerbers' (0, 0) (x); a header with file names (never paths)
  and `notes`; legend, problem list (12), and the EasyEDA source notice in visible text and `<desc>`.
  `write_overlays(run, directory, ..., png=False) -> {name: path}` (top.svg, bottom.svg; the PNGs when a renderer is at
  hand), `svg_to_png(svg, png) -> renderer | ""` (RuntimeError when it fails), `png_renderer()` (cairosvg, else a
  headless Chromium: $JLCPLACE_CHROMIUM, Playwright's headless shell, Chrome / Chromium on PATH), `board_outline(path)`
  (a .kicad_pcb's Edge.Cuts, footprints' too; None for a snapshot), `gerber_outline(gerbers, check)` (the profile layer
  through the check's map).
* cli (`jlcplace`, also `python -m jlcplace`): `check BOARD CPL [--bom] [--gerbers DIR|ZIP] [--db] [--online|--offline]
  [--cache] [--origin] [--reading] [--fail-on red|yellow] [--report] [--overlay DIR] [--json PATH|-] [--junit]
  [--checklist .md|.csv] [--github-annotations] [--lcsc-field]... [--reader] [--units] [--fixed-cpl CSV] [-q]`; `cpl
  BOARD -o CPL [...] [--force]` (writes, checks the written file, keeps no file when red unless --force: an existing CPL
  is left unchanged and the note says so, under -q naming the red refs, and naming the refs whose stored correction
  was verified wrong, "fix it and confirm again"; the reports of a run that wrote nothing name no
  CPL, "the rows jlcplace would write"; `-o -` is bad input; parts without a footprint or entry get the fallback row,
  at their body's centre);
  `fit BOARD [--ref]... [--db] [--json]`; `fetch [BOARD|CODE]... [--bom CSV] [--refresh]` (the codes of the boards'
  parts a CPL can get placed, the codes given, and with --bom every LCSC code of the BOM; none of them: bad input);
  `confirm BOARD --ref REF --db DIR --by NAME
  --source manual|preview|board [--cpl UPLOADED [--units]] [--failed] [--note] [--space/--right/--up N] [--rot --dx
  --dy]` (preview and board need --cpl or --rot, presses need --cpl; each ref's row, moved by the presses, gives the
  correction recorded, refs of one entry must agree on it, a reference two placed parts carry is refused, the part's
  EasyEDA record must be at hand (a cache miss or a failed fetch is bad input: entries are keyed by the footprint they
  were verified with; EasyEDA's own not-found answer gives an entry with no EasyEDA key), and everything is checked
  before anything is written; a part whose entry is then verified wrong gets a line saying that `check --db` holds its
  row red and `cpl --db` does not write it until the correction is fixed and confirmed again); `show CODE | BOARD --ref
  REF [--json]`; `snapshot BOARD -o board.json`. `check`, `cpl`, `fit`, `show` and `confirm` read only the record cache
  (the default, also `--offline`) unless `--online` is given; `check`, `cpl` and `fit` then name the codes the cache
  lacked and say how to fetch them (the `jlcplace fetch BOARD [--bom BOM]` to type, with the --lcsc-field, --reader and
  --cache given): after the summary, or, when the cache lacks
  at least `cli.LEAD_SHARE` (90 %) of the codes a `check` or `cpl` looks up, right after its header (`Run.lead`: "none
  of the 2 codes JLC places (C1001, C1002) has an EasyEDA record in the cache DIR, so the rows below are checked
  without JLC's footprints ..."; the rows and the exit code as they are); `check` and `cpl` print
  `pipeline.offset_hints` (origin mismatch, units, Y not negated) unless an origin finding says it. `--gerbers` (check,
  cpl): read with `gerber.read_gerbers` (unreadable: bad input), checked with `gerber.gerber_origin` against the board
  as --origin (default aux) gives it, and against the board's Edge.Cuts (`report.board_outline`); with `jlc_origin` the
  rows are checked (cpl: written) from the Gerbers' (0, 0); findings from `gerber_findings` (check: with the CPL's rows)
  and `origin_findings` (`given` = the --origin point when --origin was given; origins named by `gerber.board_origins`).
  `check` and `cpl` (its written rows) run `pipeline.off_board` with the Gerbers' outline (`report.gerber_outline`,
  when `jlc_origin`), else the board's Edge.Cuts (`report.board_outline`; a snapshot has none: not checked).
  `check --units in|mil`: when the unit changed how the CPL's numbers were read (bare numbers), a red "units" finding
  (JLC reads them in mm; built after the common offset: when the rows sit on their parts read in mm, it says the file is
  in mm already and --units misread it, not that it is in inches). `check --fixed-cpl CSV`: when the run's common offset
  is an `origin_error`, the CPL's own rows through `Offset.fix` (rows in place kept; written with `cpl.write_cpl`, from
  the run's CPL origin) and the counts of their check, as a note (ending, when the rows moved onto the board's CPL
  origin with neither --gerbers nor --origin, "right only if the Gerbers and drill files are plotted from the board's
  aux origin (x, y), which was not checked: pass --gerbers"); else a note that nothing was written; the exit code stays
  the given CPL's (a file it cannot write is bad input once the summary and the other outputs are written). Under -q the
  findings are still printed ("jlcplace: red: origin mismatch: ..."). `cpl` with a red finding and no red row says "not
  written: the origin is red". `--overlay DIR` (check, cpl): `report.write_overlays` with the Gerbers' outline (else the
  board's Edge.Cuts), the Gerbers' (0, 0), and the findings and offset hints as notes; PNGs when a renderer is at hand,
  else a note on stderr; a renderer that fails is a warning, a directory that cannot be written bad input (after the
  other outputs). `-v` is taken before or after the command; `--origin -5,3` is read as `--origin=-5,3`. `check`, `cpl`
  and `show` warn on stderr when `--db` does not exist (`fit` and `confirm` create it); a `--db` that exists and is not
  a directory is bad input. Exit codes: 0 clean, 1 yellow (rows or BOM issues) with --fail-on yellow, 2 red (fetch:
  records that could not be fetched), 3 bad input (argparse errors, unreadable or unwritable files and database files
  included; when one output cannot be written the others still are), 4 an internal error (the traceback is printed,
  unless printing it fails too, as when memory ran out): no crash exits 1; 130 interrupted (ctrl-C, "jlcplace:
  interrupted"); 141 a broken pipe on stdout (quiet). stdout/stderr escape what their encoding cannot hold
  (backslashreplace).

## action.yml — the GitHub Action

A composite action at the repository root (not in the wheel): inputs `board`, `cpl` (required), `bom`, `gerbers`, `db`,
`fail-on` (red), `args` (more check arguments, split at whitespace, newlines too), `python-version` ("3.12"; "" the
runner's), `version` ("": the action's own ref, installed from its files); outputs `exit-code`, `red` and `yellow` (red
and yellow results plus findings of that verdict, empty when the check could not run), `junit`, `checklist`, `json`.
Steps: setup-python with update-environment false (the caller's PATH and environment untouched); a venv in
`$RUNNER_TEMP` made from its python-path (reused when the spec matches; nothing written to GITHUB_PATH / GITHUB_ENV); a
cache key from the board, BOM and `args` (none: the board and BOM's alone); actions/cache/restore; `jlcplace fetch` of
the board's codes (read with the `--lcsc-field` / `--reader` of `args`) and the BOM's (a failure is a warning: exit 2
blames EasyEDA, exit 3 says its input could not be read and the check will say what); actions/cache/save before the
check (a red check fails the job): under the board's key after a complete fetch, under a key of the run
(`<key>-partial-<run id>-<attempt>`, which the next run restores) after one that fetched some records but not all;
`jlcplace check ... --github-annotations --junit --checklist --json`, the summary and checklist in the job summary, the
step's exit code jlcplace's. With records missing, a multi-pin or polar part is red (not simulated) unless `db` holds
its correction: an EasyEDA outage on a cold cache fails the step without one. Inputs reach scripts only through `env:`;
the inline Python scripts run with `-I`, so modules in the caller's workspace (a jlcplace.py, json.py) cannot shadow the
real ones. Nothing is uploaded, no HTML report or overlay is written. `tests/test_action.py` checks it as data and runs
its steps with a local runner.

## Open

* `Pad.poly` is one ring with zero-width bridges: points within `tol` of a bridge to an island count as inside
  (`geom.pad_contains` with tol > 0; check uses 1e-3 mm). A multi-ring `Pad.polys` or bridge-aware distance in
  geom would remove it.
* Fit's rms yellow is relative to the smallest pad the pads land on (a quarter of it, at least 0.1 mm); it still
  flags correct SOT-23 (0.28 mm on 0.6 mm pads) and SOT-23-6 (0.21) fits whose land patterns differ. A per-pad,
  per-axis criterion would cut that too.
* The "+" mark reader is a heuristic on EasyEDA's drawings (two crossing strokes beside a pad). It settled all 6
  name-against-number cases in JLC's preview data; where it reads nothing, such a part is red until confirmed. Against
  the pad numbers alone it is followed (yellow, "polarity evidence conflicts"): it disagreed with them 5 times in that
  data and was right each time; a + the reader puts beside the wrong pad would be followed the wrong way, so the part
  is on the preview checklist. KiCad's library convention stands in for missing pin functions: a symbol on a KiCad
  library footprint that breaks it (its anode on pad 1, no pin functions) misleads the names and the + alike. An entry
  stored before the names counted against the convention keeps its verdict and numbers until `jlcplace fit --db`
  refits it.
* `EEPad` has no plated flag (unplated holed pads are holes, not pads); slot geometry is parsed but not exposed, and
  a hole is its centre and diameter (a slotted peg hole is read as round). The holes' evidence uses the KiCad part's
  NPTH pads, which `kicad_hash` does not key (the keys must not move): two KiCad footprints alike in copper and pin
  functions, with their peg holes elsewhere, share a database entry.
* Corrections stored before the fit read holes, trimmers, "_Horizontal" footprints or a mirror image that only
  renumbers a symmetric land pattern keep their numbers, verdict and `mirror` flag (a mirrored entry: no row) until
  `jlcplace fit --db` refits them; the check's polarity rule applies at once (a stored entry does not make a trimmer
  or a right-angle part non-polar again).
* pcbnew of KiCad 6-8 (calls without a layer argument) is untested; KiCad 10 variant overrides are ignored.
* Gerbers: Excellon drill files are skipped, not read (a set whose copper carries no pads, or a check against the
  drill holes, would need an Excellon reader). Only flips, a half turn and the 25.4 scales are tried: a set turned a
  quarter turn or scaled otherwise reads as not this board's. Deprecated MI / IR / AS image commands are noted and
  ignored; the order of LM / LR / LS and the polarity of an AB block flashed under LPC follow the specification as
  read, untested against a reference renderer (KiCad uses neither). Pad-voting on a periodic layout relies on the
  translation that matches the most pads: a regular grid with many Gerber pads missing, plotted from a point that is
  none of the board's origins, could still be taken one pitch off (X2 pin attributes settle it). Net names compared
  from TO.N are KiCad's; another tool's spelling of the same nets would read as nets changed. The outline is compared
  by its bounding box only.
* Common offset: a part with neither a correction nor a record counts at its KiCad anchor, so a board of mostly
  identical off-centre connectors with no records could show a false common vector; bottom rows written with X
  negated (KiCad's "negative X for bottom") have no hypothesis.
* Pinout: names both libraries number, differently (INT1 / INT2, P1.01 / P1.03, 1IN+ / 2IN+), compare as unknown, so
  swaps between them are not seen; G against GND is unknown (a gate or a ground); a board without pin functions (none
  filled from the schematic) is not compared. A multi-pin part whose preview-verified entry has a permuted pinout stays
  red: only `confirm --source board`, or editing the entry, settles it (the red row says so; `confirm --source manual`
  on such an entry says it changed nothing). An entry stored before the pinout rule keeps its old yellow verdict until
  `jlcplace fit --db` refits it (the check of the row, with the record, is red anyway).
* The action is tested with a local runner on Linux only: not on GitHub's runners, Windows (Git Bash) or macOS.
* Verifications recorded before they held their numbers count as of the entry's numbers (a failed one while its reason
  is still in the entry): one of older numbers that a later verification replaced can still settle a bottom reading.
