@AGENTS.md

## Claude Code specifics

- Hooks in `.claude/settings.json` run `scripts/record_session.py` on every `Stop` and on
  `SessionEnd`. They write this session to `resources/sessions/`. Don't create session notes by hand.
- Your actor id is `agent:<your model id>`. Pass it to `new.py` with `--by`.
