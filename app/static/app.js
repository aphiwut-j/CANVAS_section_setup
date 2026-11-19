const state = { scope: null, courses: [], workspaces: [], sections: [], assignments: [], batch: null, operation: null, generation: 0 };
const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const response = await fetch(path, {
    ...options,
    headers: { ...(!isForm ? {"Content-Type": "application/json"} : {}), "X-Requested-With": "CanvasSectionPlanner", ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error?.message || "The request could not be completed.");
  return body;
}

function toast(message, isError = false) {
  const node = $("#toast"); node.textContent = message; node.className = `toast${isError ? " error" : ""}`; node.hidden = false;
  window.clearTimeout(toast.timer); toast.timer = window.setTimeout(() => { node.hidden = true; }, 6000);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

function toIso(value) { return value ? new Date(value).toISOString() : null; }

function selectedDateMode() {
  return document.querySelector('input[name="dateMode"]:checked').value;
}

function updateSelectionSummary() {
  const selectedAssignments = [...document.querySelectorAll(".assignment-checkbox:checked")];
  const selectedSections = [...document.querySelectorAll(".section-checkbox:checked")];
  const assignmentCount = selectedAssignments.length;
  const sectionCount = selectedSections.length;
  const courseIds = new Set([...selectedAssignments, ...selectedSections].map(input => input.dataset.courseId));
  const combinationCount = [...courseIds].reduce((total, courseId) => {
    const assignments = selectedAssignments.filter(input => input.dataset.courseId === courseId).length;
    const sections = selectedSections.filter(input => input.dataset.courseId === courseId).length;
    return total + (assignments * sections);
  }, 0);
  $("#assignmentSelectionCount").textContent = `${assignmentCount} assignment${assignmentCount === 1 ? "" : "s"} selected`;
  $("#sectionSelectionCount").textContent = `${sectionCount} section${sectionCount === 1 ? "" : "s"} selected`;
  $("#combinationCount").textContent = combinationCount
    ? `${courseIds.size} course${courseIds.size === 1 ? "" : "s"} · ${combinationCount} section-assignment preview item${combinationCount === 1 ? "" : "s"}`
    : "Select sections and assignments to build the preview.";
  $("#combinationCount").classList.toggle("limit-warning", combinationCount > 500);
}

function renderSectionOptions() {
  $("#sectionOptions").innerHTML = state.sections.map(section => `
    <label class="section-option">
      <input class="section-checkbox" type="checkbox" value="${escapeHtml(section.id)}" data-course-id="${escapeHtml(section.course_id)}">
      <span><strong>${escapeHtml(section.name || section.id)}</strong><small>${escapeHtml(section.course_name)}</small>${section.start_date ? `<small>${escapeHtml(section.sis_section_id)} · ${escapeHtml(section.start_date)} → ${escapeHtml(section.end_date)}</small>` : ""}</span>
    </label>`).join("");
  if (state.sections.length === 1) $(".section-checkbox").checked = true;
  document.querySelectorAll(".section-checkbox").forEach(input => input.addEventListener("change", updateSelectionSummary));
  updateSelectionSummary();
}

function renderAssignmentRows() {
  $("#assignmentRows").innerHTML = state.assignments.map(assignment => `
    <tr data-assignment-id="${escapeHtml(assignment.id)}" data-course-id="${escapeHtml(assignment.course_id)}">
      <td class="selection-column"><input class="assignment-checkbox" type="checkbox" value="${escapeHtml(assignment.id)}" data-course-id="${escapeHtml(assignment.course_id)}" aria-label="Select ${escapeHtml(assignment.name)} in ${escapeHtml(assignment.course_name)}"></td>
      <td><span class="assignment-name">${escapeHtml(assignment.name)}</span><span class="assignment-meta">${escapeHtml(assignment.course_name)} · ${assignment.published ? "Published" : "Draft"}</span></td>
      <td class="individual-date-cell" hidden><input class="unlock-input" type="datetime-local" aria-label="Available from for ${escapeHtml(assignment.name)}"></td>
      <td class="individual-date-cell" hidden><input class="due-input" type="datetime-local" aria-label="Due date for ${escapeHtml(assignment.name)}"></td>
      <td class="individual-date-cell" hidden><input class="lock-input" type="datetime-local" aria-label="Close date for ${escapeHtml(assignment.name)}"></td>
    </tr>`).join("");
  document.querySelectorAll(".assignment-checkbox").forEach(input => input.addEventListener("change", updateSelectionSummary));
  updateSelectionSummary();
}

function updateDateMode() {
  const different = selectedDateMode() === "different";
  const csv = selectedDateMode() === "csv";
  $("#sharedDatesPanel").hidden = different || csv;
  $("#csvDatesPanel").hidden = !csv;
  document.querySelectorAll(".individual-date-column, .individual-date-cell").forEach(node => { node.hidden = !different; });
  document.querySelectorAll(".individual-date-cell input").forEach(input => { input.disabled = !different; });
}

function collectCourseChanges() {
  const sectionInputs = [...document.querySelectorAll(".section-checkbox:checked")];
  if (!sectionInputs.length) throw new Error("Select at least one section.");
  const rows = [...document.querySelectorAll("#assignmentRows tr")].filter(row => row.querySelector(".assignment-checkbox").checked);
  if (!rows.length) throw new Error("Select at least one assignment.");
  const sameDates = selectedDateMode() === "same";
  const courseIds = new Set([...sectionInputs, ...rows].map(item => item.dataset.courseId));
  const courseChanges = [...courseIds].map(courseId => {
    const sectionIds = sectionInputs.filter(input => input.dataset.courseId === courseId).map(input => input.value);
    const courseRows = rows.filter(row => row.dataset.courseId === courseId);
    if (!sectionIds.length || !courseRows.length) {
      const course = state.courses.find(item => String(item.id) === courseId);
      throw new Error(`Select both sections and assignments for ${course?.name || `course ${courseId}`}.`);
    }
    return {
      course_id: courseId,
      changes: courseRows.flatMap(row => sectionIds.map(sectionId => ({
        assignment_id: row.dataset.assignmentId,
        section_id: sectionId,
        unlock_at: toIso(sameDates ? $("#sharedUnlockAt").value : row.querySelector(".unlock-input").value),
        due_at: toIso(sameDates ? $("#sharedDueAt").value : row.querySelector(".due-input").value),
        lock_at: toIso(sameDates ? $("#sharedLockAt").value : row.querySelector(".lock-input").value),
      }))),
    };
  });
  const total = courseChanges.reduce((sum, course) => sum + course.changes.length, 0);
  if (total > 500) throw new Error("A preview is limited to 500 section-assignment combinations. Select fewer sections or assignments.");
  return courseChanges;
}

function dateStack(dates) {
  if (!dates) return '<span class="date-stack"><em>Not assigned</em></span>';
  const format = value => value ? new Intl.DateTimeFormat(undefined, {dateStyle:"medium", timeStyle:"medium"}).format(new Date(value)) : "—";
  return `<span class="date-stack"><span>Open: ${format(dates.unlock_at)}</span><span>Due: ${format(dates.due_at)}</span><span>Close: ${format(dates.lock_at)}</span></span>`;
}

function showPreview(batch) {
  state.batch = batch;
  const changes = batch.items.filter(item => item.action !== "no_change").length;
  const assignments = new Set(batch.items.map(item => `${item.course_id}:${item.assignment_id}`)).size;
  $("#previewSummary").innerHTML = `<span><strong>${batch.items.length}</strong> section assignments</span><span><strong>${changes}</strong> Canvas changes</span><span><strong>${batch.courses.length}</strong> courses</span><span><strong>${assignments}</strong> assignments</span>`;
  $("#previewWarnings").innerHTML = batch.warnings.map(message => `<p class="warning">${escapeHtml(message)}</p>`).join("");
  $("#previewTimezone").textContent = `Dates displayed in your browser timezone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}.`;
  $("#previewRows").innerHTML = batch.items.map(item => {
    const section = state.sections.find(section => String(section.id) === String(item.section_id) && String(section.course_id) === String(item.course_id));
    return `<tr><td>${escapeHtml(item.course_name)}<small class="assignment-meta">Course #${escapeHtml(item.course_id)}</small></td><td>${escapeHtml(item.assignment_name)}<small class="assignment-meta">Assignment #${escapeHtml(item.assignment_id)}</small></td><td>${escapeHtml(item.section_name)}<small class="assignment-meta">Section #${escapeHtml(item.section_id)}${section?.sis_section_id ? ` · ${escapeHtml(section.sis_section_id)}` : ""}</small></td><td><span class="action-pill action-${item.action}">${item.action.replace("_", " ")}</span></td><td>${dateStack(item.before)}</td><td>${dateStack(item.after)}</td></tr>`;
  }).join("");
  $("#confirmPreview").checked = false; $("#applyButton").disabled = true;
  $("#scheduleCard").hidden = true; $("#previewCard").hidden = false; $("#resultCard").hidden = true;
  $("#previewCard").scrollIntoView({behavior:"smooth", block:"start"});
}

async function loadStatus() {
  try {
    const status = await api("/api/status");
    const badge = $("#connectionBadge");
    if (!status.configured) {
      $("#configurationBanner").hidden = false;
      $("#uploadScopeButton").disabled = true;
      badge.textContent = "Not configured";
      return;
    }
    $("#configurationBanner").hidden = true;
    $("#uploadScopeButton").disabled = false;
    badge.textContent = status.demo_mode ? "Demo data" : `Connected · ${status.canvas_host}`; badge.classList.add("connected");
  } catch (error) {
    $("#uploadScopeButton").disabled = true;
    $("#connectionBadge").textContent = "Not configured"; toast(error.message, true);
  }
}

async function uploadScope() {
  const input = $("#scopeFile");
  if (!input.files.length) return toast("Choose a CSV or Excel file first.", true);
  const button = $("#uploadScopeButton"); button.disabled = true; button.textContent = "Validating exact IDs…";
  const form = new FormData(); form.append("file", input.files[0]);
  form.append("import_mode", $("#importMode").value);
  form.append("reassessment_only", String($("#reassessmentOnly").checked));
  invalidateScope();
  const generation = state.generation;
  try {
    const data = await api("/api/scopes", {method:"POST", body:form});
    if (generation !== state.generation) return;
    state.scope = data.scope;
    state.courses = data.scope.courses.map(item => item.course);
    const sectionCount = data.scope.courses.reduce((total, item) => total + item.sections.length, 0);
    $("#importedCourseList").innerHTML = state.courses.map(course => `<span>${escapeHtml(course.name)} <small>#${escapeHtml(course.id)}</small></span>`).join("");
    $("#scopeSummary").innerHTML = `<span class="scope-file-name">${escapeHtml(data.scope.source_name)}</span> · ${state.courses.length} allowed course(s) · ${sectionCount} allowed section(s)`;
    $("#scopeSummary").hidden = false; $("#scopedCourseControl").hidden = false;
    if (data.scope.import_mode === "sis_dates") {
      $("#scopeSummary").append(document.createTextNode(` · ${data.scope.skipped_rows} row(s) excluded by filter · CSV dates enabled`));
    }
    $("#scheduleCard").classList.add("is-disabled");
    toast("Allowed IDs validated. No Canvas-wide list was requested.");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "Validate allowed IDs"; }
}

async function loadWorkspace() {
  const button = $("#loadCourseButton"); button.disabled = true; button.textContent = "Loading…";
  try {
    if (!state.scope) return toast("Upload and validate a scope first.", true);
    const generation = state.generation;
    const data = await api(`/api/scopes/${state.scope.id}/workspace`);
    if (generation !== state.generation) return;
    state.workspaces = data.courses;
    state.sections = data.courses.flatMap(workspace => workspace.sections.map(section => ({...section, course_name:workspace.course.name})));
    state.assignments = data.courses.flatMap(workspace => workspace.assignments.map(assignment => ({...assignment, course_id:String(workspace.course.id), course_name:workspace.course.name})));
    const csv = state.scope.import_mode === "sis_dates";
    $("#csvDateModeOption").hidden = !csv;
    $("#dateModeCsv").disabled = !csv;
    [$("#dateModeSame"), $("#dateModeDifferent")].forEach(input => { input.disabled = csv; input.closest("label").hidden = csv; });
    $(csv ? "#dateModeCsv" : "#dateModeSame").checked = true;
    renderSectionOptions(); renderAssignmentRows(); updateDateMode();
    $("#workspaceSummary").textContent = `${data.courses.length} course(s) · ${state.sections.length} sections · ${state.assignments.length} assignments`;
    $("#workspaceSummary").hidden = false; $("#scheduleCard").classList.remove("is-disabled");
    $("#scheduleCard").scrollIntoView({behavior:"smooth", block:"start"});
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "Load all provided courses"; }
}

async function preview() {
  const button = $("#previewButton"); button.disabled = true; button.textContent = "Building preview…";
  try {
    const generation = state.generation;
    const courseChanges = collectCourseChanges();
    const data = await api("/api/batches", {method:"POST", body:JSON.stringify({scope_id:state.scope?.id, course_changes:courseChanges, due_policy:$("#csvDuePolicy").value, restrict_assignments:$("#restrictAssignments").checked})});
    if (generation === state.generation) showPreview(data.batch);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = 'Preview changes <span aria-hidden="true">→</span>'; }
}

async function applyPlan() {
  if (!state.batch) return;
  const button = $("#applyButton"); button.disabled = true; button.textContent = "Backing up & applying…";
  try {
    const data = await api(`/api/batches/${state.batch.id}/execute`, {method:"POST", body:JSON.stringify({confirmation:state.batch.id})});
    state.operation = data.operation; showResult(data.operation, false);
  } catch (error) { toast(error.message, true); button.disabled = false; button.textContent = "Create backup & apply"; }
}

function showResult(operation, rolledBack) {
  rolledBack = rolledBack || operation.status === "rolled_back";
  $("#previewCard").hidden = true; $("#resultCard").hidden = false;
  const recovery = operation.status === "recovery_required";
  const verified = operation.status === "verified";
  $("#resultIcon").textContent = verified || rolledBack ? "✓" : "!";
  $("#resultTitle").textContent = rolledBack ? "Changes rolled back" : (recovery ? "Recovery required" : verified ? "Changes verified" : `Operation ${operation.status.replaceAll("_", " ")}`);
  $("#resultMessage").textContent = rolledBack
    ? "Canvas was re-read and matches the pre-change backup."
    : (recovery
      ? `The final Canvas state could not be verified. Further writes stopped. ${operation.error || "Use the verified backup to roll back."}`
      : verified ? "Canvas was re-read and every requested section date matches the preview."
      : operation.error || "This operation has not been verified. Inspect its status before continuing.");
  const backupCount = operation.backup_ids ? Object.keys(operation.backup_ids).length : 1;
  $("#resultDetails").innerHTML = `<div><dt>Operation</dt><dd>${escapeHtml(operation.id)}</dd></div><div><dt>Backups</dt><dd>${backupCount}</dd></div><div><dt>Status</dt><dd>${escapeHtml(operation.status.replace("_", " "))}</dd></div>`;
  const recoverable = ["verified", "recovery_required"].includes(operation.status);
  $("#rollbackButton").hidden = !recoverable;
  $("#rollbackButton").disabled = false; $("#rollbackButton").textContent = "Roll back this operation";
  $("#recheckOperationButton").hidden = !recoverable;
  $("#savedOperationId").value = operation.id;
  const differences = rolledBack ? [] : (operation.verification_differences || []);
  $("#recheckDifferences").hidden = !differences.length;
  $("#recheckDifferenceSummary").textContent = `${differences.length} difference(s) found. ${operation.verification_complete ? "All planned targets were checked." : "The recheck did not finish."} No Canvas changes were made by the recheck.`;
  const differenceValue = (item, key) => item.kind === "dates" ? dateStack(item[key]) : (item[key] ? "Everyone removed" : "Everyone available");
  $("#recheckDifferenceRows").innerHTML = differences.map(item => `<tr><td>${escapeHtml(item.course_name)}<small class="assignment-meta">#${escapeHtml(item.course_id)}</small></td><td>${escapeHtml(item.assignment_name)}<small class="assignment-meta">#${escapeHtml(item.assignment_id)}</small></td><td>${escapeHtml(item.section_name)}<small class="assignment-meta">${item.section_id ? `#${escapeHtml(item.section_id)}` : ""}</small></td><td>${differenceValue(item, "expected")}</td><td>${differenceValue(item, "actual")}</td></tr>`).join("");
  $("#resultCard").scrollIntoView({behavior:"smooth", block:"start"});
}

async function loadOperation() {
  const operationId = $("#savedOperationId").value.trim();
  if (!/^[a-f0-9]{32}$/.test(operationId)) return toast("Enter the complete 32-character operation ID.", true);
  const button = $("#loadOperationButton"); button.disabled = true;
  try {
    const data = await api(`/api/operations/${operationId}`);
    state.operation = data.operation; showResult(data.operation, false);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

async function recheckOperation() {
  if (!state.operation) return;
  const button = $("#recheckOperationButton"); button.disabled = true; button.textContent = "Reading Canvas…";
  $("#rollbackButton").disabled = true;
  try {
    const data = await api(`/api/operations/${state.operation.id}/recheck`, {method:"POST"});
    state.operation = data.operation; showResult(data.operation, false);
    toast(data.operation.status === "verified" ? "Saved operation verified. No Canvas changes were made." : "Dates still differ. No Canvas changes were made.", data.operation.status !== "verified");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "Recheck Canvas (no changes)"; $("#rollbackButton").disabled = false; }
}

async function rollback() {
  if (!state.operation || !window.confirm("Roll back every change made by this operation? Canvas will be re-read afterward.")) return;
  const button = $("#rollbackButton"); button.disabled = true; button.textContent = "Rolling back…";
  try {
    const data = await api(`/api/operations/${state.operation.id}/rollback`, {method:"POST", body:JSON.stringify({confirmation:state.operation.id})});
    state.operation = data.operation; showResult(data.operation, true);
  } catch (error) { toast(error.message, true); button.disabled = false; button.textContent = "Roll back this operation"; }
}

function invalidateScope() {
  state.generation += 1;
  state.scope = null; state.batch = null; state.workspaces = []; state.sections = []; state.assignments = [];
  $("#scheduleCard").hidden = false;
  $("#scheduleCard").classList.add("is-disabled");
  ["#previewCard", "#resultCard", "#scopeSummary", "#scopedCourseControl", "#workspaceSummary"].forEach(id => { $(id).hidden = true; });
  $("#sectionOptions").replaceChildren(); $("#assignmentRows").replaceChildren();
}

function updateImportOption() {
  invalidateScope();
  const sis = $("#importMode").value === "sis_dates";
  $("#sisImportOptions").hidden = !sis;
  $("#sisScopeHelp").hidden = !sis;
  $("#standardScopeHelp").hidden = sis;
  $("#scopeFormatHint").textContent = sis
    ? "SIS course_id, section_id, start_date, end_date · Maximum 500 rows"
    : "Use section_url or course_id,section_id · Maximum 500 rows";
}

document.addEventListener("DOMContentLoaded", () => {
  loadStatus();
  $("#scopeFile").addEventListener("change", event => {
    invalidateScope();
    const file = event.target.files[0];
    if (file) $(".upload-box strong").textContent = file.name;
  });
  $("#importMode").addEventListener("change", updateImportOption);
  $("#reassessmentOnly").addEventListener("change", invalidateScope);
  $("#uploadScopeButton").addEventListener("click", uploadScope);
  $("#loadCourseButton").addEventListener("click", loadWorkspace);
  document.querySelectorAll('input[name="dateMode"]').forEach(input => input.addEventListener("change", updateDateMode));
  $("#selectAllSections").addEventListener("click", () => { document.querySelectorAll(".section-checkbox").forEach(input => { input.checked = true; }); updateSelectionSummary(); });
  $("#clearSections").addEventListener("click", () => { document.querySelectorAll(".section-checkbox").forEach(input => { input.checked = false; }); updateSelectionSummary(); });
  $("#selectAllAssignments").addEventListener("click", () => { document.querySelectorAll(".assignment-checkbox").forEach(input => { input.checked = true; }); updateSelectionSummary(); });
  $("#clearAssignments").addEventListener("click", () => { document.querySelectorAll(".assignment-checkbox").forEach(input => { input.checked = false; }); updateSelectionSummary(); });
  $("#previewButton").addEventListener("click", preview);
  $("#backButton").addEventListener("click", () => { $("#previewCard").hidden = true; $("#scheduleCard").hidden = false; });
  $("#confirmPreview").addEventListener("change", event => { $("#applyButton").disabled = !event.target.checked; });
  $("#applyButton").addEventListener("click", applyPlan);
  $("#rollbackButton").addEventListener("click", rollback);
  $("#loadOperationButton").addEventListener("click", loadOperation);
  $("#recheckOperationButton").addEventListener("click", recheckOperation);
  const savedOperation = new URLSearchParams(window.location.search).get("operation");
  if (savedOperation && /^[a-f0-9]{32}$/.test(savedOperation)) {
    $("#savedOperationId").value = savedOperation; loadOperation();
  }
});
