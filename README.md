# delegate

`delegate` is a kit for delegating implementation work to coding agents, with independent evidence at each merge. It ships a delegation protocol as agent skills, and a tool that renders a specialist and a blind verifier twin for each harness from one declaration, validates the agent files, and writes each verifier's brief. The kit names no person, company, or tracker; each repository keeps its own gates and hotspots.

## Install

```bash
uv tool install "git+https://github.com/AlexanderNZ/delegate#subdirectory=skills/agent-definitions/validator"
```

This puts `agent-definitions` and `verifier-brief` on your PATH. The kit needs Python 3.11 or later and git. Nix is optional.

The two skills are plain Markdown directories: `skills/agent-delegation/` and `skills/agent-definitions/`. Copy or link them into a directory that your harness reads skills from.

## Generated files

The files that the tool generates (rendered agents, context skills, delegation docs) belong to you. They need no copyright notice or licence notice from this project.

## Licence

MIT. See [LICENSE](LICENSE).
