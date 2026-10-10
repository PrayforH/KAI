# Type diagnostic gate

`make typecheck` runs the locked Pyright version with the DeepAgents extra
installed and Linux platform semantics, matching the production runtime. It
checks the full `src` and `tests` trees in strict mode.

The baseline was captured from 1,524 pre-existing errors in unmodified develop
commit `5d05f71386fc4c83693e0ab2878eb22b66ac9659`. Removing an unused test variable
resolved one diagnostic; the remaining baseline contains 1,523 errors. These remain technical debt;
passing this gate does not mean the repository is free of type errors. The old
aggregate ceiling of 284 dated from v0.1.0 and was no longer a usable gate.

Each baseline entry identifies the repository-relative file, rule, exact message
and occurrence count. Line numbers are omitted so formatting does not change
diagnostic identity. A new error fails even when another error was fixed or the
overall error count decreased. A crash, invalid JSON/summary, or checker version
change also fails. The full current report is saved to `dist/quality/pyright.json`
and uploaded by CI.

When fixing existing errors, remove their resolved entries or decrement their
counts in the baseline as part of the same reviewed change. Do not automatically
regenerate the baseline in CI or add newly introduced errors to it. A dependency
or checker upgrade needs a separate review of changed diagnostics against the
previous code and dependency versions.
