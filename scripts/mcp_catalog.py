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
            "Search graded rules and task material in the selected domain when a missing "
            "contract, mechanism, or method is needed for the next step. Returns bounded "
            "navigation cards and deduplicated foundation_cards; each match includes a "
            "readable ref and feedback_ref for its observed revision. Pass observed task "
            "conditions to filter graded rules (include_archived discovers archived or "
            "superseded units). Pass related_next as continuation for related task blocks, "
            "navigation_only to omit inline bodies, and run_id to bind delivery events."
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
                token_budget={"type": "integer", "minimum": 1, "maximum": 64000},
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
            "Read complete graded units or task/block references by their exact returned ref. "
            "For graded units, pass refs (even for one unit) with observed conditions, visible "
            "known_refs, and token_budget to assemble the dependency closure (shared prerequisites "
            "appear once in requires and full unit bodies appear in units). A single ref supports "
            "legacy task and block navigation (first_block_ref and previous_block_ref/next_block_ref "
            "for walking a task reference slice). Pass run_id to bind delivery events."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                refs={"type": "array", "items": {"type": "string"}},
                conditions={"type": "object"},
                known_refs={"type": "array", "items": {"type": "string"}},
                token_budget={"type": "integer", "minimum": 1, "maximum": 64000},
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
            "Record optional usage events (discovered, loaded, applied, evaluated) or an "
            "up/down rating for an exact feedback_ref returned by query or explain. "
            "evaluated requires run_id and outcome (supported, refuted, inconclusive) with "
            "optional evidence_source."
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
            "Distill caller-selected, authorized evidence into C5 graded units at "
            "task_state='verified_milestone' or 'completed'. Pass related_refs to compare "
            "against existing units as a delta, or pass a foreground mindie-distill-decision/1 "
            "proposal for deterministic validation and local application with zero model calls."
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
