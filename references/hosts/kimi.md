# Kimi host capability mapping
- When starting or resuming discovery, extraction, or evidence work, read the [retrieval playbook](../retrieval-playbook.md).
- Before classifying, deduplicating, admitting, or resolving a source conflict, read the [source policy](../source-policy.md).

## Intake boundary

Follow the one-question-at-a-time intake in SKILL.md. Accept each answer as ordinary conversation text, retain the private draft across turns, and show only the current question and its choices. Wait for the user's answer before advancing; split compound fields and question 20 conditions across turns. Use a currently available choice control for one question when it can hold all choices, otherwise show lettered choices. Only the user selects or skips; an unanswered item stays pending.

Accumulate explicit answers under the twenty internal topic keys, then call `build_profile_from_questionnaire` only after collection is complete. `parse_numbered_questionnaire` is optional compatibility for a complete numbered document volunteered by the user, not a requirement for conversational replies. The mapping exists only inside the host; never ask the user for JSON or a path. An explicitly unknown readiness, strength, activity subtype, constraint, or preference remains `unknown` or empty instead of being inferred. Follow the separate profile confirmation gate before research.

## Capability map

Examples are conditional on tools callable in the current session. Inspect them before declaring anything, then apply the linked policy/playbook unchanged.

| Capability | Use | Declare | Absent fallback |
|---|---|---|---|
| search | 当前会话可调用的联网搜索用于发现公开候选页面。 | Pass --host-capability search only when callable now. | Continue in offline mode with already authenticated material; record discovery unavailable. |
| browse | 当前会话可调用的网页读取用于打开公开页面并检查引用。 | Pass --host-capability browse only when callable now. | Without it, do not claim page verification; use already authenticated material or offline mode. |
| vision | 仅在当前会话暴露图像理解时检查已提供的公开图片或 host-produced OCR/QR text。 | Pass --host-capability vision only when callable now. | Seek machine-readable HTML/XLSX/PDF/text or host-decoded QR payload, or mark the fact missing; the normal boundary remains local/host-native. |
| local_exec | 当前会话可调用的本地命令/文件工具运行仓库 CLI、adapter 和 validator。 | Workflow gate only; record its state in the handoff. | Without it, stop before deterministic calculation, disclose the limitation and move the session to a host with local execution; never emulate a result in prose. |
| file_output | 当前环境可写工作区时创建 evidence workspace 与匿名 Markdown 或 DOCX。 | Workflow gate only; record its state in the handoff. | Return a path-neutral structured handoff; do not claim an evidence bundle or report was written. |
| offline | The explicit no-live-network branch consumes authenticated local material. | Pass no search or browse claim and record the offline branch. | Remain no-live-network, pass no search/browse claim, use only already attached authenticated inputs, and label current/live facts unavailable. |

Capability loss changes coverage only; it never relaxes the linked policy. The linked retrieval playbook is the only session-loop definition; this guide adds no separate step sequence.

When an active QueryPlan pathway has missing, masked, partial, or conflicting policy evidence, pass the typed pathway observation through calculation instead of dropping that pathway. Preserve only real source IDs; a source-free missing observation stays source-free and must not acquire a policy year, target rank, deadline, or qualification conclusion.

## Safety boundary

If the environment cannot run the repository, return the structured handoff instead of calculating in chat. Browser and search tools discover or read public content. Retrieved attachments cross `scripts.downloader`; QR adapters receive decoded text rather than images. External OCR or QR processing requires explicit user authorization and evidence disclosure; the default is local/host-native processing or a missing fact.

Immediately after profile confirmation, create the public `scripts.planning_session.PlanningSessionReplayJournal` in a host-owned private absolute directory and save the confirmed revision with only the profile. Save again after preflight with its capability report and after QueryPlan binding with the canonical plan; before research, `bundle_path` is `None` and `task_outcomes` is empty. After every `ingest`, and again after `finalize_evidence` and `with_calculation`, save the new revision with the current evidence bundle and all completed typed outcomes. After process loss, `status` is diagnostic only: call `journal.load(session_id[, revision])`; resume preflight from `profile_confirmed`, plan construction from `preflight_complete`, the exact next task from `query_plan_ready` or `research_in_progress`, then use the existing finalize/calculate/publish context methods. Never promote a status snapshot or digest into a completed receipt.

Keep search, logs, evidence IDs, and output names free of PII, credentials, private paths, raw local filenames, and student identifiers. Never claim live verification without search and browse, a written file without file output, or calculation without local execution and validation.
The host owns all session JSON, commands and paths. Never ask the user to author or locate them.
