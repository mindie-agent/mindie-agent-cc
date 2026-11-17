# mindie-agent-cc 0.1.28: Runtime Skill vs. Install-Guide Separation and Manual-Trigger Boundary Cleanup

[中文](mindie-agent-cc-0.1.28.md)

- **Relocate Setup & Consent Guides to `docs/install/`**: Moves installation-only documents (`workspace-setup.md`, `github-authentication.md`, and `user-notice.md`) out of `skills/mindie-agent/references/` into repository-level `docs/install/`, updating all bilingual `README` and documentation index links.
- **Pure Runtime `SKILL.md` & Operations Reference**: Removes first-use onboarding flows, `AskUserQuestion` onboarding popups, `vllm-ascend-workspace` initialization guides, `gh auth login` instructions, `setup.py sharing` onboarding prompts, and GitHub Star prompts from `skills/mindie-agent/SKILL.md` and `skills/mindie-agent/references/setup-and-operations.md`, keeping shipped skill references strictly focused on runtime subcommands, multi-domain routing, graded schemas, and NPU lease/profiler rules.
- **Preserve Explicit Manual Trigger**: Keeps `disable-model-invocation: true` in `skills/mindie-agent/SKILL.md` frontmatter so `knowledge_*` tools remain strictly gated on explicit `/mindie-agent` activation (`P2.1`).
