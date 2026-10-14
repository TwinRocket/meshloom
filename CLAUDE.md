@AGENTS.md

## Claude-specific

- Answer the owner in French. Code, comments, commits, PRs and agent docs are in
  English; user-facing text (UI strings, `docs/user/`) exists in at least French
  and English.
- Work on a branch, one PR per lot. Never merge (the owner merges), never push to
  `main`, never create a release tag, and never deploy or touch production without
  an explicit request.
- Before saying a PR is ready, run `./scripts/quality/all_quality.sh` (see
  AGENTS.md, "Commands") and report what ran and what did not.
- Commits end with the Co-Authored-By line the session gives you; contract changes
  go in `CONTRACTS:` commits.
