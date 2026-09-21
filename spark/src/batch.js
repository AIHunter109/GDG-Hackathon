import { FIELDS, fieldRecord } from "./core.js";

const SUPPORTED = /\.(pdf|txt)$/i;
const MAX_FILE_SIZE = 5_000_000;
const SI_WORDS = new Set(["si", "shippinginstruction", "shippinginstructions"]);
const BL_WORDS = new Set(["bl", "billoflading", "draftbl"]);
const ROLE_WORDS = new Set([
  "si", "bl", "shipping", "instruction", "instructions", "bill", "of", "lading", "draft",
  "shippinginstruction", "shippinginstructions", "billoflading", "draftbl",
]);

function words(value) {
  return value.toLowerCase().replace(/([a-z])([A-Z])/g, "$1 $2")
    .split(/[^a-z0-9]+/).filter(Boolean);
}

function roleFromWords(tokens) {
  const joined = tokens.join("");
  const hasSi = tokens.some((word) => SI_WORDS.has(word)) || joined.includes("shippinginstruction");
  const hasBl = tokens.some((word) => BL_WORDS.has(word)) || joined.includes("billoflading");
  if (hasSi && hasBl) return "ambiguous";
  return hasSi ? "si" : hasBl ? "bl" : null;
}

export function pairingKey(name) {
  const stem = name.replace(/\.[^.]+$/, "");
  const tokens = words(stem).filter((word) => !ROLE_WORDS.has(word));
  return tokens.join("_") || null;
}

export function documentRole(file, source = "mixed") {
  const nameRole = roleFromWords(words(file.name.replace(/\.[^.]+$/, "")));
  const folders = (file.webkitRelativePath || "").split(/[\\/]/).slice(0, -1);
  const folderRole = roleFromWords(folders.flatMap(words));
  const roles = [nameRole, folderRole, source === "mixed" ? null : source].filter(Boolean);
  if (roles.includes("ambiguous") || new Set(roles).size > 1) return null;
  return roles[0] || null;
}

export function pairDocuments(inputs, originalIds = []) {
  const originals = new Set(originalIds);
  const groups = new Map();
  const unmatched = [];
  const rejected = [];
  for (const { file, source = "mixed" } of inputs) {
    const item = { file, source, path: file.webkitRelativePath || file.name };
    if (!SUPPORTED.test(file.name)) {
      rejected.push({ ...item, reason: "Only PDF and TXT are supported in the hosted batch uploader" });
      continue;
    }
    if (file.size > MAX_FILE_SIZE) {
      rejected.push({ ...item, reason: "File exceeds the 5 MB browser limit" });
      continue;
    }
    const key = pairingKey(file.name);
    const role = documentRole(file, source);
    if (!key || !role) {
      unmatched.push({ ...item, key, reason: !role ? "SI/BL role is unclear" : "No pairing ID in filename" });
      continue;
    }
    const group = groups.get(key) || { si: [], bl: [] };
    group[role].push({ ...item, key, role });
    groups.set(key, group);
  }
  const pairs = [];
  for (const [key, group] of groups) {
    if (group.si.length === 1 && group.bl.length === 1) {
      pairs.push({ key, si: group.si[0], bl: group.bl[0],
        status: originals.has(key) ? "original" : "pending" });
    } else {
      for (const item of [...group.si, ...group.bl]) {
        unmatched.push({ ...item, reason: group.si.length && group.bl.length
          ? "Several possible SI/BL files share this ID" : "Matching SI or BL is missing" });
      }
    }
  }
  pairs.sort((a, b) => a.key.localeCompare(b.key));
  unmatched.sort((a, b) => a.path.localeCompare(b.path));
  return { pairs, unmatched, rejected };
}

export function batchCaseId(pair) {
  const signature = [pair.key, ...[pair.si.file, pair.bl.file].flatMap((file) =>
    [file.name.toLowerCase(), file.size, file.lastModified || 0])].join("|");
  let hash = 0xcbf29ce484222325n;
  for (const character of signature) {
    hash = (hash ^ BigInt(character.charCodeAt(0))) * 0x100000001b3n & 0xffffffffffffffffn;
  }
  return `batch_${hash.toString(16).padStart(16, "0")}`;
}

const FIELD_LABELS = [
  ["shipper", /^(shipper|shipper\s*\/\s*exporter)$/i],
  ["consignee", /^(consignee|to the order of)$/i],
  ["notify_party", /^notify(?: party)?$/i],
  ["port_of_loading", /^(port of loading|pol|load port)$/i],
  ["port_of_discharge", /^(port of discharge|pod|discharge port)$/i],
  ["container_count", /^(?:total )?(?:container count|containers?|no\.? of containers)$/i],
  ["gross_weight_kg", /^(?:total )?gross\s*(?:weight|wt)(?:\s*\([^)]*\))?$/i],
];

export function extractTextFields(text, fileName) {
  const fields = {};
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*([^:]{1,90})\s*:\s*(.+?)\s*$/);
    if (!match) continue;
    const label = match[1].trim();
    const value = match[2].trim();
    const field = FIELD_LABELS.find(([name, pattern]) => !fields[name] && pattern.test(label))?.[0];
    if (!field || (field === "gross_weight_kg" && !/^\d/.test(value))) continue;
    const record = fieldRecord(field, value, fileName, 0.98, "native_txt");
    if (record?.normalized_value != null) fields[field] = { ...record, source_text: line.trim() };
  }
  return fields;
}

export function hasAllFields(fields) {
  return FIELDS.every((field) => fields[field]?.normalized_value != null);
}

function identifiers(text) {
  const patterns = {
    booking: /\bbooking\s*(?:ref(?:erence)?|no\.?|number)\s*:\s*([^\s,;]+)/i,
    oc: /\boc\s*no\.?\s*:\s*([^\s,;]+)/i,
    bl: /\b(?:bill of lading|b\/?l)\s*(?:no\.?|number)\s*:\s*([^\s,;]+)/i,
  };
  return Object.fromEntries(Object.entries(patterns).flatMap(([key, pattern]) => {
    const match = text.match(pattern);
    return match ? [[key, match[1].toUpperCase()]] : [];
  }));
}

export function validatePairIdentifiers(siText, blText) {
  const si = identifiers(siText), bl = identifiers(blText);
  const conflicts = Object.keys(si).filter((key) => bl[key] && si[key] !== bl[key]);
  return { valid: conflicts.length === 0, conflicts, si, bl };
}

export function validateDocumentConsistency(text, fields = {}) {
  const lines = String(text || "").split(/\r?\n/).map((line) => line.trim());
  const rowPositions = lines.flatMap((line, index) => /^[A-Z]{4}\d{7}$/.test(line) ? [index] : []);
  const declared = fields.container_count?.normalized_value;
  if (rowPositions.length && declared != null && rowPositions.length !== Number(declared)) {
    return { valid: false, reason: "DOCUMENT_INTERNAL_INCONSISTENCY",
      evidence: `${rowPositions.length} container rows vs declared ${declared}` };
  }
  const weights = rowPositions.flatMap((position) => {
    const value = lines.slice(position + 1, position + 4)
      .map((candidate) => /^\d[\d,]*(?:\.\d+)?$/.test(candidate) ? Number(candidate.replaceAll(",", "")) : null)
      .find((candidate) => candidate != null);
    return value == null ? [] : [value];
  });
  const total = fields.gross_weight_kg?.normalized_value;
  if (rowPositions.length && weights.length === rowPositions.length && total != null &&
      Math.abs(weights.reduce((sum, value) => sum + value, 0) - Number(total)) > 0.001) {
    return { valid: false, reason: "DOCUMENT_INTERNAL_INCONSISTENCY",
      evidence: `Container weights sum to ${weights.reduce((sum, value) => sum + value, 0)} kg vs declared ${total} kg` };
  }
  return { valid: true, container_rows: rowPositions.length, reconciled_weights: weights.length };
}
