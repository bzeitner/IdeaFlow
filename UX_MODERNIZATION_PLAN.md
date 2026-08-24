# IdeaFlow UX Modernization Plan

## Purpose

Evolve IdeaFlow from a capable internal tracker into a consistent, approachable workspace that helps users answer three questions quickly:

1. What needs my attention?
2. What should happen next?
3. What changed recently?

This plan preserves the existing Current → Tracking → Archive lifecycle while improving navigation, filtering, personalization, accessibility, inline workflows, and artifact presentation.

## Product principles

- **Human-readable by default.** Show information in the format that is easiest to understand, not merely the format in which it was stored.
- **Raw data remains available.** A formatted view must not prevent users from inspecting or downloading the original artifact.
- **Consistent behavior everywhere.** Similar filters, saves, state changes, and feedback should behave the same across screens.
- **Progressive disclosure.** Prioritize the decision a user needs to make now; reveal operational detail on demand.
- **Defaults are transparent and controllable.** Distinguish application defaults, explicit account preferences, and temporary URL/view state.
- **Reversible actions should feel lightweight.** Prefer undo over confirmation when a change can be safely reversed.
- **Accessible by design.** Keyboard, screen-reader, contrast, reduced-motion, and mobile behavior are acceptance criteria rather than follow-up work.

## Scope

### Included

- Application shell and navigation
- Current, Tracking, Archive, Feeds, and Artifacts list experiences
- User Guide content, information architecture, and contextual help
- Idea creation, editing, and detail-page hierarchy
- Filtering, sorting, saved views, and user preferences
- Inline editing and lifecycle movement
- Artifact and summary-report presentation
- Loading, success, error, empty, and undo states
- Accessibility and responsive behavior

### Not initially included

- Changing the three structural lifecycle statuses
- Reworking agent execution, graph semantics, podcast generation, or permissions logic
- A full visual rebrand
- Dark mode before the core interaction and accessibility work is complete

## Confirmed product decisions

### Filters

Filters should auto-apply by default.

- Selects, toggles, and checkboxes apply immediately.
- Search applies after a 350–500 ms debounce.
- Pagination resets to page 1 after a filter change.
- The URL remains the canonical representation of the current view.
- A visible loading state and an `aria-live` result count confirm the update.
- An explicit Apply/Run action is retained only for expensive reports, complex multi-step query builders, or operations with meaningful execution cost.
- A non-JavaScript submit button remains as progressive enhancement.

Tracking already follows much of this model. The behavior should move from page-specific JavaScript into the shared application layer and be applied consistently to Current, Archive, and Feeds. Feeds currently declares auto-submit behavior without loading the script that implements it and should be corrected in the first phase.

### Preferences and saved views

IdeaFlow should add a Profile & Preferences page with server-side, cross-device settings.

Keep these concepts separate:

| Concept | Purpose | Storage |
| --- | --- | --- |
| Product default | Safe initial experience | Application configuration |
| Personal default | A stable user working preference | Profile/database |
| Current view | The filters being used now | URL query parameters |
| Remembered local state | Optional convenience for a browser/session | Local/session storage |

Initial preferences:

- Default landing page: Current, Tracking, Feeds, or Public Projects
- Default owner scope: My Ideas or All Owners
- Default sort for Tracking and Feeds
- Comfortable or compact list density
- Default new-idea status, category, and visibility
- Collapsed/expanded family preference
- Time zone and date format
- A reset-all-saved-views action

Every page using a personal default should make that state understandable and offer **Save as my default** when the user modifies it. Local storage must not silently behave like a permanent account preference.

### Artifact and report presentation

Artifacts should have a human-friendly primary view whenever the data can be represented reliably. Raw text is the primary view only when the artifact is explicitly intended to be raw text, a log, source material, or an unsupported format.

The artifact page should provide consistent view choices:

- **Formatted** — default when a safe renderer is available
- **Table** — default or alternate for clearly tabular data
- **Raw** — exact decoded source for inspection
- **Download original** — always available
- **Open externally** — for link-only artifacts

Do not generate a table simply because text contains delimiters. Use a table only when the content has a stable row/column structure and the table communicates it more clearly than prose.

#### Format behavior

| Source format/content | Default presentation | Alternate presentation |
| --- | --- | --- |
| HTML report | Sandboxed formatted HTML | Raw source, download |
| Markdown report/summary | Rendered headings, paragraphs, lists, links, callouts, and tables | Raw Markdown, download |
| CSV/TSV | Accessible table with sticky headers, sorting, search, and overflow handling | Raw text, download |
| JSON array of consistent objects | Table when the schema is flat and stable | Tree/structured view, raw JSON, download |
| Nested or irregular JSON | Collapsible structured/tree view | Raw JSON, download |
| YAML/XML | Structured view when reliable; otherwise syntax-preserved raw view | Raw source, download |
| Plain-text narrative report | Human-readable paragraphs with URL linking and preserved section breaks | Raw text, download |
| Log/source/raw-text artifact | Raw text with wrapping toggle, search, and line numbers where useful | Download |
| Summary artifact | Purpose-built human-readable summary view | Raw/source view, download |
| PDF/image/office document | Safe inline preview when supported | Download/open externally |

#### Report presentation contract

Reports should be authored or normalized into a predictable semantic structure:

- Title and generated date
- Executive summary
- Key findings
- Evidence or supporting detail
- Recommendations
- Risks or open questions
- Next actions
- Sources/references
- Optional metrics or tabular appendices

Not every section is required, but renderers should recognize these concepts when present. The page should provide a readable content width, a generated-on/by metadata area, clear section hierarchy, linked references, and responsive tables.

#### Summary report contract

Summary artifacts must always have a human-readable version.

- Prefer formatted HTML when a trusted generator intentionally provides it.
- Otherwise render Markdown into safe semantic HTML.
- If a summary arrives as plain text, convert line breaks, recognizable headings, lists, URLs, and sections into a readable presentation without changing the underlying meaning.
- Preserve the original source and expose it through Raw and Download actions.
- Do not expose JSON, YAML, or a serialization dump as the sole summary experience.
- If structured summary data is ingested, render a human-facing summary from the structure and retain the structured payload as the source view.

#### Rendering and security requirements

- Sanitize generated HTML with an explicit allowlist before display.
- Continue to sandbox uploaded HTML; do not grant scripts, same-origin access, popups, forms, or top navigation by default.
- Sanitize rendered Markdown and reject unsafe URL schemes.
- Escape all raw and structured values before inserting them into HTML.
- Apply size, row-count, nesting-depth, and render-time limits.
- For large tables, use pagination or virtualization rather than rendering every row.
- Detect character encoding conservatively and communicate replacement/decoding failures.
- Never infer that uploaded content is trusted because an agent created it.
- Ensure table headers use `<th>` and appropriate scope; add captions or accessible names.
- Preserve downloadable bytes exactly; formatting must be a presentation layer, not a destructive conversion.

#### Recommended data-model direction

Add explicit presentation metadata rather than relying only on filename extensions:

- `media_type` or detected content type
- `presentation_mode`: auto, report, table, structured, raw, embedded
- `source_format`: html, markdown, plain, csv, tsv, json, yaml, xml, etc.
- Optional structured metadata such as schema/version and generator
- Optional cached safe-render output with renderer version and source checksum

`presentation_mode=auto` should choose a renderer conservatively. Users with management permission may override a misclassification without replacing the source file.

## Target information architecture

### Primary workspace navigation

- Current
- Tracking
- Feeds
- Archive

### Persistent primary action

- New Idea

### Secondary workspace menu

- Artifacts
- Knowledge Graph / Graph Lab
- Weekly Summaries
- Podcasts
- Guide

### Administration menu

- Users
- Ownership
- Manage/configuration

### Profile menu

- Account identity
- My roles and access
- Preferences
- Guide/help
- Sign out

This separates everyday work from configuration and prevents the header from accumulating an unscannable row of equal-weight buttons. A compact desktop sidebar is the preferred scalable shell; mobile should expose three or four high-frequency destinations and an overflow menu.

## Screen-level changes

### Current

- Prioritize next action, blockers/open questions, owner, stage, and last meaningful update.
- Offer compact and comfortable density.
- Auto-apply owner/search controls.
- De-emphasize static description when an actionable next step exists.
- Surface upcoming repeat runs where relevant.

### Tracking

- Keep attention-first sorting and filtering.
- Reduce row density by showing title, attention state, next action, stage, owner, and recency by default.
- Move rank, archive, transfer, and infrequent actions into an overflow menu.
- Autosave next action, rank, and stage with Saving/Saved/Error feedback.
- Support Enter to save and Escape to revert inline text edits.
- Add active-filter chips and a clear modified/default state.

### Archive

- Optimize for retrieval rather than management.
- Default to most recently archived.
- Show archive date and outcome/reason when available.
- Make Restore the clear item action.
- Distinguish no archived content from no filter matches.

### Feeds

- Make declared auto-apply behavior functional and shared.
- Preserve pagination and filters in the URL.
- Keep rating actions inline and provide immediate save feedback.
- Improve scan hierarchy between source, associated ideas, summary, and ratings.

### Artifacts

- Add filters for idea, kind, source format, generated date, and viewability.
- Display format/view badges and a short preview or summary.
- Open viewable artifacts in their formatted view by default.
- Add consistent Formatted/Table/Raw/Download controls on the artifact page.
- Allow users to search within large text or table artifacts.
- Provide meaningful empty, unsupported-format, malformed-content, and oversized-content states.

### Idea form

- Keep progressive disclosure.
- Mark required fields clearly and improve field-level error presentation.
- Add an unsaved-change warning.
- Use profile preferences for sensible defaults.
- Clarify Summary, Notes, Executive Summary, Next Action, and Repeat Goal with concise help text.
- Add searchable controls when parent, artifact, or owner collections become large.

### Idea detail

- Lead with status, next action, blockers/questions, and primary actions.
- Group related research, artifacts, feeds, repeat results, and council information into scannable sections.
- Use progressive disclosure for historical or agent-operational detail.
- Display summary artifacts using the same human-friendly renderer as the Artifacts area.

### Guide and contextual help

The Guide must be updated alongside the interface so it describes the current product rather than preserving an earlier workflow. It should function as a task-oriented reference, not as a feature inventory.

Restructure the Guide around what users are trying to accomplish:

1. Getting started and understanding access
2. Capturing an idea
3. Choosing Current, Tracking, or Archive
4. Finding what needs attention
5. Updating next actions, stage, rank, and ownership
6. Using filters, saved views, and personal defaults
7. Reading research, artifacts, reports, and summaries
8. Working with feeds, repeat tasks, and the persona council
9. Understanding roles and administrative capabilities
10. Troubleshooting common states such as missing ideas, unexpected filters, failed saves, or unavailable actions

Guide requirements:

- Explain the difference between product defaults, profile preferences, URL view state, and locally remembered state.
- Document that ordinary filters auto-apply and identify the exceptional operations that still require Run or Apply.
- Explain active-filter chips, Reset, Save as my default, and how to recover when a remembered view hides expected content.
- Document Formatted, Table, Structured, Raw, Download, and external artifact views, including when each is used.
- State that summary reports always provide a human-readable view and that Raw exposes the underlying source.
- Explain inline Saving/Saved/Error feedback, keyboard save/revert behavior, lifecycle undo, and archive restoration.
- Show only actions available to the reader's role where practical; otherwise label role requirements clearly.
- Use plain language, short procedures, representative examples, and annotated UI images only where they materially reduce ambiguity.
- Provide a searchable table of contents and stable section anchors so screens can link directly to relevant help.
- Include a visible "Last updated" date and, when helpful, the application release/version covered.
- Remain fully usable on mobile and meet the same heading, contrast, keyboard, and link-name accessibility requirements as the rest of the product.

Contextual help should link to the relevant Guide section from:

- Empty states and no-results states
- Profile preferences and saved-view controls
- Idea creation's advanced settings
- Repeat-task and persona-council sections
- Artifact presentation controls and unsupported-format states
- Role/access explanations
- Errors that require user action rather than a retry

The Guide should not become the substitute for self-explanatory interface design. Use contextual copy for short, local explanations and link to the Guide for concepts, detailed procedures, and troubleshooting.

#### Guide maintenance contract

Every user-facing change must answer these questions during review:

- Does an existing Guide section now describe obsolete labels, navigation, behavior, or screenshots?
- Is the new behavior discoverable without documentation?
- Does it introduce a concept that needs a Guide section or contextual-help link?
- Are role differences and mobile behavior represented correctly?
- Do automated link and template tests still pass?

Guide updates should be included in the same pull request as the corresponding UI change. A feature is not complete when its documented workflow is inaccurate.

## Shared interaction patterns

### Inline saves

- Optimistically update safe, reversible fields.
- Show Saving…, Saved, and actionable error feedback.
- Revert the field if persistence fails.
- Avoid full-page reload and scroll loss.

### Lifecycle moves

- Use consistent labels: Move to Current, Move to Tracking, Archive, Restore.
- Do not confirm easily reversible moves.
- Show an undo toast after a move or archive.
- Confirm only destructive or difficult-to-recover operations.

### Filter state

- Encode current state in the URL.
- Show active filters visibly.
- Distinguish Using your default from Modified view.
- Provide Reset and Save as my default at appropriate times.
- Reset pagination after any filtering change.

### Feedback and system state

- Establish a consistent toast/status region.
- Add loading feedback for navigation-based auto-filtering.
- Preserve user input on validation and authentication errors.
- Use separate empty states for no data and no matches.

## Visual design direction

- Retain the calm neutral palette and blue brand accent.
- Establish explicit semantic tokens for success, warning, blocked, paused, destructive, and informational states.
- Validate configurable pill foreground/background contrast.
- Increase operational page width to approximately 1120–1200 px while keeping narrative report content near 700–800 px.
- Create a clearer type scale for page titles, section headings, item titles, metadata, and helper text.
- Give next actions and attention states more visual weight than rank or incidental metadata.
- Use depth sparingly; reserve elevated surfaces for floating or especially important controls.
- Refine the landing page around a product preview and lifecycle demonstration rather than mostly descriptive copy.

## Accessibility acceptance criteria

- A visible high-contrast `:focus-visible` treatment exists for every interactive element.
- Active navigation includes `aria-current="page"`.
- A skip-to-content link is available.
- All essential workflows are keyboard operable.
- Result counts and inline-save outcomes are announced through an appropriate live region.
- Important mobile targets are at least 44 × 44 CSS pixels or have equivalent spacing.
- Status is not communicated through color alone.
- User-configurable pill colors meet contrast requirements or receive an automatically selected accessible foreground/background treatment.
- Tables have semantic headers, captions/labels, and keyboard-accessible overflow behavior.
- Motion respects `prefers-reduced-motion`.
- Form errors are summarized and associated with their fields.
- Formatted artifacts retain meaningful heading order and link names.

## Delivery plan

### Phase 1 — Consistency and accessibility foundation

1. Move auto-submit logic into the shared application script.
2. Enable consistent auto-apply on Tracking, Current, Archive, and Feeds.
3. Add loading indicators, result-count announcements, active filter summaries, and pagination reset.
4. Add focus-visible styling, skip link, `aria-current`, reduced-motion rules, and semantic status feedback.
5. Standardize empty, error, save, and toast patterns.
6. Update the Guide's filtering and navigation sections for the shared behavior and add direct contextual links from filter empty states.
7. Add tests for query persistence, auto-submit behavior, keyboard focus, non-JavaScript fallback, and Guide anchors/links.

**Exit criteria:** Filters behave consistently on every list, remain URL-addressable, and are fully usable without a pointer or JavaScript.

### Phase 2 — Artifact presentation foundation

1. Add explicit artifact format/presentation metadata and migrations.
2. Introduce a renderer registry with conservative format detection.
3. Implement safe Markdown/report rendering.
4. Implement CSV/TSV accessible table rendering.
5. Implement JSON structured view and flat-object table view.
6. Add Raw and Download actions for every file-backed viewable artifact.
7. Create the purpose-built summary-report layout and make it the default for summary artifacts.
8. Add sanitization, size/depth/row limits, malformed-input handling, and security tests.
9. Add a Guide section covering formatted, table, structured, raw, and download views, with troubleshooting for unsupported or malformed artifacts.

**Exit criteria:** Every summary has a human-readable primary view; supported reports and tables render clearly; original source is always accessible.

### Phase 3 — Navigation and workflow efficiency

1. Separate primary workspace, secondary workspace, admin, and profile navigation.
2. Replace the raw email/logout header treatment with a profile menu.
3. Simplify Tracking rows and introduce density modes.
4. Add autosaving next-action, rank, stage, and feed-rating controls.
5. Add undoable lifecycle moves and archive actions.
6. Improve Current and Archive around their distinct jobs.
7. Restructure the Guide around task-oriented workflows and update all navigation, inline-save, lifecycle-move, and undo instructions.

**Exit criteria:** High-frequency work requires fewer clicks and less scanning, while administration remains easy to find without occupying primary navigation.

### Phase 4 — Profile preferences and saved defaults

1. Add server-side preference fields and a Profile & Preferences page.
2. Implement default landing page, owner scope, sorts, density, creation defaults, time zone, and family-collapse preferences.
3. Add Save as my default and Reset saved views controls.
4. Define migration behavior from existing browser-local preferences.
5. Explain current default/modified state in the UI.
6. Document preference precedence, Save as my default, Reset saved views, cross-device behavior, and migration from browser-local settings.

**Exit criteria:** Explicit preferences sync across devices, are visible and resettable, and never make filtered content appear mysteriously absent.

### Phase 5 — Visual refinement and responsive polish

1. Apply the revised typography, spacing, width, and semantic-color system.
2. Refine landing, report, card, list, and form hierarchy.
3. Validate narrow mobile, large mobile, tablet, laptop, and wide desktop layouts.
4. Perform contrast, keyboard, screen-reader, zoom, and reduced-motion reviews.
5. Refresh Guide examples and annotated images after the visual system stabilizes; verify desktop and mobile instructions.
6. Measure core workflow outcomes and iterate.

**Exit criteria:** The interface feels cohesive at every supported viewport and meets the accessibility acceptance criteria.

## Testing strategy

### Automated

- Unit tests for format detection and renderer selection
- Sanitization tests using malicious HTML, Markdown, URLs, SVG/XML, and malformed structured data
- Table-detection tests covering irregular rows, quoting, encoding, and very large inputs
- View tests for permission enforcement, raw view, formatted view, and download behavior
- Query/default precedence tests: URL → explicit profile default → product default
- JavaScript tests for debounce, pagination reset, save feedback, and undo
- Accessibility linting for templates and rendered components
- Guide link, stable-anchor, heading-structure, and role-conditional-content tests

### Manual

- Keyboard-only completion of all primary workflows
- Screen-reader review of navigation, filters, inline saves, tables, and reports
- 200% and 400% zoom checks
- Mobile touch-target and overflow review
- Representative artifact QA: narrative Markdown, complex HTML, wide CSV, nested JSON, malformed files, logs, and oversized inputs
- Confirm that formatted output never changes downloaded source bytes
- Complete primary Guide procedures against the current desktop and mobile interfaces and verify that labels and destinations match

## Measurement

Track before and after the rollout:

- Time/clicks to find an attention item and update its next action
- Filter changes abandoned before results appear
- Frequency of Clear/Reset immediately after page entry
- Full-page reloads per quick-update workflow
- Artifact View versus Download usage
- Raw-view usage after formatted view, which can reveal renderer failures
- Save/move error rate and undo rate
- Mobile task completion rate
- Accessibility issues found in automated and manual audits

## Rollout and migration

- Ship shared filter behavior before adding account preferences so interaction semantics stabilize first.
- Introduce artifact renderers behind per-format feature flags.
- Preserve the existing raw viewer as a fallback throughout rollout.
- Backfill artifact metadata conservatively from extensions; leave ambiguous artifacts in auto/raw mode.
- Do not rewrite artifact source files during migration.
- When server-side preferences launch, offer to adopt current saved browser settings or start from product defaults.
- Roll out summary rendering first, then reports, tables, and structured data.

## Definition of done

The modernization is complete when:

- Similar controls behave consistently across IdeaFlow.
- Users can understand and control their defaults.
- Primary workflows no longer depend on repeated Apply and Save buttons.
- Every summary report has a readable formatted experience.
- Tabular data is presented as an accessible table when that is the clearest representation.
- Raw source and original downloads remain available.
- Navigation cleanly separates daily work, secondary tools, administration, and account settings.
- The application meets the accessibility criteria above on desktop and mobile.
- The Guide is task-oriented, role-aware, current with the implemented UI, and reachable through relevant contextual-help links.
- User testing confirms that people can identify what needs attention and take the next action without instruction.
