# Selvedge demo gif — vhs tape file
#
# Renders docs/demo.gif from a deterministic four-command tour:
#   1. selvedge status
#   2. selvedge blame payments.amount
#   3. selvedge diff  payments.amount
#   4. selvedge search "stripe" --limit 5
#
# The data behind these commands is seeded by scripts/demo-seed.py — a
# fictional "freightline-api" project rolling out Stripe billing over
# ~5 weeks. See that file for the narrative.
#
# Run via scripts/record-demo.sh (which seeds the DB first), or manually:
#
#     SELVEDGE_DB=$PWD/scripts/demo.selvedge.db \
#         FORCE_COLOR=1 vhs scripts/demo.tape
#
# Requires `vhs` from charmbracelet:
#     brew install vhs        (macOS)
#     go install github.com/charmbracelet/vhs@latest

Output docs/demo.gif

# Recording settings — tuned for GitHub README rendering. 1280×720 keeps
# the rich-table output readable without forcing a horizontal scroll on
# typical desktop README widths.
Set Shell        "bash"
Set FontSize     16
Set Width        1280
Set Height       720
Set Theme        "Catppuccin Mocha"
Set TypingSpeed  60ms
Set PlaybackSpeed 1.0

# Force the SELVEDGE_DB env var for the duration of the recording so
# every shell command in the tape resolves to the seeded demo DB
# regardless of where vhs was launched from.
Env SELVEDGE_DB  "scripts/demo.selvedge.db"
Env FORCE_COLOR  "1"
Env SELVEDGE_QUIET "1"
Env PS1          "$ "

# Settle the prompt before the first key.
Hide
Type "clear"
Enter
Sleep 300ms
Show

# 1. Overview — how many events are in the store and what landed last.
Type "selvedge status"
Sleep 600ms
Enter
Sleep 4s

# 2. The headline — most recent change to payments.amount, with the
#    full reasoning the agent wrote when it landed the retype.
Type "selvedge blame payments.amount"
Sleep 600ms
Enter
Sleep 5s

# 3. Same entity, full history — shows the evolution from cents-as-INT
#    to NUMERIC. Two events; both diffs and reasoning visible.
Type "selvedge diff payments.amount"
Sleep 600ms
Enter
Sleep 5s

# 4. Search by topic — finds every event that mentions "stripe" across
#    entity paths, diffs, and reasoning. Capped at 5 so the gif stays
#    under the README's reasonable height budget.
Type 'selvedge search "stripe" --limit 5'
Sleep 600ms
Enter
Sleep 5s

# Hold the final frame so a casual viewer has time to read the last row.
Sleep 2s
