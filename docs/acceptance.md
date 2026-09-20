# Native acceptance

Status: in progress, 2026-09-21. Developer checks are diagnostic only and do not count as native acceptance. Windows desktop acceptance is pending a real Windows host.

| Scenario | Actual observation | Status |
| --- | --- | --- |
| Explicit native entry and MCP correlation | Claude Code 2.1.269, configured model deepseek-flash/high, task `645fae2e-ef23-4e7d-9559-f4dd0cb9db6a`; UserPromptExpansion and PreToolUse supplied native identity; MCP metadata carried the same call ID; one silent Stop; 2.8 seconds, zero retries | Host capability verified |
| Native installation and version readback | Owned isolated Claude profile; marketplace add and plugin install reported success; native inventory returned enabled version 0.0.1 and its cache path | Host capability verified |
| Native update API | Updated the same owned marketplace source to 0.0.2; native plugin update returned exact old/new versions; native inventory reported 0.0.2 | Host capability verified |
| Adapter first-use configuration and read-only query | Awaiting implementation review and installed-candidate run | Pending |
| Generic remote call without knowledge activation | Awaiting installed-candidate run | Pending |
| New task and fork isolation | Awaiting installed-candidate run | Pending |
| Authorized Stop, actual organizer and exact GitHub receipt cleanup | Awaiting installed-candidate run | Pending |
| Fresh task uses the resulting knowledge | Awaiting published entry and independent task | Pending |
| Complete generation switch preserves authorization and remote job control | Awaiting two installed candidate generations and actual job | Pending |

The host probes used small acceptance-only plugins, not the final adapter. They establish which native interfaces exist and how they behave. They do not establish a completed product or knowledge loop. The actual model configured in the local Claude Code host was deepseek-flash; these runs must not be described as testing an Anthropic foundation model.

Native installation never writes a private plugin registry directly or changes the user's unrelated configuration. Raw transcripts and credentials stay local; only public results and identity metadata are retained as review evidence.
