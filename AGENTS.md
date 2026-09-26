# Agent Rules

## Scope

- Skills are content plus self-contained scripts. No builds, no servers.
- One concern per skill. Don't bundle unrelated operations into one skill.
- `SKILL.md` stays under 500 lines; frontmatter `name` matches the directory.

## Scripts

- Every script runs as `python3 scripts/<name>.py --help` with zero setup
  beyond `pip install -r requirements.txt` plus documented system binaries.
- Stdlib first. New third-party dependency needs a reason in the PR.
- Non-zero exit plus a one-line `Error:` message on failure. Print a short
  summary (`Wrote ...`, `Pages: ...`) on success.

## Verification

- Test every script against real generated fixtures before committing
  (build fixtures with the same libraries, not by hand).
- `python3 -m py_compile` on every changed script.

## Git Workflow

- Work on feature branches, push via Pull Requests, never direct to main.
- Conventional Commits (`feat:`, `fix:`, `docs:`).
- Before pushing to an existing branch, check the PR is still open
  (`gh pr list --head <branch>`). Merged or closed means a new branch.
