# AGENT.md — Canvas API Web Application

## 1. Project mission

This repository is a Python-based web application whose purpose is to let users work with **Canvas LMS through the Canvas API**.

The project is **web-app first**:

- All user-facing interaction MUST happen in the web application.
- Do not require users to operate the terminal, CLI, shell scripts, Python REPL, or direct HTTP tools.
- Do not make the Canvas web UI the primary interaction surface when the requested capability can be performed through the Canvas API.
- The agent may use development tools internally, but those tools are never a substitute for the application's user interface.
- User actions, approvals, confirmations, status, errors, and results should be represented in the web UI.

The Canvas API credential already exists. Never ask the user to paste the credential into chat, source code, logs, commits, screenshots, or browser-visible output.

## 2. Source of truth and operating principles

This file incorporates the project-wide guidance from the supplied `agents.md` and `base.md` files. Preserve their core principles:

- Start with read-only exploration and analysis.
- Prefer small, reviewable, reversible changes.
- Preserve existing project conventions.
- Use OOP and MVC-style separation where it improves clarity.
- Prefer small modules with narrow responsibilities.
- Use containers for project tooling when the repository supports or requires them.
- Keep documentation current.
- Maintain `.agent/CONTINUITY.md` as the canonical high-signal workspace briefing.
- Never expose secrets.
- Verify code changes with the strongest practical checks: format, lint, tests, type checking, build, and startup/browser verification as applicable.

For the detailed baseline, see the supplied guidance: the project profile and core engineering rules emphasize extensibility, separation of concerns, and reversible changes. [Source: supplied `agents.md`]

## 3. Mandatory research-first rule

**Research is required before implementation or any external state change.**

For every non-trivial task:

1. Read `.agent/CONTINUITY.md` first, if it exists.
2. Identify the requested outcome and acceptance criteria.
3. Inspect the relevant repository files, tests, routes, services, and documentation before editing.
4. Determine whether Canvas API behavior, permissions, endpoint details, or dependency versions may have changed.
5. Consult the most recent **official Instructure/Canvas documentation first** for Canvas API behavior.
6. Cross-check a second reputable source when compatibility, permissions, destructive behavior, or migration risk matters.
7. Record important discoveries and decisions in `.agent/CONTINUITY.md`.

Do not rely on memory for current Canvas API behavior when official documentation is available.

### Canvas documentation priorities

Prefer, in order:

1. Current official Instructure Canvas API documentation.
2. Official Instructure release notes/changelogs and upstream repository documentation.
3. Reputable technical references only when official material does not answer the question.

When documentation has moved or an older Canvas URL redirects, prefer the current official documentation rather than preserving a stale URL.

## 4. Canvas API policy

### 4.1 Authentication and secrets

- Store the existing Canvas credential in the application's secret/configuration mechanism, never in source code.
- Never print, echo, serialize, commit, or expose the credential.
- Never place a Canvas access token in a URL query string unless a specific official API requirement makes it unavoidable; prefer the HTTP `Authorization: Bearer <token>` header.
- Redact authorization headers and secrets from application logs, test output, traces, error pages, screenshots, and telemetry.
- Do not request the user's token in chat.

Canvas's official OAuth documentation recommends the Authorization header and warns that putting access tokens in query strings or POST parameters increases the chance of leakage. Treat this as the project default. [Official source: Instructure Canvas OAuth2 documentation]

### 4.2 API abstraction

Keep Canvas communication behind a dedicated service/client layer rather than spreading HTTP calls throughout UI handlers.

Preferred separation:

```text
app/
├── controllers/        # Web request orchestration
├── services/
│   ├── canvas/         # Canvas API client and endpoint-specific services
│   ├── backup/         # Pre-change snapshot and rollback support
│   └── research/       # Optional source/documentation helpers
├── models/             # Local application/domain models
├── views/              # HTML/UI rendering
├── templates/          # Web UI
└── static/             # Browser assets
```

Adapt the structure to the existing framework rather than performing a large restructuring solely to match this example.

### 4.3 REST vs GraphQL

Prefer the Canvas REST API when a documented REST endpoint directly supports the operation. Use Canvas GraphQL only when it materially improves the requested workflow and the required schema/permissions are verified from current official documentation.

Treat GraphQL mutations as remote writes with the same dry-run, backup, approval, verification, and rollback requirements as REST writes.

## 5. Web-app-only interaction rule

The browser application is the only user interaction surface.

The UI MUST provide, where relevant:

- Resource search and selection.
- A clear description of what will change.
- Before/after information sufficient for informed approval.
- An explicit confirmation step for remote writes.
- Progress/status for long-running operations.
- Success/failure results.
- A visible link or action to inspect the backup/operation record where appropriate.
- A rollback control for supported operations.

The agent MUST NOT depend on prompts such as:

```text
Are you sure? [y/N]
Enter course ID:
Paste token:
Type DELETE to continue:
```

Those controls belong in the web UI, not the terminal.

## 6. Remote writes: mandatory safety gate

**Canvas writes are never routine side effects. They are controlled operations.**

Before any remote `POST`, `PUT`, `PATCH`, `DELETE`, GraphQL mutation, file upload, import, or other state-changing Canvas operation:

1. Research the current official endpoint documentation.
2. Resolve the exact Canvas resource(s) and scope/context.
3. Perform read-only discovery of the current state.
4. Produce a dry-run/diff of the intended change.
5. Obtain the user's explicit approval through the web application.
6. **Create and verify a backup/rollback artifact before the write.**
7. Record the operation and backup identifier.
8. Execute the smallest possible write.
9. Re-read the affected resource(s) from Canvas and verify the expected result.
10. Record the outcome in the operation log and `.agent/CONTINUITY.md` when materially relevant.

If any required precondition fails, **do not write**.

### No silent writes

A read-only request must remain read-only. Do not opportunistically modify data, "fix" unrelated inconsistencies, normalize records, or perform cleanup that the user did not request.

## 7. Mandatory backup-before-change policy

**No Canvas state-changing operation may occur until a backup file exists and has been verified.**

This is a hard requirement, not a best-effort preference.

### 7.1 What the backup must contain

For each affected resource, capture enough information to reconstruct the previous state using documented Canvas API operations, including where applicable:

- UTC timestamp.
- Canvas base URL/host identifier, without secrets.
- HTTP method and endpoint template.
- Request path and resource IDs.
- Relevant query parameters.
- The complete pre-change response/body for the affected resource.
- Related/nested objects that are required for rollback.
- For file/content changes, the original file/content itself when technically possible, plus metadata needed for restoration.
- The planned change/diff.
- A rollback procedure or endpoint sequence.
- Backup format/version.
- Integrity information such as a checksum when practical.

Never store authorization headers or tokens in the backup.

### 7.2 Backup locations

Use a dedicated repository-local operational area, for example:

```text
.agent/
├── backups/
│   ├── <timestamp>_<operation-id>/
│   │   ├── manifest.json
│   │   ├── before.json
│   │   ├── before-content.*       # only when needed for file/content rollback
│   │   └── rollback.md
│   └── ...
└── CONTINUITY.md
```

If the backup is sensitive, apply appropriate filesystem permissions and ensure it is excluded from source control. Never commit Canvas user data or credentials accidentally.

### 7.3 Backup verification

A backup is considered valid only after the agent verifies that:

- The file(s) exist.
- The expected resource identifiers are present.
- The captured state is non-empty and structurally valid.
- The backup matches the resource state just read from Canvas.
- The rollback procedure is known or explicitly marked unsupported.

If verification fails, stop before the write.

### 7.4 Rollback-first design

Every write feature should be designed together with its rollback path.

Before implementing a new write capability, answer:

- What exact state changes?
- What exact state is captured before the change?
- Which Canvas endpoint(s) can restore it?
- Are there fields that cannot be restored exactly?
- Are there asynchronous jobs whose final state must be checked?
- What happens if the write partially succeeds?

If an operation cannot be safely rolled back, classify it as **non-reversible** and require an additional explicit confirmation in the web UI before execution. Never pretend a JSON snapshot guarantees full rollback when the API semantics do not support it.

## 8. Destructive operations

Treat the following as high risk:

- Delete operations.
- Bulk updates/deletes.
- Grade or submission changes.
- Enrollment or permissions changes.
- Course content replacement or migration.
- File replacement/deletion.
- Developer key/access-token administration.
- Operations affecting many users/courses.

For high-risk operations, additionally require:

- Exact target enumeration.
- A preview of the target count and representative identifiers.
- A complete backup of every target where rollback is supported.
- An explicit high-risk confirmation in the web UI.
- Post-write verification and a rollback plan.

Prefer bounded batches over unbounded bulk writes.

## 9. Pagination, throttling, and reliability

Canvas endpoints commonly return paginated collections. Implement pagination explicitly instead of assuming one response contains all results.

Where the endpoint supports it:

- Request a reasonable `per_page` value.
- Follow the API's pagination links/metadata until the requested result set is complete.
- Avoid silently truncating results.

Canvas API requests are throttled. The client must:

- Respect Canvas throttling/rate-limit signals and response headers.
- Avoid unnecessary polling and duplicate requests.
- Prefer sequential requests for mutation-heavy workflows unless official documentation and the endpoint semantics support safe concurrency.
- Use bounded retries with backoff for transient failures.
- Never retry a write blindly when the operation may have succeeded but the response was lost. Re-read the resource and reconcile state before retrying.

## 10. Idempotency and partial failure

For every write operation:

- Determine whether the Canvas endpoint is idempotent.
- If the operation is not naturally idempotent, implement application-level safeguards against duplicate execution.
- Persist an operation ID before the remote write when practical.
- Associate the operation ID with its backup and verification records.
- On timeout/connection failure, do not assume failure. Query Canvas to determine the actual state before retrying.

For multi-resource changes, prefer a sequence that can be resumed or rolled back safely. Do not imply transactional guarantees that Canvas does not provide.

## 11. Browser verification

Because all user interaction occurs through the web app, code changes affecting user workflows must be verified in the visual/browser environment when feasible.

Verification should cover:

- App starts successfully.
- Relevant route/page loads.
- Authenticated Canvas connectivity works without exposing credentials.
- Read-only data appears correctly.
- Write preview/diff is understandable.
- Confirmation is explicit.
- Backup status is visible or inspectable.
- Successful writes show verified results.
- Failure states are actionable and do not leak secrets.
- Rollback actions are clearly represented where supported.

## 12. Error handling and user experience

Errors should be safe, actionable, and understandable.

Do not expose:

- Access tokens.
- Authorization headers.
- Internal stack traces in production UI.
- Sensitive Canvas payloads unrelated to the user's task.

The UI should distinguish at least:

- Validation error.
- Authentication/authorization failure.
- Canvas API error.
- Rate limiting.
- Network/transient error.
- Backup failure.
- Write failure.
- Verification failure.
- Rollback failure.

A failed backup is a **hard stop** for any operation that would modify Canvas.

A failed post-write verification should enter a recovery state: stop further writes, inspect actual Canvas state, and offer the appropriate rollback/recovery path.

## 13. Documentation requirements

Maintain:

- `README.md` — setup, environment, launch, browser access, testing, Canvas configuration, and safety model.
- `USER_MANUAL.md` — user-facing workflows through the web app.
- `requirements.txt` — runtime dependencies.
- `requirements-dev.txt` — development/test tools when needed.
- `.agent/CONTINUITY.md` — canonical current-workspace briefing.
- `.agent/backups/` — operational rollback artifacts, excluded from source control when appropriate.

The README must explain:

- What the application does.
- How to configure the Canvas base URL/credential without exposing secrets.
- How to launch the web app.
- How to access it in a browser.
- Which operations are read-only vs write-capable.
- How the backup-before-change safeguard works.
- How to verify/rollback supported changes.
- How to run tests and checks.

## 14. Continuity rules

Maintain `.agent/CONTINUITY.md` as a living, compact, factual briefing.

Read it at the start of every task before acting.

Update it only when there is a meaningful change to:

- plans,
- decisions,
- progress,
- discoveries,
- outcomes,
- known limitations.

Every entry must include:

- ISO timestamp.
- Provenance tag: `[USER]`, `[CODE]`, `[TOOL]`, or `[ASSUMPTION]`.
- `UNCONFIRMED` when a fact is not verified.

Do not use the continuity file as a transcript. Compress old material into concise milestones.

## 15. Testing and definition of done

A task is complete only when:

- The requested behavior is implemented.
- Relevant tests/checks have been run.
- Build/type/lint checks have been attempted where applicable.
- Browser verification has been performed for affected user flows when feasible.
- Remote API behavior has been verified from fresh reads after a write.
- Backup/rollback behavior has been verified for any changed write path.
- Errors/warnings are resolved or explicitly documented as out of scope.
- Documentation is updated for impacted workflows.
- `.agent/CONTINUITY.md` is updated when the task materially changes project state or decisions.

For a new Canvas write capability, the minimum acceptance path is:

```text
Research -> Read current state -> Dry run -> Web confirmation
        -> Verified backup -> Write -> Re-read/verify -> Record outcome
        -> Roll back on failure when supported
```

## 16. What the agent must never do

Never:

- Expose the Canvas API key/token.
- Ask the user to paste credentials into chat or source code.
- Make an unpreviewed remote write.
- Make a remote write without a verified pre-change backup.
- Skip research because an endpoint "looks obvious".
- Skip post-write verification.
- Perform destructive or bulk actions without explicit web-app confirmation.
- Use terminal prompts as a substitute for the web UI.
- Assume a timed-out write failed without checking Canvas.
- Claim rollback is possible when the API semantics cannot guarantee restoration.
- Make unrelated cleanup/refactoring during a focused Canvas change unless necessary and documented.
- Commit backups containing sensitive Canvas data or secrets.

## 17. Recommended project structure

Use the existing repository structure when it is sound. Otherwise, prefer a structure along these lines:

```text
repo/
├── AGENT.md
├── README.md
├── USER_MANUAL.md
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── .gitignore
├── .agent/
│   ├── CONTINUITY.md
│   └── backups/
├── app/
│   ├── __init__.py
│   ├── models/
│   ├── views/
│   ├── controllers/
│   ├── services/
│   │   ├── canvas/
│   │   └── backup/
│   ├── templates/
│   └── static/
├── tests/
├── scripts/
├── docs/
└── container/
```

Do not create this structure mechanically if the existing framework already provides a better convention.

## 18. Current Canvas research notes

These notes are deliberately limited to verified, high-value project rules and should be re-checked against current official documentation when implementing endpoint-specific behavior:

- Canvas official documentation recommends sending OAuth access tokens in the `Authorization: Bearer <ACCESS-TOKEN>` header and discourages query-string transport because it can increase token leakage risk.
- Canvas API requests are subject to throttling; clients should respect the API's throttling signals/headers and avoid aggressive request patterns.
- Canvas exposes both REST and GraphQL APIs. GraphQL mutations are state-changing operations and therefore fall under this project's same write safety controls.
- Canvas file uploads can involve multiple API steps and asynchronous processing; upload workflows must be treated as multi-step operations and verified to completion before being reported as successful.
- Canvas's documentation is generated from the Canvas source/API documentation and is actively updated; endpoint behavior should be researched from the current official docs at implementation time.

## 19. External references

Prefered sources when researching Canvas behavior:

- Canvas LMS REST API documentation: https://canvas.instructure.com/doc/api/all_resources.html
- Canvas OAuth2 documentation: https://canvas.instructure.com/doc/api/file.oauth.html
- Canvas GraphQL API documentation: https://canvas.instructure.com/doc/api/graph_ql.html
- Canvas file upload documentation: https://canvas.instructure.com/doc/api/file.file_uploads.html

These references were checked during preparation of this AGENT.md. The official documentation notes that Canvas API documentation has moved to the Instructure Developer Documentation Portal; prefer the current official destination when implementing new integrations.

