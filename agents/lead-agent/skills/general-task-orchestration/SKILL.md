---
name: general-task-orchestration
description: Turn a general user request into a scoped, tool-backed and verifiable result without assuming a business domain.
---

# General task orchestration

Use this workflow for multi-step execution tasks that do not already have a more specific business Agent. Simple conversational answers do not need this workflow:

1. Restate the concrete outcome internally and identify the evidence needed to prove completion.
2. Use supplied conversation context first. Inspect the workspace only when relevant files or execution evidence are needed; ordinary questions and comparisons need no workspace inspection.
3. Choose only tools that are actually available in the current Run.
4. Match the current stage: answer in chat for questions and comparisons; use file tools only for requested files or necessary execution. When the user reserves a choice or confirmation, present it and stop before dependent work.
5. Verify outputs, distinguish observed facts from inference, and report unresolved inputs.

When a request requires domain data, credentials, or a specialized workflow that is not available, stop at the honest boundary and explain which Agent or MCP capability is needed. Do not simulate an unavailable business system.
