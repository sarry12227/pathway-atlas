# Generic host capability mapping
- When starting or resuming discovery, extraction, or evidence work, read the [retrieval playbook](../retrieval-playbook.md).
- Before classifying, deduplicating, admitting, or resolving a source conflict, read the [source policy](../source-policy.md).

## Capability map

Use this fallback mapping for an unknown host. Examples are conditional on tools callable in the current session. Inspect them before declaring anything, then apply the linked policy/playbook unchanged.

| Capability | Use | Declare | Absent fallback |
|---|---|---|---|
| search | A callable search API discovers candidate public pages. | Pass --host-capability search only when callable now. | Use explicit user-supplied URLs/local artifacts or offline mode. |
| browse | A callable browser/page reader opens public pages and inspects citations. | Pass --host-capability browse only when callable now. | Without it, do not claim page verification; request a saved public artifact or use offline mode. |
| vision | Callable vision/OCR inspects supplied public images or host-produced OCR and QR text. | Pass --host-capability vision only when callable now. | Seek machine-readable HTML/XLSX/PDF/text, ask for a structured OCR row JSON or decoded QR payload, or mark the fact missing; the normal boundary remains local/host-native. |
| local_exec | A shell/runner executes repository CLIs, adapters, and validators. | Workflow gate only; record its state in the handoff. | Without it, stop before deterministic calculation and ask the user to run the named command or move to a host with local execution; never emulate a result in prose. |
| file_output | A writable workspace creates the evidence workspace and anonymous Markdown or DOCX. | Workflow gate only; record its state in the handoff. | Return a path-neutral structured handoff; do not claim an evidence bundle or report was written. |
| offline | The explicit no-live-network branch consumes authenticated local material. | Pass no search or browse claim and record the offline branch. | Remain no-live-network, pass no search/browse claim, use only user-supplied authenticated local inputs, and label current/live facts unavailable. |

## Preflight

Run the same probe on every host:

```text
python scripts/preflight.py [--host-capability search] [--host-capability browse] [--host-capability vision]
```

Square brackets mean include that flag only when the capability is callable; they are not literal arguments. Machine tiers are `full`, `standard`, `offline`. The runtime probes optional modules `docx`, `openpyxl`, `pdfplumber`. `local_exec` and `file_output` are workflow gates recorded in the handoff, not preflight capability values.

## Ordered handoff

1. Inspect actual tools and run `preflight.py` with only callable search/browse/vision flags.
2. Build or read `QueryPlan`; retain validated `ProvinceConfig.mode`.
3. Execute the linked retrieval playbook task-by-task.
4. Send attachments, text, OCR, or QR payload through the matching adapter and secure downloader boundary.
5. Add candidates and facts with field provenance to `EvidenceStore`, finalize, and require `validate_evidence.py` success before deterministic calculation.
6. Generate anonymous report files only through current public CLIs; disclose every degradation.

Capability loss changes coverage only; it never relaxes the linked policy. Record the fallback whenever a step cannot run.

## Safety boundary

Browser and search tools discover or read public content. Pipeline downloads cross `scripts.downloader`; QR adapters receive decoded text rather than images. Host vision may inspect already supplied public images locally or in-session. An external OCR or QR service requires explicit user authorization and evidence disclosure; the default is local/host-native processing or a missing fact.

Keep search, logs, evidence IDs, and output names free of PII, credentials, private paths, raw local filenames, and student identifiers. Never claim live verification without search and browse, a written file without file output, or calculation without local execution and validation.
