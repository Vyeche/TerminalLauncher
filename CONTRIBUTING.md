# Contributing to Terminal Launcher

Thanks for helping! Issues and pull requests are welcome. Everyone taking part follows the
[Code of Conduct](CODE_OF_CONDUCT.md). Security problems go through [SECURITY.md](SECURITY.md),
not a public issue.

## Before you start
- What it does and how to use it: [README.md](README.md) and the [wiki](https://github.com/Vyeche/TerminalLauncher/wiki).
- The working rules (also for AI coding agents): [AGENTS.md](AGENTS.md).

```bash
git clone https://github.com/Vyeche/TerminalLauncher.git && cd TerminalLauncher
```

## Test your change
Everything runs from one command, without touching your own list, terminals or kitty window:
```bash
bash scripts/check.sh           # syntax, dry-run launches in every mode, the login entry,
                                # privacy, and the window on a hidden display
bash scripts/check.sh --quick   # the same without the window test (no Xvfb needed)
```
The window test needs `xvfb-run` (package `xvfb`). To try the app by hand, give it a throwaway
config so your real list stays out of it:
```bash
T=$(mktemp -d); XDG_CONFIG_HOME=$T/cfg XDG_CACHE_HOME=$T/cache python3 terminal-launcher.py
```
Add `--dry-run` to see the commands instead of opening terminals. `--all`, `--all-windows` and
`--autostart` without `--dry-run` reopen your real agent sessions, which may already be open.

A fix comes with a check in `scripts/check.sh` that fails without it.

## The Gauntlet
Every pull request runs the **Gauntlet** (`.github/workflows/gauntlet.yml`) on a clean Ubuntu runner:
actionlint over the workflows, then `scripts/check.sh` with GTK 4 and libadwaita from Ubuntu. Its
`required` check sums them up. A PR's code runs with a read-only token and no secrets. HOL Guard's
repository scan runs on `main` (and weekly), not on pull requests. A PR is merged once the Gauntlet
is green and the change has been reviewed.

## Code review
[CodeRabbit](https://coderabbit.ai) reviews every pull request to `main` and leaves comments; its
settings are in `.coderabbit.yaml`, which points it at the rules in AGENTS.md. Reply to a comment
with `@coderabbitai` to ask it something. Its review is advice: the maintainer decides.

## Dependencies
The app has none beyond Python 3, GTK 4 and libadwaita from your distribution. Dependabot keeps the
Gauntlet's pinned GitHub Actions up to date, one pull request per update.

## Style
- Plain, friendly, short words in everything people read. Buttons say what happens ("Add 3 to kitty").
- Every control has a tooltip. Follow the libadwaita patterns already used.
- Keep README and the wiki (`docs/wiki/`, with a line in `Whats-New.md`) in step with behavior.
- No personal data in code, docs, tests or screenshots: made-up names and paths only.
