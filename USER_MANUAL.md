# Canvas Section Planner — User Manual

## What it does

The application assigns existing Canvas sections to existing assignments and gives each section its own available, due, and closing dates. One operation can include multiple provided courses, sections, and assignments. It presents a complete preview and creates verified backups before changing Canvas.

## Assign sections and dates

1. On Windows, double-click `Start Canvas.vbs` in the project folder to start the app and open your browser (default port 8010). If the app is already running, open `http://127.0.0.1:8010` directly.
2. Confirm the connection badge shows your Canvas host. A “Demo data” badge means Canvas will not be contacted.
3. Prepare a CSV or `.xlsx` file with a `section_url` header and one Canvas section link per row, for example `https://canvas.example.edu/courses/3033/sections/9658`. The existing `course_id,section_id` format is still accepted.
4. Select **Choose CSV or Excel**, choose the file, and then select **Validate allowed IDs**.
5. Review the imported file summary. The application checks only the exact IDs in the file and does not list all Canvas courses or sections.
6. Select **Load all provided courses**. Only courses represented in the uploaded file are loaded.
7. Select one or more sections across those courses. Use **Select all** or **Clear selection** when helpful.
8. Choose a date mode:
   - **Same dates**: enter one schedule that will be copied to every selected section and assignment combination in every included course. This is the default.
   - **Different by assignment**: enter a separate schedule beside each assignment; that assignment's schedule is applied to selected sections in its own course.
9. Select assignments across the provided courses. Use **Select all** or **Clear selection** when helpful. Sections are never paired with assignments from a different course.
10. Enter at least one date for every selected assignment:
   - **Available from**: when students can open or submit the assignment.
   - **Due**: when the assignment is due.
   - **Closes**: when submissions stop.
11. Leave **Remove the “Everyone” audience** selected when the assignment should only be available through Canvas assignment overrides. Existing overrides for other recipients are preserved and are reported in the preview.
12. Select **Preview changes**.
13. Review every assignment, section, action, previous date, and new date. Correct warnings before continuing if necessary.
14. Select the confirmation checkbox and then **Create backup & apply**.
15. Wait for the verified result. The operation is successful only after the application re-reads Canvas and confirms the change.

## Use a SIS section report with dates

1. Choose **SIS section report with start and end dates** under **Import option**.
2. Upload the existing CSV or XLSX report with `course_id`, `section_id`,
   `start_date`, and `end_date`. These are SIS course/section IDs in this option.
3. Leave **Re-assessment sections only** checked to include IDs containing both
   “re” and “assessment”, ignoring case. Uncheck it to include every section.
   Changing this filter requires validating the file again.
4. Select **Validate allowed IDs**, review the included/excluded counts, then
   **Load all provided courses**. The app checks exact SIS matches and ownership.
   If SIS information is unavailable, an administrator must check permissions.
5. Select sections and assignments. **Dates from uploaded report** is automatic:
   each section's start sets **Available from**, and its end sets **Closes**.
   You do not need to enter either date. Canvas converts exactly midnight Due and
   Available until to 11:59:59 pm on the same date in the API credential owner's
   timezone. Available from keeps its time. Review the final times in the preview,
   including long windows such as dates in 2030. The preview labels the API timezone
   and the browser timezone used to display dates.
6. Choose the due-date behavior. **Due one day before each section's end date**
   is the default: it subtracts 24 hours, then applies Canvas's midnight rule.
   For an end of 30 December 2030 at midnight +11:00 and API timezone Australia/Sydney,
   Due is 29 December at 11:59:59 pm and Available until is 30 December at 11:59:59 pm.
   Dates outside the imported availability window block preview. Alternatively, choose **Keep existing due dates**,
   **Use each section's end date as its due date** or **No due date**. These choices
   affect the selected section overrides; base assignment dates are not edited.
7. Keep or uncheck **Remove the Everyone audience**, as appropriate.
8. Preview and review all dates and visibility, then confirm **Create backup & apply**.
   The same verified backups, fresh-read verification, and rollback controls apply.

The original section-link/numeric-ID option and its manual date controls remain
available. The SIS report option does not create sections or assignments.

## Recheck a saved operation without changing Canvas

Use **Review a saved operation** to enter its full 32-character operation ID,
then choose **Load operation**. Choose **Recheck Canvas (no changes)** to check
all planned section dates and visibility against fresh Canvas reads and verified
backups. This works after an app restart and can resolve a Recovery required
status caused by the earlier midnight conversion mismatch. Rechecking does not
apply dates again or roll anything back. A genuine mismatch remains in recovery.
The result lists expected and current values for each difference and indicates
whether all planned targets were checked.
The original operation records and backups are retained.

## Roll back an operation

After a successful operation, select **Roll back this operation** and confirm the browser prompt. The application reverses only the changes recorded for that operation and then verifies Canvas against the backup.

If rollback verification fails, stop further changes and retain the operation and backup identifiers for investigation.

## Common messages

- **Canvas configuration required**: the application administrator must configure the Canvas URL and credential in the environment.
- **Scope file error**: correct the reported header, URL host/path, row, ID, ownership, size, or file-format problem and upload again.
- **Imported scope expired**: the application restarted; upload the allowlist again.
- **Canvas changed after the preview**: another edit occurred. Nothing was written; create a new preview.
- **Backup failure**: no Canvas write was attempted.
- **Authorization failure**: the configured Canvas user lacks access or the credential is no longer valid.
- **Rate limit**: wait before trying the read or preview again.
- **Verification failure**: the application stopped additional writes. Use the displayed operation record and rollback path.

## Limitations

This version works with existing courses, sections, and assignments. The original import option defines the allowed Canvas scope and uses manual schedules. The SIS report option also imports section start/end dates for assignment overrides; reports do not provide assignment-specific due dates. Existing overrides not selected in a plan are not deleted automatically. A multi-course preview is limited to 500 section-assignment combinations in total.

