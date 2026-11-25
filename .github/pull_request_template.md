## What and why
<!-- What changes for someone using Terminal Launcher, and why. Link the issue if there is one. -->

## How it was tested
<!-- The Gauntlet runs scripts/check.sh on this PR. Say what else you checked by hand. -->

- [ ] `bash scripts/check.sh` passes locally (the Gauntlet runs it too)
- [ ] Tested against a throwaway config (`XDG_CONFIG_HOME`), not my real list (see AGENTS.md → Testing)
- [ ] UI changes: a screenshot below, in the light and the dark theme

## Keeps the rules (AGENTS.md)
- [ ] Per user only: no sudo, no system files
- [ ] No new dependencies beyond Python 3, GTK 4 and libadwaita
- [ ] Every new control has a tooltip; text is plain and short
- [ ] README (and `docs/wiki/`, with a line in Whats-New) in step with what users see
- [ ] No personal data: paths, session IDs, hostnames, e-mail

## Screenshots
