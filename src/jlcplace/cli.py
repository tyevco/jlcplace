"""The jlcplace command line.

    jlcplace check BOARD CPL       simulate a CPL from any tool against the board
    jlcplace cpl BOARD -o CPL      write the CPL (corrections from the database, else fitted), check it, refuse if red
    jlcplace fit BOARD             fit corrections (and store them with --db)
    jlcplace fetch BOARD|CODE ...  download EasyEDA records into the cache
    jlcplace confirm BOARD --ref   record a verification (manual, JLC preview, assembled board) in the database
    jlcplace show CODE | BOARD --ref REF
    jlcplace snapshot BOARD -o board.json

BOARD is a .kicad_pcb file or a JSON snapshot. Exit codes: 0 clean; 1 something yellow and --fail-on yellow; 2
something red (for fetch: records that could not be fetched); 3 bad input (missing or malformed files, bad options).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings
from pathlib import Path

from . import __version__, geom, report
from .board import load_board, save_snapshot
from .check import check_bom
from .convention import BOTTOM_READINGS, DEFAULT, cpl_row_for, norm_angle
from .cpl import assembled, atomic_write, natural_key, read_bom, read_cpl, rows_for_board, write_cpl
from .db import SOURCES, CorrectionDB
from .easyeda import EEClient, EEError, check_code, default_cache_dir
from .fit import ee_landings, fit_part
from .model import BOTTOM, GREEN, RED, VERDICT_ORDER, VERIFIED, YELLOW, Correction, EEFootprint, NotFound
from .parts import ee_key
from .pipeline import apply_bom_codes, check, correction, describe, fallback, is_code, lookup_ee, resolve, \
    row_lines

EXIT_OK, EXIT_YELLOW, EXIT_RED, EXIT_INPUT = 0, 1, 2, 3
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
            board = load_board(args.board, origin=args.origin, lcsc_fields=tuple(args.lcsc_field),
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


def _client(args, offline=None) -> EEClient:
    return EEClient(args.cache, offline=args.offline if offline is None else offline)


def _db(args) -> CorrectionDB | None:
    return CorrectionDB(args.db) if getattr(args, "db", None) else None


def _write(path: str, text: str) -> None:
    if path == "-":
        sys.stdout.write(text)
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, text)


def _color(stream) -> bool:
    return hasattr(stream, "isatty") and stream.isatty() and not os.environ.get("NO_COLOR")


def _verdict_code(verdicts, fail_on: str) -> int:
    worst = min(verdicts, key=lambda v: VERDICT_ORDER.get(v, 0), default=VERIFIED)
    return EXIT_RED if worst == RED else EXIT_YELLOW if fail_on == YELLOW and worst == YELLOW else EXIT_OK


# ------------------------------------------------------------------------------------------------------- check, cpl

def _stdout_once(args) -> None:
    if args.json == "-" and args.github_annotations:
        raise BadInput("--json - and --github-annotations both write to stdout; give --json a file")


def _finish(args, run, notes=()) -> int:
    """Print the summary (to stderr when --json - takes stdout), write the requested reports, return the exit code."""
    out = sys.stderr if args.json == "-" else sys.stdout
    if not args.quiet:
        out.write(report.summary(run, color=_color(out)))
    for n in notes:
        print(n, file=out)
    if args.github_annotations:
        for line in report.github_annotations(run, fail_on=args.fail_on):
            print(line)
    if args.json:
        _write(args.json, json.dumps(report.to_dict(run), indent=1) + "\n")
    if args.report:
        _write(args.report, report.html(run))
    if args.junit:
        _write(args.junit, report.junit(run, fail_on=args.fail_on))
    if args.checklist:
        csv_ = args.checklist.lower().endswith(".csv")
        _write(args.checklist, report.checklist_csv(run) if csv_ else report.checklist_markdown(run))
    return run.exit_code(args.fail_on)


def cmd_check(args) -> int:
    _stdout_once(args)
    board = _board(args)
    rows = _rows(args.cpl, args.units)
    bom = _bom(args.bom)
    if bom is not None:
        apply_bom_codes(board, bom)
    resolved = resolve(board, _client(args), _db(args), snap=args.snap)
    run = check(board, rows, resolved, reading=args.reading, bom_rows=bom, cpl_path=args.cpl)
    if bom is not None:
        run.bom_messages = check_bom(board, bom, rows)
    return _finish(args, run)


def cmd_cpl(args) -> int:
    _stdout_once(args)
    board = _board(args)
    bom = _bom(args.bom)
    if bom is not None:
        apply_bom_codes(board, bom)
    resolved = resolve(board, _client(args), _db(args), snap=args.snap, use_fallback=True)
    rows, _ = rows_for_board(board, {ref: r.corr for ref, r in resolved.items() if r.corr is not None}, args.reading)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(out.parent / f".{out.name}.jlcplace-check.tmp")
    try:
        write_cpl(tmp, rows)
        written = read_cpl(tmp)                    # check what is in the file, not what was meant to be
        run = check(board, written, resolved, reading=args.reading, bom_rows=bom)
        run.cpl_path, run.row_lines = str(out), row_lines(tmp, run.results)
        if bom is not None:
            run.bom_messages = check_bom(board, bom, written)
        red = run.worst() == RED
        if red and not args.force:
            note = f"jlcplace: {out} not written: the rows above are red (--force writes it anyway)"
        else:
            os.replace(tmp, out)
            note = f"jlcplace: wrote {out} ({len(rows)} rows)" + (", red rows included (--force)" if red else "")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return _finish(args, run, [note])


# ------------------------------------------------------------------------------------------------------- fit

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
             if p.lcsc and (p.ref in want if want else assembled(p))]
    client, db = _client(args), _db(args)
    out = sys.stderr if args.json == "-" else sys.stdout
    ees: dict = {}
    data, verdicts = [], []
    w = max((len(p.ref) for p in parts), default=0)
    for part in parts:
        if part.lcsc not in ees:
            ees[part.lcsc] = lookup_ee(client, part.lcsc)
        ee = ees[part.lcsc]
        stored = ""
        if isinstance(ee, EEFootprint):
            corr = fit_part(part, ee, snap=args.snap)
            if db is not None:
                try:
                    db.upsert(part.lcsc, corr, footprint=part.footprint, ee_title=ee.title)
                    stored = "stored"
                except ValueError as e:
                    stored = f"not stored: {e}"
        else:
            corr = fallback(part, ee)
        verdicts.append(corr.verdict)
        data.append(_fit_dict(part, ee, corr, stored))
        if not args.quiet:
            what = corr.reasons[0] if corr.reasons else ""
            print(f"{corr.verdict.upper():<6}  {part.ref:<{w}}  {part.lcsc:<10} {part.layer:<6}  "
                  + (f"rot {corr.rot:g}, dx {corr.dx:.4f}, dy {corr.dy:.4f}{', mirrored' if corr.mirror else ''} "
                     f"({corr.method}, rms {corr.rms:.3f}, {corr.matched} matched)" if corr.method != "none"
                     else "not fitted") + (f"  {what}" if what else "") + (f"  [{stored}]" if stored else ""),
                  file=out)
    if not args.quiet:
        counts = {v: verdicts.count(v) for v in (RED, YELLOW, GREEN)}
        print(f"{len(parts)} parts: {counts[RED]} red, {counts[YELLOW]} yellow, {counts[GREEN]} green"
              + (f"; database {args.db}" if db is not None else ""), file=out)
    if args.json:
        _write(args.json, json.dumps({"tool": report.TOOL, "board": board.path, "fits": data}, indent=1) + "\n")
    return _verdict_code(verdicts, args.fail_on)


# ------------------------------------------------------------------------------------------------------- fetch

def cmd_fetch(args) -> int:
    client = EEClient(args.cache, min_interval=args.min_interval)
    codes: list[str] = []
    for t in args.targets:
        if is_code(t):
            codes.append(check_code(t))
        else:
            args.board = t
            board = _board(args)
            codes += sorted({p.lcsc for p in board.parts if p.lcsc and assembled(p)}, key=natural_key)
    codes = list(dict.fromkeys(codes))
    got = missing = failed = 0
    for code in codes:
        before = client.requests
        try:
            res = client.get(code, refresh=args.refresh)
        except EEError as e:
            failed += 1
            print(f"{code}: failed: {e}")
            continue
        how = "fetched" if client.requests > before else "cached"
        if isinstance(res, NotFound):
            missing += 1
            print(f"{code}: not found ({res.message}; {how})")
        else:
            got += 1
            print(f"{code}: {res.title}, {len(res.pads)} pads ({how})")
    print(f"{len(codes)} codes: {got} footprints, {missing} not found, {failed} failed; cache {client.cache_dir}")
    return EXIT_RED if failed else EXIT_OK


# ------------------------------------------------------------------------------------------------------- confirm

def cmd_confirm(args) -> int:
    board = _board(args)
    db = CorrectionDB(args.db)
    client = _client(args)
    presses = {"space": args.space, "right": args.right, "up": args.up}
    for ref in args.ref:
        part = board.part(ref)
        if part is None:
            raise BadInput(f"{ref} is not on the board")
        if not part.lcsc:
            raise BadInput(f"{ref} has no LCSC code")
        ee = lookup_ee(client, part.lcsc)
        key = ee_key(ee) if isinstance(ee, EEFootprint) else ""
        h = geom.pad_hash(part.copper_pads())
        try:
            if args.rot is not None:
                typed = Correction(rot=norm_angle(args.rot), dx=args.dx, dy=args.dy, method="manual", status="manual",
                                   verdict=YELLOW, ee_key=key, kicad_hash=h, reasons=[f"typed in by {args.by}"])
                db.upsert(part.lcsc, typed, footprint=part.footprint,
                          ee_title=ee.title if isinstance(ee, EEFootprint) else "")
            stored = db.lookup(part.lcsc, ee_key=key, kicad_hash=h)
            if stored is None or stored.status == "stale":
                if not isinstance(ee, EEFootprint):
                    raise BadInput(f"{ref}: no EasyEDA footprint ({ee.message}) and no stored correction to confirm; "
                                   "give one with --rot/--dx/--dy")
                fitted = fit_part(part, ee, snap=args.snap)
                db.upsert(part.lcsc, fitted, footprint=part.footprint, ee_title=ee.title)
                stored = fitted
            c = db.confirm(part.lcsc, ee_key=stored.ee_key, kicad_hash=stored.kicad_hash, by=args.by,
                           source=args.source, board=Path(board.path).name, ref=ref, layer=part.layer,
                           ok=not args.failed, presses=presses if any(presses.values()) else None, note=args.note,
                           part=part)
        except (ValueError, LookupError) as e:
            raise BadInput(f"{ref}: {e}") from None
        row = ""
        if not c.mirror:
            x, y, a = cpl_row_for(part, c, board.origin, args.reading)
            row = f"; row X {x:.4f} Y {y:.4f} rot {a:g}"
        print(f"{ref} {part.lcsc}: {c.status}, {c.verdict}: {describe(c)}{row} ({db.file(part.lcsc)})")
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
               "copper_pads": len(part.copper_pads()), "pad_hash": geom.pad_hash(part.copper_pads())}
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
    pad = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict) and v and k != "pad_layers":
            print(f"{pad}{k}:")
            _print_tree(v, indent + 1)
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            print(f"{pad}{k}: {len(v)}")
            for x in v:
                print(f"{pad}  - " + ", ".join(f"{a}={json.dumps(b)}" for a, b in x.items()
                                               if not isinstance(b, (dict, list)) or b))
        else:
            print(f"{pad}{k}: {v if isinstance(v, str) else json.dumps(v)}")


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
    board = _board(args)
    try:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        save_snapshot(board, args.output)
    except OSError as e:
        raise BadInput(f"{args.output}: {e}") from None
    print(f"jlcplace: wrote {args.output}: {len(board.parts)} parts ({board.source}), origin {board.origin_kind} "
          f"({board.origin[0]:g}, {board.origin[1]:g})")
    return EXIT_OK


# ------------------------------------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    board = argparse.ArgumentParser(add_help=False)
    g = board.add_argument_group("board")
    g.add_argument("--origin", default="aux", help="CPL origin: aux (default), grid, absolute, or 'X,Y' in mm")
    g.add_argument("--lcsc-field", action="append", default=[], metavar="NAME",
                   help="footprint field holding the LCSC code, searched before the usual names (repeatable)")
    g.add_argument("--reader", default="auto", choices=("auto", "pcbnew", "sexpr", "json"),
                   help="how to read BOARD (auto: .json is a snapshot, else pcbnew if it imports, else sexpr)")

    ee = argparse.ArgumentParser(add_help=False)
    g = ee.add_argument_group("EasyEDA records")
    g.add_argument("--cache", default=None, metavar="DIR", help=f"record cache (default {default_cache_dir()})")
    g.add_argument("--offline", action="store_true", help="never use the network; only cached records")
    g.add_argument("--snap", type=float, default=0.5, metavar="DEG", help=argparse.SUPPRESS)

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
    g.add_argument("-q", "--quiet", action="store_true", help="no terminal summary")

    p = _Parser(prog="jlcplace", description="Check and correct JLCPCB pick-and-place (CPL) files for KiCad boards "
                                             "by simulating JLC's placement with its own EasyEDA footprints.",
                epilog="Exit codes: 0 clean, 1 yellow with --fail-on yellow, 2 red, 3 bad input.")
    p.add_argument("--version", action="version", version=f"jlcplace {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="log what the EasyEDA client does")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)

    s = sub.add_parser("check", parents=[board, ee, db, reading, out], help="simulate a CPL against the board",
                       description="Simulate every CPL row with JLC's EasyEDA footprint and report where its "
                                   "pads land.")
    s.add_argument("board", metavar="BOARD")
    s.add_argument("cpl", metavar="CPL")
    s.add_argument("--units", default="mm", choices=("mm", "in", "mil"),
                   help="unit of bare numbers in the CPL (KiCad's CSV position file does not say; default mm)")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("cpl", parents=[board, ee, db, reading, out], help="write a corrected CPL and check it",
                       description="Write the CPL JLC needs (corrections from --db, else fitted), check the written "
                                   "file, and refuse to keep it when a row is red.")
    s.add_argument("board", metavar="BOARD")
    s.add_argument("-o", "--output", required=True, metavar="CPL")
    s.add_argument("--force", action="store_true", help="write the CPL even when rows are red (exit code still 2)")
    s.set_defaults(func=cmd_cpl)

    s = sub.add_parser("fit", parents=[board, ee, db], help="fit corrections (store them with --db)")
    s.add_argument("board", metavar="BOARD")
    s.add_argument("--ref", action="append", metavar="REF", help="only these parts (repeatable)")
    s.add_argument("--json", metavar="PATH", help="write the fits as JSON ('-' for stdout)")
    s.add_argument("--fail-on", default=RED, choices=(RED, YELLOW))
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(func=cmd_fit)

    s = sub.add_parser("fetch", parents=[board], help="download EasyEDA records into the cache")
    s.add_argument("targets", nargs="+", metavar="BOARD|CODE")
    s.add_argument("--cache", default=None, metavar="DIR", help=f"record cache (default {default_cache_dir()})")
    s.add_argument("--refresh", action="store_true", help="fetch again even when cached")
    s.add_argument("--min-interval", type=float, default=0.7, metavar="S", help="seconds between requests")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("confirm", parents=[board, ee, reading], help="record a verification in the database",
                       description="Record that a part's placement was checked: manual (datasheet), preview (JLC's "
                                   "placement preview) or board (the assembled board). Fits and stores a correction "
                                   "first when the database has none.")
    s.add_argument("board", metavar="BOARD")
    s.add_argument("--ref", action="append", required=True, metavar="REF")
    s.add_argument("--db", required=True, metavar="DIR")
    s.add_argument("--by", required=True, metavar="NAME", help="who checked it")
    s.add_argument("--source", required=True, choices=[k for k in SOURCES if "-" not in k])
    s.add_argument("--note", default="")
    s.add_argument("--failed", action="store_true", help="the check showed the placement wrong (marks it red)")
    s.add_argument("--space", type=int, default=0, help="space presses in JLC's preview (90 deg ccw each)")
    s.add_argument("--right", type=int, default=0, help="right-arrow presses (negative: left)")
    s.add_argument("--up", type=int, default=0, help="up-arrow presses (negative: down)")
    s.add_argument("--rot", type=float, help="type the correction in: rot (degrees) ...")
    s.add_argument("--dx", type=float, default=0.0, help="... and the EasyEDA origin in the library frame, mm")
    s.add_argument("--dy", type=float, default=0.0)
    s.set_defaults(func=cmd_confirm)

    s = sub.add_parser("show", parents=[board, ee, db, reading], help="show what jlcplace knows about a code or part")
    s.add_argument("target", metavar="CODE|BOARD")
    s.add_argument("--ref", metavar="REF", help="with BOARD: the part to show")
    s.add_argument("--json", metavar="PATH", help="write it as JSON ('-' for stdout)")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("snapshot", parents=[board], help="save the board as JSON (checks without KiCad)")
    s.add_argument("board", metavar="BOARD")
    s.add_argument("-o", "--output", required=True, metavar="JSON")
    s.set_defaults(func=cmd_snapshot)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:                    # --help, --version, usage errors
        return int(e.code or 0)
    if not logging.getLogger().handlers and not log.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("jlcplace: %(levelname)s: %(message)s"))
        log.addHandler(h)
        log.propagate = False
    log.setLevel(logging.INFO if args.verbose else logging.WARNING)
    try:
        return args.func(args)
    except BadInput as e:
        print(f"jlcplace: {e}", file=sys.stderr)
        return EXIT_INPUT
    except EEError as e:
        print(f"jlcplace: {e}", file=sys.stderr)
        return EXIT_INPUT
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
