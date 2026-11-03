# OIG Proxy canonical catalog release governing specification draft

<!-- fleet-spec: oig-proxy-catalog-20261101 -->

- Status: PREPARATION DRAFT FOR AUTHORIZED FLEET CRITIQUE; implementation REQs remain unimported; no implementation or release claim.
- Authority: Approach B, one canonical typed parameter catalog, approved for preparation only; `.omx/plans/release-preparation-authority.json` controls the current boundary.
- Current operator authority permits Fleet critique and iterative preparation-document corrections only, including isolated immutable review-input transfer. Do not import implementation REQs, dispatch implementation, integrate code, run product checks or perform hardware/publication actions. This specification itself grants no additional authority.
- No product code, schema, tests, HA configuration or running service is changed by this specification. Only isolated document-snapshot commits/input transfer serve review identity; no product commit, GitHub PR/push, HA access, deployment or publication is authorized here.
- Every test, command and scenario below is PLANNED, NOT IMPLEMENTED and NOT RUN for this release. Proposed `tests/v2/test_*.py` files and test nodes are future deliverables, not assertions that those tests exist. Commands are acceptance definitions, not instructions to execute during preparation.
- The last verified own-HA private image was `3558`; this preparation stage has not checked or changed its current runtime state. Existing uncommitted prototype changes are evidence to audit, never a candidate to promote wholesale.
- Local product reference HEAD and read-only GitHub main snapshot for `Muriel2Horak/oig-proxy`: `ef71e916ca9d646a6495760d3e1dc6ce034f0ec4`; provenance in `.omx/research/release-source-pins.json`. This snapshot is reference only; later current execution base, server object availability, target server repository path and deployable candidate remain unverified.
- Fleet format reference: `.omx/research/fleet-req-ac-format.md`; pinned GitHub main `adb59c89978b88a5dceb90f6350af063abb181b1`, timestamp `2026-11-01T17:50:09Z`, 19 manifest-verified sources under `.omx/research/fleet-contract-sources/adb59c8/`. Governing grammar: pinned `scripts/fleet/fleet-req:72-316`. Deployed maintenance version, switches, record schema and imported IDs remain unknown.
- These syntactically proposed IDs have not been imported. Parser success cannot establish server admission, deployed-contract compatibility, test success or permission to execute. Do not describe this draft as server-admission-ready.
- Operator clarification is resolved: MNP was a typo for build verification and tests; no unidentified MNP tool is pending.

## Supporting preparation contracts

- `.omx/plans/oig-release-ownership-contract.md`: C00, W02-RI, exact serialized file handoffs, independent oracle authors and final-pass ownership.
- `.omx/plans/oig-release-qualification-scope-contract.md`: Q01, separate bounded evidence experiments, explicit reduced-scope records and candidate re-attribution.
- `.omx/plans/oig-release-start-graph.json`: sole authoritative proposed start graph; coarse W01-W12 package prerequisites are reporting summaries only. Missing authority, artifact/handoff or unexpanded setting/cohort branch blocks dispatch.
- `.omx/plans/oig-release-legacy-test-contract.md` and `oig-release-legacy-test-inventory.json`: exact-base existing test/support population, per-file owners and real collection reconciliation.
- `.omx/plans/oig-release-applicability-contract.md`: immutable canonical population, actual scope-bound applicability and source-aware amendment/reuse rules.
- `.omx/plans/oig-release-acceptance-cases.proposal.md`: required negative-case families for the versioned W10 acceptance manifest. All clauses remain preparation proposals, not action grants.

## Sources and evidence rules

- S1: `.omx/research/summary.md`; selected catalog architecture and accepted entity/state contract.
- S2: `.omx/plans/brownfield-map.md`; affected runtime boundaries, current/private limitations and exclusions.
- S3: `reports/release-design/sensor-inventory-findings.md` and `reports/release-design/sensor-inventory-initial.json`; complete initial inventory, workbook cells, counts, source hashes, unresolved meanings and observed-only facts.
- S4: `reports/release-design/entity-contract-findings.md`; identity formulas, discovery retry/tombstone issues, state versus intent, validation, lifecycle and native HA help limits.
- S5: `reports/release-design/protocol-release-findings.md`; profile limits, setting qualification, seven stream/session regressions, anti-replay, automation barriers and prerelease boundary.
- Preserve source kind, path, immutable hash/revision, sheet/cell or code location, product/firmware/layout applicability and confidence. Distinguish `vendor_documented`, `project_validated`, `observed_only`, `encoding_limit`, `project_policy`, `not_applicable` and `unknown`.
- Historical private evidence may inform review but must not enter public artifacts as raw captures, real serial numbers, secrets or unredacted identifiers. Use parameterized `<BOX_ID>`, `<NAMESPACE>` and synthetic devices in examples and tests.
- Acceptance evidence must identify candidate revision/image digest, REQ ID, canonical AC name, exact command/scenario, environment/tool versions, actual result, raw output/artifact reference and material limits. Preserve FAIL, NOT RUN and UNVERIFIABLE; never replace missing evidence with a green aggregate.
- Command expectations below are exit codes. Scenario `pass` means all stated observable assertions and retained evidence exist; it is not a human assertion without a record. Generated test cases alone cannot prove the generating catalog's meaning.

## Dependency and unresolved acceptance boundary

- Order: REQ 1 freezes evidence; REQ 2 establishes the catalog; REQ 3 and REQ 16 qualify semantics/control permissions; REQ 4 freezes identities before REQ 5 discovery migration.
- REQ 6-15 establish confirmed-state, validation, transaction, framing, replay and profile boundaries before REQ 20-22 executable examples/E2E.
- REQ 18-21 consume reviewed contracts and Q01-qualified recipe inputs. Explicit phase order: REQ 23 quality plus REQ 24 pre-rc-independent-ac-verdict and security ACs; REQ 25 own-HA RC; REQ 26 post-rc-independent-evidence-verdict; REQ 26 publication. Future phase ACs remain NOT RUN at earlier gates; no single AC covers both sides of the RC.
- Agreed first prerelease scope: own HA and own BOX, with individually qualified MODE directions and the AAC `100 A` example; preserve the operator's retained-test-image preference. This scope agreement does not authorize execution during preparation or waive device/setting qualification.
- Engineering work: derive evidence-backed profile thresholds/identifiability/freshness proposals, known-device offline evidence routes, confirmation-source policy, independent CI/security/build command selection and suggested observation/repeat counts. Review these concrete proposals against held-out evidence and the accepted contract; do not ask the operator to invent CRC thresholds or delay authorized research while engineering inputs remain unresolved.
- Operator scope decisions before affected hardware/migration/publication actions: exact allowed live settings/values and competing-planner boundaries, observation/repeat acceptance, any entity/unit/history migration exception, any reduced release/GRID deferral, activation/runtime-health recovery or explicit rollback scope, and the separate execution/publication permissions already required by authority.
- Keep unresolved product semantics, bounds and GRID behavior blocked for WRITES while preserving truthful RO/raw/unknown observations. Safety exclusions and explanatory drafts are valid preparation outputs; they do not deliver the requested functional GRID automation. Its acceptance remains NOT DELIVERED until qualified and tested, or explicitly deferred through an operator-accepted reduced release scope with truthful AC applicability/status.

## REQ 1 - Account for every source identity and candidate

- Priority rationale: completeness precedes new discovery or permissions.
- Sources: S1-S3. Dependencies: none.
- Freeze map/workbook/policy/evidence hashes and reconcile exactly 430 map records, 22 warning definitions, 335 distinct Databaze identities across 362 physical rows, 95 Databaze-only and 190 map-only identities. Preserve duplicate product rows rather than collapsing their semantics.
- Account separately for all 190 initial map-only identities: 65 IsNew aliases, 54 map extensions/aliases requiring provenance, 37 proxy/twin synthetic records, 33 actual aggregates and 1 RTU identity outside Databaze. Also account for conditional battery banks, message/FSP references, dynamic dimensions and observed-only candidates including `tbl_batt:ID_SubD`; these are separate populations and must not be double-counted. Initial JSON contains 526 union records and 23 FSP references; record justified deltas rather than freezing those as a universal future count.
- Assign every candidate an explicit supported/informational/envelope/administrative/alias/reserved/unsupported/unknown disposition with reason, source, applicability, owner and control decision. Write-marked vendor access alone does not enable a control.
- Planned evidence: source-accounting matrix, duplicate/variant reconciliation, zero unexplained omission/delta report and per-disposition review.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-1
priority: P0
acceptance:
  ### unit
  - name: complete-source-accounting
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_inventory.py::test_all_map_warnings_vendor_rows_and_candidates_accounted
    expect: 0
  ### integration
  - name: reproducible-source-provenance
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_inventory.py::test_frozen_sources_reconcile_without_unexplained_deltas
    expect: 0
  ### scenario
  - name: no-write-from-vendor-access
    scenario: Review the disposition matrix against frozen sources; every candidate has a reason and product/firmware provenance, duplicates remain distinguishable, and an unsupported or merely write-marked vendor identity produces no enabled command.
    expect: pass
```

## REQ 2 - Make one typed catalog authoritative

- Sources: S1-S4. Dependencies: REQ 1.
- Extend existing metadata into one versioned typed catalog; derive discovery, admission constraints, help, Czech reference and test-matrix inputs deterministically from reviewed records. Retain compatibility adapters for existing loader/import contracts including `CONTROL_WRITE_WHITELIST` and `SETTING_CONSTRAINTS`; no second manually authoritative permission list.
- Represent exact table/key, stable identity/component overrides, device applicability, raw/state/wire types, scaling/unit, telemetry domain, unknown/sentinel codes, separate command domain, bounds/step origin/precision/enums, dependencies/action semantics, profiles, source confidence and unknown reason.
- Reject invalid versions, duplicate incompatible identities, writable records without complete constraints/provenance and contradictory adapter output. Preserve runtime operation without GitHub, documentation generation or network lookup.
- C00 is owned by the release architecture coordinator and independently reviewed after W01, before W02-I; freeze the exact schema/adapter/state-control artifacts and source/definition hashes defined in `.omx/plans/oig-release-ownership-contract.md`. W02-I consumes this contract; it cannot invent its own required input.
- Canonical logical identity is the exact case-preserved protocol pair `(table, key)`; preserve existing lookup strings `table:key` and existing alias mappings. Do not identify a record by its translated name or a live BOX serial.
- Retain product/firmware/layout variants beneath that logical identity as explicit stable `variant_id` records with applicability selectors and provenance. Duplicate vendor rows remain distinguishable source rows; they do not create duplicate HA identities. Context resolution selects exactly one qualified variant or returns an explicit unresolved/ambiguous result; no arbitrary first/last match or generic write fallback.
- Separate `schema_version` (reader compatibility), `catalog_revision` (immutable reviewed content) and qualified profile/evidence generation. Unsupported schema versions fail closed for controls. A semantic or permission change changes the catalog revision and its generated-artifact manifest; it does not silently change existing HA IDs.
- Freeze adapter shapes and examples in C00: loader payload remains `{"sensors": {"table:key": metadata}}`, loader lookup/iteration signatures remain compatible; `CONTROL_WRITE_WHITELIST` remains `dict[str, set[str]]`; `SETTING_CONSTRAINTS` remains `dict[tuple[str, str], SettingConstraint]` with preserved import names and numeric-field meanings. These generated adapters are compatibility outputs of the canonical catalog, never independent permission authorities.
- In the whitelist compatibility adapter, membership means a released `_cfg` identity exists and must be retained, including a currently blocked/demoted control, or a new identity is qualified for creation in at least one declared release context. Membership is not current write permission. Do not drop an existing identity because its command domain becomes unknown, empty or narrower. The global key union does not authorize creating that identity for unrelated BOX/product/firmware contexts.
- For the actual target context, discovery existence, MQTT component, stable object/unique IDs and state/command topics come from the frozen W04 identity manifest plus its explicit approved migration records. New identity creation additionally requires qualified applicability. Never derive a switch/number/select component from `SETTING_CONSTRAINTS` bounds, whitelist membership, translated label or a newly observed value. An unknown/missing identity resolution blocks the affected discovery migration instead of guessing a component.
- `SETTING_CONSTRAINTS` contains only catalog-derived compatibility/display metadata that the unbound legacy shape can represent truthfully. Its min/max/step fields retain their numeric meanings, but they are neither a universal device command domain nor a component-selection rule. C00 records provenance and applicability separately. If variants have no unambiguous unbound numeric/display representation, return an explicit unresolved adapter result through the documented companion resolution API; do not invent a union/intersection, copy an arbitrary variant or substitute `0..1`. Missing/unresolved legacy metadata cannot remove a released entity or select its component.
- Freeze two distinct context-resolved results in C00: `resolve_discovery(context, table, key)` returns the frozen identity/component, confirmed-state codec, display-compatible current UI domain, permission status/reason and representation result; `resolve_command(context, table, key, value)` returns the authoritative qualified command domain/preconditions and explicit allow/reject result. Both derive from the same catalog revision and identity/migration evidence. Resolve exactly one applicable variant or fail closed with a reason.
- Enumerate and migrate every current consumer of both legacy containers. Discovery must use `resolve_discovery`; any enqueue/serialization/pre-send path must use `resolve_command`. No consumer may infer write authorization from whitelist membership, a legacy `SettingConstraint`, discovery publication or a visible `_cfg`. Reject unbound/unknown/ambiguous command contexts before queueing and before send. Preserve compatibility import shapes without preserving an unsafe implicit authorization shortcut.
- C00 freezes adapter/state-control binding field names, types and resolution/validation schema, including discovery_identity_manifest_sha256, semantic_state_codec_contract_sha256, command_context_schema_sha256 and binding_addendum_sha256. It does not bind nonexistent W03/W04 artifacts or future runtime implementation hashes; pending bindings cannot authorize activation.
- C00-LATE-BINDINGS later supplies a versioned reviewed contract-freeze addendum with actual W03 semantic-codec contract and W04 identity/representation hashes, affected scopes, parent freeze hash, superseded revision, provenance/review and explicit pending/blocked records. Codec hashes describe semantic conversion contracts, not not-yet-written runtime modules. Complete accounting permits guarded authoring; only resolved matching scope bindings permit activation. Later semantic changes reopen affected representation decisions through the source-aware amendment process.
- Freeze versioned state/control envelopes and the callback message-metadata shape in `state-control-contract-v1.json`: retained/DUP/QoS fields, exact target identity, request/generation/source identities, confirmation source/freshness, timestamps, compatibility mapping and unknown-field handling. C00 specifies interfaces; later W05-W08 implement and verify them.
- Planned evidence: typed validation failures, deterministic generation diff and existing-import compatibility results.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-2
priority: P0
acceptance:
  ### unit
  - name: typed-catalog-rejects-invalid-definitions
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_schema.py::test_invalid_versions_duplicates_and_incomplete_writes_fail_closed
    expect: 0
  ### integration
  - name: canonical-consumers-and-adapters
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_generation.py::test_discovery_validation_help_docs_and_legacy_adapters_share_authority
    expect: 0
  ### scenario
  - name: deterministic-offline-generation
    scenario: Generate all catalog-derived artifacts twice from one reviewed revision without network access; bytes match, drift checking detects an edited generated artifact, and runtime startup uses the catalog without a generator or remote service.
    expect: pass
```

## REQ 3 - Qualify Czech meanings units and value domains

- Sources: S3-S4. Dependencies: REQ 1-2.
- Give every public record a meaningful Czech name, purpose, expected effect, unit/scaling, raw/decoded values and applicability. Correct copied inverter wording for peripherals and the Lat latency/latitude conflict from evidence. Decode all 22 warnings through the real loader/processor path, not catalog-presence checks.
- Resolve or explicitly mark conflicts for BAT_FORMAT observed `18`, FMT_ON observed `2`, boiler W versus Wh, BAT_AA W/Wh, annual kWh/MWh, product-specific FV_V2/FV_I2/FV_P2 and schedule/WD encoding. Until qualified, retain truthful raw/unknown RO information and block affected writes; do not silently map unknown codes to off or coerce them into a known enum.
- Separate measurement/encoding domains from allowed commands. Do not invent GRID `0/1`, AAC `0..125`, `999 A` operating limits, BOX MODE `4/5` support or a charging electrical reference side from names/storage width/private observations.
- Runtime/test/consumer ownership and final-pass prerequisites follow `.omx/plans/oig-release-ownership-contract.md` and the expanded AC matrix. W02-RI integrates reviewed catalog/permission deltas; W05-M owns message-metadata ingress before W07; W08-C owns server/main confirmation hooks after W06/W07. A leaf requests the named owner/handoff rather than silently broadening paths.
- Planned evidence: per-record semantic review and explicit uncertainty/permission report; independently authored conflict fixtures.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-3
priority: P0
acceptance:
  ### unit
  - name: preserve-unknown-values-and-conflicts
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_semantics.py::test_bat_format_fmt_on_units_variants_and_unknown_codes_remain_truthful
    expect: 0
  ### integration
  - name: warnings-and-czech-domain-attribution
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_semantics.py::test_real_loader_decodes_all_warnings_and_correct_device_labels
    expect: 0
  ### scenario
  - name: evidence-backed-semantic-review
    scenario: Review all 430 records and warning explanations against provenance; every public meaning/value/unit is qualified or visibly unknown, unresolved control conflicts remain disabled, and no observed value or encoding bound becomes a permitted range without independent qualification.
    expect: pass
```

## REQ 4 - Preserve entity identities and public value contracts

- Sources: S2-S4. Dependencies: REQ 1-3 before discovery changes.
- Freeze unique IDs, actual entity-registry IDs in authorized fixtures, components, device grouping/case, object suggestions, namespace, command/state topics and raw/public value formats. Preserve customized `entity_id`, existing `unique_id`, add-on slug and statistics identity while changing visible Czech labels/help.
- Test MODE, PROXY_MODE, one/multiple battery banks, aggregates/mirrors, IsNew normalization, custom namespaces and historical retained discovery. Do not infer actual registry IDs from default suggestions or create duplicate `_2` entities.
- Any required component, unit, state-class/history or public-format correction needs a per-entity reviewed migration exception and operator acceptance before execution. Do not silently convert SA/RQRESET switches to buttons, move devices or delete/recreate registry entries.
- Treat any restriction/removal of an already released write permission as a behavior migration, including smaller value domains, narrower firmware/profile applicability and unqualified prior MODE directions. W04 owns a per-entity permission-change ledger; W08 consumes it for discovery/state behavior and W12 consumes it for Czech release notes. Each record contains stable entity/unique IDs and topics, affected cohort, prior permission/domain, proposed permission/domain, evidence/reason, blocked-write explanation, retained-state behavior, recovery/rollback compatibility, operator decision/reference and candidate revision. Approval applies before the affected own-HA migration test and before broader release; this draft approves no demotion.
- Retain an existing`_cfg` entity's identity, component, state/command topics and truthful confirmed-state contract when its write permission is demoted. When the frozen component can represent the confirmed value losslessly, keep the entity visibly write-blocked with the companion permission reason/help, reject scalar/envelope writes before queueing and before send, and preserve/add its RO counterpart. Do not tombstone/delete/recreate it, publish a fabricated replacement value or infer a component from the new command-domain shape. Requalification re-enables the same identity/component only if the confirmed-state codec and permitted UI command domain remain representable under that frozen component and all migration conditions are satisfied. Otherwise require an explicit per-entity reviewed and operator-approved migration/contract exception before affected activation; this draft approves none. Never create a new `_cfg` for a never-released unqualified setting.

- W04 owns a representation-compatibility decision for every released `_cfg`, recorded in the identity/migration ledger and consumed by W08-D and the release gate. Prove that the frozen component/state codec can represent each qualified confirmed value and allowed command value with the agreed unit/scaling and without collapsing distinct values. A non-binary confirmed BAT_FORMAT `18` or FMT_ON `2` cannot be coerced to switch `on`, `off`, `0`, `1`, the last requested value, or a made-up bounded value.
- If a known confirmed domain/value cannot be represented by the frozen `_cfg` component, mark that identity `representation_mismatch` and block the affected candidate/cohort activation and dependent recipes. During preparation and isolated diagnostics retain truthful raw RO/status evidence and the exact prior identity; do not claim that `_cfg=unknown` plus RO raw `18` satisfies the paired confirmed-state contract. A safe blocked diagnostic case may pass its guard test while the affected functional pair acceptance remains NOT DELIVERED. Only an explicit per-entity operator-approved migration or contract exception can change that obligation; no exception is accepted now.
- The exception record identifies old/new component and IDs, affected cohorts, exact unrepresentable values/codec, proposed visible behavior, state/history/statistics consequences, command-domain consequences, rollback/registry behavior, independent review and operator decision reference. Re-run the affected native-HA and identity ACs on the resulting immutable candidate. Never silently turn a switch into a number or broaden a number/select domain through regenerated constraints.
- An unexpected later observation outside the qualified component codec is a visible representation fault, not confirmation success: stop affected writes/recipes, preserve identity and truthful raw evidence, and retain the unresolved fault for approved recovery/requalification. It cannot be reported as continued fulfillment of both-entities-confirmed-state acceptance.
- A blocked retained command topic is not a write permission: its route exists solely to provide a precise correlated rejection and preserve compatibility. For unqualified/unknown values, retain the existing public value representation and truthful raw/unknown RO evidence; do not invent a smaller legal command domain to make discovery appear valid.
- Runtime/test/consumer ownership and final-pass prerequisites follow `.omx/plans/oig-release-ownership-contract.md` and the expanded AC matrix. W02-RI integrates reviewed catalog/permission deltas; W05-M owns message-metadata ingress before W07; W08-C owns server/main confirmation hooks after W06/W07. A leaf requests the named owner/handoff rather than silently broadening paths.
- Planned evidence: before/after identity manifest, isolated HA registry migration output, explicit exception ledger and rollback compatibility proof.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-4
priority: P0
acceptance:
  ### unit
  - name: stable-identity-and-value-snapshots
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_identity.py::test_labels_help_and_catalog_adapters_preserve_ids_topics_components_and_values
    expect: 0
  ### integration
  - name: retained-registry-migration
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_identity.py::test_custom_registry_names_variants_and_retained_configs_migrate_without_duplicates
    expect: 0
  ### scenario
  - name: reviewed-migration-exceptions
    scenario: Compare immutable before/after identity manifests and isolated HA registry evidence for the actual candidate activation set; require every existing identity to have a compatible confirmed-state representation or an explicit per-entity operator-approved migration/contract exception revalidated on this candidate, preserve reviewed IDs/components/topics/public formats, require approved permission-restriction records, prove representable demoted cfg entities retain the same confirmed value and blocked reason with zero queued/wire writes, and prove compatible requalification and approved rollback preserve identity without replay; separate incompatible BAT_FORMAT/FMT_ON negative fixtures must block activation, while any unresolved actual-candidate representation mismatch prevents this scenario from passing.
    expect: pass
```

## REQ 5 - Publish a read-only and writable pair for permitted settings

- Sources: S1-S4. Dependencies: REQ 2-4, REQ 6 and qualified permissions from REQ 16.
- Publish one base RO plus one separate writable `_cfg` entity for every release-permitted setting, including proxy-local configuration. Both display the same confirmed value and `_cfg` remains non-optimistic; RO-only records have no command topic.
- Preserve existing number/switch/select choices unless REQ 4 approves an exception. Existing 29 private permissions are audit input, not an instruction to enable all 29 publicly.
- Track RO/control publication success independently; retry a failed sibling, correct PROXY_MODE RO tombstoning, restore discovery after reconnect and never tombstone an active approved entity. Discovery payloads explicitly set command `retain=false`; discovery configuration itself may remain retained under the preserved contract.
- Discovery distinguishes qualified writable controls from retained previously released controls whose writes are now blocked under the approved REQ 4 ledger. Both retain their confirmed-state source; blocked controls expose the permission reason through supported native status/help surfaces and keep the exact identity. Tombstone rules cannot remove a demoted released control. Broker restart, reconnect, downgrade/requalification and partial sibling publication must preserve this distinction.
- Runtime/test/consumer ownership and final-pass prerequisites follow `.omx/plans/oig-release-ownership-contract.md` and the expanded AC matrix. W02-RI integrates reviewed catalog/permission deltas; W05-M owns message-metadata ingress before W07; W08-C owns server/main confirmation hooks after W06/W07. A leaf requests the named owner/handoff rather than silently broadening paths.
- W08-D must decouple discovery identity/component from mutable permissions and legacy constraint shapes. Consume the frozen W04 identity manifest through the C00 discovery resolver, including released blocked identities. Changing a command permission, disabling controls or narrowing a qualified profile cannot suppress or re-type an existing `_cfg`; a new qualified identity is created only in its applicable context.
- Before affected discovery activation, validate each RO/`_cfg` confirmed-state codec and native component against the actual qualified value domain and any known observations. An unrepresentable confirmed value blocks the affected activation until the REQ 4 exception gate is satisfied. Raw diagnostic visibility does not substitute for fulfillment of the paired-state contract.
- Retained discovery, reconnect, partial sibling retries, downgrade and requalification preserve the frozen component/IDs/topics and the current representation decision. Do not emit a component-changing retained payload from a changed min/max/step tuple.
- Planned evidence: permission-to-discovery matrix, retained broker payloads and partial-failure/reconnect transcripts.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-5
priority: P0
acceptance:
  ### unit
  - name: complete-confirmed-state-pairs
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_discovery.py::test_every_permitted_setting_has_ro_cfg_and_readonly_has_no_command
    expect: 0
  ### integration
  - name: independent-discovery-retry
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_discovery.py::test_partial_publish_reconnect_and_proxy_ro_tombstone_preserve_pairs
    expect: 0
  ### e2e
  - name: ha-pairs-use-confirmed-state
    scenario: In an isolated HA and broker fixture validate every identity in the actual candidate activation manifest, with no unapproved representation mismatch: each required RO/cfg pair shows the same confirmed value during pending requests and permission changes, blocked cfg controls retain reviewed component/IDs/topics and precise rejection reasons without Setting bytes, adapter whitelist/constraint changes cannot remove or re-type them, and reconnect/requalification creates no duplicate identities or replay; separately inject unrepresentable switch values 18 and 2 to prove the activation guard blocks them without coercion; guard-test success cannot substitute for any actual pair's functional result, and any actual candidate mismatch without an explicitly approved and revalidated per-entity exception keeps this scenario unaccepted.
    expect: pass
```

## REQ 6 - Publish BOX-confirmed state without optimistic ACK echo

- Sources: S1-S5. Dependencies: REQ 2, REQ 10 and REQ 16.
- Receipt, enqueue, wire send, MQTT QoS ACK, transport ACK and valueless BOX application ACK must never publish the requested value to ordinary state or mark it applied. Apply the same rule to local and cloud-origin paths; ACK-only timeout cannot upgrade weak evidence to confirmation.
- Accept qualified BOX table/event observations with exact device/key/value/source/freshness; keep physical execution/readback claims distinct. For command completion, the qualified profile defines correlated application ACK plus matching authoritative BOX event OR fresh authoritative readback, retaining `confirmation_source`; a qualified event need not wait for an infrequent table refresh. Natural BOX observations can update truthful public state independently of a local request. Update RO and `_cfg` with a real mismatching observation while leaving the requested command unconfirmed/rejected/uncertain as appropriate. Partial table merges retain per-key provenance/freshness; actual-table mirrors preserve source identity.
- Require fresh table readback only for an explicit fresh-readback claim or a profile that needs it; physical-current claims require independent measurement. A qualified event can establish the confirmed setting without proving physical current.
- Proxy-only configuration is the documented exception: confirm only after successful local-runtime application with `confirmation_source=local_runtime`; never claim a BOX ACK.
- Freeze a completion anchor when the final byte of a local Setting is accepted by the transport write (not a later asynchronous drain/flush return): proxy boot, exact BOX connection/session, profile generation, monotonic send-completion time and receive watermark. Retain the first-byte receive sequence/time of each candidate BOX frame. A command-completing event/readback must start strictly after the send anchor on the same eligible BOX session/profile; a frame begun before the anchor, a buffered prior frame, cached state, a mirror or a replay cannot complete the request. Profile-qualified device timestamps/data versions must also be consistent with the anchor; clocks without a qualified mapping are not an independent freshness proof.
- Post-send arrival is necessary but not by itself sufficient for a fresh-readback claim. The profile must establish a post-send BOX snapshot through qualified device timestamp/data-version or readback-request/response provenance. Otherwise label the observation `receipt_only`; it can remain truthful observed-state evidence but cannot satisfy a command-completion or fresh-readback barrier. Retain the exact evidence and anchor in lifecycle status.
- A profile may accept a matching authoritative change event plus the correlated application ACK without waiting for sparse table refresh. Require event-after-ACK ordering only where that profile's proven protocol requires it; do not reject a valid post-send event that precedes its delayed ACK when the profile permits that order.
- A fresh explicit equal-value request remains a distinct request and is not deduplicated by value. If it is transmitted and no qualified change event occurs, confirm it only from the profile-qualified post-send fresh readback; ACK plus an old equal-valued observation is insufficient. At its bounded deadline, retain the confirmed observed value and terminate the unproven request as `uncertain` with `equal_value_without_fresh_confirmation`; do not fabricate a change event or resend it automatically.
- Report observed value and local-write attribution separately. A matching event lacking a request identity is eligible for local completion only under the qualified profile's discriminating signature and ordering/exclusion rules. An observed overlapping same-key cloud/planner/LCD write, conflicting source evidence, or an indistinguishable same-value event updates truthful confirmed state but sets local attribution `ambiguous`; it cannot mark the local request applied or release a dependent local-request barrier. Keep `confirmation_source` and `attribution` explicit; never claim proof of physical current or exclusive local causation from state equality.
- Runtime/test/consumer ownership and final-pass prerequisites follow `.omx/plans/oig-release-ownership-contract.md` and the expanded AC matrix. W02-RI integrates reviewed catalog/permission deltas; W05-M owns message-metadata ingress before W07; W08-C owns server/main confirmation hooks after W06/W07. A leaf requests the named owner/handoff rather than silently broadening paths.
- Existing tests/support files follow `.omx/plans/oig-release-legacy-test-contract.md` and exact-base `.omx/plans/oig-release-legacy-test-inventory.json`: 37 Python modules, 31 test modules and 461 static test symbols, not collected/executed counts. W10-LEGACY has explicit per-file ownership/review; L00 freezes baseline/transition/handoff policy before H00 and affected legacy work, L01 reconciles real base/candidate collection and every reviewed semantic update before full-suite final acceptance. Retained legacy cases count for guard/coverage but do not prove a new canonical AC by default; fake legacy labels, unexplained removals/parameter loss or stale module/fixture hashes are rejected.
- Preserve genuine tbl_events and confirmed-callback routing positives with qualified source/profile/freshness context. Known, non-exhaustive legacy contradictions include ACK-only requested-value echo, ACK-only audit terminal confirmation and timeout-to-confirmed tests; W10-LEGACY updates those with W08-C/W06-T review, retaining genuine-event positives and no-echo/uncertainty adversarial controls. Do not delete a valid event test merely because its callback also had an invalid weak-ACK caller.
- Planned evidence: state-topic transcripts, independent BOX event/readback fixtures, mismatch/timeout assertions and local application-failure cases.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-6
priority: P0
acceptance:
  ### unit
  - name: weak-ack-never-fabricates-state
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_confirmed_state_contract.py::test_request_send_ack_and_ack_timeout_never_echo_queued_value
    expect: 0
  ### integration
  - name: observed-values-and-local-exception
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_confirmed_state_contract.py::test_ack_event_or_readback_completion_mismatch_partial_merge_mirror_and_local_sources
    expect: 0
  ### e2e
  - name: confirmed-state-evidence-boundary
    scenario: Submit qualified different-value and equal-value requests through every supported ingress; valueless ACKs, cached equal state, pre-send frames and delayed stale readbacks cannot complete them; profile-qualified post-send ACK plus matching event completes without sparse-table refresh, qualified fresh readback completes a no-change request, missing fresh evidence terminates uncertain without replay, and a competing indistinguishable same-value writer updates both public entities while local attribution remains ambiguous and dependent writes stay blocked.
    expect: pass
```

## REQ 7 - Expose per-setting requested intent lifecycle and truthful diagnostics

- Sources: S2-S5. Dependencies: REQ 2, REQ 6 and REQ 10-13. These dependencies are one-way: REQ 13 requires the lifecycle contract from the catalog, not this completed diagnostic implementation.
- Publish separate per-device/per-setting status with version, boot/request/audit/generation identity, actual wire IDs/session, requested and observed values, source/freshness, creation/expiry/transition times, reason and supersession. Proposed stable route: `<NAMESPACE>/<TARGET_DEVICE_ID>/settings/<table>/<key>/status`; freeze the exact route/schema with the MQTT contract before implementation. Keep pending intent outside confirmed public value topics.
- Freeze documented machine states covering requested, queued, sent, acknowledged, applied, rejected, expired, superseded, interrupted and uncertain outcomes. Publish only evidence-backed transitions; no terminal applied result from queue length zero.
- Status must operate when optional telemetry/audit storage is disabled and have a bounded retained-state/restart contract. Derive cloud connection status from cloud signals, BOX-data freshness from measurements and queue/inflight diagnostics from actual state; implement advertised publishers before documenting them.
- Include immutable `send_anchor`, observation first-byte receive time/sequence, snapshot timestamp/data-version provenance, `attribution` and the bounded uncertainty reason. `applied` means the qualified request-completion contract was satisfied; public state can independently change while that request remains uncertain.
- Planned evidence: transition matrix, telemetry-disabled output, stale/partial-state diagnostics and negative physical-completion assertions.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-7
priority: P1
acceptance:
  ### unit
  - name: lifecycle-evidence-and-reasons
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_setting_lifecycle.py::test_each_transition_has_request_evidence_timestamps_and_terminal_reason
    expect: 0
  ### integration
  - name: diagnostics-independent-of-telemetry
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_setting_lifecycle.py::test_status_sources_publish_with_telemetry_disabled_and_stale_state
    expect: 0
  ### scenario
  - name: operator-sees-state-and-intent
    scenario: Inspect success, validation rejection, NACK, replacement, timeout, restart and uncertainty transcripts; each setting exposes confirmed value separately from requested value and correlated lifecycle, and cloud/BOX connectivity or freshness labels match their real signal sources.
    expect: pass
```

## REQ 8 - Enforce identical strict command validation on every path

- Sources: S2-S5. Dependencies: REQ 2-3 and REQ 16.
- Enforce exact type/aliases, explicit finite checks, min/max, exact step and origin, precision, enum membership, applicability, capabilities, companion constraints, effective permission and expiry for HA services, canonical JSON MQTT, scalar compatibility MQTT and delivery. Reject unknown/RO/missing-constraint definitions before enqueue and before wire serialization.
- Restrict Boolean aliases to declared binary controls. Use decimal/rational alignment rather than permissive float tolerances. Resolve zone terminal-second/sentinel and WD encoding contradictions from evidence; do not infer operational ranges from U16.
- Intersect catalog bounds with qualified device/profile capabilities and revalidate dependent mode/profile/limits immediately before send. A changed prerequisite terminally rejects the queued command with a reason; it does not silently normalize to another command.
- Runtime/test/consumer ownership and final-pass prerequisites follow `.omx/plans/oig-release-ownership-contract.md` and the expanded AC matrix. W02-RI integrates reviewed catalog/permission deltas; W05-M owns message-metadata ingress before W07; W08-C owns server/main confirmation hooks after W06/W07. A leaf requests the named owner/handoff rather than silently broadening paths.
- Planned evidence: independent boundary matrices including neighbors, NaN/infinity, booleans, fractions, malformed input, wrong profiles, unknown enums and changed prerequisites; zero wire bytes on rejection.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-8
priority: P0
acceptance:
  ### unit
  - name: strict-types-bounds-step-and-enums
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_validation.py::test_independent_boundary_type_finite_precision_step_enum_and_missing_constraint_matrix
    expect: 0
  ### integration
  - name: all-ingress-and-delivery-validation
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_validation.py::test_ha_json_scalar_and_presend_checks_reject_without_wire_bytes
    expect: 0
  ### scenario
  - name: no-ui-validation-bypass
    scenario: Bypass HA UI limits with direct MQTT and alter profile/mode/companion state while queued; the server rejects every disallowed value consistently, publishes a precise correlated reason, and sends no invalid Setting frame.
    expect: pass
```

## REQ 9 - Bind MQTT envelopes and frames to the target device

- Sources: S2, S4-S5. Dependencies: REQ 2, REQ 8.
- Freeze the authoritative versioned MQTT contract. Preserve currently supported `oig/<BOX_ID>/control/set` JSON and `<NAMESPACE>/<BOX_ID>/set/<table>/<key>` scalar routes through validated adapters unless a specific approved compatibility migration replaces one. Do not reactivate obsolete V1 routes or unsupported string/SSID/T_Room commands.
- Validate exact path length, namespace, topic device, payload target, table/key and enrolled session/transport/XML identities. Scope queues/status by device; proxy-local controls target the proxy identity explicitly.
- Carry retain/DUP/QoS/message metadata into admission and define request-ID, issue/expiry and boot/session behavior. Unknown/mismatching/ambiguous targets fail closed for local commands while ordinary safe forwarding/RO remains available.
- Runtime/test/consumer ownership and final-pass prerequisites follow `.omx/plans/oig-release-ownership-contract.md` and the expanded AC matrix. W02-RI integrates reviewed catalog/permission deltas; W05-M owns message-metadata ingress before W07; W08-C owns server/main confirmation hooks after W06/W07. A leaf requests the named owner/handoff rather than silently broadening paths.
- Planned evidence: both-route parser fixtures, same-key multi-device traffic, exact rejection/status and no cross-device writes or confirmations.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-9
priority: P0
acceptance:
  ### unit
  - name: exact-device-scoped-envelope-parsing
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_mqtt_control_contract.py::test_json_scalar_paths_metadata_and_target_mismatches
    expect: 0
  ### integration
  - name: multi-device-queue-and-frame-isolation
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_mqtt_control_contract.py::test_same_key_two_devices_proxy_identity_and_foreign_frame_rejection
    expect: 0
  ### e2e
  - name: route-to-enrolled-device-only
    scenario: Send both supported schemas to two synthetic BOX identities through a local broker and TCP peers; only the exact enrolled target receives its qualified Setting, malformed or foreign targets receive correlated rejection, and legacy obsolete routes remain inactive.
    expect: pass
```

## REQ 10 - Bind immutable commands to actual wire and confirmation evidence

- Sources: S2, S4-S5. Dependencies: REQ 8-9. Consume the catalog's profile/setting contracts; final qualified-profile integration is verified with REQ 14-16 and REQ 22, not a prerequisite for freezing immutable correlation.
- Freeze each sent command's request/generation/device/accepted-session identity, table/key/value, profile generation, exact serialized bytes/hash and actual allocated wire message/setting IDs. Audit records must use actual wire IDs, not queued placeholder IDs.
- Match ACK/NACK/event/readback by the qualified signature and immutable identity; never reread a mutable same-key queue entry to decide which value completed. Distinguish unrelated cloud/planner observations from local completion while retaining them as truthful state.
- Out-of-order, duplicate, delayed, foreign-device or foreign-session ACKs cannot consume another request, falsely apply a value or release a dependent barrier. Keep mismatch and uncertain outcomes auditable.
- Correlation includes the immutable send/receive anchor and attribution policy from REQ 6. Preserve competing same-key cloud/planner observations through the whole request interval. When the wire/event format provides no discriminating request/source signal, document that limit in the profile and return ambiguous attribution instead of inventing an ID match.
- Planned evidence: immutable command-to-wire-to-status trace and negative correlation matrix independent of queue lookups.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-10
priority: P0
acceptance:
  ### unit
  - name: immutable-generation-wire-binding
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_command_correlation.py::test_immutable_request_generation_session_and_actual_wire_ids
    expect: 0
  ### integration
  - name: late-foreign-and-mismatched-confirmation
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_command_correlation.py::test_late_duplicate_foreign_ack_nack_event_and_readback_cannot_complete_another_request
    expect: 0
  ### scenario
  - name: exact-audit-to-wire-attribution
    scenario: Reconstruct each accepted command from ingress through exact wire bytes and final evidence on one candidate revision; every completion names actual device/session/generation/wire IDs, and a competing cloud/planner observation never masquerades as the local request's completion.
    expect: pass
```

## REQ 11 - Make replacement and session ownership safe

- Sources: S4-S5. Dependencies: REQ 9-10.
- Retain immutable inflight work when a new same-device/key request arrives; replace only eligible unsent intent with explicit supersession. An older ACK cannot delete or confirm an unsent replacement.
- Release queue, session and global ownership consistently on matching terminal evidence, timeout, disconnect and cancellation. A cleared session cannot block a new session or accept a foreign session's ACK.
- Retain the four reproduced regressions: old ACK consumes replacement; foreign device/session ACK accepted; ACK leaves selected session occupied; disconnect retains global ownership. Cover interleavings and passing controls, not expected failures accepted as success.
- Planned evidence: all four former RED cases become retained passing regressions with queue/session/global snapshots and no lost request.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-11
priority: P0
acceptance:
  ### unit
  - name: replacement-and-foreign-ack-regressions
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_command_queue_regressions.py::test_old_ack_preserves_replacement_and_foreign_ack_preserves_inflight
    expect: 0
  ### integration
  - name: terminal-and-disconnect-ownership-cleanup
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_command_queue_regressions.py::test_ack_and_disconnect_clear_session_global_ownership_without_losing_other_work
    expect: 0
  ### scenario
  - name: all-four-session-defects-regress
    scenario: Execute the four named session/replacement reproductions with reordered ACKs, replacement and reconnect controls; every old defect fails against characterization and passes against the candidate, no unsent request is falsely confirmed, and subsequent authorized work can proceed.
    expect: pass
```

## REQ 12 - Preserve transparent byte streams during local injection

- Sources: S2, S5. Dependencies: REQ 10-11.
- Preserve ordinary cloud/BOX forwarding byte-for-byte across arbitrary fragment boundaries, coalesced frames, polling and local injection. Do not forward a partial prefix twice, discard a coalesced sibling or insert a local frame inside an unfinished cloud frame.
- Retain the three reproduced stream regressions: fragmented BOX ingress duplication, coalesced DATA+poll sibling loss and cloud/local fragment interleaving. Cover both directions, multiple concatenated frames, malformed/CRC-failing frames, disconnect mid-frame and all supported injection poll families.
- Maintain deterministic bounded buffering and one complete-frame write owner; blocked/unqualified local control must preserve ordinary unmodified forwarding and RO.
- Planned evidence: input/output byte transcripts, no-loss/no-duplication assertions and independent CRC/frame-order verification for all three formerly RED cases.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-12
priority: P0
acceptance:
  ### unit
  - name: fragmented-and-coalesced-forwarding
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_stream_injection_regressions.py::test_fragmented_box_bytes_once_and_coalesced_siblings_preserved
    expect: 0
  ### integration
  - name: frame-atomic-injection-and-session-end
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_stream_injection_regressions.py::test_cloud_fragments_local_injection_crc_failure_and_disconnect_remain_frame_atomic
    expect: 0
  ### e2e
  - name: three-stream-defects-byte-exact
    scenario: Replay sanitized synthetic fragment/coalescence/interleaving cases through TCP peers with concurrent cloud and local activity; all ordinary bytes arrive exactly once in order, local frames remain complete and valid, and each of the three historical defects has a retained passing regression.
    expect: pass
```

## REQ 13 - Prevent replay and honor expiry reconnect and control disable

- Sources: S2, S4-S5. Dependencies: REQ 8-12; lifecycle payload contract from REQ 2, with REQ 7 diagnostics integrated afterward.
- Reject retained commands and expired/stale envelopes. Use an explicitly nonpersistent command subscription session: MQTT 3.1.1 `clean_session=true`, or MQTT 5 `clean_start=true` with session expiry `0`; subscribe scalar control topics at QoS `0`, independently of telemetry/discovery QoS. On each broker connection epoch keep control admission closed until the configured clean-session contract, no resumed prior session and expected SUBACK grant are verified. Reject retained messages and callbacks from an older connection epoch; never infer freshness solely from receipt-time TTL. A broker/session configuration that cannot establish these command-channel properties leaves controls blocked with a reason.
- Scalar payloads contain no publisher creation timestamp/request identity, so the proxy cannot prove their age before publication or detect arbitrary publisher-side delayed intent. Document this limit. Require the versioned expiring envelope route for automations that need publisher-to-wire expiry, durable request identity or backlog rejection across publisher outages; never advertise receipt-time scalar TTL as that guarantee. For accepted fresh scalar controls, start a bounded unsent-work TTL at ingress and never reconstruct them after restart.
- Deduplicate explicit request identities/QoS redelivery without treating two fresh equal-valued operator actions as the same request. Keep bounded non-runnable tombstones; never reconstruct executable work from retained observed/status/requested snapshots.
- Broker/HA/proxy restart, reconnect, snapshot restoration or profile/session change must not automatically resend uncertain or pending commands. Restore subscriptions/discovery deterministically, mark interrupted/expired work, require a fresh explicit request, and make `control_mqtt_enabled=false` effective at both admission and pre-send boundaries.
- Add a real-broker persistent-session backlog negative control: deliberately queue a non-retained scalar while the proxy is offline, reconnect under the required nonpersistent/QoS0 policy, and prove it cannot emit Setting bytes; a deliberately misconfigured resumed session must leave control blocked. Cover retained delivery, old-connection callbacks, failed/unexpected SUBACK, new explicit scalar action after readiness, and versioned envelope expiry across publisher delay. This is planned evidence, not a claim that the existing client already supplies it.
- Runtime/test/consumer ownership and final-pass prerequisites follow `.omx/plans/oig-release-ownership-contract.md` and the expanded AC matrix. W02-RI integrates reviewed catalog/permission deltas; W05-M owns message-metadata ingress before W07; W08-C owns server/main confirmation hooks after W06/W07. A leaf requests the named owner/handoff rather than silently broadening paths.
- Planned evidence: real local broker restart/redelivery tests, clock-driven deadlines, persisted-state restore, effective-disable zero-wire assertions and accurate status.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-13
priority: P0
acceptance:
  ### unit
  - name: ttl-idempotency-and-disable
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_control_replay_safety.py::test_unsent_ttl_request_dedup_fresh_equal_actions_and_effective_disable
    expect: 0
  ### integration
  - name: retained-reconnect-restart-no-replay
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_control_replay_safety.py::test_real_broker_retained_redelivery_reconnect_restart_and_snapshot_never_requeue
    expect: 0
  ### e2e
  - name: no-spontaneous-write-after-recovery
    scenario: Restart isolated HA, broker and proxy with retained commands/status, a deliberately queued persistent-session scalar backlog and interrupted queued/inflight records; prove the required clean command session/QoS0 readiness gate discards old-epoch/backlog intent, a resumed or unverified session blocks controls, snapshot restoration and control-off emit no Setting bytes, stale work has terminal reasons, and only a new qualified explicit request after readiness can write.
    expect: pass
```

## REQ 14 - Qualify wire profiles per device layout and evidence epoch

- Sources: S2, S5. Dependencies: REQ 1, REQ 9-10.
- Replace universal private N44/K7BFC use with explicitly supported qualified device/layout/profile cohorts. Bind qualification to exact identity, layout, provenance, evidence interval, profile/schema generation and freshness. Do not present equivalent CRC-state inference as recovered universal vendor semantics.
- Admit complete exact-byte, CRC-valid natural evidence; exclude mutations, replay, redactions, duplicates and mixed identities. Engineering proposes a bounded hypothesis family, diverse-prefix identifiability, independently retained chronological holdouts, cloud-Setting/BOX evidence requirements and justified thresholds/freshness policy. Independently review and freeze those concrete proposals before write qualification; no arbitrary count is advertised as protocol truth and no operator-invented CRC threshold is required for research.
- Unknown/stale/ambiguous/rotated/disagreeing profiles block local writes both at admission and pre-send, invalidate unsent old-generation work and expose precise reasons while preserving cloud forwarding and RO. No random ver fallback, candidate cycling or automatic uncertain retry.
- Runtime/test/consumer ownership and final-pass prerequisites follow `.omx/plans/oig-release-ownership-contract.md` and the expanded AC matrix. W02-RI integrates reviewed catalog/permission deltas; W05-M owns message-metadata ingress before W07; W08-C owns server/main confirmation hooks after W06/W07. A leaf requests the named owner/handoff rather than silently broadening paths.
- Planned evidence: independently verified byte-exact inner ver/outer CRC vectors for both serializers, held-out N39/N42/N44 cohorts, wrong/corrupt/rotated profiles and no-write fail-closed traces.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-14
priority: P0
acceptance:
  ### unit
  - name: independent-profile-and-crc-vectors
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_wire_profile_qualification.py::test_both_serializers_independent_inner_ver_outer_crc_and_heldout_cohorts
    expect: 0
  ### integration
  - name: qualification-fails-closed-on-change
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_wire_profile_qualification.py::test_unknown_ambiguous_stale_rotated_and_changed_generation_block_writes_preserve_forwarding
    expect: 0
  ### scenario
  - name: qualified-profile-evidence-review
    scenario: Review the frozen qualification policy and independent holdout results for every advertised cohort; insufficient or cross-direction-unproven evidence remains unqualified, persisted old profiles alone grant no write authority, and production has no unconditional private N44/K7BFC fallback.
    expect: pass
```

## REQ 15 - Define known and unqualified offline bootstrap separately

- Sources: S2, S5. Dependencies: REQ 13-14; engineering derives and reviews the offline qualification proposal, while any later hardware execution outside agreed scope needs the applicable operator decision.
- Distinguish online enrolled known-device requalification, known-device offline startup and unknown/unqualified offline startup. A stored profile is diagnostic/bootstrap evidence only; require fresh valid identity/layout/profile agreement before any allowed offline write.
- Unknown devices, rotated profiles and insufficient cloud-to-Setting qualification remain read-only with controls unavailable offline; truthful RO observations remain available. Document whether an enrolled cohort can requalify offline, the measured prerequisites and the exact unsupported cases; do not promise universal offline cold-start control.
- Preserve accepted session binding and the same confirmation/anti-replay contract in connected and offline delivery. Cover cloud loss before send, between ACK and event, restart, profile rotation and cloud return without resending uncertain work.
- Planned evidence: explicit bootstrap truth table, paired known/unknown simulator traces and no-write forwarding/RO assertions in unsupported cases.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-15
priority: P0
acceptance:
  ### unit
  - name: offline-bootstrap-truth-table
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_offline_control_bootstrap.py::test_enrolled_fresh_stale_unknown_and_rotated_profiles_have_explicit_permissions
    expect: 0
  ### integration
  - name: offline-session-confirmation-and-recovery
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_offline_control_bootstrap.py::test_cloud_loss_restart_return_and_offline_send_preserve_session_without_replay
    expect: 0
  ### e2e
  - name: known-versus-unqualified-offline
    scenario: Start enrolled-qualified and unknown BOX simulators without cloud, then rotate a profile and restore cloud; only explicitly qualified fresh known-device cases can write, all others expose the documented reason and keep RO/forwarding, and recovery never silently retries prior intent.
    expect: pass
```

## REQ 16 - Qualify every enabled setting independently

- Sources: S3-S5. Dependencies: REQ 2-3. Qualify catalog permission/evidence policy independently of runtime implementation; REQ 8 and REQ 14-15 later enforce it, and REQ 22 verifies their integrated behavior.
- Maintain a release permission matrix per exact table/key/device/firmware/layout: meaning, command type/unit/reference side, allowed values/range/step/precision, serialization vectors, companion constraints, ACK signature, event/readback/physical proof, timeout and uncertainty semantics.
- Checksum success alone does not establish setting execution. Keep unqualified keys RO/disabled. MODE BOX values are distinct from inverter MODE; qualify each allowed direction. Keep current private AAC exactly 100 restriction distinct from a general range; distinguish AAC_MAX_CHRG from A_MAX_CHRG and require separately evidenced companion limits.
- Qualify time/weekday schedules and one-shot SA/RQRESET repetition/completion deliberately without silently changing existing components. BAT_FORMAT/FMT_ON conflicts and GRID flags remain blocked until their specific evidence gaps close.
- Q01 is the W03-owned qualification-route gate defined in `.omx/plans/oig-release-qualification-scope-contract.md`. Before permission-population or affected W09 recipe leaves start, record per-setting/cohort missing facts, immutable sources, source-qualified/authorized-experiment/explicit-scope-decision-required/blocked route, owner and prerequisites. Cover GRID, unresolved BAT/FMT, boiler schedules/zones and SA/RQRESET repetition. Hardware proposals need separate exact setting/value/probe/planner/recovery authority; no guessed code/range is sent. Qualification precedes the final candidate and RC. Post-RC catalog/profile/recipe changes require a new candidate, affected closure plus mandatory quality/pre-RC gates, fresh own-HA RC and final review.
- Runtime/test/consumer ownership and final-pass prerequisites follow `.omx/plans/oig-release-ownership-contract.md` and the expanded AC matrix. W02-RI integrates reviewed catalog/permission deltas; W05-M owns message-metadata ingress before W07; W08-C owns server/main confirmation hooks after W06/W07. A leaf requests the named owner/handoff rather than silently broadening paths.
- Planned evidence: reviewed permission matrix, independent positive/negative vectors for every enabled value class and denied-key wire silence.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-16
priority: P0
acceptance:
  ### unit
  - name: per-setting-permission-evidence
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_setting_release_matrix.py::test_every_enabled_setting_has_qualified_values_profiles_serialization_and_confirmation
    expect: 0
  ### integration
  - name: setting-actions-and-denied-domains
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_setting_release_matrix.py::test_mode_domains_aac_exact_policy_schedules_actions_and_unqualified_keys
    expect: 0
  ### scenario
  - name: no-unqualified-write-permission
    scenario: Review each proposed enabled setting against its immutable evidence and companion limits; private AAC100 success and checksum prediction grant no wider range, observed BAT_FORMAT18/FMT_ON2 grant no command code, and unresolved actions/schedules/GRID remain disabled with an explicit reason.
    expect: pass
```

## REQ 17 - Exclude private mutation and raw replay hooks from production

- Sources: S2, S4-S5. Dependencies: REQ 10 and REQ 14; REQ 22 later verifies the resulting production surface.
- Exclude private `protocol/private_ver_probe.py` mutation machinery, cloud-transform hooks/imports/state and private `--tto-probe` deployment routes from the production candidate. Remove the `/data/replay_setting_frame.xml` one-shot raw-frame bypass and equivalent undocumented injection paths from production control delivery.
- Retain sanitized independent vectors and ordinary MQTT-to-TCP regressions; an absent/disabled flag or unarmed file alone is insufficient evidence that a production bypass cannot run.
- Audit candidate image/package, runtime imports, arguments, file hooks and connected/offline paths. Preserve transparent cloud forwarding/RO and legitimate qualified commands after isolation/removal. Keep private captures, serials and secrets outside shipped artifacts.
- Planned evidence: production artifact inspection, negative hook activation cases and qualified ordinary-route regression output.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-17
priority: P0
acceptance:
  ### unit
  - name: production-has-no-private-injection-path
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_production_control_surface.py::test_probe_imports_flags_cloud_mutation_and_raw_replay_are_absent_or_unreachable_by_construction
    expect: 0
  ### integration
  - name: artifact-hook-attempts-cannot-mutate
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_production_control_surface.py::test_candidate_image_replay_file_probe_arguments_and_cloud_frames_cannot_bypass_admission
    expect: 0
  ### scenario
  - name: sanitized-production-artifact-review
    scenario: Independently inspect the exact candidate package/image and control call paths; no private raw replay or cloud mutation mechanism can emit a production Setting, public fixtures contain synthetic identifiers only, and approved MQTT control plus ordinary forwarding still works.
    expect: pass
```

## REQ 18 - Present help through supported native HA surfaces

- Sources: S1, S3-S4. Dependencies: REQ 2-7 and REQ 16.
- Generate concise Czech meaning/effects/unit/legal-values/applicability/confirmation help from the reviewed catalog. Use supported RO or companion-sensor attributes, real device-level documentation URL and optional native HA dashboard with RO value, `_cfg`, requested value, status and Markdown help.
- Do not add an unsupported generic MQTT `description` field or rely on number/select attributes absent from their supported schemas. Keep core validation and control correctness independent of optional dashboard installation.
- Keep stable metadata separate from high-frequency timestamps/raw frames to limit recorder churn. Represent unknown codes and blocked-control reasons visibly; do not make explanatory UI imply physical execution.
- Runtime/test/consumer ownership and final-pass prerequisites follow `.omx/plans/oig-release-ownership-contract.md` and the expanded AC matrix. W02-RI integrates reviewed catalog/permission deltas; W05-M owns message-metadata ingress before W07; W08-C owns server/main confirmation hooks after W06/W07. A leaf requests the named owner/handoff rather than silently broadening paths.
- Planned evidence: native discovery schema validation, isolated HA rendered/help assertions and stable-attribute churn checks.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-18
priority: P1
acceptance:
  ### unit
  - name: supported-help-schema-only
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_ha_catalog_help.py::test_native_component_schemas_help_links_unknown_codes_and_stable_attributes
    expect: 0
  ### integration
  - name: dashboard-optional-core-enforcement
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_ha_catalog_help.py::test_native_dashboard_displays_help_state_intent_status_without_controlling_validation
    expect: 0
  ### e2e
  - name: czech-help-visible-in-native-ha
    scenario: In isolated supported HA, inspect representative number/switch/select and RO/status entities with and without the optional dashboard; Czech purpose, units, legal values, blocked reason and confirmed-versus-requested state are readable and no unsupported description property is required.
    expect: pass
```

## REQ 19 - Publish coherent Czech catalog and operational documentation

- Sources: S1-S5. Dependencies: REQ 2-9, REQ 13-16 and REQ 18.
- Prepare Czech GitHub quickstart, searchable complete parameter catalog, authoritative MQTT contract, migration guide, troubleshooting and current security/control policy. Generate approved values/constraints/help from the catalog and link exact entities/topics and confirmation semantics.
- Explain MODE versus PROXY_MODE, BOILER/HDO/time representation, one-shot actions, state versus intent, profiles/offline availability, retained/expiry/dedup, rejection/uncertainty, reconnect and deliberate fresh-request recovery. Correct stale GRID permissions and obsolete Twin/topic/string-setting claims; mark historical evidence instead of silently rewriting it as current fact.
- Use `<BOX_ID>`, `<NAMESPACE>`, synthetic devices, `retain=false`, bounded matching waits, prerequisites, timeout/failure/restoration steps and native HA plus raw MQTT equivalents. Do not publish unqualified GRID automation or hardware-effect claims beyond evidence.
- Planned evidence: generated-reference coverage/link checks, documentation contract fixtures and executable snippet results from REQ 20-22. Publication itself remains separately gated by REQ 26.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-19
priority: P1
acceptance:
  ### unit
  - name: complete-czech-reference-and-links
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_documentation.py::test_every_disposition_has_current_czech_reference_provenance_and_resolvable_links
    expect: 0
  ### integration
  - name: mqtt-migration-and-troubleshooting-contract
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_documentation.py::test_quickstart_mqtt_security_migration_and_failure_examples_match_runtime
    expect: 0
  ### scenario
  - name: documentation-readiness-review
    scenario: Review Czech quickstart/catalog/MQTT/migration/troubleshooting on the candidate revision; supported and blocked behavior is explicit, old V1 and unsupported examples cannot be mistaken for active routes, all IDs are parameterized, and no private capture or unmeasured hardware-effect claim is published.
    expect: pass
```

## REQ 20 - Stage higher AC charging behind exact UPS confirmation

- Sources: S1-S5. Dependencies: REQ 6-16, REQ 19 and explicit charge-policy qualification.
- Provide tested native HA service and raw MQTT examples: request qualified UPS MODE, await the profile-defined correlated application ACK plus matching authoritative MODE event OR fresh authoritative MODE readback, retain `confirmation_source`, recheck mode/profile/companion limits immediately before AAC, then request the individually approved `AAC_MAX_CHRG=100` example and await its qualified ACK-plus-event OR fresh-readback confirmation. Match exact device/key/value/generation/session and the profile-defined freshness window at each stage.
- A qualified matching BOX event establishes confirmed configured value without waiting for an infrequent table refresh. Require fresh table readback only when the profile needs it or a fresh-readback claim is made; require independent measurement for physical-current/power claims. The historical AAC100 prototype proved application ACK and exact execution event, not a fresh raw table readback or physical current.
- Do not enqueue MODE and AAC together, release AAC on generic ACK/queue-empty or use blind delay. Distinguish configured current from measured physical current; 100 A is an accepted sample requiring device policy, not a universal permitted range, electrical-side claim or promise of charging power.
- Abort on NACK, expiry, missing/mismatching/stale confirmation, disconnect/profile change, competing cloud/planner command, mode reversal or invalid companion limits. Define explicit restoration of prior settings; never automatically restore by blind replay after uncertain execution.
- Distinguish a fresh observed UPS precondition from completion of a newly sent MODE request. A recipe may omit a redundant MODE write when qualified current-state evidence already establishes UPS and its freshness/context/writer assumptions remain valid; label the barrier `observed_precondition`, retain its evidence, and revalidate it immediately before the AAC send. If the recipe sends MODE, require that request's anchored completion; an ACK-only equal-value or ambiguous same-value result aborts the dependent AAC step. Do not silently convert an uncertain MODE request into a successful observed-precondition path.
- Document the unavoidable observe/send race in the Czech HA/MQTT recipes: an unobserved cloud/planner/LCD MODE change can reach the BOX between the last UPS observation and AAC application. Proxy-side serialization/revalidation is a bounded freshness check, not an atomic BOX compare-and-set or proof of MODE at send time. Coordinate competing planners under the agreed operating policy when that guarantee is needed; no device conditional-write capability is assumed. On an observed intervening change, ambiguous attribution or lost session, stop dependent writes and mark uncertainty; if AAC was already sent, retain evidence and use only separately approved recovery, never claim it can be unsent or silently restore settings.
- Planned evidence: simulator wire/state sequence, actual HA/MQTT snippet execution, every barrier failure and restoration/physical-claim limit.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-20
priority: P0
acceptance:
  ### unit
  - name: ups-charge-barrier-and-preconditions
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_charge_recipe.py::test_aac_stage_accepts_qualified_ack_event_or_fresh_readback_and_revalidates_limits
    expect: 0
  ### integration
  - name: charge-recipe-aborts-and-restoration
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_charge_recipe.py::test_event_completion_readback_claims_nack_timeout_disconnect_competing_change_and_restore_policy
    expect: 0
  ### e2e
  - name: native-ha-and-mqtt-ups-aac-example
    scenario: Execute documented native HA snippets in isolated HA with a real local test broker and BOX peer, and raw MQTT snippets against the same peer; no AAC bytes precede profile-defined ACK-plus-qualified-event OR fresh-readback UPS confirmation, an ACK alone never releases the barrier, qualified AAC event confirmation completes without waiting for table refresh and records confirmation_source, explicit fresh-readback/physical-current claims require their separate evidence, and injected failures stop later writes.
    expect: pass
```

## REQ 21 - Qualify GRID discharge contexts and topology-specific examples

- Sources: S1-S5. Dependencies: REQ 3, REQ 8-16, REQ 19 and GRID/topology qualification.
- Explain GRID_PV_ON as discharge-to-grid context with FVE available and GRID_PV_OFF as context without FVE; suffixes do not establish enable polarity or an FVE on/off command. Block unqualified WRITES until legal codes, applicability, FVE criterion/hysteresis, safe intermediate states, precedence with TO_GRID/LOAD/PRRTY and confirmation path are qualified; preserve truthful RO/raw/unknown observations throughout.
- Separate grid export from feeding non-backed loads. Document actual topology, connection/measurement/CT point and sign convention before executable examples; do not promise zero export or infer load location from a key name.
- Deliver native HA/MQTT examples for qualified requested scenarios, both update directions and permitted intermediate states, with exact confirmation, abort and restoration. While unqualified, engineering research may produce Czech explanatory truth tables with explicit unknown cells and no copy/paste write recipe; requested functional GRID automation remains NOT DELIVERED. Safety exclusions/truth tables cannot satisfy functional acceptance. A reduced release needs explicit operator-accepted GRID deferral and a reviewed applicability/scope update; deferred functional tests stay NOT RUN rather than being reported as passed.
- Functional ACs pass only for delivered behavior. Unqualified functional ACs stay NOT RUN/UNVERIFIABLE or FAIL as observed, and the outcome stays NOT DELIVERED. Only a W12-owned, independently reviewed explicit operator decision under `.omx/plans/oig-release-qualification-scope-contract.md` may exclude named functional ACs from a reduced release scope; record DEFERRED/NOT DELIVERED, never PASS. The RO/blocked safety AC remains mandatory. No deferral is accepted now.
- Enforce `.omx/plans/oig-release-applicability-contract.md`: full canonical case/AC population, source/definition/oracle bindings, reviewed amendments and actual accepted scope decisions govern applicability; no missing case means not applicable, excluded functionality never becomes PASS, retained safety/quality ACs remain mandatory. No reduced scope is accepted now.
- Planned evidence: qualified context/precedence matrix, topology diagrams/review, denied unqualified writes and simulator tests for every enabled scenario.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-21
priority: P0
acceptance:
  ### unit
  - name: grid-unknown-codes-stay-readonly
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_grid_recipe.py::test_unqualified_grid_writes_blocked_while_raw_and_unknown_observations_remain_visible
    expect: 0
  ### integration
  - name: qualified-context-and-topology-scenarios
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_grid_recipe.py::test_qualified_grid_native_ha_mqtt_recipes_cover_both_contexts_topologies_intermediates_and_failures
    expect: 0
  ### scenario
  - name: no-unproven-grid-automation
    scenario: Execute qualified native HA and MQTT recipes for both requested FVE contexts and separate export/non-backed-load topologies on the exact candidate; cover both update directions and permitted intermediates, verify exact confirmation and uncertainty aborts, retain qualified semantics/topology evidence and demonstrate no unsupported zero-export claim. Every requested functional case has executed successfully; blocked writes, truth tables, absent qualification, missing fixtures or a scope deferral cannot satisfy this criterion.
    expect: pass
```

## REQ 22 - Exercise independent offline end-to-end behavior

- Sources: S1-S5. Dependencies: REQ 5-21.
- Build isolated deterministic real local MQTT-broker to proxy TCP/BOX-peer E2E coverage and sanitized independent byte vectors. Isolated HA is mandatory for actual native-service/discovery assertions required by REQ 5, REQ 18 and native recipe ACs; it is optional only for broker/TCP-only cases. Broker simulation alone cannot establish native HA behavior. No real BOX, cloud, own HA or private network is needed for offline acceptance.
- Cover supported devices/settings/ingress routes, profile and state qualification, successful and rejected commands, BOX confirmation, fragmentation/coalescence/interleaving, replacement, cross-device/session, retained/TTL/dedup, reconnect/restart, offline known/unknown, disabled control and charge/qualified-grid recipes.
- Assert actual MQTT state/status/discovery plus byte-exact wire/output effects, not only generated snapshots or mocked queue calls. Include independent semantic/boundary oracles alongside catalog-generated matrices; retain all seven regression identities in coverage reports.
- The mandatory acceptance case manifest includes independently sealed W01/W03 source/semantic/boundary/permission oracles and seeded faults for REQ 3/8/14/16: wrong unknown-code/unit meaning, min/max/step boundary, wrong device/profile/CRC bytes and unauthorized setting/cohort. Each corresponding suite must reject its seed and pass the unmutated control. Retain every seed ID/outcome and reject omitted manifest cases; generated catalog outputs are not their own expected semantic oracle.
- Enforce `.omx/plans/oig-release-applicability-contract.md`: full canonical case/AC population, source/definition/oracle bindings, reviewed amendments and actual accepted scope decisions govern applicability; no missing case means not applicable, excluded functionality never becomes PASS, retained safety/quality ACs remain mandatory. No reduced scope is accepted now.
- Existing tests/support files follow `.omx/plans/oig-release-legacy-test-contract.md` and exact-base `.omx/plans/oig-release-legacy-test-inventory.json`: 37 Python modules, 31 test modules and 461 static test symbols, not collected/executed counts. W10-LEGACY has explicit per-file ownership/review; L00 freezes baseline/transition/handoff policy before H00 and affected legacy work, L01 reconciles real base/candidate collection and every reviewed semantic update before full-suite final acceptance. Retained legacy cases count for guard/coverage but do not prove a new canonical AC by default; fake legacy labels, unexplained removals/parameter loss or stale module/fixture hashes are rejected.
- Planned evidence: reproducible simulator versions/seeds, named case manifest, broker/TCP transcripts and failure-path result mapping per REQ/AC.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-22
priority: P0
acceptance:
  ### unit
  - name: independent-simulator-oracles
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_e2e_harness.py::test_independent_wire_state_and_boundary_oracles_detect_seeded_failures
    expect: 0
  ### integration
  - name: complete-local-broker-tcp-matrix
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_e2e_harness.py::test_real_local_broker_tcp_and_isolated_ha_routes_profiles_failures_and_seven_regressions
    expect: 0
  ### e2e
  - name: isolated-candidate-acceptance-matrix
    scenario: Run the complete retained offline E2E matrix on one immutable candidate with the real local test broker/TCP peers and mandatory isolated HA for every retained native-service/discovery/recipe assertion; reconcile full baseline case collection, exact executed assertions and any explicitly authorized functional deselections against canonical REQ/AC definition and spec-source hashes and accepted scope-decision hashes, retain every excluded functional result as NOT RUN/DEFERRED without a PASS claim, require all retained safety/quality and functional cases due at this phase to pass, and leave missing fixture, missing qualification or unapproved omitted GRID cases blocking rather than treating them as successful reduced coverage.
    expect: pass
```

## REQ 23 - Gate builds tests lint types and runtime coverage

- Sources: S1-S5; `.github/workflows/ci.yml`, `.github/workflows/pylint.yml`, `requirements-dev.txt` as reference-only current configuration. Dependencies: integrated REQ 1-22 artifacts for final acceptance; H00 bootstrap below does not wait for their completed implementation.
- Before a later PR or candidate approval, build the actual production add-on image on the agreed target architecture and run meaningful unit/integration/E2E checks, configured lint including Dockerfile, runtime Pylint and mypy. Extend the tests-only Pylint scope to runtime code. Type-check the same runtime paths and byte-identical source inputs; if an isolated layout workaround is needed for the hyphenated add-on path, document the mapping/input hashes and preserve failures without requiring an unrelated package restructure.
- Enforce the operator-requested unrounded runtime statement coverage strictly above 80 percent. Report branch coverage and changed-branch/failure evidence separately; no additional universal branch percentage threshold is approved by this preparation. The runtime-coverage gate executes the full retained pytest acceptance scope with the mandatory guard, produces raw coverage JSON and compares statement counts (`covered_statements * 100 > total_statements * 80`), not the rounded combined pytest-cov display. The statement denominator must be nonzero and match the exact candidate runtime manifest; branch counts are retained faithfully, with explicit N/A if there are no branches. Keep line/branch reports and changed-path/failure coverage; do not substitute Fleet AC ratios/private helper coverage.
- Pin tool versions and actual build/lint/type/E2E commands during future plan freeze; report every exact result on the tested candidate. Unavailable checks remain blocking/NOT RUN, never waived by `|| true`. MNP is resolved as build and tests.

- Every canonical pytest invocation uses the mandatory versioned `tests/v2/conftest.py` acceptance guard; absence/disabling of that guard invalidates evidence. Bind the guard and `tests/acceptance/case-manifest.json` hashes to the candidate and exact REQ/AC definitions. Require nonempty expected collection, zero skips/xfails/xpasses, all expected executed cases passed, required fixture readiness/version checks and explicit named-case coverage for each multi-claim AC. Missing HA/broker/container dependencies fail the applicable AC; exit 0 alone is insufficient. Do not count a pass-bodied test as evidence: independent vectors and seeded mutations must prove its assertions detect the promised failures.
- Named H00, owned by W10-HARNESS, bootstraps the guard and initial canonical case manifest after C00 and pre-W02 W01 schema-oracle freeze, before W02-I selected AC execution. H00 requires the applicable implementation authority/imported definitions but not W02-W09 completed implementation; its generic rejection self-tests use independent synthetic fixtures. Final W10 consumer execution remains after integration. W10 owns this guard and the case manifest before any product AC is accepted. Each canonical node has reviewed expected case IDs, source/oracle hashes, fixture requirements and result-count rules. Full-suite coverage does not waive a missing required case; raw JUnit/executed/skipped counts and guard output must match the manifest. The guard's own tests seed skip, xfail, empty collection, omitted case, missing fixture and a vacuous oracle and require rejection.
- C01 freezes the reviewed manifest-generation rule and reconciliation algorithm for production-packaged Python under `addon/oig-proxy`, excluding `addon/oig-proxy/tests/**`, test fixtures and explicitly nonpackaged private probes; every real production module remains included. Coverage uses the exact candidate runtime manifest; Pylint, mypy and Bandit must cover at least that complete runtime set, with any additional test/fixture/source superset disclosed separately. Flake8 checks that runtime set plus named test paths; current directory commands are provisional, not a claim of identical scope or working import resolution. After integration, C01-CANDIDATE-MANIFEST applies the unchanged rule to the immutable candidate and independently reviews/freezes the exact paths, content hashes and packaging mapping. Expected new modules under that rule require normal candidate reconciliation, not a semantic C01 amendment. Rule/exclusion/tool changes still require the reviewed amendment procedure. The wrapper rejects differences from that frozen candidate instance, and no production path can disappear from coverage or required runtime analysis.
- `--strict-markers` and `xfail_strict=true` provide supplementary checks only: xfail_strict rejects XPASS, not XFAIL or skips. The mandatory guard and executed-case evidence enforce zero required skipped/xfail/empty cases. C01 pins the exact pytest/coverage/plugin configuration and raw metric precision; acceptance-guard self-tests seed rounded/combined-score false greens, an omitted production module and missing metric counts.
- I00 imports the entire governing specification atomically, including REQ 23/24, only after the applicable implementation/import authority and exact reviewed source identity are established. Their quality commands are provisional definitions until C01; import is neither permission to execute nor evidence of acceptance. C01 is not an import prerequisite. C01 validates and freezes exact quality commands, environment, layout, versions, paths and metric policy before any REQ 23/24 quality AC execution or acceptance. If validation changes an imported command, case, governing rule or scope binding, use the explicit amendment/re-import procedure: retain old/new source and definition hashes, independently review the change, atomically re-import the whole amended spec and invalidate affected evidence plus its downstream acceptance closure. No prior PASS transfers to a changed governing source/definition merely because the importer calls a REQ unchanged.
- C01 output `docs/verification/quality-gate-manifest.json` requires versioned gate IDs, exact argv/cwd/environment/PYTHONPATH, pinned tool/config/dependency/image hashes, runtime population/exclusion rules (exact candidate paths are frozen by C01-CANDIDATE-MANIFEST), byte-equivalent temporary-layout mapping, metric numerator/denominator/precision, evidence destinations and unmasked failure policy. Validate actual command admissibility against the deployed verifier; FLAGGED/UNVERIFIED cannot count as PASS. Collection/capability-only probes remain explicitly non-acceptance evidence and cannot mint an AC token.
- C01-CANDIDATE-MANIFEST outputs `docs/verification/candidates/<candidate-id>/runtime-source-manifest.json` and independent review. Required fields: schema version, immutable candidate commit/tree, C01 rule/tool/config hashes, packaging-input hashes, target architecture, sorted production paths/content hashes, explicit exclusions/rule references and reconciliation/reviewer proof. Actual later build evidence binds the produced image digest and proves packaged production modules match this exact candidate manifest; no pre-build image inspection is claimed. Expected new modules under the unchanged rule create a normal new candidate instance, not a C01 definition amendment; changed rules/exclusions/tool semantics still require reviewed amendment/invalidation.
- C01 completes only when the applicable authored quality nodes, guard/wrapper and tool/environment inputs are collectable and validated. Non-quality authoring and narrowly bounded H00 synthetic bootstrap do not wait for C01; no ordinary canonical quality acceptance occurs during bootstrap.
- No imported acceptance definition or governing semantic prose is silently changed. Fleet's REQ definition hash covers ID, priority and acceptance text only; it does not cover the requirement prose or referenced contracts. Bind every acceptance record and release gate additionally to the full governing spec source SHA-256, referenced contract hashes, case-population/applicability manifest and actual scope-decision hashes.
- The requirement-record owner maintains `docs/verification/requirement-amendments/<amendment-id>.json`. W10 proposes the record; the affected contract/feature owners supply impact analysis; an independent reviewer approves before revised evidence can count. Required fields are amendment ID/status/owner, old/new full source and per-REQ definition hashes, referenced contract/command/environment/case/scope deltas, changed REQ/AC/case IDs, exact reason, semantic versus editorial classification, affected candidate/evidence/dependency closure, required reruns or supported reuse, reviewer proof and superseded record references. Scope narrowing additionally requires actual operator scope acceptance; it cannot be authorized by an engineering review alone.
- Semantic prose/contract changes reopen every affected AC and dependent barrier, pre-RC/RC/final-review/publication approval identified by the reviewed impact closure, even if Fleet reports the REQ definition unchanged. Preserve old results as historical; re-import changed canonical definitions when required by the actual importer and rerun/review the affected assertions on the exact current candidate. A prose-only semantic change still updates the full-source binding and its amendment record.
- An editorial-only change may retain existing execution evidence when the independent impact review demonstrates unchanged meaning, commands, cases, scope, candidate and assertions. Record an explicit old-to-new source equivalence/reuse disposition for each reused evidence set; never relabel an old execution as having run against new bytes. Unaffected evidence may also be retained through a reviewed dependency-closure disposition. No pending or unreviewed impact assessment grants reuse.
- Operator grants retain their actual terms and artifact/scope bindings. A document-equivalence record cannot broaden hardware/recovery/publication permission. Qualification/catalog/profile/runtime changes still require the candidate and RC re-attribution rules; an editorial classification cannot disguise them.
- The authorized requirement-record owner imports the approved revision as required; exact atomic-import sequencing follows the I00/C01 paragraph in this REQ and `.omx/plans/oig-release-start-graph.json`. Draft syntax validation is not evidence of import readiness or product success.
- Enforce `.omx/plans/oig-release-applicability-contract.md`: full canonical case/AC population, source/definition/oracle bindings, reviewed amendments and actual accepted scope decisions govern applicability; no missing case means not applicable, excluded functionality never becomes PASS, retained safety/quality ACs remain mandatory. No reduced scope is accepted now.
- The guard enforces the full acceptance-population/applicability contract above. It records complete collection, execution/deselection identities, canonical mapping, immutable baseline reconciliation and accepted scope-decision hashes. Zero exit alone cannot establish applicability or completeness. Extend guard self-tests to require rejection of an unmapped case, dropped expected ID, stale definition/source/decision hash, missing amendment, fake accepted decision, unapproved functional deselection, safety/quality exclusion and a falsely green aggregate with an excluded functional AC marked PASS; include a legitimate future reduced-scope fixture whose excluded result stays NOT RUN/DEFERRED while all retained checks execute.
- The renamed guard test retains all prior skip/xfail/xpass, empty, missing-fixture and vacuous-oracle seeds, and adds manifest removal/hash/decision/scope-bypass, excluded safety/quality and false-PASS cases. Its synthetic accepted-decision fixture grants no real scope permission.
- Existing tests/support files follow `.omx/plans/oig-release-legacy-test-contract.md` and exact-base `.omx/plans/oig-release-legacy-test-inventory.json`: 37 Python modules, 31 test modules and 461 static test symbols, not collected/executed counts. W10-LEGACY has explicit per-file ownership/review; L00 freezes baseline/transition/handoff policy before H00 and affected legacy work, L01 reconciles real base/candidate collection and every reviewed semantic update before full-suite final acceptance. Retained legacy cases count for guard/coverage but do not prove a new canonical AC by default; fake legacy labels, unexplained removals/parameter loss or stale module/fixture hashes are rejected.
- Planned evidence: immutable candidate build log/digest, JUnit/coverage reports, lint/Pylint/mypy outputs and configured CI gating proof. Commands below are proposed; environment/tool availability is not established.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-23
priority: P0
acceptance:
  ### unit
  - name: runtime-suite-coverage-above-eighty
    cmd: python3 tools/run_release_quality_gate.py runtime-coverage
    expect: 0
  - name: acceptance-guard-rejects-false-green
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_acceptance_guard.py::test_manifest_mapping_hashes_removal_scope_exclusion_and_false_green_fail_closed
    expect: 0
  - name: runtime-pylint
    cmd: python3 -m pylint addon/oig-proxy
    expect: 0
  - name: python-lint
    cmd: python3 -m flake8 addon/oig-proxy tests/v2
    expect: 0
  ### integration
  - name: production-build-and-type-gates
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_release_quality_gates.py::test_production_build_mypy_identical_runtime_inputs_and_dockerfile_lint_propagate_failures
    expect: 0
  ### scenario
  - name: immutable-quality-evidence
    scenario: Review actual production-image build, unit/integration/E2E, Dockerfile lint, Python lint, runtime Pylint, mypy and line/branch coverage logs for the same candidate; mypy covers the same runtime paths with byte-identical documented inputs if layout isolation is needed, all required commands pass, unrounded runtime statement coverage is strictly above 80 percent on the complete candidate runtime manifest and branch metrics/changed-branch evidence are retained without an unapproved extra threshold with reviewed exclusions, and missing evidence or failure blocks candidate approval.
    expect: pass
```

## REQ 24 - Require scoped security OWASP checks and independent review

- Sources: S2, S4-S5 and current security workflow as reference. Dependencies: REQ 8-17, REQ 22-23.
- Run required changed-scope security, dependency/container/secret checks and approved OWASP-mapped abuse tests on the candidate. Map input validation, target/session authorization, replay, corruption, resource bounds, dependency risks and secret/publication handling to concrete assertions; no generic OWASP claim without named scope/results.
- Review existing workflow commands for swallowed exits and stale V1 tests before treating them as gates. Bandit/other scan output alone cannot replace behavioral authorization/replay tests or focused changed-path security review. Do not expand this to an unsolicited unrelated full-repository scan.
- Obtain pre-RC independent review of catalog semantics, identity migration, transaction/profile boundaries, all seven regressions and every canonical AC/evidence mapping. Account for REQ 25-26 future hardware/publication gates explicitly as NOT RUN; do not require their future outcomes before prerelease admission. Reviewer must be independent of authors; later Fleet use must also satisfy the actually verified deployed independence contract.
- W10 owns `tools/run_release_quality_gate.py` and the C01 gate manifest. The security-supply-chain gate runs pinned dependency, production-image/container and secret scanners on the exact candidate/artifact with real unmasked exits; retain versions, scope, findings and reviewed dispositions. Its behavioral test injects each scanner failure/missing result and rejects a green aggregate. Configuration/exception changes require the same C01 definition-amendment review; no placeholder command satisfies this gate.
- Enforce `.omx/plans/oig-release-applicability-contract.md`: full canonical case/AC population, source/definition/oracle bindings, reviewed amendments and actual accepted scope decisions govern applicability; no missing case means not applicable, excluded functionality never becomes PASS, retained safety/quality ACs remain mandatory. No reduced scope is accepted now.
- The pre-RC reviewer reconciles every due retained AC/case and accepted scope decision against the original population. Missing/removed GRID cases cannot silently become not applicable; a reduced-scope recommendation explicitly names undelivered original functionality.
- Planned evidence: candidate-scoped scanner logs, dependency/secret disposition, OWASP abuse matrix and independent pre-RC findings/verdict. The named AC pre-rc-independent-ac-verdict gates REQ 25; REQ 26 owns a separate post-RC review AC. Neither passes on the other phase's evidence.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-24
priority: P0
acceptance:
  ### unit
  - name: owasp-control-abuse-matrix
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_security.py::test_target_authorization_replay_malformed_payload_corruption_and_resource_bounds
    expect: 0
  ### integration
  - name: production-static-security
    cmd: python3 -m bandit -r addon/oig-proxy -x addon/oig-proxy/tests -f json -o reports/catalog-bandit.json
    expect: 0
  - name: dependency-container-secret-gates
    cmd: python3 tools/run_release_quality_gate.py security-supply-chain
    expect: 0
  ### scenario
  - name: pre-rc-independent-ac-verdict
    scenario: Retain candidate-scoped dependency/container/secret and OWASP-mapped results and independent pre-RC review of every applicable implemented/offline AC; record later own-HA, post-RC review and publication ACs NOT RUN, retain all findings and scope decisions, and admit RC only with no missing required pre-RC evidence or unresolved blocking finding. No post-RC evidence is required or claimed by this criterion.
    expect: pass
```

## REQ 25 - Qualify an own-HA prerelease with recoverable identity

- Sources: S1-S5. Dependencies: REQ 4, REQ 13-16, REQ 22-23 and REQ 24's unit/integration security ACs and named pre-rc-independent-ac-verdict AC; separate explicit own-HA execution authorization remains mandatory. Own-HA/own-BOX first-RC scope is already agreed.
- Preserve current private image 3558 untouched during preparation. Before any future own-HA RC, capture immutable candidate/source/image/options identity, known prior/current image digests, discovery/entity registry/namespace/network/add-on identities and approved device/settings/planner boundaries. Engineering proposes evidence-based repeat/observation counts for operator hardware acceptance before execution.
- Test qualified MODE directions and individually approved AAC target with independent wire and profile-defined ACK-plus-event OR fresh-readback evidence, failure/uncertainty, ordinary new sessions, restart/reconnect and approved offline cases. Preserve confirmation_source; require fresh readback only for a fresh-readback claim/profile need, independent LCD observation for LCD claims and measured current for physical-current claims. Do not claim reliability from isolated historical success.
- An ordinary failed command/test retains the selected candidate test image. Stop new writes, retain evidence, retire/stop the uncertain request without replay, and use approved recovery on that same candidate. Prior-image rollback is limited to approved activation/runtime-health recovery or an explicit operator rollback instruction; never perform blanket image rollback merely because a test failed.
- Preserve IDs and keep legacy automations inactive during either recovery route. No automatic pending/uncertain resend. Image rollback cannot undo an executed BOX command; define explicit operator-approved setting restoration, passive forwarding/RO recovery checks and the evidence needed before any new deliberate request.
- W11 owns `docs/verification/own-ha-rc/recovery-policy.json`, frozen and independently reviewed before requesting own-HA execution authorization. Bind it to the candidate/prior image and define named observable predicates, evidence signals, numeric observation windows, allowed same-candidate recovery steps and the exact condition allowing prior-image restoration. The operator's own-HA decision must explicitly accept that policy; missing thresholds or acceptance blocks activation, not an ordinary test assertion.
- Eligible activation/runtime-health recovery predicates are candidate start failure within the approved startup window, inability to sustain the required transparent BOX/cloud forwarding or RO path within its approved health window, and attributable destructive identity/registry corruption that cannot be stopped through the approved same-candidate recovery. These predicates require retained evidence and the approved recovery policy; they are not inferred from a failed Setting, ACK timeout, parameter mismatch, stale table, refused unqualified control or an ordinary failing acceptance assertion. A duplicate-entity discovery defect blocks further migration/release and invokes approved same-candidate repair; it is not automatically an image-rollback trigger. The policy must classify that fixture explicitly.
- An unclassified failure stops new writes and preserves the candidate/evidence while requesting the missing operator decision; it never defaults to automatic prior-image rollback. Recovery of image/options and deliberate restoration of an already changed BOX setting remain separate authorized actions.
- Planned evidence: simulator same-candidate and conditional image-recovery tests first; later separately authorized own-HA prerelease matrix, retained-candidate/recovery manifest and verified recovery report. No HA action is authorized or performed by this specification.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-25
priority: P0
acceptance:
  ### unit
  - name: rollback-manifest-preserves-identity
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_prerelease_rollback.py::test_image_options_namespace_registry_and_entity_identity_manifest
    expect: 0
  ### integration
  - name: failed-candidate-recovery-no-replay
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_prerelease_rollback.py::test_failed_command_retains_candidate_retires_uncertain_request_and_limits_image_rollback
    expect: 0
  ### scenario
  - name: separately-authorized-own-ha-prerelease
    scenario: After separate explicit own-HA execution authorization, run the already agreed own-BOX first-RC matrix with accepted repeat counts and profile-defined confirmation evidence; an ordinary failed test retains the candidate image and retires uncertain work through approved same-candidate recovery, prior-image rollback occurs only for approved activation/runtime-health recovery or explicit instruction, all identities/image3558 remain recoverable without replay, and incomplete hardware/recovery evidence blocks wider-release recommendation.
    expect: pass
```

## REQ 26 - Release through reviewed PRs with bounded cohort claims

- Sources: S1-S5 and preparation authority. Dependencies: REQ 1-25, plus its own post-rc-independent-evidence-verdict AC before pr-and-publication-governance; separate implementation/Fleet/remote/publication permissions as applicable.
- Own the independent post-RC review after REQ 25 separately-authorized-own-ha-prerelease passes on the exact candidate; review actual RC results and all applicable pre-publication evidence/scope decisions. Record the review's own verdict without requiring its own AC or later publication facts to have passed in advance.
- Reverify actual repository remote/account, server path, immutable execution base and deployed Fleet contract only when authorized after maintenance; preserve imported REQ/AC definition/source hashes and explicit evidence mapping. Syntax validation alone cannot authorize import/admission/launch.
- Land every remote product/document change through PR with correct account wrapper, independent review and required CI evidence. Never push directly to a protected/default target or promote all prototype changes wholesale. Keep unsupported cohorts/settings fail-closed and describe exactly what is qualified.
- Select version after compatibility audit; `2.2.0-rc.1` is a provisional proposal only. Prepare Czech release notes, supported profiles/settings, migration/rollback and known limits. Require own-HA RC evidence and separate publication approval before broader release; no implicit launch, HA test or publication approval follows from this draft.
- W12 records scope decisions under the required schema in `.omx/plans/oig-release-qualification-scope-contract.md`; verify exact operator text/reference, accepted status, AC/definition/source/scope binding, independent review and candidate/profile/catalog hashes. Reject proposed/rejected/stale/overbroad exclusions, missing operator proof, a deferred safety-baseline AC, excluded functional AC marked PASS, or inherited approval after candidate changes. Czech notes preserve every deferred requested function as NOT DELIVERED.
- List every approved released-permission restriction as a behavior regression with affected cohort, retained `_cfg` identity, visible blocked reason, rejected command behavior and requalification/recovery path. A missing operator decision for an affected permission migration blocks activation/publication; retaining an entity or blocking writes does not silently waive this release gate.
- Enforce `.omx/plans/oig-release-applicability-contract.md`: full canonical case/AC population, source/definition/oracle bindings, reviewed amendments and actual accepted scope decisions govern applicability; no missing case means not applicable, excluded functionality never becomes PASS, retained safety/quality ACs remain mandatory. No reduced scope is accepted now.
- Reject missing/unmapped canonical cases/results, population shrinkage without reviewed amendment and required actual operator proof, stale source/definition/oracle/decision hashes, excluded functionality marked PASS, safety/quality exclusion, hidden deselection and full-delivery claims from a reduced-scope aggregate.
- Planned evidence: traceability/quality/permission checklist with immutable candidate and target revision, reviewed PR/check links and later actual publication facts. Current IDs remain unimported, tests NOT RUN and Fleet deployed version unknown.

```fleet-req
id: oig-proxy-catalog-20261101-REQ-26
priority: P0
acceptance:
  ### unit
  - name: release-evidence-and-permission-boundaries
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_release_manifest.py::test_candidate_req_ac_quality_cohort_and_authorization_manifest_rejects_missing_evidence
    expect: 0
  ### integration
  - name: release-artifact-compatibility
    cmd: python3 -m pytest -q --strict-markers -o xfail_strict=true tests/v2/test_catalog_release_manifest.py::test_packaged_docs_catalog_profiles_identity_and_runtime_match_tested_candidate
    expect: 0
  ### scenario
  - name: post-rc-independent-evidence-verdict
    scenario: After the exact candidate passes the separately authorized own-HA prerelease AC, retain independent final review of actual RC outcomes and all applicable pre-publication ACs and scope decisions; every prior finding has an evidence-backed disposition, no required pre-publication evidence or blocker remains unresolved, the review verdict is retained, and later publication facts remain NOT RUN until publication occurs.
    expect: pass
  - name: pr-and-publication-governance
    scenario: After explicit stage-specific permissions, verify remote/account/base and deployed contract, trace every required AC and accepted deferral to the immutable candidate, obtain post-RC final independent evidence review before publication, land changes only through checked PRs, and record actual RC/publication revision and supported cohort; missing own-HA evidence, unresolved requested functional GRID acceptance without explicit scope deferral, final review or publication permission prevents broader release.
    expect: pass
```

## Draft readiness and remaining gates

- Defined: 26 proposed REQ IDs and 83 canonical named ACs: 29 unit, 27 integration, 9 E2E and 18 scenario. Counts are regenerated after explicit definition amendments; syntax and document review do not establish product acceptance.
- Not established: any implementation, proposed test existence/execution, passing quality/security result, implementation admission/import/launch, later execution base/server availability, HA prerelease or publication. The verified GitHub main snapshot is provenance only.
- Own-HA/own-BOX first-RC scope is agreed. Engineering still derives/reviews profile identifiability/threshold/freshness, offline evidence, confirmation and CI/security/build proposals; authorized research does not wait for operator-invented protocol thresholds. Actual hardware values/planner/repeat acceptance, migration exceptions, reduced GRID scope, recovery/rollback and publication decisions remain subject to the applicable operator scope approvals.
- Unknown field semantics retain truthful RO/raw/unknown observations and block unqualified WRITES. Requested functional GRID automation remains NOT DELIVERED until qualified/tested or explicitly deferred by the operator through a reviewed reduced scope; explanatory drafts alone are insufficient.
- Current authority permits Fleet critique and preparation-document corrections only. Implementation dispatch, product checks, hardware/HA actions and publication require their applicable separate explicit decisions; the agreed first-RC destination grants no current hardware permission.
