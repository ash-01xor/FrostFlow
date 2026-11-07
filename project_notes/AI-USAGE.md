# AI assistance record

The signed team contract requires a faithful declaration of actual prompts, tools and results. This entry starts that record; team members must review the output, understand it and add subsequent use. Preserve the original session transcript separately if needed for exact prompts.

## 2026-11-01 — repository foundation and P1 scaffold

**Tool:** OpenAI Codex.

**Prompt summary (paraphrase):** Help a beginner team start its cold-chain coursework repository using the existing guide and the sibling EquipmentLoan structure. Write a suitable README, put shared tasks/progress in a tracked subfolder, and begin the first part before owners are assigned.

**Context read:** course project specification, cold-chain proposal, signed team contract, existing implementation guide/integration agreement, EquipmentLoan implementation and documentation, DCR example README, Camunda BPMN skill and upstream documentation. The guides existed before this session; this entry does not assert their authorship.

**AI-produced output:** repository layout, README, progress/requirements/Git notes, P1 BPMN XML, Python HTTP handlers/workers/SQLite journal, Compose packaging, example request, offline tests and live-check script. Codex ran BPMN lint, Compose validation, 10 offline tests, the live P1 engine check and post-restart assertions. The [run guide](../docs/RUNNING.md#automated-verification) records the verification results. No food-safety validation, external API integration or conformance results were produced.

**Human review pending:** inspect the changes, reproduce the real-engine scenario, check assumptions against course expectations, explain the model/worker/message relationship and adapt the implementation as a team. Record the reviewing members and any changes here. AI assistance does not replace each member's required process contribution or understanding.

## 2026-11-01 — documentation cleanup

**Tool:** OpenAI Codex.

**Prompt summary (paraphrase):** Treat this as the official team and submission repository; remove unnecessary files and Markdown, retain useful team context, and polish the README for the project.

**Output:** rewrote the project README, consolidated run instructions and implementation/verification notes into `docs/RUNNING.md`, and removed duplicate documentation, placeholder component READMEs and the machine-specific run snapshot. Retained the existing implementation/integration guides, task board and this declaration record. Application code and process models were unchanged.

## 2026-11-01 — P1 exception flow, P2 handoff and compliance

**Tool:** OpenAI Codex.

**Prompt summary (paraphrase):** Implement the stock-wait/replenishment/cancellation flow, protected P2 dispatch handoff and P1 DCR compliance model discussed separately, with verification and a beginner walkthrough. Clarification limited cancellation to stock waiting, left damaged-packaging handling outside scope, and required actual engine runs, model PDF and log conformance evidence.

**Output:** updated P1 BPMN and its PDF; initial P2 BPMN/PDF; inventory/replenishment/cancellation handlers; durable command/handoff outbox and P2 acknowledgement/inbox; flat DCR XML and local replay/XES tooling; separately labelled invalid trace; offline/live checks; reviewed actual XES execution evidence; updates to the existing guides/README/task board. The original 100-crate dataset and vehicle reservations are retained on registry upgrade; isolated data was used for new scenarios.

**Verification:** 13 offline tests, two BPMN lint checks, model PDF export/render inspection, and real Zeebe normal/cancelled/replenished cases. Actual logs were replayed against the XML; the deliberately invalid trace was rejected. Results are in `compliance/p1/verification.json`. No processdoctor import or P4 DCR-PAIS execution is claimed. The team must review, reproduce, understand and attribute its members' process contributions.

**Review fixes:** Codex addressed unroutable shipment IDs, compliance verification accepting unparsed logs, and links to separately supplied PDFs. Added regression checks for rejected IDs without side effects, supported ID routing, malformed/unsupported evidence and successful saved-log replay. All 17 offline tests, focused Python checks and Compose validation passed; live-engine scenarios were not repeated for these validation/documentation changes.

**Presentation support:** At the team's request for a clear three-minute demonstration, Codex added `tools/demo.py`, an interactive terminal menu using the existing HTTP endpoints, and documented the rehearsal sequence in the existing run guide. A live isolated Zeebe rehearsal checked normal dispatch/P2 acknowledgement, blocked early dispatch, cancellation and replenishment. No new business process or external API was implemented.

**First-commit review:** Codex reviewed code and documentation for teammate handover, preserved later P2 progress on registration retries, and tightened rejection of unsupported compliance models/evidence. Added regression coverage (19 offline tests total) and P2/P3 extension instructions to the existing integration agreement. Verified the 26 candidate files in a clean temporary copy with freshly installed dependencies, new engine data, both BPMN lints, Compose validation, actual P1/P2 scenarios, XES replay, service restart and stack recreation. Refreshed reviewed evidence and source/model hashes. External processdoctor import and teammate model review remain pending. No commit or push was performed.

## 2026-11-01 — five-product dataset

**Tool:** OpenAI Codex.

**Prompt summary (paraphrase):** Replace unnamed stock with five distinct chilled items, adopt product/batch-specific inventory and document/update the implementation consistently.

**Output:** catalogue of milk, yoghurt, fresh cheese, chicken breast and prepared salad (20 crates each, 12 retail packs per crate); persisted product/batch stock, shipment validation and identities, targeted replenishment, product-aware menu controls, updated scenario/tests and XES attributes. Updated the existing guides and task board. Legacy pooled registries are preserved and rejected rather than assigned invented product identities. Existing engine evidence remains historical; no food-safety validation is claimed. The shared CHILLED-DEMO profile has no numeric range yet.

**Verification:** 28 offline tests (including menu selection), Python compilation, catalogue JSON and Compose validation; updated engine run and submission-evidence refresh remain pending. No existing containers, engine volumes or runtime datasets were changed.

## 2026-11-01 — publication readiness review

**Tool:** OpenAI Codex.

**Prompt summary (paraphrase):** Review the official repository before the team creates its GitHub repository, confirm teammate setup and documentation, remove unnecessary material, resolve issues and explain publishing steps.

**Output and checks:** tightened startup instructions with Compose readiness waiting and documented pause/resume, preset demo quantities and feature-branch collaboration. Added a portable Ruff-cache ignore and removed an obsolete pre-stock-check compatibility state. Extended live checks to verify P2 product/batch engine variables and untouched product inventory. Reviewed only the 27 candidate files in a clean temporary copy with fresh Python dependencies: 28 offline tests, both BPMN lints, Compose validation, current five-product engine scenarios, XES/DCR replay and persistence after stack recreation passed. Refreshed reviewed execution evidence and matching source/model hashes. Existing practice datasets were preserved; the temporary review stack was removed. External course-tool import and teammate review remain pending. No commit, remote creation or push was performed.

## 2026-11-01 — ProcessDoctor XML compatibility

**Tool:** OpenAI Codex, local headless Chromium/Puppeteer and the actual ProcessDoctor website.

**Prompt summary (paraphrase):** Resolve the XML import error on processdoctor.compute.dtu.dk before publishing the repository.

**Correction:** inspected the live site's importer and added required portal event metadata, layout coordinates and an empty subprocess resource section to `compliance/p1/rules.xml`. Confirmed the logical events, all 53 relationships and initial marking match the prior model exactly. Application code, BPMN definitions and the real execution log were unchanged. Added a portal-metadata regression test; 29 local tests pass.

**Actual site verification:** in an isolated headless browser, selected Conformance → Open Model → Open DCR Solution XML, imported the corrected graph (nine events/53 relations), uploaded the real three-trace XES log and clicked Check. All three variants were Conforming with zero structural violations. A separately generated XES representation of the existing synthetic cancellation-then-dispatch trace was Partially Violating with one structural violation. Saved both result screenshots and recorded the observed outcome, hashes and metadata-only correction in `compliance/p1/verification.json`. Updated the site URL and exact reproduction steps in the existing docs. No new Zeebe run was claimed for this display-format correction; no commit/push was performed. Human ownership attribution and teammate review remain separate completion tasks.

**Ownership attribution:** at the user's explicit request, recorded Ashvanth as P1's responsible student in the task board, README/guide, BPMN documentation, model PDF and DCR title. Regenerated and inspected the attributed PDF, linted P1, preserved executable/logical semantics and recorded the attribution-only hash changes. Removed duplicate collaboration/review wording; P2–P6 remain unassigned and teammate review remains pending.

**Publication preparation:** at the user's request, removed the student attribution from the P1 BPMN source/PDF, retained project ownership records and the DCR title, and regenerated the PDF. P1 BPMN source now exactly matches the engine-verified hash again. BPMN lint, 29 offline tests and Compose validation passed. Preserved the user's task-board edits and prepared the initial commit for the supplied FrostFlow GitHub remote.
