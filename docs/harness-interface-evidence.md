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
| Structured result submission | Actual 2.1.269 on macOS and 2.1.299 on Windows advertise only `StructuredOutput` to a localhost synthetic provider. Separate macOS 2.1.269 and WSL 2.1.299 controls without `--max-turns` accepted a valid fourth submission after three schema-invalid submissions, and ended five invalid submissions with `error_max_structured_output_retries`. Repeated ordinary text instead ended after two requests with native success but no `structured_output`; the adapter rejects that result. Claude owns its structured-output correction and termination, without a second turn cap or text fallback in this adapter. | `WorkerEnvelopeTests.test_structured_output_is_authoritative_over_prose`, `test_missing_structured_output_does_not_repair_fenced_prose`, `test_native_structured_correction_preserves_accumulated_usage_in_one_invocation`, `test_terminal_limits_keep_distinct_safe_diagnostics_and_one_call` and `test_one_batch_one_call_and_prompt_carries_only_public_blocks` preserve the strict result, native retry boundary and usage contract. |

The local HTTP structured-output experiment uses fabricated token counters.
Its native cost estimate is not a paid model-usage receipt. It proves the native
submission and parser path; it does not prove that a particular backend will
follow the instructions. Earlier paid failures remain failed and are not
replayed or relabeled as successes.

`model_calls` counts the admitted native invocation, not its HTTP requests.
The native result supplies accumulated usage across its internal turns. The
four-submission correction control reported `num_turns: 5` because native
tool-result events also contribute to that count.

The worker uses `stream-json --verbose` to observe schema rejection events
inside that same invocation. Full streams from native 2.1.269 and 2.1.299
ended with exactly one terminal event, with no later warning or event. The
terminal `structured_output` remains the only source of accepted metadata.
A duplicate terminal or trailing event is rejected while retaining valid
usage already reported by the first terminal.

The failure diagnostic retains only submission/rejection counts and
schema-owned paths paired with the observed validator keywords `required`,
`enum` and `maxLength`. Other paths or error forms become `unknown`. Tool
identifiers, submitted values, model prose and raw validator messages are
not retained. Native localhost fixtures exercise three rejected submissions
followed by success and five rejected submissions followed by exhaustion.
They verify the production parser and the absence of private-value canaries
in the diagnostic. The event transport has a separate 1 MiB bound; accepted
metadata still has the shared 32 KiB limit. These limits do not add a retry.

The historical macOS summary that exhausted native validation remains a
known failed invocation. Its old JSON-only receipt did not retain individual
schema rejections, so neither these localhost fixtures nor a later successful
synthetic Flash request establishes the cause of that historical failure.

The official CLI and environment-variable references were retrieved on
2026-10-16. They document no default agentic-turn limit and a native
`MAX_STRUCTURED_OUTPUT_RETRIES` default of five attempts. The adapter sets
neither limit. The SDK structured-output reference also requires checking
that a successful native result actually contains structured output. These
documentation claims were checked against the native experiments above:

- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/env-vars
- https://platform.claude.com/docs/en/agent-sdk/structured-outputs

The release review retains the exact native binaries, source hashes, complete
native events and experiment receipts outside the product tree. The registry
references this source-controlled summary rather than treating an unavailable
documentation URL as verified support evidence.
