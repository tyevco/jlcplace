---
name: setup-jlcplace
description: Find or install the jlcplace CLI (Python 3.10+, no KiCad needed) that the other jlcplace skills run. Use when a jlcplace command is not found, or the user asks to install, update or locate jlcplace.
allowed-tools: Bash(jlcplace --version) Bash(command -v *) Read
---

# Find or install jlcplace

The other jlcplace skills link here. Do the steps below once per session, before the first `jlcplace` command.

## 1. Look for it

```sh
jlcplace --version
```

It prints `jlcplace 0.1.0` or later. If the command is not found, look in these places before installing anything:

* the project's own virtual environment: `.venv/bin/jlcplace` (Windows: `.venv\Scripts\jlcplace.exe`);
* the uv and pipx tool directory: `~/.local/bin/jlcplace`;
* the fallback virtual environment from step 2: `~/.local/share/jlcplace-venv/bin/jlcplace`;
* `python3 -m jlcplace --version`, which works when jlcplace is installed in the current Python.

If you find it at a full path, call it by that path for the rest of the session. Put that path where the skills
write `jlcplace`.

## 2. Install it (ask first)

Installing downloads and runs code from https://github.com/tyevco/jlcplace. Tell the user that and wait for a yes.
Then use the first of these that is available (`command -v uv`, `command -v pipx`):

```sh
uv tool install git+https://github.com/tyevco/jlcplace
```

```sh
pipx install git+https://github.com/tyevco/jlcplace
```

If the system Python is older than 3.10, uv can fetch a newer one: add `--python 3.12` to the uv command.

Without uv or pipx, use a virtual environment of its own. This needs Python 3.10 or later (`python3 --version`) and
git:

```sh
python3 -m venv ~/.local/share/jlcplace-venv
~/.local/share/jlcplace-venv/bin/python -m pip install git+https://github.com/tyevco/jlcplace
~/.local/share/jlcplace-venv/bin/jlcplace --version
```

On Windows, use `py -m venv %LOCALAPPDATA%\jlcplace-venv` and `%LOCALAPPDATA%\jlcplace-venv\Scripts\jlcplace.exe`.
uv and pipx put the command in `~/.local/bin`. If that directory is not on `PATH`, call the command by its full path,
or run `uv tool update-shell` or `pipx ensurepath` and start a new shell. Never use `sudo pip install`.

To update jlcplace later, run `uv tool install --reinstall git+https://github.com/tyevco/jlcplace`, or
`pipx install --force git+https://github.com/tyevco/jlcplace`, or the venv's
`python -m pip install --upgrade git+https://github.com/tyevco/jlcplace`.

## 3. What jlcplace needs

* **No KiCad.** jlcplace reads `.kicad_pcb` files (KiCad 5 to 10) with a built-in reader. It uses KiCad's `pcbnew`
  Python module only when it can import it. If a run reports a problem with the pcbnew reader, add `--reader sexpr`.
  Never install KiCad just for jlcplace.
* **EasyEDA records.** JLC assembles with the footprints of its EasyEDA library, and jlcplace simulates those
  footprints. It reads them from a local cache: `$JLCPLACE_CACHE`, else `$XDG_CACHE_HOME/jlcplace`, else
  `~/.cache/jlcplace`. `--cache DIR` picks another one. Only `jlcplace fetch` and the `--online` option download
  records. They contact the EasyEDA API, rate-limited. Ask the user before running either one. Nothing else uses the
  network.
* **Licensing.** EasyEDA's footprint data belongs to the JLCEDA/EasyEDA Official Library. Keep the record cache and
  any `--report` HTML file (it draws EasyEDA pad outlines) on this machine. Never commit them, attach them to a
  public issue or pull request, or upload them as CI artifacts. The JSON, JUnit, checklist and CPL outputs and the
  correction database hold derived numbers only, so they can be committed.

## 4. Check the flags before using them

Flags change between versions. Before the first use of a command, read its help: `jlcplace check --help`,
`jlcplace cpl --help`, `jlcplace confirm --help`. A skill may mention a newer option, for example `--gerbers`: if
`jlcplace check --help` lists it, the installed version has it. Otherwise leave it out.

Exit codes for every command: 0 clean, 1 yellow with `--fail-on yellow`, 2 red (for `fetch`: records that could not
be fetched), 3 bad input (the message says which file or option), 4 internal error (a bug: report it with the
traceback at https://github.com/tyevco/jlcplace/issues), 130 interrupted, 141 the reader of stdout went away
(`jlcplace ... | head`), which is not an error.
