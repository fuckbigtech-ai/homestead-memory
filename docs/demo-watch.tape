# docs/demo-watch.tape — the README hero GIF.
#
# Committed so the recording is a build artifact rather than a one-off. The previous
# demo.gif was made by hand in July with no script, which meant nobody could regenerate
# it when the output changed, and a stale GIF of a tool that has moved on is a
# screenshot that lies.
#
# Regenerate with:   vhs docs/demo-watch.tape
# Requires:          vhs (https://github.com/charmbracelet/vhs) and `hsm` on PATH.
#
# `hsm watch --demo` is self-contained (throwaway vault, real ledger.append, real
# verify_chain), so this tape does no setup and stages nothing. What you see recorded is
# what the command prints.

Output docs/demo-watch.gif

Set Shell "bash"
Set FontSize 16
# Width is set by the longest real line, the chain-break report at ~110 chars. At 1000px
# it wrapped mid-word ("(edit / ed in place)"), which reads as a broken tool. Height is
# trimmed to the ~19 lines the demo actually emits so there is no dead space under it.
Set Width 1180
Set Height 470
Set Padding 22
Set Theme "Catppuccin Mocha"
Set TypingSpeed 55ms
Set PlaybackSpeed 1.0

Hide
Type "clear" Enter
Show

Sleep 600ms
Type "hsm watch --demo"
Sleep 400ms
Enter
Sleep 4s
