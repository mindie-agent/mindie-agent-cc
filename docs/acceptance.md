# Native acceptance

## Current 0.0.1 checkpoint, 2026-10-16

Release `0.0.1` declares exactly Claude Code `2.1.269` and `2.1.299`, not the
versions between them. The [interface evidence](harness-interface-evidence.md)
records the native contracts. The shared
[release evidence](https://github.com/mindie-agent/mindie-agent/blob/codex/internal-release-0.0.1/docs/releases/0.0.1.md)
keeps the platform receipts, failures and dependency deltas separately identified.
This page does not turn an older installed candidate into a new native run.

The product scripts at `dc6df762085108314aa72593baa084cc4e507ee2` are byte-identical
to installed `4e00a517cf73e2b468c34f9f3d5b8a0149a4486d`. The final lock pins
knowledge `81fb4e222f0cba5cffe50824ffd55b33d312283d`, remote-dev
`46a91a5b409506760ea530f7037a3f9f04d9a2d7` and diagnostics
`10cc3391ed358d7b7e0e1acb99ca5be4d1934b47`. The remote endpoint-classification
delta has focused source/handler and complete-lock CI checks; the installed
`4e00a51` receipts retain their original remote `077f2b5` pin. Subsequent README
and test-fixture changes do not change the product scripts, interfaces or lock.

| Environment | Retained natural business evidence | Later native delta evidence |
| --- | --- | --- |
| macOS / MBP | `ac20279`: first and incremental Stop, body/index/usage, query/explain and local publication | `4e00a51` / Claude 2.1.299: cross-project fork/resume, parent-history exclusion, explicit-off cancellation and actual main-session MCP cancellation |
| Native Windows / Lenovo | `ac20279`: first and incremental Stop, body/index/usage, query/explain and local publication | `4e00a51` / Claude 2.1.299: fork/resume, explicit off, main-session MCP cancellation, SSH read/error and live-owner cutover |
| WSL / Ubuntu 24.04 | `071e077`: first and incremental Stop, body/index/usage, query/explain and local publication; `ac20279` adds stream/line-ending and closure checks | `4e00a51` / Claude 2.1.299: fork/resume, parent-history exclusion, explicit-off cancellation and actual main-session MCP cancellation |

The three cancellation probes matched native MCP cancellation to the running
operation and verified its processes exited while Claude remained responsive.
They used localhost synthetic responses, not external model calls or remote
worker/job execution. The final fork cells were left installed, off and retired;
earlier business cells have separately recorded keep-data removal.

An extra acceptance-only Stop observer failed on the parent turn in these fork
probes: JSON input decoding on macOS/WSL and unquoted Python invocation in
Windows Git Bash. The product Stop and durable captures passed independently.
The observer was corrected for future use with offline tests; the original
failures remain retained, and no rerun is claimed. Exact-head component CI
`38830138492` passed on all three OSes for `dc6df76`; it is not a substitute for
the native evidence above.

The September results below describe the previous implementation, including
manual activation, generation switching and timer behavior. Their source
identities and pending entries remain historical; they do not define the
current installation flow or current release gates.

## Historical checkpoint, 2026-09-21

Status: merge review, 2026-09-21. Developer checks do not count as native acceptance. Windows desktop acceptance is scheduled after merge on the user-provided real host; it is not a gate for this adapter merge. No release is claimed.

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
| Authorized Stop and local organizer | Same real task `f9b34719…`: capture-only sharing fixture, no repository target; native init 4.40 seconds and review 18.19 seconds, one organizer attempt succeeded using local deepseek-flash/high. Two candidates were rejected by outbound privacy scanning; one configuration-status draft remained local and its batch was disabled, with no PR URL/head | Native invocation and protection passed; useful domain extraction is not claimed |
| GitHub receipt and cleanup | No knowledge PR was created to manufacture acceptance evidence | Post-merge integration acceptance |
| Fresh task consumes a newly contributed CC experience | Requires the contribution path above | Pending |
| Optional feedback and default approval settings | No feedback vote was sent; model cases used an authorized isolated test permission setting | Pending |
| Native rollback after installation result loss | Actual native install of retained candidate `48bc9e7` and exact readback, followed by an injected result-loss exception: restored original `c8f1436` native package with readback in 12.96 seconds; two native install calls, unchanged complete current tuple, zero retries | Passed on this isolated macOS profile |
| OS timer and Windows host | Native macOS timer/main-poll execution and real Windows acceptance not exercised | Post-merge integration acceptance |
| Organizer authentication modes | Current native provider env was inspected; OAuth-only organizer invocation was not exercised | Pending |

An earlier switch attempt failed before download because Git depth was an integer; this was fixed and the failed evidence retained. In that attempt, the model added an incomplete endpoint selector to a job-status request; lookup correctly failed and the exact owned job was then cancelled via its saved record. The corrected native run supplied only the supported job alias and completed control successfully.

All model runs above used zero automatic retries. Native source/interpreter/config version selection and account/repository scope are distinct from idle task authorization. Raw transcripts, hidden reasoning and credentials are not published. Local evidence includes filtered native public records, tool-call bindings, native installation inventory, and the failed switch record.

The early capability probes used a small acceptance-only plugin; product results in the later rows used the installed adapter. Source changes after a tested candidate require their own relevant verification. Official host interfaces: [hooks](https://code.claude.com/docs/en/hooks), [plugins](https://code.claude.com/docs/en/plugins-reference).

The corrected code candidate is `c8f1436574279c4e1c98d00e6e50402d85f4d68b`. Its 35 component checks passed, including a real local process with an empty non-executable argument; these checks do not replace the native results above. The owned remote jobs were cancelled. After the sharing-on check, its temporary community configuration was restored to sharing-off and the idle knowledge service acknowledged stopping with activity 0 and no queued captures. No global Claude configuration or unrelated plugin was changed.

The sharing-on check used the current task’s actual public review of earlier remote job results; no synthetic transcript or raw transcript upload was used. The consumed range included the final review answer; remaining transcript bytes were native hook/system metadata. The stored status-only draft is a generation-quality miss, not evidence that a useful new experience was produced. Routine activation/configuration reports should yield no entries. Changed and untested settings must not be called failed without recorded failure evidence; the prompt now states these distinctions explicitly. The revised wording has been reviewed but not rerun through a model as part of this merge check.

Identity review retained the exact native task + tool-use ID + tool name + parameter digest binding, atomic one-use consumption, and rejection of missing/contradictory identity. No guessed native identity or activation through ordinary remote calls was added. Rollback review retained the previous complete generation, restores the pointer under the exclusive operation lock, invokes the actual native installer for the previous package, and reports success only after exact native readback; an unsuccessful restore is reported explicitly.

Merge scope is the thin Claude Code adapter and its bounded local lifecycle. The remaining contribution publication/receipt cleanup, new-task use of a newly contributed CC entry, feedback, OAuth-only organizer authentication and scheduled polling are post-merge integration acceptance, not claims of current release readiness. Further domain-generation quality evaluation remains open. No known code correctness or identity defect is intentionally deferred to merge.
