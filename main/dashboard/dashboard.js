const FIELDS = [
  "shipper",
  "consignee",
  "notify_party",
  "port_of_loading",
  "port_of_discharge",
  "container_count",
  "gross_weight_kg",
];
const LABEL = {
  BL_COMPARISON: "Document comparison",
  SI_REQUEST: "SI request",
  INVOICE_QUERY: "Invoice query",
  GENERAL: "General",
  SPAM: "Spam",
  OK: "No mismatch detected",
  MISMATCH: "Mismatch detected",
  NEEDS_REVIEW: "Human review required",
  PROCESSING_FAILED: "Processing failed",
  PROCESSING: "Processing",
  NOT_APPLICABLE: "Not applicable",
};
const FILTERS = [
  ["all", "All"],
  ["PROCESSING", "Processing"],
  ["OK", "No mismatch"],
  ["MISMATCH", "Mismatch"],
  ["NEEDS_REVIEW", "Human review"],
  ["NOT_APPLICABLE", "Other emails"],
  ["PROCESSING_FAILED", "Failed"],
];
const $ = (id) => document.getElementById(id);
const node = (tag, text = "", className = "") => {
  const x = document.createElement(tag);
  x.textContent = text ?? "";
  x.className = className;
  return x;
};
const add = (parent, ...children) => {
  parent.append(...children);
  return parent;
};
const apiPath = (kind, id, index) =>
  "/api/" +
  kind +
  "/" +
  encodeURIComponent(id) +
  (index === undefined ? "" : "/" + index);
const button = (label, handler, className = "") => {
  const b = node("button", label, className);
  b.type = "button";
  b.onclick = handler;
  return b;
};
let state = {
  cases: {},
  decisions: {},
  metrics: {},
  features: {},
  page: "dashboard",
  selected: null,
  email: null,
  filter: "all",
  mismatch: 0,
};
async function request(url, body) {
  const response = await fetch(
    url,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed");
  return result;
}
function prettyField(field) {
  return (
    field?.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase()) || "—"
  );
}
function resultLabel(c) {
  return c.category !== "BL_COMPARISON"
    ? "Not applicable"
    : c.status === "NEEDS_REVIEW" &&
        state.decisions[c.email_id]?.action === "resolve"
      ? "Human review completed"
      : LABEL[c.status] || "Processing";
}
function aiReviewStatus(c, id) {
  if (c.status !== "NEEDS_REVIEW") return null;
  if (state.decisions[id]?.action === "resolve") return null;
  return c.ai_review?.status || "pending";
}
function resultKey(c) {
  return c.category === "BL_COMPARISON" &&
    c.status === "NEEDS_REVIEW" &&
    state.decisions[c.email_id]?.action === "resolve"
    ? "RESOLVED_REVIEW"
    : c.category === "BL_COMPARISON"
      ? c.status
      : "NOT_APPLICABLE";
}
function shortDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
}
function latestUpdate(c, decision) {
  const caseTime = Date.parse(c.updated_at || "") || 0,
    decisionTime = Date.parse(decision?.updated_at || "") || 0;
  return decisionTime > caseTime
    ? decision.updated_at
    : c.updated_at || decision?.updated_at;
}
function unresolved(id, c) {
  return (
    c.status === "NEEDS_REVIEW" && state.decisions[id]?.action !== "resolve"
  );
}
function issue(c) {
  const code = c.internal_reason || "";
  if (c.status === "PROCESSING_FAILED")
    return {
      name: "Document processing failed",
      field: "—",
      reason:
        (c.processing_step || "Processing") +
        " failed" +
        (c.failed_attachment
          ? " for " + c.failed_attachment.split("/").pop()
          : "") +
        ": " +
        (c.error || "Unknown technical error"),
    };
  if (code === "MISSING_SI_AND_BL")
    return {
      name: "Missing documents",
      field: "SI and draft BL",
      reason: "No attachments were supplied.",
    };
  if (code === "MISSING_SI_OR_BL")
    return {
      name: "Missing document",
      field: c.documents?.si
        ? "Draft BL"
        : c.documents?.bl
          ? "SI"
          : "SI / draft BL",
      reason: "A required document was not found.",
    };
  if (code === "AMBIGUOUS_DOCUMENT_ROLE")
    return {
      name: "Document identification",
      field: "SI / draft BL",
      reason: "Unable to confidently identify the SI and draft BL.",
    };
  if (code === "POSSIBLE_WRONG_DOCUMENT_PAIR")
    return {
      name: "Document pairing",
      field: "—",
      reason:
        "Shipment identifiers conflict. Check that these documents belong together.",
    };
  if (code === "DOCUMENT_INTERNAL_INCONSISTENCY")
    return {
      name: "Document values",
      field: "Container count / gross weight",
      reason: "Document values require verification.",
    };
  if (code === "LOW_EXTRACTION_CONFIDENCE")
    return {
      name: "Uncertain extraction",
      field: (c.uncertain_fields || []).map(prettyField).join(", ") || "—",
      reason: "One or more extracted values need confirmation.",
    };
  if (code === "MISSING_REQUIRED_FIELD")
    return {
      name: "Missing value",
      field: (c.missing_fields || []).map(prettyField).join(", ") || "—",
      reason: "A required shipment value could not be read.",
    };
  if (code === "UNREADABLE_DOCUMENT")
    return {
      name: "Unreadable document",
      field: "—",
      reason:
        (c.failed_attachment
          ? c.failed_attachment.split("/").pop() + ": "
          : "") + (c.error || "The attachment could not be read reliably."),
    };
  return {
    name: "Verification needs review",
    field: "—",
    reason: "Inspect the documents and confirm the values.",
  };
}
function nextAction(c) {
  const code = c.internal_reason || "";
  if (c.status === "PROCESSING_FAILED") return "Retry processing.";
  if (code === "MISSING_SI_AND_BL" || code === "MISSING_SI_OR_BL")
    return "Obtain the missing document, then retry when it is available.";
  if (
    code === "AMBIGUOUS_DOCUMENT_ROLE" ||
    code === "POSSIBLE_WRONG_DOCUMENT_PAIR"
  )
    return "Open the attachments and select the correct SI and draft BL.";
  if (code === "DOCUMENT_INTERNAL_INCONSISTENCY")
    return "Check container rows and totals against the source document.";
  if (code === "UNREADABLE_DOCUMENT")
    return state.features.ai_enabled
      ? "Inspect the attachment and retry with OCR / Vision."
      : "Request a readable copy or review the attachment manually; retry after the source is updated.";
  return "Review the source evidence, then confirm or correct the value.";
}
function metric(title, value, filter) {
  const item = add(
    node("button", "", "metric" + (state.filter === filter ? " active" : "")),
    node("strong", String(value)),
    node("span", title),
  );
  item.type = "button";
  item.setAttribute("aria-pressed", String(state.filter === filter));
  item.onclick = () => setFilter(filter);
  return item;
}
function setFilter(filter) {
  state.filter = filter;
  $("status-filter").value = "all";
  renderDashboard();
}
function visibleCases() {
  const search = $("search").value.toLowerCase(),
    category = $("category-filter").value,
    status = $("status-filter").value;
  return Object.entries(state.cases).filter(([id, c]) => {
    const e = c.email || {},
      key = resultKey(c),
      filter = state.filter;
    const filterMatch =
      filter === "all" ||
      (filter === "comparison" && c.category === "BL_COMPARISON") ||
      (filter === "NEEDS_REVIEW" && unresolved(id, c)) ||
      filter === key;
    const statusMatch =
      status === "all" ||
      (status === "NEEDS_REVIEW" && unresolved(id, c)) ||
      status === key;
    return (
      filterMatch &&
      statusMatch &&
      (category === "all" || category === c.category) &&
      [id, e.from, e.subject].some((v) =>
        String(v || "")
          .toLowerCase()
          .includes(search),
      )
    );
  });
}
function renderDashboard() {
  const m = state.metrics;
  $("metrics").replaceChildren(
    metric("Total Emails", m.total || 0, "all"),
    metric("Document Checks", m.comparison_requests || 0, "comparison"),
    metric("No Mismatch Detected", m.automatically_cleared || 0, "OK"),
    metric("Mismatch Detected", m.mismatches || 0, "MISMATCH"),
    metric("Human Review Required", m.human_review || 0, "NEEDS_REVIEW"),
    metric("Processing", m.processing || 0, "PROCESSING"),
  );
  $("quick-filters").replaceChildren(
    ...FILTERS.map(([key, label]) => {
      const b = button(
        label,
        () => setFilter(key),
        "chip" + (state.filter === key ? " active" : ""),
      );
      b.setAttribute("aria-pressed", String(state.filter === key));
      return b;
    }),
  );
  const entries = visibleCases();
  $("case-count").textContent =
    entries.length + " of " + (m.total || 0) + " cases";
  $("case-table").replaceChildren(
    ...entries.map(([id, c]) => {
      const e = c.email || {},
        row = node("tr", "", "clickable");
      row.tabIndex = 0;
      row.onclick = () => openCase(id);
      row.onkeydown = (event) => {
        if (event.key === "Enter") openCase(id);
      };
      const status = add(
        node("td"),
        node("span", resultLabel(c), "badge " + resultKey(c)),
      );
      if (
        state.decisions[id]?.action === "resolve" &&
        c.status === "NEEDS_REVIEW"
      )
        status.append(node("div", "Resolved by reviewer", "secondary-text"));
      const aiStatus = aiReviewStatus(c, id);
      if (aiStatus === "pending" || aiStatus === "running")
        status.append(
          node("div", "AI reviewing...", "secondary-text ai-pending"),
        );
      else if (aiStatus === "done")
        status.append(node("div", "AI report ready", "secondary-text"));
      const action =
        c.status === "PROCESSING_FAILED"
          ? "Retry"
          : unresolved(id, c)
            ? "Review"
            : "View";
      const actionButton = button(
        action,
        (event) => {
          event.stopPropagation();
          action === "Retry" ? retry(id) : openCase(id);
        },
        "small-btn primary",
      );
      row.append(
        add(
          node("td"),
          node("span", e.subject || "No subject", "subject"),
          node("div", id, "secondary-text"),
        ),
        node("td", e.from || "Unknown sender"),
        add(
          node("td"),
          node("span", LABEL[c.category] || c.category, "badge " + c.category),
        ),
        status,
        node("td", String(e.attachments?.length || 0)),
        node("td", shortDate(latestUpdate(c, state.decisions[id]))),
        add(node("td"), actionButton),
      );
      return row;
    }),
  );
  if (!entries.length) {
    const cell = node("td", "No cases match these filters.", "muted");
    cell.colSpan = 7;
    $("case-table").append(add(node("tr"), cell));
  }
}
function route() {
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash.startsWith("case/") && state.cases[hash.slice(5)]) {
    state.page = "case";
    state.selected = hash.slice(5);
  } else {
    state.page = "dashboard";
    state.selected = null;
  }
  render();
  if (state.page === "case") fetchEmail(state.selected);
}
function render() {
  $("dashboard-view").classList.toggle("hidden", state.page !== "dashboard");
  $("case-view").classList.toggle("hidden", state.page !== "case");
  if (state.page === "dashboard") renderDashboard();
  else renderCase();
}
async function load() {
  try {
    const data = await request("/api/cases");
    Object.assign(state, data);
    $("deployment-state").textContent =
      "Shipping operations · " +
      (state.features.cloud_mode ? "Cloud application" : "Local prototype");
    $("ai-state").textContent = state.features.ai_enabled
      ? "AI assistance available."
      : "AI assistance unavailable in this demo.";
    route();
  } catch (error) {
    $("dashboard-view").replaceChildren(
      node("p", error.message, "notice error"),
    );
  }
}
function showDashboard() {
  if (location.hash !== "#dashboard") location.hash = "dashboard";
  else route();
  window.scrollTo(0, 0);
}
function openCase(id) {
  state.mismatch = 0;
  state.email = null;
  if (location.hash !== "#case/" + encodeURIComponent(id))
    location.hash = "case/" + encodeURIComponent(id);
  else route();
  window.scrollTo(0, 0);
}
async function fetchEmail(id) {
  try {
    state.email = await request(apiPath("email", id));
    if (state.page === "case" && state.selected === id) renderCase();
  } catch (error) {
    $("case-content").prepend(node("p", error.message, "notice error"));
  }
}
function metadata(label, value) {
  return [node("dt", label), node("dd", value || "—")];
}
function summaryMeta(label, value) {
  return add(node("div"), node("dt", label), node("dd", value || "—"));
}
function section(title) {
  return add(node("section", "", "card"), node("h3", title));
}
function renderCase() {
  const id = state.selected,
    c = state.cases[id];
  if (!c) return;
  const e = state.email?.email_id === id ? state.email : c.email || {},
    area = $("case-content");
  area.replaceChildren();
  const head = section("Case result"),
    box = node("div", "", "case-summary " + c.status),
    count = c.mismatches?.length || 0;
  const headline =
    state.decisions[id]?.action === "resolve" && c.status === "NEEDS_REVIEW"
      ? "Human review completed"
      : c.status === "MISMATCH"
        ? count + " mismatch" + (count === 1 ? "" : "es") + " detected"
        : c.status === "NEEDS_REVIEW" && c.uncertain_fields?.length
          ? "Human review required for " +
            c.uncertain_fields.length +
            " field" +
            (c.uncertain_fields.length === 1 ? "" : "s")
          : resultLabel(c);
  box.append(
    node("div", id + " · " + (LABEL[c.category] || c.category), "eyebrow"),
    node("h2", headline),
    node("p", e.subject || "No subject"),
  );
  if (c.status === "MISMATCH")
    box.append(
      node(
        "p",
        "Check " +
          (c.mismatches || []).map((m) => prettyField(m.field)).join(", ") +
          ". Confirm the discrepancy or correct a value.",
      ),
    );
  if (c.status === "NEEDS_REVIEW" || c.status === "PROCESSING_FAILED") {
    box.append(node("p", issue(c).reason));
    if (
      state.decisions[id]?.action === "resolve" &&
      c.status === "NEEDS_REVIEW"
    )
      box.append(
        node(
          "p",
          "An operator marked this review complete. Automated verification remains inconclusive.",
        ),
      );
    else box.append(node("p", "Next action: " + nextAction(c)));
  }
  if (c.status === "PROCESSING")
    box.append(
      node("p", c.processing_step || "Document verification is in progress."),
    );
  if (c.category !== "BL_COMPARISON")
    box.append(
      node(
        "p",
        "This email does not require an SI versus draft BL comparison.",
      ),
    );
  box.append(
    add(
      node("dl"),
      summaryMeta("Sender", e.from),
      summaryMeta("Classification", LABEL[c.category] || c.category),
      summaryMeta("Updated", shortDate(latestUpdate(c, state.decisions[id]))),
      summaryMeta(
        "Shipping Instruction",
        c.documents?.si?.split("/").pop() || "Unidentified",
      ),
      summaryMeta(
        "Draft BL",
        c.documents?.bl?.split("/").pop() || "Unidentified",
      ),
    ),
  );
  head.append(box);
  area.append(head);
  if (
    c.category === "BL_COMPARISON" &&
    c.status !== "PROCESSING" &&
    (c.si_fields || c.bl_fields)
  )
    renderComparison(c, area);
  if (c.category === "BL_COMPARISON") renderReview(c, area);
  renderSource(c, e, area);
}
function fieldResult(a, b) {
  if (
    !a ||
    !b ||
    a.normalized_value == null ||
    b.normalized_value == null ||
    a.confidence < 0.9 ||
    b.confidence < 0.9
  )
    return ["Needs review", "review"];
  return a.normalized_value === b.normalized_value
    ? ["Match", "match"]
    : ["Mismatch", "mismatch"];
}
function fieldCell(f, showNormalized = false) {
  const td = node("td");
  if (!f) return add(td, node("span", "Missing", "muted"));
  td.append(node("div", f.raw_value, "raw"));
  if (showNormalized && f.normalized_value != null)
    td.append(
      node("span", "Compared as: " + String(f.normalized_value), "sub"),
    );
  if (f.confidence < 0.9)
    td.append(
      node(
        "span",
        "Source: " +
          (f.source_text || f.source || "Unknown") +
          " · " +
          Math.round((f.confidence || 0) * 100) +
          "% confidence",
        "sub",
      ),
    );
  td.title = f.source_text || f.source || "";
  return td;
}
function renderComparison(c, area) {
  const sectionNode = section("Shipping Instruction vs draft BL"),
    table = node("table", "", "compare");
  table.append(
    add(
      node("thead"),
      add(
        node("tr"),
        ...["Field", "Shipping Instruction", "Draft BL", "Result"].map((t) =>
          node("th", t),
        ),
      ),
    ),
  );
  const body = node("tbody");
  FIELDS.forEach((name) => {
    const si = c.si_fields?.[name],
      bl = c.bl_fields?.[name],
      [label, kind] = fieldResult(si, bl),
      showNorm =
        kind === "match" &&
        si &&
        bl &&
        String(si.raw_value).toLowerCase().replace(/\s/g, "") !==
          String(bl.raw_value).toLowerCase().replace(/\s/g, "");
    const row = node(
      "tr",
      "",
      kind === "mismatch"
        ? "mismatch clickable"
        : kind === "review"
          ? "uncertain"
          : "",
    );
    if (kind === "mismatch") {
      const index = (c.mismatches || []).findIndex((m) => m.field === name);
      row.tabIndex = 0;
      row.setAttribute(
        "aria-label",
        "Open " + prettyField(name) + " mismatch details",
      );
      row.onclick = () => selectMismatch(c, index);
      row.onkeydown = (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectMismatch(c, index);
        }
      };
      if (index === state.mismatch) row.classList.add("selected");
    }
    row.append(
      node("td", prettyField(name)),
      fieldCell(si, showNorm),
      fieldCell(bl, showNorm),
      node("td", label, "result " + kind),
    );
    body.append(row);
  });
  table.append(body);
  sectionNode.append(add(node("div", "", "table-scroll"), table));
  if (c.mismatches?.length) {
    sectionNode.append(
      node(
        "p",
        "Select a highlighted row to inspect the discrepancy and source evidence.",
        "muted",
      ),
      node("div", "", "mismatch-slot"),
    );
    renderMismatchDetail(c, sectionNode.querySelector(".mismatch-slot"));
  }
  if (c.validation) {
    const lines = [];
    if (c.validation.pairing?.conflicts?.length)
      lines.push(
        "Conflicting shipment identifiers: " +
          c.validation.pairing.conflicts.join(", "),
      );
    for (const [role, result] of Object.entries(c.validation.consistency || {}))
      if (!result.valid)
        lines.push(role.toUpperCase() + ": " + result.evidence);
    if (lines.length)
      sectionNode.append(node("div", lines.join("\n"), "notice error"));
  }
  area.append(sectionNode);
}
function selectMismatch(c, index) {
  state.mismatch = index;
  document
    .querySelectorAll(".compare tr.mismatch")
    .forEach((row, i) => row.classList.toggle("selected", i === index));
  const slot = document.querySelector(".mismatch-slot");
  renderMismatchDetail(c, slot);
  slot?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function renderMismatchDetail(c, slot) {
  if (!slot) return;
  const m = c.mismatches?.[state.mismatch];
  slot.replaceChildren();
  if (!m) return;
  const panel = node("div", "", "mismatch-panel"),
    values = node("div", "", "evidence-grid");
  values.append(
    add(
      node("div", "", "evidence-box"),
      node("strong", "Shipping Instruction"),
      node("div", m.si.raw_value),
      node(
        "p",
        "Source: " + (m.si.source_text || m.si.source || "Unavailable"),
        "field-evidence",
      ),
    ),
    add(
      node("div", "", "evidence-box"),
      node("strong", "Draft BL"),
      node("div", m.bl.raw_value),
      node(
        "p",
        "Source: " + (m.bl.source_text || m.bl.source || "Unavailable"),
        "field-evidence",
      ),
    ),
  );
  panel.append(
    node("h3", prettyField(m.field) + " — mismatch detected"),
    node("p", prettyField(m.field) + " values differ."),
    values,
  );
  const controls = add(
    node("div", "", "actions"),
    button(
      "Confirm mismatch",
      () => decision(c.email_id, "confirm"),
      "primary",
    ),
    button("Open SI source", () => openSource(c, "si")),
    button("Open BL source", () => openSource(c, "bl")),
  );
  if (state.decisions[c.email_id]?.action === "confirm")
    controls.append(
      button("Draft correction email", () => draft(c.email_id, panel)),
    );
  panel.append(controls);
  slot.append(panel);
}
function renderAiOpinion(c, panel) {
  if (!state.features.ai_enabled) return;
  const review = c.ai_review,
    card = node("div", "", "ai-opinion");
  if (!review || review.status === "pending" || review.status === "running") {
    card.append(
      node("h4", "AI opinion"),
      node("p", "AI is reviewing this case -- check back shortly.", "notice"),
    );
  } else if (review.status === "done") {
    card.append(
      node("h4", "AI opinion (for reference only)"),
      node("p", review.assessment),
      node("p", 'Proof: "' + review.proof + '"', "sub"),
      node("p", "Suggested next step: " + review.recommended_action),
    );
  } else {
    card.append(
      node("h4", "AI opinion"),
      node(
        "p",
        "AI review is unavailable for this case right now; the evidence below still stands on its own.",
        "muted",
      ),
    );
  }
  panel.append(card);
}
function renderReview(c, area) {
  if (c.status === "OK" || c.status === "MISMATCH") {
    if (c.status === "OK")
      area.append(
        add(
          section("Verification complete"),
          node(
            "p",
            "All seven required fields match after normalization.",
            "notice",
          ),
        ),
      );
    return;
  }
  const panel = section(
    c.status === "PROCESSING_FAILED"
      ? "Processing failed"
      : c.status === "PROCESSING"
        ? "Processing"
        : "Human review required",
  );
  if (c.status === "PROCESSING") {
    panel.append(
      node(
        "p",
        c.processing_step || "Document verification is in progress.",
        "notice",
      ),
    );
    area.append(panel);
    return;
  }
  if (
    state.decisions[c.email_id]?.action === "resolve" &&
    c.status === "NEEDS_REVIEW"
  ) {
    panel.append(
      node(
        "p",
        "Review completed by an operator. The original evidence remains available below.",
        "notice",
      ),
    );
    area.append(panel);
    return;
  }
  const i = issue(c);
  panel.append(
    node(
      "div",
      i.name + ": " + i.reason,
      "notice " + (c.status === "PROCESSING_FAILED" ? "error" : "warning"),
    ),
    node("p", "Recommended action: " + nextAction(c)),
  );
  const actions = node("div", "", "actions"),
    hasPdf = c.email?.attachments?.some((path) =>
      path.toLowerCase().endsWith(".pdf"),
    );
  if (c.status === "PROCESSING_FAILED") {
    actions.append(
      button("Retry processing", () => retry(c.email_id), "primary"),
    );
    if (state.features.ai_enabled && hasPdf)
      actions.append(
        button("Retry with OCR / Vision", () => retry(c.email_id, true)),
      );
    panel.append(actions);
    area.append(panel);
    return;
  }
  const saved = state.decisions[c.email_id];
  if (saved)
    panel.append(
      node(
        "p",
        "Reviewer action: " +
          saved.action.replaceAll("_", " ") +
          (saved.note ? " · " + saved.note : ""),
        "notice",
      ),
    );
  renderAiOpinion(c, panel);
  if (!c.si_fields && !c.bl_fields) {
    panel.append(
      node(
        "p",
        c.email?.attachments?.length
          ? "Expand Attachments below to inspect or assign document roles."
          : "No attachments are available in this case.",
        "muted",
      ),
    );
    actions.append(
      button("Retry extraction", () => retry(c.email_id)),
      button("Resolve case", () => decision(c.email_id, "resolve")),
    );
    if (state.features.ai_enabled && hasPdf)
      actions.append(
        button("Retry with OCR / Vision", () => retry(c.email_id, true)),
      );
    panel.append(actions);
    area.append(panel);
    return;
  }
  const selected =
      c.uncertain_fields?.[0] || c.missing_fields?.[0] || FIELDS[0],
    roleName = !c.si_fields?.[selected]
      ? "si"
      : !c.bl_fields?.[selected]
        ? "bl"
        : c.si_fields?.[selected]?.confidence < 0.9
          ? "si"
          : c.bl_fields?.[selected]?.confidence < 0.9
            ? "bl"
            : "si";
  const fieldData = c[roleName + "_fields"]?.[selected];
  if (fieldData)
    panel.append(
      node(
        "p",
        prettyField(selected) +
          ": " +
          fieldData.raw_value +
          " · " +
          Math.round((fieldData.confidence || 0) * 100) +
          "% confidence · Source: " +
          (fieldData.source_text || fieldData.source || "Unknown"),
        "field-evidence",
      ),
    );
  const row = node("div", "", "form-row"),
    role = node("select"),
    field = node("select"),
    value = node("input");
  role.setAttribute("aria-label", "Document to review");
  field.setAttribute("aria-label", "Field to review");
  role.append(node("option", "SI"), node("option", "BL"));
  role.value = roleName.toUpperCase();
  FIELDS.forEach((f) => {
    const option = node("option", prettyField(f));
    option.value = f;
    field.append(option);
  });
  field.value = selected;
  value.placeholder = "Enter corrected value";
  value.setAttribute("aria-label", "Corrected value");
  row.append(role, field, value);
  panel.append(row);
  if (fieldData)
    actions.append(
      button(
        "Confirm selected value",
        () =>
          decision(c.email_id, "confirm_value", {
            role: role.value.toLowerCase(),
            field: field.value,
          }),
        "primary",
      ),
    );
  actions.append(
    button("Save corrected value", () =>
      decision(c.email_id, "correct", {
        role: role.value.toLowerCase(),
        field: field.value,
        value: value.value,
      }),
    ),
  );
  if (c.si_fields?.[selected] && c.bl_fields?.[selected])
    actions.append(
      button("Mark values equivalent", () =>
        decision(c.email_id, "mark_equivalent", {
          field: field.value,
        }),
      ),
    );
  actions.append(button("Retry extraction", () => retry(c.email_id)));
  if (state.features.ai_enabled && hasPdf)
    actions.append(
      button("Retry with OCR / Vision", () => retry(c.email_id, true)),
    );
  actions.append(button("Resolve case", () => decision(c.email_id, "resolve")));
  panel.append(actions);
  area.append(panel);
}
function renderSource(c, e, area) {
  const grid = node("div", "", "detail-grid"),
    mail = node("section", "", "card"),
    mailDetails = node("details");
  mailDetails.append(
    node("summary", "Full email"),
    add(
      node("dl", "", "meta"),
      ...metadata("From", e.from),
      ...metadata("To", e.to),
      ...metadata("Subject", e.subject),
    ),
    node("pre", e.body || "No email body", "email-body"),
  );
  mail.append(mailDetails);
  const files = node("section", "", "card"),
    fileDetails = node("details");
  fileDetails.id = "attachments-details";
  const paths = e.attachments || [];
  fileDetails.append(node("summary", "Attachments (" + paths.length + ")"));
  if (!paths.length)
    fileDetails.append(
      node("p", "No attachments were supplied.", "notice error"),
    );
  else {
    const list = node("div", "", "file-list");
    paths.forEach((path, index) => {
      const role =
          c.documents?.si === path
            ? "Shipping Instruction / Reference"
            : c.documents?.bl === path
              ? "Draft Bill of Lading"
              : "Other / unassigned",
        fileButton = add(
          node("button", "", "file"),
          node("strong", path.split("/").pop()),
          node("small", role),
        );
      fileButton.onclick = () => preview(c.email_id, index, path, fileDetails);
      list.append(fileButton);
    });
    fileDetails.append(list, node("div", "", "preview-slot"));
    if (
      ["AMBIGUOUS_DOCUMENT_ROLE", "POSSIBLE_WRONG_DOCUMENT_PAIR"].includes(
        c.internal_reason,
      )
    )
      fileDetails.open = true;
  }
  files.append(fileDetails);
  grid.append(mail, files);
  area.append(grid);
}
async function preview(id, index, path, details) {
  details.open = true;
  const slot = details.querySelector(".preview-slot");
  if (!slot) return;
  slot.replaceChildren(node("p", "Loading " + path + "…", "muted"));
  const c = state.cases[id],
    select = node("div", "", "actions");
  if (c.category === "BL_COMPARISON")
    select.append(
      button("Use as SI", () => selectDocument(id, "si", path)),
      button("Use as draft BL", () => selectDocument(id, "bl", path)),
    );
  try {
    if (path.toLowerCase().endsWith(".pdf")) {
      const frame = node("iframe");
      frame.src = apiPath("attachment", id, index);
      frame.title = path;
      slot.replaceChildren(select, frame);
    } else {
      const doc = await request(apiPath("document", id, index));
      slot.replaceChildren(select, node("pre", doc.text, "doc-text"));
    }
  } catch (error) {
    slot.replaceChildren(select, node("p", error.message, "notice error"));
  }
}
function openSource(c, role) {
  const path = c.documents?.[role],
    paths =
      (state.email?.email_id === c.email_id ? state.email : c.email)
        ?.attachments || [],
    index = paths.indexOf(path),
    details = $("attachments-details");
  if (!details) return;
  details.open = true;
  details.scrollIntoView({ behavior: "smooth", block: "start" });
  if (index >= 0) preview(c.email_id, index, path, details);
}
async function refresh() {
  const data = await request("/api/cases");
  Object.assign(state, data);
  render();
  if (state.page === "case") await fetchEmail(state.selected);
}
async function decision(id, action, extra = {}) {
  try {
    await request(apiPath("decision", id), {
      action,
      ...extra,
    });
    await refresh();
  } catch (error) {
    alert(error.message);
  }
}
async function retry(id, vision = false) {
  try {
    const old = state.cases[id];
    state.cases[id] = {
      ...old,
      status: "PROCESSING",
      processing_step: vision
        ? "Retrying with OCR / Vision"
        : "Retrying document verification",
    };
    state.metrics.processing = (state.metrics.processing || 0) + 1;
    if (state.page === "dashboard") {
      state.filter = "PROCESSING";
      $("status-filter").value = "all";
    }
    render();
    await request(apiPath("retry", id), { vision });
    if (state.page === "dashboard") state.filter = "all";
    await refresh();
  } catch (error) {
    if (state.page === "dashboard") state.filter = "all";
    await refresh();
    alert(error.message);
  }
}
async function selectDocument(id, role, path) {
  try {
    await request(apiPath("select_document", id), {
      role,
      path,
    });
    await refresh();
  } catch (error) {
    alert(error.message);
  }
}
async function draft(id, panel) {
  try {
    const result = await request(apiPath("draft", id), {});
    panel.append(node("pre", result.draft, "draft"));
  } catch (error) {
    alert(error.message);
  }
}
$("back-button").onclick = showDashboard;
$("search").oninput = renderDashboard;
$("category-filter").onchange = renderDashboard;
$("status-filter").onchange = () => {
  state.filter = "all";
  renderDashboard();
};
window.addEventListener("hashchange", route);
load();
function hasPendingAiReview() {
  return Object.entries(state.cases).some(([id, c]) => {
    const status = aiReviewStatus(c, id);
    return status === "pending" || status === "running";
  });
}
setInterval(() => {
  if (hasPendingAiReview()) refresh();
}, 4000);
