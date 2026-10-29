"""Native knowledge MCP schemas, separate from execution and session identity.

[Role]
Defines the knowledge and factual-status tools advertised by the Claude adapter.
[Invariants]
Schemas describe public inputs only; execution owns manual-domain admission.
[Timeouts & Locks]
Pure in-memory definitions; no I/O, services, subprocesses or locks.
[Side-Effect Policy]
Importing the catalog never activates knowledge or starts background work.
"""
import rsi_explore

KNOWLEDGE_TOOL_DEFS = [
    dict(
        name="mindie_status",
        description=(
            "Read factual MindIE status for the current bound task. "
            "Includes safe failure categories and this task's contribution batch IDs "
            "for recovery inspection. Does not activate, enable sharing or retry work."
        ),
        inputSchema=dict(type="object", properties={}, additionalProperties=False),
    ),
    dict(
        name="knowledge_sync",
        description=(
            "Synchronize the configured knowledge feeds for the current manually "
            "enabled domain. Returns each feed's sync outcome, including deferred "
            "or failed work. Does not activate knowledge or enable contribution."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(domain={
                "type": "string",
                "description": "Optional configured domain; omit for the installation default.",
            }),
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_query",
        description=(
            "Use when a missing contract, mechanism or method could change the next action. "
            "Skip retrieval when code and current evidence support a fix and verification; "
            "probe missing environment facts first. "
            "Search graded rules and task material in the selected domain. Each match has a readable ref "
            "and feedback_ref for its observed revision. Pass known task conditions to filter graded rules. "
            "Graded corpora return bounded cards plus deduplicated foundation_cards; include_archived "
            "also discovers archived or superseded units when investigating old conclusions. "
            "navigation_only returns navigation cards without inline bodies; run_id records "
            "deduplicated delivery events for an existing run. "
            "For related task blocks, pass related_next as continuation without query. "
            "A changed corpus rejects the continuation. References are advisory."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                query={"type": "string"},
                limit={"type": "integer", "minimum": 1, "maximum": 20},
                conditions={"type": "object"},
                continuation={"type": "string"},
                domain={
                    "type": "string",
                    "description": "Optional configured domain name; omit for the installation default.",
                },
                domain_path={"type": "string"},
                token_budget={"type": "integer", "minimum": 1, "maximum": 16000},
                include_unknown={"type": "boolean"},
                include_archived={"type": "boolean"},
                navigation_only={
                    "type": "boolean",
                    "default": False,
                    "description": "Return navigation cards only; omit inline result bodies while keeping exact read costs.",
                },
                run_id={
                    "type": "string",
                    "description": "Bind delivery and discovered events to this run for deduplicated usage accounting.",
                },
                **rsi_explore.schema_properties(),
            ),
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_explain",
        description=(
            "Read only knowledge needed for the next action; an exact returned ref needs no new query. "
            "For graded units, prefer refs even for one unit, with observed conditions, fully visible "
            "known_refs and an explicit token_budget. Copy revision references exactly. Stop reading "
            "once the next action and required premises are clear; new evidence can reopen retrieval. "
            "A single ref keeps the "
            "historical contract: a graded-unit ref returns its exact current revision and conditions; "
            "a task ref returns the current navigation and first_block_ref; a block ref returns that "
            "immutable block with previous_block_ref/next_block_ref for walking the task. "
            "Batch refs return the shared dependency closure once in requires and full unit bodies "
            "in units, using conditions and known_refs (only exact id@revision units still fully "
            "visible) to avoid re-sending visible bodies; unresolved reports condition or budget "
            "exclusions instead of truncated steps. The content is a reference slice, not the full "
            "case or a verified report. run_id binds the delivered units to an existing run."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                refs={"type": "array", "items": {"type": "string"}},
                conditions={"type": "object"},
                known_refs={"type": "array", "items": {"type": "string"}},
                token_budget={"type": "integer", "minimum": 1, "maximum": 16000},
                run_id={
                    "type": "string",
                    "description": "Bind delivery and loaded events to this run for deduplicated usage accounting.",
                },
                **rsi_explore.schema_properties(),
            ),
            required=[],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_feedback",
        description=(
            "Record optional usage evidence for an exact observed ref: the historical up/down rating, "
            "or a 4-stage usage event (discovered, loaded, applied, evaluated). evaluated requires "
            "run_id and outcome. evidence_source distinguishes caller-claimed evidence from verified "
            "run or ablation evidence on evaluated events. "
            "Copy the exact feedback_ref returned by query/explain into ref. Never required."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                rating={"type": "string", "enum": ["up", "down"]},
                reason={"type": "string"},
                event_type={
                    "type": "string",
                    "enum": ["discovered", "loaded", "applied", "evaluated"],
                },
                run_id={"type": "string"},
                step_id={"type": "string"},
                outcome={
                    "type": "string",
                    "enum": ["supported", "refuted", "inconclusive"],
                },
                evidence_source={
                    "type": "string",
                    "enum": ["agent_declared", "run_manifest_verified", "ablation_verified"],
                    "default": "agent_declared",
                    "description": "How an evaluated outcome was established; defaults to an agent declaration.",
                },
            ),
            required=["ref"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_distill",
        description=(
            "Distill only explicitly selected evidence into C5 graded units. The "
            "shared runtime redacts the selected inputs before the model call. This "
            "requires the current active capture lease and saved contribution "
            "authorization. task_state is explicit; an ordinary Stop does not mean "
            "completion. Pass related_refs to compare the new evidence against existing units, "
            "or a foreground mindie-distill-decision/1 proposal for deterministic validation "
            "and application without another model call. This is one paid native model call "
            "when no proposal is supplied: it never discovers transcripts, grants publication, "
            "or retries an uncertain result."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                inputs=dict(
                    type="array",
                    minItems=1,
                    items=dict(
                        type="object",
                        properties=dict(
                            kind={"enum": [
                                "experiment_ledger", "run_manifest",
                                "authorized_transcript", "ecosystem_repo",
                                "hardware_spec", "ablation_receipt",
                            ]},
                            outcome={"enum": [
                                "observed", "verified", "compile_failed",
                                "infra_failed", "measurement_invalid", "in_flight",
                            ]},
                            source_locator={"type": "string", "minLength": 1},
                            content={"type": "string", "minLength": 1},
                            evidence_refs={
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            repetitions={"type": "integer", "minimum": 1},
                        ),
                        required=[
                            "kind", "outcome", "source_locator", "content",
                            "evidence_refs",
                        ],
                        additionalProperties=False,
                    ),
                ),
                task_state={"enum": [
                    "in_flight", "verified_milestone", "completed",
                ]},
                domain={
                    "type": "string",
                    "description": "Optional configured domain name; omit for the installation default.",
                },
                related_refs={"type": "array", "items": {"type": "string"}},
                proposal={"type": "object"},
            ),
            required=["inputs", "task_state"],
            additionalProperties=False,
        ),
    ),
]
