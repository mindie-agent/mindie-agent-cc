# MindIE Agent for Claude Code

Claude Code adapter for MindIE Agent. This repository owns the native plugin entry, task identity, hooks, transcript parsing, installation and runtime switching. Shared knowledge and remote development stay in their own repositories.

Development is in progress. This is not a released or fully accepted implementation.

MindIE Agent inherits all nine [VAWS design principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md). See the [Harness boundary and lifecycle contract](https://github.com/mindie-agent/mindie-agent/blob/main/docs/harness-boundary-and-lifecycle.md).

Acceptance requires actual local Claude Code runs and observed external outcomes. Passing unit tests or fabricated hook input does not establish native acceptance. macOS is the first real host; Windows desktop acceptance remains pending.

## Setup (isolated)

```bash
python3 -m venv .venv
.venv/bin/pip install -r runtime-requirements.txt
.venv/bin/python scripts/setup.py \
  --config /tmp/mindie-cc/cc.json \
  --root /tmp/mindie-cc/data \
  --knowledge-python .venv/bin/python \
  --claude-config-dir /tmp/mindie-cc/claude \
  --no-schedule
```

`--no-native-install` writes MindIE files only. Native install uses:

```bash
claude plugin marketplace add /path/to/host-package
claude plugin install mindie-agent@mindie-agent-cc --json -y
claude plugin list --json
```

Explicit task entry is `/mindie-agent:init`. Contribution:

```text
/mindie-agent:sharing-enable --repository owner/repo --account USER --project-root /absolute/path --visibility public
```

Component checks: `python3 -m unittest discover -s tests -v`. These are not host acceptance.
