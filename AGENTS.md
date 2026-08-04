# Repository Agent Instructions

## Browser Verification

- Use an isolated, headless Playwright context for browser verification.
- Do not attach to, select, or reuse the user's existing Chrome tabs or profile.
- Keep screenshots, traces, profiles, and logs in temporary or ignored output locations, then remove task-only artifacts after verification.
- The repository's `BrowserTool` must remain headless-only; `headless: false` is not a supported mode.
