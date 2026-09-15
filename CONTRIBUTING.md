# Contributing

Thanks for looking. This is a personal Touch Bar configuration that happens to
be useful to others, so the most welcome contributions are bug reports with
enough detail to reproduce, and fixes that come with a spec change where the
behaviour was actually wrong.

## Before you start

- This is a macOS-only project. A physical Touch Bar is not required, but BTT's
  Control Strip mode is the only way to see the widgets on a Mac without one.
- Read [`specs/FEATURES.md`](specs/FEATURES.md) first. It is the behavioural
  contract: which widget draws what, when it draws nothing, and what each one
  shows when a dependency is missing. The code is expected to be rebuildable
  from it, so behaviour changes belong there too.
- [`specs/NOTE.md`](specs/NOTE.md) holds hard-won empirical findings (layout
  keys, preset rules). Check it before rediscovering one.

## Running the checks

```sh
tests/run.sh                 # the Lyrics suite: stdlib unittest, no dependencies
actions/doctor.sh            # environment, helper build, BTT permissions
```

`tests/run.sh` must stay green. It needs nothing installed because the widgets
themselves run under whatever `python3` BTT's `PATH` finds, and it redirects
every path it touches into a temporary directory, so it never reads or writes
your live `cache/` or `logs/`.

If you change anything the preset invokes, run `actions/doctor.sh` as well —
it exercises the same questions a fresh checkout asks.

`pytest.ini`, `ruff.toml`, and `pyrightconfig.json` exist for editors and for
ad-hoc runs. The suite is deliberately stdlib `unittest` rather than pytest,
because the widgets run without `site-packages` and the tests must too — so
`ruff check .` reports a large pre-existing backlog (its config comes from a
pytest-based Python-library template, which flags that deliberate choice along
with the CJK punctuation the layout code relies on). Treat ruff as advisory
here, not as a gate.

## Style

- **Shell** is `zsh`, always with `set -u`. Widget scripts set their own `PATH`
  because BTT runs them with a minimal environment.
- **Python** uses `from __future__ import annotations`, type hints on public
  functions, and stdlib only — no third-party packages, ever. The widgets run
  without `site-packages`.
- **Comments explain why, not what.** The existing code is heavily commented
  where a decision was non-obvious (why a timeout exists, why a value is read
  from a file instead of a variable). Match that; do not add narration.
- **No new dependencies** where a shell builtin or the standard library will
  do. Every `fork` in a widget that runs every second is a real cost, which is
  why the shared runtime uses zsh builtins such as `zstat` and `zselect`.
- **Small diffs.** Fix the behaviour that is wrong rather than refactoring
  around it.

## Reporting a bug

Please include:

- macOS version and BTT version.
- Which widget, and what it showed instead of the expected value (a short label
  like `NO CODEXBAR` is the widget reporting its own failure — include it).
- The output of `actions/doctor.sh`.
- For Lyrics issues, `widgets/now-playing-lyrics.sh --report`, and
  `--diagnose` if you can.

Note that a widget drawing nothing can be correct: Now Playing hides itself
when no allowed player holds the session, and the Star widget hides with the
row. See the per-feature documents under [`specs/design/`](specs/design/)
before assuming it is a bug.

## Licensing

Contributions are accepted under the [PolyForm Noncommercial 1.0.0
license](LICENSE). By opening a pull request you confirm you have the right to
submit the work under those terms. Do not add third-party code, artwork, or
logos without recording them in [`THIRD-PARTY.md`](THIRD-PARTY.md).
