#!/usr/bin/env sh
# Install the review-loop helper and the shared peer-review skill for both CLIs.
#
#   review-loop  -> `uv tool install --editable <this repo>` (on PATH via uv's tool bin)
#   skill        -> symlinked into ~/.claude/skills/peer-review (Claude Code)
#                   and ~/.codex/skills/peer-review (Codex CLI)
#
# Inside this repository nothing needs installing: `.agents/skills` (Codex) and the
# `.claude/skills` symlink (Claude Code) already expose the skill, and
# `uv run review-loop` runs the helper.
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
skill="$repo/.agents/skills/peer-review"

uv tool install --editable "$repo" --force >/dev/null
echo "review-loop: $(command -v review-loop || echo "installed; add $(uv tool dir --bin) to PATH")"

for dir in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
    mkdir -p "$dir"
    target="$dir/peer-review"
    if [ -e "$target" ] && [ ! -L "$target" ]; then
        echo "skip: $target exists and is not a symlink" >&2
        continue
    fi
    ln -sfn "$skill" "$target"
    echo "skill: $target -> $skill"
done
