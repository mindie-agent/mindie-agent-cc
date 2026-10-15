# Claude Code interface evidence

The release declaration lists only Claude Code 2.1.269 and 2.1.299. These are
tested version points, not a claim that every intervening or newer version works.
`scripts/harness.py` uses the shared semantic-version validator and checks that
every registered production symbol, test identifier and local evidence file
exists. CI runs this check before component tests. The metadata command binds
the resulting declaration and product version to a full source commit; it does
not publish a Release.

The experiments below used isolated native profiles. Control-only experiments
sent no user message and made no model request. Component tests are separate
from native acceptance and from backend quality.

| Interface | Observed native behavior | Reproducible contract checks |
| --- | --- | --- |
| Git marketplace and installation record | 2.1.269 and 2.1.299 installed the local fixture or selected MindIE package and returned version, scope, enablement and install path through `plugin list --json`. A 2.1.269 Git fixture rejected a full SHA as a branch ref; an explicit tag plus the same profile marketplace declaration updated the native catalog. The checkout SHA must still match the selected official Release before plugin installation. | `tests/test_release_update.py` validates selected SHA before plugin update and refuses to resolve a still-running writer from a matching version snapshot. |
| Per-marketplace settings | Actual 2.1.269 selected-tag installation consumed the exact profile's `extraKnownMarketplaces` declaration. A separate native disable/update experiment preserved `enabled: false` after updating the local fixture. The implementation changes only this marketplace's `source.ref` and `autoUpdate`; it refuses unknown source declarations. The disable-through-update native experiment has not yet been repeated on 2.1.299. | `test_native_first_install_policy_preserves_exact_source_and_other_choices` and `test_native_source_declaration_preserves_other_settings_and_explicit_off` check that unrelated settings and explicit disable are unchanged. |
| Native initialization | Isolated `--init-only` completed SessionStart preparation without a conversation on both tested versions. Exit zero alone was insufficient: a Windows attempt returned zero while MCP loading failed, so discovery and fixed-runtime readback are separate gates. | `tests/test_setup_install.py` covers first preparation, compatibility and state-preserving activation. |
| Native Hook payload and warning | Real Stop events on both tested versions supplied native session identity and transcript location. Static `systemMessage` warning output uses no block decision or model context. Explicit valid off remains quiet; malformed authority is a warning. | `tests/test_launch_entry.py` checks bad configuration, missing fields, invalid schemas and valid off without a model. |
| MCP stdio | Real native SDK control initialization discovered plugin MCP servers on both versions without a model turn. The facade handles cancellation independently of active tool work. | `tests/test_front_control.py` keeps a real facade process alive across cancellation and a complete helper-binding change. This is a process contract test, not evidence of a complete native update. |
| Structured result submission | Actual 2.1.269 on macOS and 2.1.299 on Windows advertise only `StructuredOutput` to a localhost synthetic provider. A direct submission passes the exact schema and shared validation. A later 2.1.299 WSL control established that ordinary JSON text needs the native structured-output correction: `--max-turns 1` ends before that correction; `--max-turns 2` permits the native enforcement message and second submission. The adapter permits that single native correction and still accepts only `structured_output`. | `WorkerEnvelopeTests.test_structured_output_is_authoritative_over_prose`, `test_structured_tool_result_event_is_not_an_extra_model_call`, `test_native_structured_correction_preserves_accumulated_usage_in_one_invocation` and `test_native_max_turns_error_is_failed_with_known_usage` preserve the strict result and usage contract. |

The local HTTP structured-output experiment uses fabricated token counters.
Its native cost estimate is not a paid model-usage receipt. It proves the native
submission and parser path; it does not prove that a particular backend will
follow the instructions. Earlier paid failures remain failed and are not
replayed or relabeled as successes.

`model_calls` counts the admitted native invocation, not its HTTP requests.
The native result supplies accumulated usage across its internal turns. The
WSL text-then-submission control made two localhost requests and reported
`num_turns: 3` because native tool-result events also contribute to that count.

The release review retains the exact native binaries, source hashes, complete
native events and experiment receipts outside the product tree. The registry
references this source-controlled summary rather than treating an unavailable
documentation URL as verified support evidence.
