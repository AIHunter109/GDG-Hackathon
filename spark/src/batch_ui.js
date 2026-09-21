import { batchCaseId, pairDocuments, pairingKey } from "./batch.js";

const MAX_FILES = 2_000;
const MAX_ROWS = 100;

function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  return node;
}

function add(parent, ...children) {
  parent.append(...children);
  return parent;
}

function option(value, label) {
  const node = element("option", label);
  node.value = value;
  return node;
}

export function mountBatchUploader({ container, ready, originalIds, hasRecord, processPair, onSaved }) {
  const card = element("section", "", "card");
  card.innerHTML = `
    <div class="card-head"><h2>Batch upload SI and BL documents</h2><span class="badge">Private workspace</span></div>
    <p class="muted">Select mixed PDF/TXT files, a mixed folder, or separate SI and BL folders. Matching shipment IDs in filenames are paired first; unclear files stay unprocessed until you pair them manually. TXT is read in your browser. PDFs go to Gemini only when you press Process. Files must be under 5 MB each.</p>
    <div class="toolbar batch-inputs">
      <label>Mixed files <input type="file" name="mixed_files" multiple accept=".pdf,.txt" /></label>
      <label>Mixed folder <input type="file" name="mixed_folder" webkitdirectory multiple /></label>
      <label>SI folder <input type="file" name="si_folder" webkitdirectory multiple /></label>
      <label>BL folder <input type="file" name="bl_folder" webkitdirectory multiple /></label>
    </div>
    <div class="actions"><button type="button" class="small-btn primary" id="batch-preview-button">Preview pairs</button></div>
    <p id="batch-status" class="notice">No batch selected. Files stay in this browser; saved text and results go to private Firestore.</p>
    <div id="batch-preview" class="hidden">
      <p id="batch-counts" class="muted"></p>
      <div class="table-scroll"><table class="master"><thead><tr><th>Shipment ID</th><th>SI file</th><th>BL file</th><th>Status</th></tr></thead><tbody id="batch-pairs"></tbody></table></div>
      <div id="batch-unmatched"></div>
      <div id="batch-rejected"></div>
      <div class="actions">
        <label>Pairs this run <input id="batch-limit" type="number" min="1" max="10" value="5" /></label>
        <button type="button" class="small-btn primary" id="batch-process">Process next pairs</button>
        <button type="button" class="small-btn" id="batch-pause" disabled>Pause after current pair</button>
        <button type="button" class="small-btn" id="batch-retry-failed">Retry failed pairs</button>
      </div>
    </div>`;
  container.append(card);

  const get = (selector) => card.querySelector(selector);
  let plan = null;
  let busy = false;
  let pauseRequested = false;

  function status(message, error = false) {
    const slot = get("#batch-status");
    slot.textContent = message;
    slot.className = `notice${error ? " error" : ""}`;
  }

  function inputs() {
    return [
      ...Array.from(get('[name="mixed_files"]').files, (file) => ({ file, source: "mixed" })),
      ...Array.from(get('[name="mixed_folder"]').files, (file) => ({ file, source: "mixed" })),
      ...Array.from(get('[name="si_folder"]').files, (file) => ({ file, source: "si" })),
      ...Array.from(get('[name="bl_folder"]').files, (file) => ({ file, source: "bl" })),
    ];
  }

  function renderUnmatched() {
    const slot = get("#batch-unmatched");
    slot.replaceChildren();
    if (!plan.unmatched.length) return;
    slot.append(element("h3", `Unpaired files (${plan.unmatched.length})`));
    const note = element("p", "Choose one SI and one BL below to pair files the filename rules could not match. Unpaired files are never processed.", "muted");
    slot.append(note);
    const controls = element("div", "", "toolbar");
    const si = element("select"), bl = element("select"), key = element("input");
    si.setAttribute("aria-label", "Unpaired SI file");
    bl.setAttribute("aria-label", "Unpaired BL file");
    key.placeholder = "Shipment ID (optional)";
    key.setAttribute("aria-label", "Manual shipment ID");
    si.append(option("", "Select SI file"));
    bl.append(option("", "Select BL file"));
    plan.unmatched.forEach((item, index) => {
      const label = `${item.path} — ${item.reason}`;
      si.append(option(String(index), label));
      bl.append(option(String(index), label));
    });
    const pairButton = element("button", "Pair selected files", "small-btn");
    pairButton.type = "button";
    pairButton.onclick = () => {
      const siIndex = Number(si.value), blIndex = Number(bl.value);
      if (!si.value || !bl.value || siIndex === blIndex) {
        status("Select two different files for SI and BL.", true);
        return;
      }
      const siFile = plan.unmatched[siIndex], blFile = plan.unmatched[blIndex];
      const entered = key.value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_");
      const inferred = pairingKey(siFile.file.name);
      const pairKey = entered || inferred || `manual_${plan.pairs.length + 1}`;
      if (plan.pairs.some((pair) => pair.key === pairKey)) {
        status("That shipment ID is already paired. Enter a different ID or review the existing pair.", true);
        return;
      }
      plan.pairs.push({ key: pairKey, si: siFile, bl: blFile,
        status: originalIds().includes(pairKey) ? "original" : "pending" });
      plan.unmatched = plan.unmatched.filter((_, index) => index !== siIndex && index !== blIndex);
      refreshSaved();
      render();
      status(`Paired ${siFile.path} with ${blFile.path}. Review the SI/BL roles before processing.`);
    };
    controls.append(si, bl, key, pairButton);
    slot.append(controls);
    const list = element("ul", "", "muted");
    for (const item of plan.unmatched.slice(0, MAX_ROWS)) list.append(element("li", `${item.path}: ${item.reason}`));
    if (plan.unmatched.length > MAX_ROWS) list.append(element("li", `Showing first ${MAX_ROWS} unpaired files.`));
    slot.append(list);
  }

  function render() {
    if (!plan) return;
    get("#batch-preview").classList.remove("hidden");
    const counts = plan.pairs.reduce((totals, pair) => {
      totals[pair.status] = (totals[pair.status] || 0) + 1;
      return totals;
    }, {});
    get("#batch-counts").textContent =
      `${plan.pairs.length} paired; ${counts.pending || 0} ready; ${counts.saved || 0} saved; ` +
      `${counts.original || 0} already in the original bundle; ${plan.unmatched.length} unpaired; ${plan.rejected.length} unsupported or oversized.` +
      (plan.pairs.length > MAX_ROWS ? ` Showing first ${MAX_ROWS} pairs below; processing uses all ready pairs.` : "");
    const rows = plan.pairs.slice(0, MAX_ROWS).map((pair) => {
      const row = element("tr");
      row.append(
        element("td", pair.key),
        element("td", pair.si.path),
        element("td", pair.bl.path),
        element("td", pair.status === "original" ? "Existing email — open its Case Detail" :
          pair.status === "saved" ? "Saved in history" :
            pair.status === "failed" ? `Failed: ${pair.error}` : pair.status),
      );
      return row;
    });
    get("#batch-pairs").replaceChildren(...rows);
    renderUnmatched();
    const rejected = get("#batch-rejected");
    rejected.replaceChildren();
    if (plan.rejected.length) {
      rejected.append(element("h3", `Files not accepted (${plan.rejected.length})`));
      const list = element("ul", "", "muted");
      for (const item of plan.rejected.slice(0, MAX_ROWS)) list.append(element("li", `${item.path}: ${item.reason}`));
      if (plan.rejected.length > MAX_ROWS) list.append(element("li", `Showing first ${MAX_ROWS} rejected files.`));
      rejected.append(list);
    }
    get("#batch-process").disabled = busy || !plan.pairs.some((pair) => pair.status === "pending");
    get("#batch-pause").disabled = !busy;
    get("#batch-preview-button").disabled = busy;
  }

  function refreshSaved() {
    for (const pair of plan.pairs) {
      pair.id = batchCaseId(pair);
      if (pair.status === "pending" && hasRecord(pair.id)) pair.status = "saved";
    }
  }

  get("#batch-preview-button").onclick = () => {
    if (!ready()) {
      status("Sign in with the project owner account before previewing a batch.", true);
      return;
    }
    const selected = inputs();
    if (!selected.length) {
      status("Select mixed files or one or more folders first.", true);
      return;
    }
    if (selected.length > MAX_FILES) {
      status(`Choose up to ${MAX_FILES} files per preview, then reselect the next group. Saved pairs are skipped.`, true);
      return;
    }
    plan = pairDocuments(selected, originalIds());
    refreshSaved();
    render();
    status("Review the pairs and unpaired files, then process a small group. Keep this tab open while files are processing.");
  };

  get("#batch-process").onclick = async () => {
    if (!plan || busy || !ready()) return;
    const limit = Math.min(10, Math.max(1, Number(get("#batch-limit").value) || 5));
    const next = plan.pairs.filter((pair) => pair.status === "pending").slice(0, limit);
    if (!next.length) return;
    busy = true;
    pauseRequested = false;
    render();
    let saved = 0;
    for (const pair of next) {
      if (pauseRequested) break;
      pair.status = "processing";
      status(`Processing ${pair.key} (${saved + 1} of ${next.length} this run). TXT stays local; PDFs are sent to Gemini.`);
      render();
      try {
        await processPair(pair, pair.id);
        pair.status = "saved";
        saved += 1;
      } catch (error) {
        pair.status = "failed";
        pair.error = String(error.message || error).slice(0, 240);
        status(`Stopped at ${pair.key}: ${pair.error}. Saved pairs remain in history.`, true);
        break;
      }
      render();
    }
    busy = false;
    render();
    if (saved) onSaved();
    if (!plan.pairs.some((pair) => pair.status === "failed")) {
      status(`${saved} pair${saved === 1 ? "" : "s"} saved in this run. Continue with the next group when ready.`);
    }
  };

  get("#batch-pause").onclick = () => {
    pauseRequested = true;
    status("Pausing after the current pair finishes.");
  };

  get("#batch-retry-failed").onclick = () => {
    if (!plan || busy) return;
    for (const pair of plan.pairs) if (pair.status === "failed") {
      pair.status = "pending";
      pair.error = "";
    }
    render();
    status("Failed pairs are ready to retry. Previously saved pairs will be skipped.");
  };
}
