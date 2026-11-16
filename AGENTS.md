## Agent skills

### Issue tracker

Local markdown files under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical triage roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context (`GLOSSARY.md` and `docs/adr/` at the repo root). See `docs/agents/domain.md`.

## Workflow
The human starts these steps: /grill-with-docs, /opsx:propose, /opsx:apply, /code-review, /opsx:archive.
- Specs live only in OpenSpec (openspec/). Never use /to-spec, /to-tickets, /implement or /shape-spec.
- Vocabulary: GLOSSARY.md. Use its terms in specs, code and tests. It holds vocabulary only, never specs.
- Conventions: agent-os/standards/index.yml. Load only the standards that match the files you touch.
- Build with the tdd skill. Test only at confirmed seams. Do not mock internal modules.
- Commit before review. Review runs in a fresh session: /code-review BASE openspec/changes/NAME.

## Skills

Before writing or changing any code, read and follow:
- `.agents/skills/ponytail/SKILL.md`
- `.agents/skills/karpathy-guidelines/SKILL.md`