# Canvas Section Planner

This project is managed by a human and developed with the assistance of AI.

Canvas Section Planner is a browser-based Python application for assigning an uploaded allowlist of existing Canvas course sections to existing assignments with section-specific availability, due, and closing dates.

The bulk editor selects allowed sections and assignments across one or more provided courses. Dates can be entered once for every section-assignment combination or separately for each assignment across its selected sections.

The current scope is intentionally narrow: it does not create courses, sections, or assignments. Course and section IDs are extracted from Canvas section URLs or read from legacy ID columns in an uploaded CSV or Excel file; the application never requests Canvas-wide course or section lists.

## Safety model

Every Canvas write follows this sequence:

1. Parse the uploaded allowlist in memory and validate each exact course and section ID.
2. Read assignments and overrides only for the courses explicitly represented by those URLs or IDs.
3. Show a before/after preview in the web application.
4. Require explicit browser confirmation.
5. Re-read the affected assignments and reject stale previews.
6. Create and checksum a repository-local backup for every affected course under `.agent/backups/` before the first write.
7. Apply writes sequentially without blindly retrying uncertain writes.
8. Re-read Canvas and verify the requested dates and visibility.
9. Store an operation record and provide a rollback control.

Backups and operation records can contain Canvas course information. They are excluded from Git and never contain the API token.

## Sharing the project

Keep local `.env` files, Canvas reports, backups, operation records, logs, screenshots,
and workspace continuity notes private. `.gitignore` excludes the known local runtime
locations, but cannot protect files already tracked or sensitive files saved elsewhere.
Review all files and any existing Git history before sharing. Keep operational backups
securely outside Git: a fresh copy cannot recheck or roll back earlier operations without
the corresponding records and backups.

The included `.env.example` contains placeholders. A fresh copy can start without a
credential and show the configuration message, or run sample data with
`CANVAS_DEMO_MODE=true`. Live Canvas access requires local configuration and a valid
credential; never commit those values. Generate a unique local `SECRET_KEY`.

## Configuration

Copy `.env.example` to `.env` and configure the values there, or set them in the process/container environment. The application automatically reads the repository-local `.env` file at startup; existing process variables take precedence.

- `CANVAS_BASE_URL`: HTTPS root of the Canvas instance, such as `https://canvas.example.edu`.
- `CANVAS_API_TOKEN`: existing Canvas API credential. Keep it in the environment; never place it in source code or a URL.
- `SECRET_KEY`: a random application secret.
- `APP_HOST` and `APP_PORT`: optional; defaults are `127.0.0.1` and `8010`.
- `CANVAS_DEMO_MODE=true`: optional local sample-data mode that never contacts Canvas.

The Canvas user represented by the credential needs permission to view the course and sections and manage assignments.

## Course and section allowlist

Upload a `.csv` or `.xlsx` file through the web application. The recommended format has one `section_url` column:

```csv
section_url
https://canvas.example.edu/courses/3033/sections/9658
https://canvas.example.edu/courses/3034/sections/9720
```

The previous format remains supported:

```csv
course_id,section_id
42,201
42,202
```

- Section URLs must use the configured Canvas host and the path `/courses/<course_id>/sections/<section_id>`; a trailing slash is accepted.
- The URL automatically supplies both IDs. In the example above, the first row supplies course `3033` and section `9658`.
- Legacy numeric IDs should preferably be stored as text in Excel.
- A file may contain both formats. When a row supplies a URL and IDs, they must agree.
- Each row allows one section in one course.
- Duplicate rows are removed.
- Each section is verified to belong to the supplied course.
- Files are limited to 2 MB and 500 data rows; `.xls` is not supported.
- Upload contents are held in memory only and expire when the app restarts.
- Scope validation uses exact `GET /courses/:id` and `GET /sections/:id` reads. Assignment reads are limited to explicitly provided courses; preview also reads `GET /users/self/profile` for the API user's timezone, retaining only that field. Broad course and section list endpoints are not used.

A sample is available from **Download CSV template** on the page.

## SIS section reports with dates

The additional **SIS section report with start and end dates** import option accepts
CSV/XLSX reports with `course_id,section_id,start_date,end_date` columns. These IDs
are SIS identifiers, including numeric-looking SIS IDs; the original numeric Canvas
ID/section-link option remains the default. An optional `status` column must be
`active` for included rows. Extra columns such as `name` are ignored.

**Re-assessment sections only** is checked by default in this option. It includes
rows whose section ID contains both `re` and `assessment`, ignoring case. Uncheck
it to include all sections, then validate the file again. The summary shows the
excluded row count. Filtering happens before Canvas lookups.

The app resolves only the supplied SIS IDs using exact course/section reads,
requires matching SIS identifiers in the responses, verifies ownership, and keeps
the numeric Canvas IDs for assignment changes. The credential needs access to SIS
information in addition to course/assignment permissions. Missing/inaccessible
resources, mismatched ownership, conflicting duplicate rows, and invalid dates
stop import. No broad course/section scan or Canvas SIS import is performed.

Each section's `start_date` becomes the assignment override's `unlock_at`, and
`end_date` supplies `lock_at`. Both must be ISO timestamps with a timezone.
Before approval, the app applies Canvas's documented upstream `fancy_midnight`
behavior: when Due or Available until falls exactly at midnight in the API user's
timezone, Canvas stores the end of that same day (23:59:59). Available from is not
changed by this rule. Non-midnight timestamps retain their time. The preview shows
the normalized values and the API user's timezone; displayed dates use the browser
timezone, which is also labeled. Long windows such as 2030 end dates remain visible.
No start/end date entry is required. Windows timezone data is supplied by `tzdata`.

Since reports have no assignment due dates, the default is **Due one day before each
section's end date**. The app subtracts 24 hours from the imported end, then applies
Canvas's midnight rule to Due and Available until. For example, with API timezone
Australia/Sydney, an end of 30 December 2030 at 00:00 +11:00 gives Due 29 December
at 23:59:59 and Available until 30 December at 23:59:59. Preview, write, and fresh-read
verification use these same explicit timestamps, without a blanket time tolerance.
If the API user's timezone changes after preview, execution stops before backup/write.
The browser control also offers three other choices:
keep the existing section override due date (or the assignment default when no
section override exists), use the section end date, or explicitly set no due date
for the selected section override. An automatically calculated or preserved due
date outside the imported window blocks preview with an actionable error.
Imported start/end dates are enforced by the server, independently of browser
payload dates. Users still select assignments and may toggle **Remove Everyone**.
The existing 500-combination preview limit and all backup/verification/rollback
controls apply. A downloadable demo report is provided in this option.

## Recheck a saved operation

Use **Review a saved operation**, enter the full operation ID, and select **Load
operation**, then **Recheck Canvas (no changes)**. This reads the saved, checksummed
backups and every planned assignment/section plus visibility settings. Rechecks
perform Canvas GETs only; they never repeat writes or trigger rollback. Old previews
containing unadjusted midnight dates are interpreted using the current API user's
timezone. An operation is marked verified only if all expected dates/visibility
match. Real mismatches remain in recovery. The app shows expected/current values
for every mismatch. It also
states whether all targets were checked. The operation audit retains the previous
status/error and records the recheck with `remote_writes: 0`. Backup files and original
change records are preserved. Existing rollback still requires browser confirmation.

## Run locally

Python 3.11 or newer is recommended. After dependencies are installed, Windows users can double-click `Start Canvas.vbs` to start the app in the background and open the browser at the default port 8010. If already running, open the browser URL directly. Recreate `.venv` and reinstall the requirements after transferring the project; Python environments are machine-specific. The PowerShell launcher automatically uses the project environment and works from any working directory.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\start_app.ps1
```

Open `http://127.0.0.1:8010` in a browser. For a safe sample-data walkthrough:

```powershell
.\scripts\start_app.ps1 -Demo
```

All user actions occur in the web application; there are no operational CLI prompts.

## Run with a container

```powershell
docker compose up --build
```

Then open `http://127.0.0.1:8010`. Docker was not available in the initial development environment, so the container definition is supplied but local container execution remains unverified.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy app
.\.venv\Scripts\python.exe -m pytest
```

## Current behavior and limitations

- One preview can include multiple provided courses. Sections are paired only with assignments from their own course.
- Each preview expands selected sections and assignments within each course into explicit section-assignment combinations.
- **Same dates** copies one available, due, and closing schedule to every selected combination in every course; **Different by assignment** exposes a separate schedule for each assignment and applies it across that assignment's selected sections.
- A single preview is limited to 500 section-assignment combinations so backup, sequential writes, verification, and rollback remain bounded.
- Existing assignment overrides for recipients not selected in the current plan are preserved.
- Enabling “Remove the Everyone audience” sets `only_visible_to_overrides`; students covered by any preserved override may still see the assignment.
- Blank date fields clear that date on an existing section override. At least one of available, due, or close is required for each selected assignment.
- Browser-local date/time values are converted to ISO 8601 UTC timestamps before previewing.
- Multi-course execution remains sequential, but every course is backed up before the first write. Rollback covers overrides created or updated by the operation and the assignment visibility flag across all included courses. Concurrent edits can cause rollback verification to stop in a recovery state.
- Assignment creation, section creation, arbitrary assignment-specific date-file imports, legacy `.xls`, and quiz-specific authoring are outside the current version.

## API references

- [Canvas Assignments API](https://developerdocs.instructure.com/services/canvas/resources/assignments)
- [Canvas Courses API](https://developerdocs.instructure.com/services/canvas/resources/courses)
- [Canvas Sections API](https://developerdocs.instructure.com/services/canvas/resources/sections)
- [Canvas SIS ID lookups and escaping](https://developerdocs.instructure.com/services/canvas/basics/file.object_ids)
- [Canvas SIS CSV field definitions](https://developerdocs.instructure.com/services/canvas/sis/file.sis_csv)
- [Canvas user profile/timezone](https://developerdocs.instructure.com/services/canvas/resources/users)
- [Canvas midnight conversion](https://github.com/instructure/canvas-lms/blob/master/gems/canvas_time/lib/canvas_time.rb)
- [Assignment override date setters](https://github.com/instructure/canvas-lms/blob/master/app/models/assignment_override.rb)
- [Canvas Pagination](https://developerdocs.instructure.com/services/canvas/basics/file.pagination)
- [Canvas Throttling](https://developerdocs.instructure.com/services/canvas/basics/file.throttling)

