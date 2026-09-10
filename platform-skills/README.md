# Vendored platform Skills

Third-party Agent Skills shipped with the platform catalog. Files are stored
byte-for-byte as published upstream (including each package's own LICENSE) so
provenance can be audited; only the catalog-facing description is normalised to
the platform model's 500-character budget.

| Upstream | License | Packages |
| --- | --- | --- |
| [MiniMax-AI/skills](https://github.com/MiniMax-AI/skills) @ `60aaae5` | MIT | `minimax-docx`, `minimax-xlsx`, `minimax-pdf`, `pptx-generator`, `color-font-skill`, `design-style-skill`, `ppt-editing-skill`, `slide-making-skill` |
| [anthropics/skills](https://github.com/anthropics/skills) @ `41bbe19` | Apache-2.0 | `skill-creator`, `mcp-builder`, `internal-comms`, `theme-factory`, `frontend-design`, `web-artifacts-builder`, `webapp-testing`, `canvas-design`, `algorithmic-art` |

## Deliberately not vendored

Anthropic's `docx`, `pptx`, `xlsx` and `pdf` document Skills are published under
an Anthropic-proprietary licence whose "Additional Restrictions" forbid copying
these materials, retaining copies outside Anthropic's Services, creating
derivative works, and distributing them to third parties. `doc-coauthoring`
carries no licence file at all (all rights reserved by default). Office document
work is covered by the MIT-licensed MiniMax packages instead.

## Runtime requirements

The office packages generate `.docx` / `.pptx` through Node libraries, so the
worker image installs Node plus `docx` and `pptxgenjs` globally (see
`deploy/docker/api-update.Dockerfile`). LibreOffice and pandoc are intentionally
absent: those paths are conversion/recalculation extras, and the affected Skills
degrade to reporting the missing tool instead of silently failing.
