"""The jlcplace command line.

    jlcplace check BOARD CPL       simulate a CPL from any tool against the board (and its Gerbers: --gerbers)
    jlcplace cpl BOARD -o CPL      write the CPL (corrections from the database, else fitted), check it, refuse if red
    jlcplace fit BOARD             fit corrections (and store them with --db)
    jlcplace fetch BOARD|CODE ...  download EasyEDA records into the cache (--bom: the BOM's codes too)
    jlcplace confirm BOARD --ref   record a verification (manual, JLC preview, assembled board) in the database
    jlcplace show CODE | BOARD --ref REF
    jlcplace snapshot BOARD -o board.json

BOARD is a .kicad_pcb file or a JSON snapshot. Only `fetch`, and the other commands with --online, use the network
(the EasyEDA API); otherwise records come from the cache. JLC lays the CPL over the Gerbers from the Gerbers' own
(0, 0): with --gerbers, `check` and `cpl` take that point as the CPL origin, and a CPL written from another origin
(or an --origin that names another) is red for the whole run. Exit codes: 0 clean; 1 something yellow and --fail-on
yellow; 2 something red (for fetch: records that could not be fetched); 3 bad input (missing or malformed files, a
database file that cannot be read, bad options); 4 an internal error (a bug), never 1; 130 interrupted (ctrl-C);
141 the reader of stdout went away (`jlcplace ... | head`).
"""

from __future__ import annotations

import argparse
import errno
import json
import logging
import math
import os
import re
import sys
import traceback
import warnings
from dataclasses import replace
from pathlib import Path

from . import __version__, geom, report
from .board import load_board, save_snapshot
from .check import ANGLE_TOL, ROW_TOL, check_bom, check_row, failure_text
from .convention import BOTTOM_READINGS, DEFAULT, cpl_row_for, norm_angle, row_correction
from .cpl import atomic_write, jlc_places, natural_key, read_bom, read_cpl, rows_for_board, write_cpl
from .db import ROW_REASON, SOURCES, STATUS_RANK, CorrectionDB, DBError, presses_to_row
from .easyeda import EEClient, EEError, check_code, default_cache_dir
from .fit import ee_landings, fit_part
from .gerber import board_origins, gerber_origin, read_gerbers
from .model import BOTTOM, GREEN, RED, VERDICT_ORDER, VERIFIED, YELLOW, Correction, CplRow, EEFootprint, NotFound, \
    printable
from .parts import ee_key, kicad_hash
from .pipeline import Finding, apply_bom_codes, bom_overrides, bom_refs, check, correction, describe, fallback, \
    gerber_findings, is_code, jlc_origin, key_reasons, lookup_ee, off_board, offset_hints, offset_tol, \
    origin_findings, resolve, row_lines
from .report import plural

EXIT_OK, EXIT_YELLOW, EXIT_RED, EXIT_INPUT, EXIT_INTERNAL, EXIT_INTERRUPTED = 0, 1, 2, 3, 4, 130
EXIT_BROKEN_PIPE = 141                         # 128 + SIGPIPE, what a shell reports for `jlcplace ... | head`
log = logging.getLogger("jlcplace")


class BadInput(Exception):
    """A file or option the command cannot use: exit code 3."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):                  # argparse's own exit code 2 would read as "red"
        self.print_usage(sys.stderr)
        self.exit(EXIT_INPUT, f"{self.prog}: error: {message}\n")


# ------------------------------------------------------------------------------------------------------- inputs

def _board(args):
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            board = load_board(args.board, origin=args.origin or "aux", lcsc_fields=tuple(args.lcsc_field),
                               reader=args.reader)
    except FileNotFoundError:
        raise BadInput(f"{args.board}: no such file") from None
    except ImportError as e:
        raise BadInput(f"{args.board}: the pcbnew reader needs KiCad's Python module ({e}); try --reader sexpr") \
            from None
    except (OSError, ValueError) as e:
        raise BadInput(f"{args.board}: {e}") from None
    for w in caught:
        if "jlcplace" in str(w.filename):          # ours (e.g. the reader fallback), not KiCad's own notices
            log.warning("%s", w.message)
    return board


def _rows(path, units: str):
    try:
        return read_cpl(path, units=units)
    except FileNotFoundError:
        raise BadInput(f"{path}: no such file") from None
    except (OSError, ValueError) as e:
        raise BadInput(str(e)) from None


def _bom(path):
    if not path:
        return None
    try:
        return read_bom(path)
    except FileNotFoundError:
        raise BadInput(f"{path}: no such file") from None
    except (OSError, ValueError) as e:
        raise BadInput(str(e)) from None


def _client(args) -> EEClient:
    """Records from the cache; from the EasyEDA API too only with --online."""
    return EEClient(args.cache, offline=not args.online)


LEAD_SHARE = 0.9          # the cache lacks this share of the board's records: the note leads the output


def _fetch_command(args) -> str:
    """The `jlcplace fetch` that downloads the records a command's board and BOM need, as one would type it."""
    words = ["jlcplace", "fetch", args.board] if getattr(args, "board", None) else ["jlcplace", "fetch", "BOARD"]
    if getattr(args, "bom", None):
        words += ["--bom", args.bom]
    for name in getattr(args, "lcsc_field", None) or ():
        words += ["--lcsc-field", name]
    if getattr(args, "reader", "auto") != "auto":
        words += ["--reader", args.reader]
    if getattr(args, "cache", None):
        words += ["--cache", args.cache]
    # a path as it is (Windows' too), quoted only when a shell would split it
    return " ".join(w if re.fullmatch(r"[\w@%+=:,./\\~-]+", w) else f'"{w}"' for w in map(str, words))


def _misses_note(client: EEClient, args=None, looked_up: int = 0) -> tuple[list[str], list[str]]:
    """What to do about records the cache lacked (the commands do not download them unless asked), as (lines to say
    right after the summary's header, lines to say after it): when the cache lacks (almost) every record of the
    `looked_up` codes, every row below says "not simulated" for that one reason, so the note comes first."""
    n = len(client.misses)
    if not n:
        return [], []
    missing = sorted(client.misses, key=natural_key)
    codes = ", ".join(missing[:8]) + (f" and {n - 8} more" if n > 8 else "")
    fetch = f"`{_fetch_command(args)}` downloads them (rate-limited), or pass --online"
    if looked_up and n >= LEAD_SHARE * looked_up:
        # the trailing line's words ("no EasyEDA record in the cache DIR for ..."), which the docs and the Claude
        # Code plugin's skills look for
        what = "the one code" if looked_up == 1 else f"{n} of the {looked_up} codes"
        rows = "the rows" if getattr(args, "quiet", False) else "the rows below"          # -q prints none
        return [(f"jlcplace: no EasyEDA record in the cache {client.cache_dir} for {what} JLC places ({codes}), so "
                 f"{rows} are checked without JLC's footprints (red where a part needs one): {fetch}")], []
    return [], [f"jlcplace: no EasyEDA record in the cache {client.cache_dir} for {plural(n, 'code')} ({codes}): "
                f"{fetch}"]


def _db(args, *, creates: bool = False) -> CorrectionDB | None:
    """The --db database. Not a directory: bad input. Missing: a warning, unless the command creates it (fit); a
    typo would otherwise drop every stored and verified correction without a word."""
    path = getattr(args, "db", None)
    if not path:
        return None
    if Path(path).exists() and not Path(path).is_dir():
        raise BadInput(f"--db {path}: not a directory (the database is a directory of JSON files)")
    if not creates and not Path(path).exists():
        print(printable(f"jlcplace: warning: --db {path} does not exist: no stored corrections are used "
                        f"(`jlcplace fit --db` or `confirm` creates it)"), file=sys.stderr)
    return CorrectionDB(path)


def _write(path: str, text: str) -> None:
    if path == "-":
        sys.stdout.write(text)
        return
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(p, text)
    except OSError as e:
        raise BadInput(f"{path}: cannot write: {_why(e)}") from None


def _why(e: OSError) -> str:
    """An OSError said plainly: a directory on the path that is a file is "not a directory" (mkdir says it exists)."""
    if isinstance(e, (FileExistsError, NotADirectoryError)):
        return "a directory on its path is a file (not a directory)"
    return str(e.strerror or e)


def _color(stream) -> bool:
    return hasattr(stream, "isatty") and stream.isatty() and not os.environ.get("NO_COLOR")


def _verdict_code(verdicts, fail_on: str) -> int:
    worst = min(verdicts, key=lambda v: VERDICT_ORDER.get(v, 0), default=VERIFIED)
    return EXIT_RED if worst == RED else EXIT_YELLOW if fail_on == YELLOW and worst == YELLOW else EXIT_OK


# ------------------------------------------------------------------------------------------------------- check, cpl

def _stdout_once(args) -> None:
    if args.json == "-" and args.github_annotations:
        raise BadInput("--json - and --github-annotations both write to stdout; give --json a file")


def _finish(args, run, notes=(), outline=None, errors=()) -> int:
    """Print the summary (to stderr when --json - takes stdout), write the requested reports, return the exit code.
    Under -q the run's findings are still printed: they make every row wrong. `errors`: outputs that could not be
    written already (--fixed-cpl): the others are written all the same, then it is bad input."""
    out = sys.stderr if args.json == "-" else sys.stdout
    if not args.quiet:
        out.write(report.summary(run, color=_color(out)))       # run.lead right after its header
    else:
        for f in run.findings:
            print(printable(f"jlcplace: {f.verdict}: {report.headline(f)}"), file=out)
        notes = list(run.lead) + list(notes)
    for n in notes:
        print(printable(n), file=out)
    if args.github_annotations:
        for line in report.github_annotations(run, fail_on=args.fail_on):
            print(line)
    outputs = [(args.json, lambda: json.dumps(report.to_dict(run), indent=1) + "\n"),
               (args.report, lambda: report.html(run)), (args.junit, lambda: report.junit(run, fail_on=args.fail_on)),
               (args.checklist, lambda: report.checklist_csv(run) if args.checklist.lower().endswith(".csv")
                else report.checklist_markdown(run))]
    failed = list(errors)
    for path, text in outputs:
        if path:
            try:
                _write(path, text())
            except BadInput as e:                  # write the other outputs anyway, then say so (exit 3)
                failed.append(str(e))
    if args.overlay:
        try:
            _overlays(args, run, notes, outline)
        except BadInput as e:
            failed.append(str(e))
    if failed:
        raise BadInput("; ".join(failed))
    return run.exit_code(args.fail_on)


def _overlays(args, run, notes, outline) -> None:
    """--overlay DIR: top.svg and bottom.svg, and their PNGs when a renderer is at hand (report.svg_to_png). Like the
    HTML report they draw EasyEDA pad outlines: for the user's own review, written only where asked."""
    g = run.gerbers
    notes = [f.message for f in run.findings] + [n.removeprefix("jlcplace: ") for n in notes
                                                 if n.startswith("jlcplace: ") and "would sit on their parts" in n]
    if outline is None:
        outline = report.board_outline(args.board)
    try:
        wrote = report.write_overlays(run, args.overlay, outline=outline, notes=notes,
                                      gerber_origin=g.origin if g is not None and g.origin is not None else None,
                                      png=True)
    except OSError as e:
        raise BadInput(f"--overlay {args.overlay}: cannot write: {e.strerror or e}") from None
    except RuntimeError as e:                  # the renderer failed: the SVGs are written, the PNGs are a bonus
        print(printable(f"jlcplace: warning: --overlay: no PNG ({e}); the SVGs are in {args.overlay}"),
              file=sys.stderr)
        return
    if not any(k.endswith(".png") for k in wrote):
        print(printable(f"jlcplace: --overlay: SVGs only in {args.overlay} (no PNG renderer: pip install cairosvg, or "
                        "install Chrome or Chromium, or set JLCPLACE_CHROMIUM)"), file=sys.stderr)


def _gerbers(args, board, rows=None):
    """--gerbers: (the board with the CPL origin JLC reads the rows from, the Gerbers' origin check, its findings).
    The Gerbers' (0, 0) is the CPL origin when the set is this board's and a plain shift of it; otherwise the board
    keeps --origin (default aux) and the set's problem is a red finding. The set's outline is compared with the
    board's Edge.Cuts; `rows`, the CPL's, say which sides need paste. Without --gerbers: (board, None, [])."""
    path = getattr(args, "gerbers", None)
    if not path:
        return board, None, []
    try:
        gs = read_gerbers(path)
    except FileNotFoundError:
        raise BadInput(f"--gerbers {path}: no such file or directory") from None
    except (OSError, ValueError) as e:         # GerberError is a ValueError naming the file and the block
        raise BadInput(f"--gerbers {path}: {e}") from None
    except MemoryError:                        # what the expansion budget did not foresee: bad input all the same
        raise BadInput(f"--gerbers {path}: the files expand past this machine's memory: not a plot of a board") \
            from None
    chk = gerber_origin(board, gs, edge=report.board_outline(args.board))
    og = jlc_origin(chk)
    if og is not None:
        board = replace(board, origin=og, origin_kind="gerber")
    return board, (gs, chk), gerber_findings(chk, rows)


def _judge_origin(args, run, board, gerbers, found) -> list[str]:
    """Fill in the run's Gerbers, findings and common offset; the terminal hints that remain (pipeline.offset_hints:
    an origin mismatch without Gerbers to tell which origin is JLC's, a unit or a Y axis)."""
    given = (board.origin, board.origin_kind) if args.origin else (None, "")
    origins = board_origins(board)
    if gerbers is not None:
        run.gerbers, run.gerbers_path = gerbers[1], str(args.gerbers)
    run.findings = found + origin_findings(run, given=given[0], given_kind=given[1], units=getattr(args, "units", "mm"),
                                           origins=origins)
    if any(f.status == "origin" for f in run.findings):
        return []                              # the finding says it, with the Gerbers' origin
    return offset_hints(run, units=getattr(args, "units", "mm"), origins=origins, off=run.offset)


def _units_finding(args, rows, off=None) -> list[Finding]:
    """--units tells jlcplace how to read a position file's bare numbers, not JLC: a CPL whose numbers it read in
    another unit than mm is red (JLC reads them in mm). When the rows sit on their parts read in mm (`off`, the
    run's common offset, says so), the file is in mm already and --units misread it: the finding says that."""
    if args.units == "mm":
        return []
    try:
        as_mm = read_cpl(args.cpl)
    except (OSError, ValueError):
        return []
    if [(r.x, r.y) for r in as_mm] == [(r.x, r.y) for r in rows]:
        return []                              # its numbers carry their unit
    if off is not None and off.origin_error and off.unit == "mm":
        return [Finding("units", RED, f"the CPL's numbers are in mm already ({off.rows} of {off.total} rows sit on "
                                      f"their parts read so), as JLC reads them: --units {args.units} misread them; "
                                      f"check without it")]
    word = {"in": "inches", "mil": "mils"}.get(args.units, args.units)
    return [Finding("units", RED, f"the CPL's numbers are in {word} (read so because of --units {args.units}), but "
                                  "JLC reads a CPL's numbers in mm: write it again in mm (KiCad: --units mm)")]


def _codeless_hint(run, bom) -> list[str]:
    """A hint when most rows lack a code and no BOM was given: the codes may live in the BOM only."""
    if bom is not None:
        return []
    n = sum(1 for r in run.results if r.row is not None and "the board part has no LCSC code" in r.messages)
    rows = sum(1 for r in run.results if r.row is not None)
    if rows and 2 * n > rows:
        return [(f"jlcplace: {n} of {plural(rows, 'row')} have no LCSC code on the board: pass --bom BOM.csv (JLC "
                 f"assembles from it) or --lcsc-field NAME")]
    return []


def _outline(gerbers):
    """The board outline JLC sees: the Gerbers' profile through their origin check (None: read Edge.Cuts)."""
    if gerbers is None or jlc_origin(gerbers[1]) is None:
        return None
    return report.gerber_outline(*gerbers)


def _off_board(args, run, gerbers):
    """Rows outside the board outline JLC sees (pipeline.off_board): the Gerbers' profile, else the board's Edge.Cuts
    (none for a snapshot), also when the Gerbers' check rejected their profile (plotted from another origin, another
    board's: a finding says so, and the rows are not blamed for it). Returns the Gerbers' outline for the overlays
    (None: they read Edge.Cuts themselves)."""
    outline = _outline(gerbers)
    judged = outline if outline and gerbers[1].outline_ok else None
    off_board(run, judged or report.board_outline(args.board), "the Gerbers' outline" if judged else "Edge.Cuts")
    return outline


def cmd_check(args) -> int:
    _stdout_once(args)
    board = _board(args)
    rows = _rows(args.cpl, args.units)
    bom = _bom(args.bom)
    jlc, gerbers, found = _gerbers(args, board, rows)
    overrides = apply_bom_codes(board, bom) if bom is not None else []
    client = _client(args)
    resolved = resolve(board, client, _db(args), snap=args.snap, bom_rows=bom, cpl_refs={r.ref for r in rows})
    run = check(jlc, rows, resolved, reading=args.reading, bom_rows=bom, cpl_path=args.cpl)
    outline = _off_board(args, run, gerbers)
    if bom is not None:
        run.bom_messages = bom_overrides(overrides) + check_bom(board, bom, rows)
    hints = _judge_origin(args, run, board, gerbers, found)
    k = len(found)                             # after the Gerbers' findings, before the origin's: the offset tells
    run.findings[k:k] = _units_finding(args, rows, run.offset)      # whether the file was in mm all along
    fixed, errors = [], []
    if args.fixed_cpl:
        try:
            fixed = [_fixed_cpl(args, run, resolved, bom, gerbers)]
        except BadInput as e:                  # the summary and the other outputs are written first, then exit 3
            errors.append(str(e))
    run.lead, missing = _misses_note(client, args, _codes(resolved))
    return _finish(args, run, hints + fixed + _codeless_hint(run, bom) + missing, outline, errors)


def _codes(resolved) -> int:
    """How many codes a run looked up: one record each."""
    return len({r.part.lcsc for r in resolved.values() if r.part.lcsc})


def _fixed_cpl(args, run, resolved, bom, gerbers=None) -> str:
    """--fixed-cpl PATH: the CPL's own rows (their angles kept) with the common offset taken out (pipeline.Offset.fix:
    in mm, y up, from the run's CPL origin: the Gerbers' with --gerbers), checked again as `check` checks a CPL (the
    board outline too); rows that sit on their parts already are kept. Only for an offset that moves (nearly) every
    row: an origin, unit or y axis error."""
    off = run.offset
    if off is None or not off.origin_error:
        return f"jlcplace: --fixed-cpl: no common offset to take out: {args.fixed_cpl} not written"
    keep = set(off.in_place)
    rows = [r if r.ref in keep else off.fix(r) for r in run.rows]
    _no_dash_path(args.fixed_cpl, "--fixed-cpl")
    try:
        Path(args.fixed_cpl).parent.mkdir(parents=True, exist_ok=True)
        write_cpl(args.fixed_cpl, rows)
    except OSError as e:
        raise BadInput(f"--fixed-cpl {args.fixed_cpl}: cannot write: {_why(e)}") from None
    again = check(run.board, rows, resolved, reading=run.reading, bom_rows=bom)
    _off_board(args, again, gerbers)
    again = again.counts()
    how = (f"moved by ({round(-off.dx, 3) + 0.0:+.3f}, {round(-off.dy, 3) + 0.0:+.3f}) mm"
           if off.scale == 1.0 and off.y_up else "in mm" if off.y_up else "with Mid Y negated")
    unsure = ""
    if run.board.origin_kind != "gerber" and not args.origin and math.hypot(off.dx, off.dy) > offset_tol(run.board):
        # moved onto an origin nothing vouches for: JLC reads the rows from the Gerbers' (0, 0), which only they say
        o = run.board.origin
        unsure = (f"; right only if the Gerbers and drill files are plotted from the board's {run.board.origin_kind} "
                  f"origin ({o[0]:g}, {o[1]:g}), which was not checked: pass --gerbers")
    return (f"jlcplace: wrote {args.fixed_cpl} ({len(rows) - len(keep)} rows {how}): its check: {again[RED]} red, "
            f"{again[YELLOW]} yellow, {again[GREEN]} green, {again[VERIFIED]} verified{unsure}")


def _no_dash_path(path: str, option: str) -> None:
    if path == "-":
        raise BadInput(f"{option} -: it writes a file; give it a name")


def _no_dash(args, what: str) -> None:
    if args.output == "-":
        raise BadInput(f"-o -: {what} writes a file; give it a name (--json - prints the results)")


_FINDING_WORDS = {"origin": "the origin", "gerbers": "the Gerbers"}


def _shown(refs) -> str:
    """'D1', 'D1, U3 and 2 more'."""
    return ", ".join(refs[:6]) + (f" and {len(refs) - 6} more" if len(refs) > 6 else "")


def _refs(results) -> str:
    """'D1 is red', 'D1, U3 and 2 more are red'."""
    refs = list(dict.fromkeys(r.ref for r in results))
    return printable(f"{_shown(refs)} {'is' if len(refs) == 1 else 'are'} red")


def cmd_cpl(args) -> int:
    _stdout_once(args)
    _no_dash(args, "cpl")
    board = _board(args)
    bom = _bom(args.bom)
    jlc, gerbers, found = _gerbers(args, board)   # with --gerbers, the rows are written from the Gerbers' origin
    overrides = apply_bom_codes(board, bom) if bom is not None else []
    client = _client(args)
    # with the BOM, also the parts the board excludes from its position files or BOM that it lists: JLC places them
    resolved = resolve(board, client, _db(args), snap=args.snap, use_fallback=True, bom_rows=bom)
    rows, _ = rows_for_board(jlc, {ref: r.corr for ref, r in resolved.items() if r.corr is not None}, args.reading,
                             bom_refs=bom_refs(bom))
    out = Path(args.output)
    tmp = str(out.parent / f".{out.name}.jlcplace-check.tmp")
    try:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            write_cpl(tmp, rows)
        except OSError as e:
            raise BadInput(f"{out}: cannot write: {e.strerror or e}") from None
        written = read_cpl(tmp)                    # check what is in the file, not what was meant to be
        run = check(jlc, written, resolved, reading=args.reading, bom_rows=bom)
        outline = _off_board(args, run, gerbers)
        if bom is not None:
            run.bom_messages = bom_overrides(overrides) + check_bom(board, bom, written)
        hints = _judge_origin(args, run, board, gerbers, found)
        red = run.worst() == RED
        if red and not args.force:
            # the reports name no file (there is none): "the rows jlcplace would write"
            bad = [r for r in run.results if r.verdict == RED]
            if bad:
                why = _refs(bad) if args.quiet else "the rows above are red"
                wrong = list(dict.fromkeys(r.ref for r in bad if r.status == "verified-wrong"))
                many = len(wrong) > 1
                if wrong:
                    # the database's own numbers, shown wrong in the preview or on the board: --db writes them again
                    whose = (f"{'their' if many else 'its'} stored correction{'s were' if many else ' was'}"
                             if args.quiet and len(wrong) == len({r.ref for r in bad}) else
                             f"the stored correction{'s' if many else ''} of {printable(_shown(wrong))} "
                             f"{'were' if many else 'was'}")
                    why += f"; {whose} verified wrong: fix {'them' if many else 'it'} and confirm again"
            else:                                  # every row lands, but not on the board JLC is sent
                what = list(dict.fromkeys(_FINDING_WORDS.get(f.status, f.status) for f in run.findings
                                          if f.verdict == RED))
                why = " and ".join(what) + (" is" if what == ["the origin"] else " are") + " red"
            note = f"jlcplace: {out} not written: {why} (--force writes it anyway)"
            if out.exists():
                note += f"; the existing {out} is left unchanged"
        else:
            try:
                os.replace(tmp, out)
            except OSError as e:
                raise BadInput(f"{out}: cannot write: {e.strerror or e}") from None
            run.cpl_path, run.row_lines = str(out), row_lines(out, run.results)
            note = f"jlcplace: wrote {out} ({len(rows)} rows)" + (", red rows included (--force)" if red else "")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    run.lead, missing = _misses_note(client, args, _codes(resolved))
    return _finish(args, run, [note] + hints + missing, outline)


# ------------------------------------------------------------------------------------------------------- fit

def _placeable(part) -> bool:
    """A part a CPL can get placed: assembled, or excluded only from the position files (JLC never sees KiCad's
    attributes: a CPL row and a BOM line place it), never a DNP or board-only part (cpl.jlc_places)."""
    return jlc_places(part, None, {part.ref})


def _fit_dict(part, ee, corr: Correction, stored: str) -> dict:
    return {"ref": part.ref, "lcsc": part.lcsc, "layer": part.layer, "footprint": part.footprint,
            "easyeda": ee.title if isinstance(ee, EEFootprint) else None,
            "message": ee.message if isinstance(ee, NotFound) else "", "rot": corr.rot, "dx": corr.dx, "dy": corr.dy,
            "mirror": corr.mirror, "method": corr.method, "rms": corr.rms, "matched": corr.matched,
            "verdict": corr.verdict, "score": corr.score, "reasons": list(corr.reasons), "db": stored,
            "alternatives": corr.alternatives}


def cmd_fit(args) -> int:
    board = _board(args)
    want = set(args.ref or ())
    unknown = sorted(want - {p.ref for p in board.parts}, key=natural_key)
    if unknown:
        raise BadInput(f"not on the board: {', '.join(unknown)}")
    parts = [p for p in sorted(board.parts, key=lambda p: natural_key(p.ref))
             if p.lcsc and (p.ref in want if want else _placeable(p))]
    client, db = _client(args), _db(args, creates=True)
    out = sys.stderr if args.json == "-" else sys.stdout
    ees: dict = {}
    data, verdicts = [], []
    stored_by: dict[tuple, tuple] = {}         # (code, ee key, KiCad key) -> (ref, correction) stored in this run
    w = max((len(p.ref) for p in parts), default=0)
    for part in parts:
        if part.lcsc not in ees:
            ees[part.lcsc] = lookup_ee(client, part.lcsc)
        ee = ees[part.lcsc]
        stored = ""
        if isinstance(ee, EEFootprint):
            corr = fit_part(part, ee, snap=args.snap)
            if db is not None:
                key = (part.lcsc, corr.ee_key, corr.kicad_hash)
                first = stored_by.setdefault(key, (part.ref, corr))
                if first[0] != part.ref and describe(first[1]) != describe(corr):
                    # one entry per (code, footprints, pin names): a second part that fits otherwise must not replace
                    # the first one's correction behind its back
                    stored = f"not stored: {first[0]} has the same code and footprint and fits {describe(first[1])}"
                else:
                    try:
                        db.upsert(part.lcsc, corr, footprint=part.footprint, ee_title=ee.title)
                        stored = "stored"
                    except DBError:
                        raise
                    except ValueError as e:
                        stored = f"not stored: {e}"
        else:
            corr = fallback(part, ee)
        verdicts.append(corr.verdict)
        data.append(_fit_dict(part, ee, corr, stored))
        if not args.quiet:
            what = key_reasons(corr, 1)[0] if corr.reasons else ""     # why it is yellow or red first
            print(printable(f"{corr.verdict.upper():<6}  {part.ref:<{w}}  {part.lcsc:<10} {part.layer:<6}  "
                            + (f"rot {corr.rot:g}, dx {corr.dx:.4f}, dy {corr.dy:.4f}"
                               f"{', mirrored' if corr.mirror else ''} ({corr.method}, rms {corr.rms:.3f}, "
                               f"{corr.matched} matched)" if corr.method != "none" else "not fitted")
                            + (f"  {what}" if what else "") + (f"  [{stored}]" if stored else "")), file=out)
    if not args.quiet:
        counts = {v: verdicts.count(v) for v in (RED, YELLOW, GREEN)}
        print(f"{plural(len(parts), 'part')}: {counts[RED]} red, {counts[YELLOW]} yellow, {counts[GREEN]} green"
              + (f"; database {args.db}" if db is not None else ""), file=out)
        for n in sum(_misses_note(client, args), []):
            print(printable(n), file=out)
    if args.json:
        _write(args.json, json.dumps({"tool": report.TOOL, "board": board.path, "fits": data}, indent=1) + "\n")
    return _verdict_code(verdicts, args.fail_on)


# ------------------------------------------------------------------------------------------------------- fetch

def cmd_fetch(args) -> int:
    if not args.targets and not args.bom:
        raise BadInput("fetch: give a board, LCSC codes or --bom CSV")
    client = EEClient(args.cache, min_interval=args.min_interval)
    codes: list[str] = []
    for t in args.targets:
        if is_code(t):
            codes.append(check_code(t))
        else:
            args.board = t
            board = _board(args)
            codes += sorted({p.lcsc for p in board.parts if p.lcsc and _placeable(p)}, key=natural_key)
    bom = _bom(args.bom)
    if bom is not None:
        # JLC places the BOM's codes, also where the board names another code or none (codes kept in the BOM only)
        codes += [b["lcsc"] for b in bom if is_code(b.get("lcsc") or "")]
    codes = list(dict.fromkeys(codes))
    got = missing = failed = 0
    for code in codes:
        before = client.requests
        try:
            res = client.get(code, refresh=args.refresh)
        except EEError as e:
            failed += 1
            print(printable(f"{code}: failed: {e}"))
            continue
        how = "fetched" if client.requests > before else "cached"
        if isinstance(res, NotFound):
            missing += 1
            print(printable(f"{code}: not found ({res.message}; {how})"))
        else:
            got += 1
            print(printable(f"{code}: {res.title}, {len(res.pads)} pads ({how})"))
    print(printable(f"{plural(len(codes), 'code')}: {plural(got, 'footprint')}, {missing} not found, {failed} "
                    f"failed; cache {client.cache_dir}"))
    return EXIT_RED if failed else EXIT_OK


# ------------------------------------------------------------------------------------------------------- confirm

def _confirm_options(args, pressed: bool) -> None:
    """A verification is of the row that was uploaded, which need not be jlcplace's correction: preview and board
    verifications, and presses (relative to that row), name the CPL."""
    if args.cpl and args.rot is not None:
        raise BadInput("give the CPL that was uploaded (--cpl) or type the correction in (--rot), not both")
    if pressed and not args.cpl:
        raise BadInput("the presses are relative to the row JLC's preview showed: give the CPL that was uploaded "
                       "(--cpl FILE)")
    if pressed and args.failed:
        raise BadInput("presses say how the part was put right: they cannot come with --failed")
    if args.source in ("preview", "board") and not args.cpl and args.rot is None:
        raise BadInput(f"--source {args.source}: give the CPL that was uploaded (--cpl FILE): what JLC showed is its "
                       f"row, which need not be jlcplace's correction")


def _part(board, ref: str):
    """The part a reference names: BadInput when none does, or when several parts JLC places carry it (a row or a
    verification of it cannot tell them apart)."""
    ps = [p for p in board.parts if p.ref == ref]
    if not ps:
        raise BadInput(f"{ref} is not on the board")
    placed = [p for p in ps if p.placed]
    if len(placed) > 1:
        raise BadInput(f"{ref} is on the board {len(placed)} times: JLC cannot tell them apart (give each part its "
                       f"own reference)")
    return (placed or ps)[0]


def _uploaded_row(rows, part, path: str):
    mine = [r for r in rows if r.ref == part.ref]
    if not mine:
        raise BadInput(f"{path}: no row for {part.ref}")
    if len(mine) > 1:
        raise BadInput(f"{path}: {len(mine)} rows for {part.ref}")
    if mine[0].layer != part.layer:
        raise BadInput(f"{path}: the row for {part.ref} says {mine[0].layer}, the part is on the {part.layer}")
    return mine[0]


def _close(a: Correction, b: Correction) -> bool:
    """The same placement as far as a row shows it: within check's ROW_TOL and ANGLE_TOL (a correction's row moves by
    exactly as much as the correction does)."""
    da = abs((a.rot - b.rot + 180.0) % 360.0 - 180.0)
    return a.mirror == b.mirror and da <= ANGLE_TOL and math.hypot(a.dx - b.dx, a.dy - b.dy) <= ROW_TOL


def cmd_confirm(args) -> int:
    board = _board(args)
    db = CorrectionDB(args.db)
    client = _client(args)
    presses = {"space": args.space, "right": args.right, "up": args.up}
    pressed = any(presses.values())
    _confirm_options(args, pressed)
    rows = _rows(args.cpl, args.units) if args.cpl else None
    cpl_name = Path(args.cpl).name if args.cpl else ""
    plans = []
    for ref in dict.fromkeys(args.ref):
        part = _part(board, ref)
        if not part.lcsc:
            raise BadInput(f"{ref} has no LCSC code")
        try:
            # a verification is stored under the EasyEDA footprint it was made with (a later version makes it stale):
            # a record the cache lacks, or that cannot be fetched, is not "EasyEDA has no footprint" (NotFound)
            ee = client.get(part.lcsc)
        except EEError as e:
            raise BadInput(f"{ref}: the EasyEDA record of {part.lcsc} is not at hand ({e}); a verification is stored "
                           f"under the EasyEDA footprint it was made with: `jlcplace fetch BOARD` downloads it, or "
                           f"pass --online") from None
        key = ee_key(ee) if isinstance(ee, EEFootprint) else ""
        h = kicad_hash(part)
        uploaded = None
        if rows is not None:
            # what was checked: the uploaded row, moved by the presses in the preview, and its correction
            seen = presses_to_row(_uploaded_row(rows, part, args.cpl), **presses)
            how = f"; presses: space {args.space}, right {args.right}, up {args.up}" if pressed else ""
            uploaded = replace(row_correction(part, seen, board.origin, args.reading), method="manual",
                               status="manual", verdict=YELLOW, ee_key=key, kicad_hash=h,
                               reasons=[f"{ROW_REASON} of {ref} in {cpl_name} ({args.source}, {args.by}{how})"])
        stored = db.lookup(part.lcsc, ee_key=key, kicad_hash=h)
        fitted = None
        if (stored is None or stored.status == "stale") and isinstance(ee, EEFootprint):
            fitted = fit_part(part, ee, snap=args.snap)
        elif stored is None and uploaded is None and args.rot is None:
            raise BadInput(f"{ref}: no EasyEDA footprint ({ee.message}) and no stored correction to confirm; give the "
                           "uploaded CPL (--cpl) or the correction (--rot/--dx/--dy)")
        plans.append((ref, part, ee, key, h, uploaded, fitted, fitted or stored or uploaded))
    # everything is checked before anything is written: the parts of one entry (code, EasyEDA footprint, KiCad pads
    # and pin functions) must agree on it, and a verified entry is not given other numbers behind its back
    first: dict[tuple, tuple] = {}
    for ref, part, ee, key, h, uploaded, fitted, base in plans:
        if uploaded is None:
            continue
        other = first.setdefault((part.lcsc, key, h), (ref, uploaded))
        if other[0] != ref and not _close(other[1], uploaded):
            raise BadInput(f"{other[0]} and {ref} share a database entry ({part.lcsc} on the same footprints), but "
                           f"their rows give {describe(other[1])} and {describe(uploaded)}: confirm only the parts "
                           f"whose rows were right")
        if _close(base, uploaded):
            continue
        if args.failed:
            raise BadInput(f"{ref}: the row in {cpl_name} is not the stored correction's ({describe(base)}): its "
                           f"failure says nothing about that correction; nothing recorded")
        rank = STATUS_RANK.get(base.status, 0)
        if rank >= STATUS_RANK["preview-verified"] and rank >= STATUS_RANK[SOURCES[args.source]]:
            raise BadInput(f"{ref}: the stored correction is {base.status} with other numbers ({describe(base)}; "
                           f"the row in {cpl_name} gives {describe(uploaded)}): if that verification was wrong, "
                           f"record it with --failed and its own row first")
    for ref, part, ee, key, h, uploaded, fitted, _ in plans:
        title = ee.title if isinstance(ee, EEFootprint) else ""
        try:
            if args.rot is not None:
                typed = Correction(rot=norm_angle(args.rot), dx=args.dx, dy=args.dy, method="manual", status="manual",
                                   verdict=YELLOW, ee_key=key, kicad_hash=h, reasons=[f"typed in by {args.by}"])
                db.upsert(part.lcsc, typed, footprint=part.footprint, ee_title=title)
            stored = db.lookup(part.lcsc, ee_key=key, kicad_hash=h)
            if stored is None or stored.status == "stale":
                stored = fitted or uploaded            # no record to fit: the uploaded row is the correction
                db.upsert(part.lcsc, stored, footprint=part.footprint, ee_title=title)
            replaced = stored if uploaded is not None and not _close(stored, uploaded) else None
            c = db.confirm(part.lcsc, ee_key=stored.ee_key, kicad_hash=stored.kicad_hash, by=args.by,
                           source=args.source, board=Path(board.path).name, ref=ref, layer=part.layer,
                           ok=not args.failed, presses=presses if pressed else None, note=args.note, part=part,
                           correction=uploaded if replaced is not None else stored if pressed else None)
        except DBError:
            raise
        except (ValueError, LookupError) as e:
            raise BadInput(f"{ref}: {e}") from None
        row = ""
        if not c.mirror:
            x, y, a = cpl_row_for(part, c, board.origin, args.reading)
            row = f"; row X {x:.4f} Y {y:.4f} rot {a:g}"
        was = f"; replaces the {replaced.status} correction {describe(replaced)}" if replaced is not None else ""
        print(printable(f"{ref} {part.lcsc}: {c.status}, {c.verdict}: {describe(c)}{row}{was} ({db.file(part.lcsc)})"))
        if c.failure is not None:
            print(printable(f"jlcplace: {ref}: its stored correction is {failure_text(c.failure)}: `check --db` "
                            f"holds its row red, and `cpl --db` does not write it (unless --force), until the "
                            f"correction is fixed and confirmed again"))
        if args.source == "manual" and not args.failed and STATUS_RANK.get(c.status, 0) > STATUS_RANK["manual"] \
                and isinstance(ee, EEFootprint) and not c.mirror:
            res = check_row(part, CplRow(ref, x, y, part.layer, a), ee, board.origin, corr=c, reading=args.reading,
                            alt_readings=False)
            if res.status == "pinout":                 # the datasheet's word does not outrank the preview's
                print(printable(f"jlcplace: {ref}: the entry stays {c.status} (a manual verification ranks below it), "
                                f"and its pinout stays red: an assembled board that works settles it (--source "
                                f"board), or editing the entry"))
    return EXIT_OK


# ------------------------------------------------------------------------------------------------------- show

def _show_code(args) -> dict:
    code = check_code(args.target)
    ee = lookup_ee(_client(args), code)
    d: dict = {"lcsc": code}
    if isinstance(ee, EEFootprint):
        layers: dict[str, int] = {}
        for p in ee.pads:
            layers[p.layer] = layers.get(p.layer, 0) + 1
        d["easyeda"] = {"title": ee.title, "package": ee.package, "key": ee.key(), "db_key": ee_key(ee),
                        "pads": len(ee.pads), "pad_layers": layers,
                        "numbers": sorted({p.number for p in ee.pads}, key=natural_key),
                        "pin_names": len(ee.pin_names), "marks": len(ee.marks), "tokens": ee.tokens}
    else:
        d["easyeda"] = {"found": False, "message": ee.message}
    db = _db(args)
    if db is not None:
        d["db"] = {"file": str(db.file(code)), "entries": db.entries(code)}
    return d


def _show_ref(args) -> dict:
    args.board = args.target
    board = _board(args)
    part = board.part(args.ref)
    if part is None:
        raise BadInput(f"{args.ref} is not on the board")
    d: dict = {"ref": part.ref, "lcsc": part.lcsc, "lcsc_field": part.lcsc_field, "value": part.value,
               "footprint": part.footprint, "layer": part.layer, "x": part.x, "y": part.y, "angle": part.angle,
               "flags": [n for n in ("dnp", "exclude_pos", "exclude_bom", "board_only", "tht") if getattr(part, n)],
               "copper_pads": len(part.copper_pads()), "pad_hash": geom.pad_hash(part.copper_pads()),
               "kicad_hash": kicad_hash(part)}
    if not part.lcsc:
        return d
    ee = lookup_ee(_client(args), part.lcsc)
    d["easyeda"] = ({"title": ee.title, "key": ee.key(), "db_key": ee_key(ee), "pads": len(ee.pads)}
                    if isinstance(ee, EEFootprint) else {"found": False, "message": ee.message})
    corr, source = correction(part, ee, _db(args), snap=args.snap)
    if corr is None:
        return d
    d["correction"] = {"source": source, "rot": corr.rot, "dx": corr.dx, "dy": corr.dy, "mirror": corr.mirror,
                       "method": corr.method, "status": corr.status, "verdict": corr.verdict, "rms": corr.rms,
                       "matched": corr.matched, "reasons": corr.reasons, "alternatives": corr.alternatives}
    if not corr.mirror:
        names = [args.reading] if part.layer != BOTTOM else list(BOTTOM_READINGS)
        d["rows"] = {n: dict(zip(("x", "y", "rotation"), cpl_row_for(part, corr, board.origin, n))) for n in names}
    if isinstance(ee, EEFootprint):
        d["landings"] = [{"ee": lnd.ee_number, "kicad": lnd.kicad_number, "net": lnd.net, "dist": lnd.dist}
                         for lnd in ee_landings(part, ee, corr)]
    return d


def _print_tree(d, indent: int = 0) -> None:
    """The data, one key per line (text from EasyEDA and the database with its control characters as \\xNN)."""
    pad = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict) and v and k != "pad_layers":
            print(printable(f"{pad}{k}:"))
            _print_tree(v, indent + 1)
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            print(printable(f"{pad}{k}: {len(v)}"))
            for x in v:
                print(printable(f"{pad}  - " + ", ".join(f"{a}={json.dumps(b)}" for a, b in x.items()
                                                         if not isinstance(b, (dict, list)) or b)))
        else:
            print(printable(f"{pad}{k}: {v if isinstance(v, str) else json.dumps(v)}"))


def cmd_show(args) -> int:
    if args.ref:
        d = _show_ref(args)
    elif is_code(args.target):
        d = _show_code(args)
    else:
        raise BadInput(f"{args.target}: give an LCSC code, or a board with --ref REF")
    if args.json:
        _write(args.json, json.dumps(d, indent=1) + "\n")
    if args.json != "-":
        _print_tree(d)
    return EXIT_OK


# ------------------------------------------------------------------------------------------------------- snapshot

def cmd_snapshot(args) -> int:
    _no_dash(args, "snapshot")
    board = _board(args)
    try:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        save_snapshot(board, args.output)
    except OSError as e:
        raise BadInput(f"{args.output}: {e}") from None
    print(printable(f"jlcplace: wrote {args.output}: {len(board.parts)} parts ({board.source}), origin "
                    f"{board.origin_kind} ({board.origin[0]:g}, {board.origin[1]:g})"))
    return EXIT_OK


# ------------------------------------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="log what the EasyEDA client does")

    board = argparse.ArgumentParser(add_help=False, parents=[common])
    g = board.add_argument_group("board")
    g.add_argument("--origin", default=None,
                   help="CPL origin: aux (default), grid, absolute, or 'X,Y' in mm or with a unit ('4in,-500mil'); "
                        "check and cpl with --gerbers take the Gerbers' (0, 0), and an --origin elsewhere is red")
    g.add_argument("--lcsc-field", action="append", default=[], metavar="NAME",
                   help="footprint field holding the LCSC code, searched before the usual names (repeatable)")
    g.add_argument("--reader", default="auto", choices=("auto", "pcbnew", "sexpr", "json"),
                   help="how to read BOARD (auto: .json is a snapshot, else pcbnew if it imports, else sexpr)")

    ee = argparse.ArgumentParser(add_help=False)
    g = ee.add_argument_group("EasyEDA records")
    g.add_argument("--cache", default=None, metavar="DIR", help=f"record cache (default {default_cache_dir()})")
    g.add_argument("--snap", type=float, default=0.5, metavar="DEG", help=argparse.SUPPRESS)

    def network(s):
        """--online/--offline, added to each command's own "EasyEDA records" group: before Python 3.13, argparse
        moved a mutually exclusive group inherited through parents= out of its argument group in --help."""
        grp = next(a for a in s._action_groups if a.title == "EasyEDA records")
        m = grp.add_mutually_exclusive_group()
        m.add_argument("--online", action="store_true",
                       help="download the records the cache lacks from the EasyEDA API (rate-limited; default: only "
                            "cached records, which `jlcplace fetch` downloads)")
        m.add_argument("--offline", action="store_true", help="only cached records (the default)")
        return s

    db = argparse.ArgumentParser(add_help=False)
    db.add_argument("--db", metavar="DIR", help="correction database directory (one JSON file per LCSC code)")

    reading = argparse.ArgumentParser(add_help=False)
    reading.add_argument("--reading", default=DEFAULT, choices=list(BOTTOM_READINGS),
                         help="how JLC reads a bottom-side rotation (default %(default)s)")

    out = argparse.ArgumentParser(add_help=False)
    g = out.add_argument_group("reports")
    g.add_argument("--fail-on", default=RED, choices=(RED, YELLOW), help="exit 1 on yellow too (default: red only)")
    g.add_argument("--report", metavar="HTML", help="write the HTML report")
    g.add_argument("--json", metavar="PATH", help="write the results as JSON ('-' for stdout)")
    g.add_argument("--junit", metavar="XML", help="write JUnit XML")
    g.add_argument("--checklist", metavar="PATH", help="write the preview checklist (.md, or .csv)")
    g.add_argument("--github-annotations", action="store_true", help="print GitHub Actions annotations")
    g.add_argument("--bom", metavar="CSV", help="the JLC BOM: codes for parts that lack one, and consistency checks")
    g.add_argument("--gerbers", metavar="GERBERS",
                   help="the Gerbers ordered with the CPL (a directory, a .zip or one file): JLC lays the CPL over "
                        "them from their own (0, 0), which becomes the CPL origin; red when the CPL was written from "
                        "another origin or the Gerbers are not this board's as it is")
    g.add_argument("--overlay", metavar="DIR",
                   help="write top.svg and bottom.svg (and PNGs when a renderer is at hand) of the board as JLC's "
                        "preview shows it, problem parts circled (draws EasyEDA outlines: for your own review)")
    g.add_argument("-q", "--quiet", action="store_true", help="no terminal summary")

    p = _Parser(prog="jlcplace", description="Check and correct JLCPCB pick-and-place (CPL) files for KiCad boards "
                                             "by simulating JLC's placement with its own EasyEDA footprints.",
                epilog="Exit codes: 0 clean, 1 yellow with --fail-on yellow, 2 red, 3 bad input, 4 internal error, "
                       "130 interrupted, 141 stdout closed by its reader.")
    p.add_argument("--version", action="version", version=f"jlcplace {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", default=False, help="log what the EasyEDA client does")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)

    s = network(sub.add_parser("check", parents=[board, ee, db, reading, out],
                               help="simulate a CPL against the board",
                       description="Simulate every CPL row with JLC's EasyEDA footprint and report where its "
                                   "pads land."))
    s.add_argument("board", metavar="BOARD", help="the KiCad board (.kicad_pcb) or a JSON snapshot of it")
    s.add_argument("cpl", metavar="CPL", help="the CPL to check: from any tool (CSV/TSV, or KiCad's .pos)")
    s.add_argument("--units", default="mm", choices=("mm", "in", "mil"),
                   help="unit of bare numbers in the CPL (KiCad's CSV position file does not say; default mm)")
    s.add_argument("--fixed-cpl", metavar="CSV",
                   help="when every row is off by one origin, unit or y axis: write the CPL with that taken out "
                        "(its rows moved onto the Gerbers' origin, or the CPL origin: then right only for Gerbers "
                        "plotted from it, which --gerbers checks), and check it")
    s.set_defaults(func=cmd_check)

    s = network(sub.add_parser("cpl", parents=[board, ee, db, reading, out],
                               help="write a corrected CPL and check it",
                       description="Write the CPL JLC needs (corrections from --db, else fitted), check the written "
                                   "file, and refuse to keep it when a row is red."))
    s.add_argument("board", metavar="BOARD", help="the KiCad board (.kicad_pcb) or a JSON snapshot of it")
    s.add_argument("-o", "--output", required=True, metavar="CPL", help="where to write the CPL")
    s.add_argument("--force", action="store_true", help="write the CPL even when rows are red (exit code still 2)")
    s.set_defaults(func=cmd_cpl)

    s = network(sub.add_parser("fit", parents=[board, ee, db], help="fit corrections (store them with --db)"))
    s.add_argument("board", metavar="BOARD", help="the KiCad board (.kicad_pcb) or a JSON snapshot of it")
    s.add_argument("--ref", action="append", metavar="REF", help="only these parts (repeatable)")
    s.add_argument("--json", metavar="PATH", help="write the fits as JSON ('-' for stdout)")
    s.add_argument("--fail-on", default=RED, choices=(RED, YELLOW),
                   help="exit 1 on a yellow fit too (default: red only)")
    s.add_argument("-q", "--quiet", action="store_true", help="no terminal summary")
    s.set_defaults(func=cmd_fit)

    s = sub.add_parser("fetch", parents=[board], help="download EasyEDA records into the cache")
    s.add_argument("targets", nargs="*", metavar="BOARD|CODE",
                   help="boards (every LCSC code on them) and/or LCSC codes such as C7519")
    s.add_argument("--bom", metavar="CSV", help="the JLC BOM: its LCSC codes too (JLC places the BOM's codes, also "
                                                 "where the board has none)")
    s.add_argument("--cache", default=None, metavar="DIR", help=f"record cache (default {default_cache_dir()})")
    s.add_argument("--refresh", action="store_true", help="fetch again even when cached")
    s.add_argument("--min-interval", type=float, default=0.7, metavar="S", help="seconds between requests")
    s.set_defaults(func=cmd_fetch)

    s = network(sub.add_parser("confirm", parents=[board, ee, reading], help="record a verification in the database",
                       description="Record that a part's placement was checked: manual (datasheet), preview (JLC's "
                                   "placement preview) or board (the assembled board). What was checked is the row "
                                   "of the CPL that was uploaded (--cpl, needed for preview and board), moved by the "
                                   "keys pressed in the preview; its correction is stored when it differs from the "
                                   "database's. Fits and stores a correction first when the database has none."))
    s.add_argument("board", metavar="BOARD", help="the KiCad board (.kicad_pcb) or a JSON snapshot of it")
    s.add_argument("--ref", action="append", required=True, metavar="REF",
                   help="the part that was checked (repeatable)")
    s.add_argument("--cpl", metavar="CPL",
                   help="the CPL that was uploaded to JLC (what was checked; the presses are relative to its rows)")
    s.add_argument("--units", default="mm", choices=("mm", "in", "mil"),
                   help="unit of bare numbers in the CPL (default mm)")
    s.add_argument("--db", required=True, metavar="DIR", help="correction database directory to record it in")
    s.add_argument("--by", required=True, metavar="NAME", help="who checked it")
    s.add_argument("--source", required=True, choices=[k for k in SOURCES if "-" not in k],
                   help="what was checked: manual (datasheet), preview (JLC's preview) or board (assembled)")
    s.add_argument("--note", default="", help="free text stored with the verification")
    s.add_argument("--failed", action="store_true",
                   help="the check showed the placement wrong: its row is red in `check --db` and `cpl --db` until "
                        "the correction is fixed and confirmed again")
    s.add_argument("--space", type=int, default=0, help="space presses in JLC's preview (90 deg ccw each)")
    s.add_argument("--right", type=int, default=0, help="right-arrow presses (negative: left)")
    s.add_argument("--up", type=int, default=0, help="up-arrow presses (negative: down)")
    s.add_argument("--rot", type=float, help="type the correction in: rot (degrees) ...")
    s.add_argument("--dx", type=float, default=0.0, help="... and the EasyEDA origin in the library frame, mm")
    s.add_argument("--dy", type=float, default=0.0, help="... in mm (with --rot and --dx)")
    s.set_defaults(func=cmd_confirm)

    s = network(sub.add_parser("show", parents=[board, ee, db, reading],
                               help="show what jlcplace knows about a code or part"))
    s.add_argument("target", metavar="CODE|BOARD", help="an LCSC code, or a board with --ref")
    s.add_argument("--ref", metavar="REF", help="with BOARD: the part to show")
    s.add_argument("--json", metavar="PATH", help="write it as JSON ('-' for stdout)")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("snapshot", parents=[board], help="save the board as JSON (checks without KiCad)")
    s.add_argument("board", metavar="BOARD", help="the KiCad board (.kicad_pcb) or a JSON snapshot of it")
    s.add_argument("-o", "--output", required=True, metavar="JSON", help="where to write the snapshot")
    s.set_defaults(func=cmd_snapshot)
    return p


class _Printable(logging.Formatter):
    """Log lines with their control characters as \\xNN (EasyEDA's error texts are not ours to print raw)."""

    def format(self, record) -> str:
        return printable(super().format(record))


def _glued(argv) -> list[str]:
    """'--origin -5,3' as '--origin=-5,3': argparse takes a value that starts with '-' (and is not a plain number)
    for an option."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--origin" and i + 1 < len(argv) and re.match(r"-\.?\d", argv[i + 1]):
            out.append(f"--origin={argv[i + 1]}")
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out


def _tolerant_streams():
    """An EasyEDA title in Chinese must not crash a run whose stdout is a cp1252 pipe (Windows CI logs)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):   # not a TextIOWrapper, or already closed
            pass


def _stdout_gone(e: OSError) -> bool:
    """The reader of stdout went away: EPIPE, or on Windows EINVAL from a write to a closed pipe."""
    return isinstance(e, BrokenPipeError) or (os.name == "nt" and e.errno == errno.EINVAL and not e.filename)


def _quiet_stdout():
    """After a broken pipe, point stdout at devnull so the interpreter's final flush does not raise again."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (AttributeError, ValueError, OSError):
        pass


def main(argv=None) -> int:
    _tolerant_streams()
    parser = build_parser()
    try:
        args = parser.parse_args(_glued(sys.argv[1:] if argv is None else list(argv)))
    except SystemExit as e:                    # --help, --version, usage errors
        return int(e.code or 0)
    if not logging.getLogger().handlers and not log.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(_Printable("jlcplace: %(levelname)s: %(message)s"))
        log.addHandler(h)
        log.propagate = False
    log.setLevel(logging.INFO if args.verbose else logging.WARNING)
    try:
        code = args.func(args)
        sys.stdout.flush()                     # a buffered pipe raises BrokenPipeError here, not at interpreter exit
        return code
    except (BadInput, EEError, DBError) as e:       # DBError: a database file that cannot be read or written
        print(printable(f"jlcplace: {e}"), file=sys.stderr)
        return EXIT_INPUT
    except KeyboardInterrupt:
        print("jlcplace: interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except OSError as e:                       # inputs are BadInput already: an output, the cache or stdout
        if _stdout_gone(e):                    # the reader (e.g. `head`) went away: not bad input, say nothing
            _quiet_stdout()
            return EXIT_BROKEN_PIPE
        print(printable(f"jlcplace: {f'{e.filename}: ' if e.filename else ''}{e.strerror or e}"), file=sys.stderr)
        return EXIT_INPUT
    except Exception as e:  # noqa: BLE001 - a crash must not exit 1, the code that means "yellow"
        try:
            traceback.print_exc(file=sys.stderr)
            print(f"jlcplace: internal error: {type(e).__name__}: {e} (a bug: please report it with the traceback "
                  f"above)", file=sys.stderr)
        except Exception:  # noqa: BLE001 - out of memory, stderr gone: saying so must not change the exit code
            pass
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
