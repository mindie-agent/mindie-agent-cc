# Native acceptance

Status: in progress, 2026-09-21. Developer checks do not count as native acceptance. Windows desktop acceptance remains pending a real host. No release is claimed.

The real host is **Claude Code 2.1.269 on macOS**, using its configured **deepseek-flash / high** model. These are Claude Code integration results, not Anthropic foundation-model results. Tests use an isolated native profile and actual installed plugin; the parent `MINDIE_CC_CONFIG` environment variable is removed.

| Scenario | Actual observation | Status |
| --- | --- | --- |
| Native identity seam | Probe task `645fae2e-ef23-4e7d-9559-f4dd0cb9db6a`: native expansion/PreToolUse/Stop identity, MCP call ID correlation; 2.8 seconds, zero retries | Host capability verified |
| Installed adapter and explicit configuration | Candidate `48bc9e76d6c867e629e2af3a365891ecd8ac5388`, native version `0.1.0+mindie.48bc9e76d6c8`; actual installed knowledge and remote surfaces; native launchers contain the custom config path | Passed on this host |
| Generic remote without knowledge activation | Task `904b1d90-30f2-4f9b-85b5-436aa8760130`, 8.36 seconds, real dedicated container returned the marker and Linux; only remote MCP invoked, no knowledge lease | Passed; wrapper initially reported running, not a numeric exit status |
| Explicit read-only entry | Task `f9b34719-cf4c-4851-9b66-ed820e5fabb5`, 5.05 seconds; actual slash event created its lease and persisted read-only choice; sharing disabled | Passed |
| Knowledge query and body use | Same native task: query returned two public references (7.22 seconds); a later actual knowledge_explain returned 2502 characters. The model used dtype-rounded CPU reference detail and preserved single-device/shape limitations | Passed; existing community knowledge, not a newly published CC entry |
| Sharing-off Stop | Native Stop responses were `{}`, exit 0, without continuation; capture/outbox/cursor/receipt counts remained zero; no organizer call observed | Passed for these tasks |
| Live update and original job | Same task `f9b34719…`, live host PID 96211 and original MCP PIDs 96225/96226 stayed alive. Actual GitHub candidate `48bc9e7` → `c8f1436` build/native switch took 33.29 seconds; exact installed version `0.1.0+mindie.c8f143657427`, unchanged lease/token/boundary, query returned 2, original job status running → stop cancelled with descendants drained → status cancelled | Passed explicit candidate switch; automatic main polling is not claimed |
| Fresh-task and fork isolation | Candidate `c8f1436`: fresh task `a0e42726-493e-4e14-b8bf-d1038a306103` (6.08 seconds) and actual native fork `6b4fb49a-1e0c-446c-bd4b-e0e680ea76ac` (5.22 seconds) each made one knowledge query and received “session is not manually activated”; no retry or activation; original lease alone remained enabled | Passed |
| Authorized Stop, organizer, GitHub receipt and cleanup | Not exercised; no knowledge PR was created to manufacture acceptance evidence | Pending |
| Fresh task consumes a newly contributed CC experience | Requires the contribution path above | Pending |
| Optional feedback and default approval settings | No feedback vote was sent; model cases used an authorized isolated test permission setting | Pending |
| OS timer, failure rollback and Windows host | Native macOS timer/main-poll execution, rollback after a partially changed native install, and real Windows acceptance not exercised | Pending |
| Organizer authentication modes | Current native provider env was inspected; OAuth-only organizer invocation was not exercised | Pending |

An earlier switch attempt failed before download because Git depth was an integer; this was fixed and the failed evidence retained. In that attempt, the model added an incomplete endpoint selector to a job-status request; lookup correctly failed and the exact owned job was then cancelled via its saved record. The corrected native run supplied only the supported job alias and completed control successfully.

All model runs above used zero automatic retries. Native source/interpreter/config version selection and account/repository scope are distinct from idle task authorization. Raw transcripts, hidden reasoning and credentials are not published. Local evidence includes filtered native public records, tool-call bindings, native installation inventory, and the failed switch record.

The early capability probes used a small acceptance-only plugin; product results in the later rows used the installed adapter. Source changes after a tested candidate require their own relevant verification. Official host interfaces: [hooks](https://code.claude.com/docs/en/hooks), [plugins](https://code.claude.com/docs/en/plugins-reference).

The corrected code candidate is `c8f1436574279c4e1c98d00e6e50402d85f4d68b`. Its 35 component checks passed, including a real local process with an empty non-executable argument; these checks do not replace the native results above. The owned remote jobs were cancelled and the isolated idle knowledge service was stopped after acceptance. No global Claude configuration or unrelated plugin was changed.
