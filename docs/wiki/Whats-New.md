# What's new

Changes you'd notice, newest first. To get them: `git pull` in your clone, then close and reopen the
launcher (see [Installing](Installing#updating)).

## 2026-11-24
- **First public release.** Terminal Launcher is on GitHub: a small GTK 4 / libadwaita window that
  keeps a list of your terminals and coding-agent sessions (Claude Code, omp, opencode), a plain
  shell in a folder, or a command you run often, and opens them with one click. Open them as tabs in
  one kitty window, as tabs in one GNOME Terminal window, or in separate COSMIC Terminal windows
  ([Open in](Open-In-Modes)). Mark any of them to open by themselves when you log in
  ([Start at login](Start-At-Login)). It's the renamed Claude Launcher, and moves your list over by
  itself ([Files and settings](Files-and-Settings#coming-from-claude-launcher)).
  A [40-second tour on YouTube](https://youtube.com/shorts/m40501aopTc) shows it in action.
- **Drag to reorder:** with two or more rows, each row has a drag handle. Drag a row onto another
  one and it takes that row's place. A line in your accent colour shows where it will land, the list scrolls while you drag
  near its top or bottom edge, and your ticks and scroll position stay as they were. The new order is
  saved at once. **⋮ → Move up / Move down** are still there.

  ![Dragging a row by its handle: a line shows where it will land, and the row moves there when dropped](https://raw.githubusercontent.com/Vyeche/TerminalLauncher/main/docs/images/gifs/drag.gif)
