# Features added to this copy

Base: GitHub `joshuanwankwo82-web/floating-agent-python`, commit `4318e50`.
Everything below is already applied — no patches or scripts to run.
`git status` / `git diff` shows exactly what changed.

## 1. Token Harbor provider
- **Token Harbor** is listed second in the provider presets, right after OpenRouter.
- Its base URL is `https://tokenharbor.ai/v1` and it uses the OpenAI-compatible format.
- Keys starting with `thk_` are recognised as Token Harbor keys automatically.
- Get a key at https://tokenharbor.ai/dashboard/api-keys.
- Files: `provider_autodetect.py`, `dev_tests/test_provider_autodetect.py`

## 2. "Check all" no longer drains tokens or disconnects models
- **Check all:** makes one free model-list request per key instead of a full chat
  call per model. A model the provider lists turns green without spending any
  tokens. Models the list doesn't cover get one capped ping (max 16 tokens),
  spaced out per key.
- **OpenRouter:** remaining credit is read from the free `/key` endpoint.
  - A paid model with no credit left shows red, with the reason.
  - `:free` models stay green.
- **Single-row Check:** a real ping, capped at 16 tokens.
- **Temporary problems:** rate limits (429), outages (5xx) and timeouts no longer
  turn a model red. The status is kept and the reason is shown, so
  "Remove not connected" can't delete working models any more.
- Clicking Check all again while it's running is ignored.
- Files: `settings_checks.py`, `settings_panel.py`, `dev_tests/test_settings_check_cost.py`

## 3. Profile page redesign
- **Tab order:** Settings opens on **Profile**. The switcher reads
  **[ Profile | Keys & Connect ]**.
- **Header at the top:**
  - A large round photo. Click it to upload one; with no photo it shows your initials.
  - Your name and @handles, updated as you type.
  - A profile-completeness bar.
  - **Upload photo…** and **✕ Remove photo** buttons.
- **Cards:** each section is a rounded card — About you, Links, Emails,
  Connect apps, GitHub authors, Web passwords. Fields sit in two columns.
- **Save profile** is fixed to the bottom of the page, so it's always visible.
- What gets saved is unchanged: same fields, same encrypted store.
- Files: `settings_panel.py`, `dev_tests/test_profile_page_layout.py`

## Running
- Double-click `RUN_FRIDAY.bat`.
- Or run: `python -m pip install -r requirements.txt`, then `python agent_overlay.py`.

## Tests (run on Linux + Xvfb)
| Test file | Result |
|---|---|
| test_provider_autodetect | 65/65 |
| test_settings_check_cost | 16/16 |
| test_settings_panel | 122/122 |
| test_profile_page_layout | 8/8 |
| test_user_profile | 41/41 |
| test_act_script | 131/131 |

The 12 test files that already failed on the unmodified GitHub copy still fail
the same way. `test_agent_tools_ide` is intermittently slow/flaky both before
and after these changes.
