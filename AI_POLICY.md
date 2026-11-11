# AI-assisted contributions

AI-assisted contributions are welcome. This project is itself built with an AI agent. What matters is that a human
stands behind every line.

## What you must do

- Read every line you change, and be able to explain it and defend its design in review.
- Run the tests that apply and report the real results. Never write that a test passed if you did not run it.
- Remove invented or unrelated output: made-up APIs, paths, behaviour, results, or changes outside the scope of the issue.
- Find the cause of the problem and fix that, not the symptom. Keep the change as small as the fix allows.
- Make sure you may contribute what you submit (license, provenance).

## Disclosure

If an AI tool helped write or substantially review your change, say so in the pull request, in one plain sentence
(for example: "Written with Claude Code; I read every line and ran both unit suites"). The PR template has a line for it.
Typo fixes, formatting and autocomplete need no disclosure.

Do not add the AI as a co-author or sign-off in commits (`Co-Authored-By`, `Signed-off-by`, `Reviewed-by`): authorship
and responsibility are yours.

## What the maintainer may do

Ask you to explain a change, to show how you checked it, or to add tests. Close work that you cannot explain or verify.
Review looks at the quality of the submission, not at whether a tool wrote it. There is no automated AI detection and
no automated gate for this policy yet.
