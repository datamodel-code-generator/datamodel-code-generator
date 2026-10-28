import * as Comlink from "https://cdn.jsdelivr.net/npm/comlink@4.4.2/dist/esm/comlink.mjs";

import {
  getInputValue,
  mountInputEditor,
  mountOutputViewer,
  refreshEditors,
  setInputLanguage,
  setInputValue,
  setOutputValue,
} from "./editor.js";

const appEl = document.querySelector("#app");
const stateEncoder = new TextEncoder();
const stateDecoder = new TextDecoder();
const stateFormatGzip = "gz";
const stateFormatPlain = "b64";
const stateVersion = 1;
const versionsManifestUrl = "./generated/playground-versions.json";
const fallbackVersionManifest = {
  default: "current",
  versions: [
    {
      id: "current",
      label: "Current build",
      kind: "current",
      asset_base: "./generated/",
    },
  ],
};

let rawWorker = null;
let worker = null;
let workerInitPromise = null;
let ready = false;
let running = false;
let lastOutput = "";
let configToml = "";
let autoGenerate = true;
let autoTimer = null;
let pendingAutoGenerate = false;
let activeGenerationMode = "manual";
let cliCommandText = "";
let cliOptionsDirty = true;
let cliRequestId = 0;
let appShellMounted = false;
let workspaceResizeObserver = null;
let headerCompact = false;
let workspaceCollapsed = false;
let restoredUrlState = false;
let playgroundVersions = [];
let selectedPlaygroundVersion = null;
let versionSelectionNotice = "";

function absoluteUrl(path, base = window.location.href) {
  return new URL(path, base).toString();
}

function normalizeInstall(install, assetBaseUrl) {
  if (!install || typeof install !== "object") {
    return null;
  }
  if (install.type === "wheel" && install.url) {
    return {
      type: "wheel",
      url: absoluteUrl(install.url, assetBaseUrl),
      deps: install.deps === true,
    };
  }
  if (install.type === "requirement" && install.requirement) {
    return {
      type: "requirement",
      requirement: String(install.requirement),
      deps: install.deps === true,
    };
  }
  return null;
}

function normalizeRuntimePackages(runtimePackages) {
  if (!Array.isArray(runtimePackages)) {
    return [];
  }
  return runtimePackages.map((runtimePackage) => String(runtimePackage).trim()).filter(Boolean);
}

function normalizePlaygroundVersion(entry) {
  const id = String(entry?.id || "").trim();
  if (!id) {
    return null;
  }
  const assetBaseUrl = absoluteUrl(entry.asset_base || entry.assetBase || "./generated/");
  const defaultApp = String(entry.kind || "") === "current" ? "runtime.py" : "app.py";
  return {
    id,
    label: String(entry.label || id),
    kind: String(entry.kind || "custom"),
    assetBaseUrl,
    shellUrl: absoluteUrl(entry.app_shell || entry.app_shell_url || entry.shell_url || "app-shell.html", assetBaseUrl),
    metadataUrl: absoluteUrl(
      entry.metadata || entry.metadata_url || "codegen-ui-metadata.json",
      assetBaseUrl,
    ),
    appUrl: absoluteUrl(entry.app || entry.app_url || defaultApp, assetBaseUrl),
    install: normalizeInstall(entry.install, assetBaseUrl),
    runtimePackages: normalizeRuntimePackages(entry.runtime_packages || entry.runtimePackages),
  };
}

async function loadPlaygroundVersions() {
  let manifest = fallbackVersionManifest;
  try {
    const response = await fetch(versionsManifestUrl, { cache: "no-cache" });
    if (response.ok) {
      manifest = await response.json();
    }
  } catch {
    manifest = fallbackVersionManifest;
  }
  const versions = (manifest.versions || []).map(normalizePlaygroundVersion).filter(Boolean);
  if (versions.length === 0) {
    return {
      manifest: fallbackVersionManifest,
      versions: fallbackVersionManifest.versions.map(normalizePlaygroundVersion).filter(Boolean),
    };
  }
  return { manifest, versions };
}

function resolvePlaygroundVersion(manifest, versions) {
  const requested = new URLSearchParams(window.location.search).get("version");
  const defaultId = manifest.default || versions[0]?.id;
  const selected = versions.find((version) => version.id === requested)
    || versions.find((version) => version.id === defaultId)
    || versions[0];
  versionSelectionNotice = requested && requested !== selected.id
    ? `Unknown playground version "${requested}", using ${selected.label}.`
    : "";
  return selected;
}

async function initPlaygroundVersions() {
  const { manifest, versions } = await loadPlaygroundVersions();
  playgroundVersions = versions;
  selectedPlaygroundVersion = resolvePlaygroundVersion(manifest, versions);
}

function renderVersionSelect() {
  const select = document.querySelector("#playground-version");
  if (!select || !selectedPlaygroundVersion) {
    return;
  }
  const versionIds = playgroundVersions.map((version) => version.id).join("\n");
  if (select.dataset.versionIds !== versionIds) {
    const options = playgroundVersions.map((version) => {
      const option = new Option(version.label, version.id);
      option.selected = version.id === selectedPlaygroundVersion.id;
      return option;
    });
    select.replaceChildren(...options);
    select.dataset.versionIds = versionIds;
  }
  select.value = selectedPlaygroundVersion.id;
  select.disabled = running || playgroundVersions.length < 2;
  select.title = selectedPlaygroundVersion.label;
}

function switchPlaygroundVersion(versionId) {
  const nextVersion = playgroundVersions.find((version) => version.id === versionId);
  if (!nextVersion || nextVersion.id === selectedPlaygroundVersion?.id) {
    renderVersionSelect();
    return;
  }
  const url = new URL(window.location.href);
  url.searchParams.set("version", nextVersion.id);
  setStatus(`Switching to ${nextVersion.label}...`);
  document.querySelector("#playground-version")?.setAttribute("disabled", "");
  window.location.assign(url.toString());
}

function createWorker() {
  if (worker) {
    return worker;
  }
  rawWorker = new Worker("./worker.js", { type: "module" });
  rawWorker.addEventListener("error", (event) => {
    setStatus(event.message || "Worker failed.", true);
    finishGeneration();
  });
  worker = Comlink.wrap(rawWorker);
  return worker;
}

function startWorkerWarmup() {
  const api = createWorker();
  workerInitPromise ??= api.init(
    Comlink.proxy((message) => setStatus(message)),
    selectedPlaygroundVersion,
  );
  return workerInitPromise;
}

function prepareCurrentInput() {
  if (!worker) {
    return Promise.resolve();
  }
  return worker.prepare(currentInputType());
}

function currentSchema() {
  return getInputValue();
}

function currentInputType() {
  return document.querySelector("#input-type")?.value ?? "jsonschema";
}

function renderOutput(value, language = "python") {
  const output = document.querySelector("#output");
  if (!output) {
    appEl.textContent = value;
    return;
  }
  setOutputValue(value, language);
}

function updateInputFormatState() {
  const inputType = currentInputType();
  document.querySelector("#schema")?.setAttribute("data-format", inputType);
  document.body.dataset.inputFormat = inputType;
  setInputLanguage(inputType);
}

function mountEditor() {
  mountInputEditor(document.querySelector("#schema"), currentInputType());
  mountOutputViewer(document.querySelector("#output"), "", "python");
  observeWorkspaceResize();
  refreshEditors();
}

function syncStickyOffsets() {
  const topbar = document.querySelector(".topbar");
  const topbarHeight = topbar ? Math.ceil(topbar.getBoundingClientRect().height) : 0;
  document.documentElement.style.setProperty("--topbar-offset", `${topbarHeight}px`);
}

function observeWorkspaceResize() {
  const workspace = document.querySelector(".workspace");
  if (!workspace || workspaceResizeObserver) {
    return;
  }
  workspaceResizeObserver = new ResizeObserver(() => refreshEditors());
  workspaceResizeObserver.observe(workspace);
}

function setLayoutState() {
  document.body.classList.toggle("header-compact", headerCompact);
  document.body.classList.toggle("workspace-collapsed", workspaceCollapsed);
  const headerButton = document.querySelector("#toggle-header");
  if (headerButton) {
    headerButton.textContent = headerCompact ? "Full Header" : "Compact Header";
    headerButton.setAttribute("aria-pressed", headerCompact ? "true" : "false");
  }
  const workspaceButton = document.querySelector("#toggle-workspace");
  if (workspaceButton) {
    workspaceButton.textContent = workspaceCollapsed ? "Show Editors" : "Hide Editors";
    workspaceButton.setAttribute("aria-pressed", workspaceCollapsed ? "true" : "false");
  }
  syncStickyOffsets();
  refreshEditors();
}

async function mountInitialShell() {
  const shellUrl = selectedPlaygroundVersion?.shellUrl || "./generated/app-shell.html";
  const response = await fetch(shellUrl, { cache: "no-cache" });
  if (appShellMounted || !response.ok) {
    return;
  }
  const html = await response.text();
  if (appShellMounted) {
    return;
  }
  appEl.innerHTML = html;
  appEl.classList.remove("boot");
  appShellMounted = true;
  syncStickyOffsets();
  updateInputFormatState();
  mountEditor();
  setLayoutState();
  renderVersionSelect();
}

function collectOptionEntries() {
  const form = document.querySelector("#options-form");
  return form ? Object.fromEntries(new FormData(form).entries()) : {};
}

function bytesToBase64Url(bytes) {
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlToBytes(value) {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

async function gzipText(text) {
  const stream = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function gunzipText(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return stateDecoder.decode(await new Response(stream).arrayBuffer());
}

async function encodePlaygroundState(state) {
  const json = JSON.stringify(state);
  if ("CompressionStream" in globalThis) {
    return `${stateFormatGzip}.${bytesToBase64Url(await gzipText(json))}`;
  }
  return `${stateFormatPlain}.${bytesToBase64Url(stateEncoder.encode(json))}`;
}

async function decodePlaygroundState(payload) {
  const [format, value] = payload.includes(".") ? payload.split(".", 2) : [stateFormatPlain, payload];
  const bytes = base64UrlToBytes(value);
  let json = "";
  if (format === stateFormatGzip) {
    if (!("DecompressionStream" in globalThis)) {
      throw new Error("Compressed URL state is not supported by this browser");
    }
    json = await gunzipText(bytes);
  } else if (format === stateFormatPlain) {
    json = stateDecoder.decode(bytes);
  } else {
    throw new Error(`Unknown playground state format: ${format}`);
  }
  const state = JSON.parse(json);
  if (state.v !== stateVersion) {
    throw new Error(`Unsupported playground state version: ${state.v}`);
  }
  return state;
}

function buildPlaygroundState() {
  return {
    v: stateVersion,
    inputType: currentInputType(),
    schema: currentSchema(),
    options: collectOptionEntries(),
  };
}

function statePayloadFromHash() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  return params.get("state");
}

function buildReproUrl(payload) {
  const url = new URL(window.location.href);
  if (selectedPlaygroundVersion) {
    url.searchParams.set("version", selectedPlaygroundVersion.id);
  }
  const params = new URLSearchParams(url.hash.slice(1));
  params.set("state", payload);
  url.hash = params.toString();
  return url.toString();
}

async function copyReproUrl() {
  const payload = await encodePlaygroundState(buildPlaygroundState());
  const url = buildReproUrl(payload);
  await navigator.clipboard.writeText(url);
  setStatus(url.length > 8000 ? "Copied reproducible URL. The URL is long." : "Copied reproducible URL.");
}

async function restoreStateFromUrl() {
  const payload = statePayloadFromHash();
  if (!payload) {
    return false;
  }
  const state = await decodePlaygroundState(payload);
  if (state.inputType) {
    const inputType = document.querySelector("#input-type");
    const inputTypeValue = String(state.inputType);
    const isAvailableInputType = Array.from(inputType?.options || []).some(
      (option) => option.value === inputTypeValue && !option.disabled,
    );
    if (inputType && isAvailableInputType) {
      inputType.value = inputTypeValue;
      updateInputFormatState();
    }
  }
  if (typeof state.schema === "string") {
    setInputValue(state.schema);
  }
  clearOptionControls();
  Object.entries(state.options || {}).forEach(([key, value]) => {
    const control = document.querySelector(`[data-option="${CSS.escape(key)}"]`);
    if (!control) {
      return;
    }
    if (control.type === "checkbox") {
      control.checked = value === true || value === "true" || value === "on";
    } else if (Array.isArray(value)) {
      control.value = value.join("\n");
    } else {
      control.value = String(value);
    }
    control.dispatchEvent(new Event("change", { bubbles: true }));
  });
  restoredUrlState = true;
  refreshEditors();
  return true;
}

function filterOptions(query) {
  const needle = query.trim().toLowerCase();
  document.querySelectorAll("#options-form .option-group").forEach((group) => {
    let matches = 0;
    group.querySelectorAll(".option-row").forEach((row) => {
      const label = row.querySelector(".option-label")?.textContent.toLowerCase() ?? "";
      const hit = !needle || label.includes(needle);
      row.classList.toggle("is-hidden", !hit);
      if (hit) {
        matches += 1;
      }
    });
    group.classList.toggle("is-hidden", matches === 0);
    if (needle) {
      group.open = matches > 0;
    }
  });
}

function setAutoGenerate(enabled) {
  autoGenerate = enabled;
  if (!enabled) {
    cancelAutoGenerate();
  }
  setGenerateState();
  setStatus(enabled ? "Auto Generate enabled." : "Auto Generate disabled.");
}

function cancelAutoGenerate() {
  clearTimeout(autoTimer);
  autoTimer = null;
  pendingAutoGenerate = false;
}

function scheduleAutoGenerate(delay = 650) {
  if (!autoGenerate || !ready || !currentSchema().trim()) {
    return;
  }
  clearTimeout(autoTimer);
  autoTimer = setTimeout(() => requestGenerate("auto"), delay);
}

async function refreshCliOptions() {
  if (!ready || !worker) {
    return;
  }
  const requestId = ++cliRequestId;
  cliOptionsDirty = true;
  setGenerateState();
  const [commandText, tomlText] = await Promise.all([
    worker.buildCliOptions(collectOptionEntries(), currentInputType()),
    worker.exportConfigToml(collectOptionEntries(), currentInputType()),
  ]);
  if (requestId === cliRequestId) {
    cliCommandText = commandText;
    configToml = tomlText;
    cliOptionsDirty = false;
    setGenerateState();
  }
}

function openConfigDialog() {
  const dialog = document.querySelector("#config-dialog");
  const textarea = document.querySelector("#config-toml");
  if (!dialog || !textarea) {
    return;
  }
  textarea.value = configToml;
  if (typeof dialog.showModal === "function") {
    dialog.showModal();
  } else {
    dialog.setAttribute("open", "");
  }
  textarea.focus();
}

function closeConfigDialog() {
  const dialog = document.querySelector("#config-dialog");
  if (!dialog) {
    return;
  }
  if (typeof dialog.close === "function") {
    dialog.close();
  } else {
    dialog.removeAttribute("open");
  }
}

function clearOptionControls() {
  document.querySelectorAll("[data-option]").forEach((control) => {
    if (control.type === "checkbox") {
      control.checked = false;
    } else {
      control.value = "";
    }
  });
}

function applyImportedOptions(result) {
  if (result.inputType) {
    const inputType = document.querySelector("#input-type");
    if (inputType) {
      inputType.value = result.inputType;
      updateInputFormatState();
    }
  }
  clearOptionControls();
  Object.entries(result.options || {}).forEach(([key, value]) => {
    const control = document.querySelector(`[data-option="${CSS.escape(key)}"]`);
    if (!control) {
      return;
    }
    if (control.type === "checkbox") {
      control.checked = Boolean(value);
    } else if (Array.isArray(value)) {
      control.value = value.join("\n");
    } else {
      control.value = String(value);
    }
    control.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function generationErrorMessage(error) {
  if (!error) {
    return "Unknown generation error.";
  }
  if (typeof error === "string") {
    return error;
  }
  return error.stack || error.message || String(error);
}

function generationErrorSummary(message) {
  const lines = String(message)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = lines[index];
    if (!line.startsWith("File ") && !line.startsWith("Traceback ")) {
      return line;
    }
  }
  return lines.at(-1) || "Unknown generation error.";
}

function renderGenerationFailure(error, mode) {
  const message = generationErrorMessage(error);
  const summary = generationErrorSummary(message);
  const statusText = mode === "auto"
    ? `Auto generation failed: ${summary}. Keeping the last successful output.`
    : `Generation failed: ${summary}.`;
  setStatus(statusText, true, message);
  if (mode === "auto") {
    return;
  }
  lastOutput = message;
  renderOutput(message, "python");
  document.querySelector("#copy").disabled = true;
}

function finishGeneration() {
  running = false;
  activeGenerationMode = "manual";
  setGenerateState();
  if (pendingAutoGenerate) {
    pendingAutoGenerate = false;
    scheduleAutoGenerate(120);
  }
}

async function requestGenerate(mode = "manual") {
  if (!ready || !worker) {
    return;
  }
  if (running) {
    if (mode === "auto") {
      pendingAutoGenerate = true;
    }
    return;
  }
  if (!currentSchema().trim()) {
    if (mode === "manual") {
      setStatus("Input is empty.", true);
    }
    return;
  }

  running = true;
  activeGenerationMode = mode;
  if (mode === "manual") {
    renderOutput("", "python");
    document.querySelector("#copy").disabled = true;
  } else {
    setStatus("Auto generating...");
  }
  setGenerateState();

  try {
    const result = await worker.generate(currentSchema(), currentInputType(), collectOptionEntries());
    if (result.ok) {
      lastOutput = result.output;
      renderOutput(lastOutput, "python");
      document.querySelector("#copy").disabled = !lastOutput;
      setStatus(
        mode === "auto" ? "Auto generated with the built-in formatter." : "Generated with the built-in formatter.",
      );
    } else {
      renderGenerationFailure(result.error, mode);
    }
  } catch (error) {
    renderGenerationFailure(error, mode);
  } finally {
    finishGeneration();
  }
}

function setGenerateState() {
  const button = document.querySelector("#generate");
  if (button) {
    button.disabled = !ready || running;
    button.textContent = running ? "Generating..." : "Generate";
  }
  const cliButton = document.querySelector("#copy-cli");
  if (cliButton) {
    cliButton.disabled = !ready || cliOptionsDirty;
  }
  const configButton = document.querySelector("#config");
  if (configButton) {
    configButton.disabled = !ready || cliOptionsDirty;
  }
  const autoButton = document.querySelector("#auto-generate");
  if (autoButton) {
    autoButton.disabled = !ready;
    autoButton.classList.toggle("is-active", autoGenerate);
    autoButton.setAttribute("aria-pressed", autoGenerate ? "true" : "false");
    autoButton.textContent = autoGenerate ? "Auto Generate: On" : "Auto Generate";
  }
  renderVersionSelect();
}

function updateStatusTooltip() {
  const status = document.querySelector("#status");
  if (!status) {
    return;
  }
  // Reveal the full message on hover only when the text is visually truncated.
  const explicitTitle = status.dataset.statusTitle || "";
  status.title = explicitTitle || (status.scrollWidth > status.clientWidth ? status.textContent : "");
}

function setStatus(text, isError = false, title = "") {
  const status = document.querySelector("#status");
  if (!status) {
    return;
  }
  status.textContent = text;
  status.classList.toggle("error", isError);
  if (title) {
    status.dataset.statusTitle = title;
  } else {
    delete status.dataset.statusTitle;
  }
  requestAnimationFrame(updateStatusTooltip);
}

async function finishWorkerWarmup() {
  const info = await startWorkerWarmup();
  await prepareCurrentInput();
  ready = true;
  const readyMessage = [
    `Ready: datamodel-code-generator ${info.codegenVersion}`,
    `Pyodide ${info.pyodideVersion}`,
    `Python ${info.pythonVersion}`,
  ].join(", ");
  const notices = [readyMessage];
  if (restoredUrlState) {
    notices.push("Restored URL state.");
  }
  if (versionSelectionNotice) {
    notices.push(versionSelectionNotice);
  }
  setStatus(notices.join(" "));
  setGenerateState();
  await refreshCliOptions();
  scheduleAutoGenerate(0);
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) {
    return;
  }
  const action = target.dataset.action;
  if (action === "generate") {
    requestGenerate("manual");
  } else if (action === "sample") {
    const api = createWorker();
    startWorkerWarmup();
    setInputValue(await api.sample(currentInputType()));
    setStatus(`Loaded ${currentInputType()} sample.`);
    scheduleAutoGenerate();
  } else if (action === "clear-input") {
    setInputValue("");
    cancelAutoGenerate();
    setStatus("Cleared input.");
  } else if (action === "clear-output") {
    lastOutput = "";
    renderOutput("", "python");
    document.querySelector("#copy").disabled = true;
    setStatus("Cleared output.");
  } else if (action === "copy-cli") {
    await navigator.clipboard.writeText(cliCommandText);
    setStatus(cliCommandText ? "Copied CLI command." : "Copied empty CLI command.");
  } else if (action === "copy-repro-url") {
    await copyReproUrl();
  } else if (action === "config") {
    openConfigDialog();
  } else if (action === "copy-config") {
    const textarea = document.querySelector("#config-toml");
    await navigator.clipboard.writeText(textarea?.value || configToml);
    setStatus("Copied pyproject.toml config.");
  } else if (action === "import-config") {
    if (!worker) {
      return;
    }
    const textarea = document.querySelector("#config-toml");
    const result = await worker.importConfigToml(textarea?.value || "");
    if (!result.ok) {
      setStatus(`Could not import pyproject.toml: ${result.error}`, true);
      return;
    }
    applyImportedOptions(result);
    await refreshCliOptions();
    closeConfigDialog();
    const ignored = result.ignored?.length ? ` Ignored: ${result.ignored.join(", ")}.` : "";
    setStatus(`Imported pyproject.toml config.${ignored}`);
    scheduleAutoGenerate();
  } else if (action === "close-config") {
    closeConfigDialog();
  } else if (action === "auto-generate") {
    setAutoGenerate(!autoGenerate);
    if (!autoGenerate) {
      return;
    }
    scheduleAutoGenerate(0);
  } else if (action === "toggle-header") {
    headerCompact = !headerCompact;
    setLayoutState();
  } else if (action === "toggle-workspace") {
    workspaceCollapsed = !workspaceCollapsed;
    setLayoutState();
  } else if (action === "copy" && lastOutput) {
    await navigator.clipboard.writeText(lastOutput);
    setStatus("Copied output.");
  }
});

async function startPlayground() {
  await initPlaygroundVersions();
  startWorkerWarmup();
  await mountInitialShell();
  renderVersionSelect();
  try {
    await restoreStateFromUrl();
  } catch (error) {
    setStatus(`Could not load URL state: ${error?.message || String(error)}`, true);
  }
  finishWorkerWarmup().catch((error) => {
    setStatus(error?.stack || String(error), true);
    finishGeneration();
  });
}

startPlayground().catch((error) => {
  setStatus(error?.stack || String(error), true);
});

window.addEventListener("hashchange", async () => {
  try {
    if (await restoreStateFromUrl()) {
      setStatus("Loaded state from URL.");
      prepareCurrentInput().catch((error) => {
        setStatus(error?.stack || String(error), true);
      });
      await refreshCliOptions();
      scheduleAutoGenerate(0);
    }
  } catch (error) {
    setStatus(`Could not load URL state: ${error?.message || String(error)}`, true);
  }
});

window.addEventListener("resize", () => {
  syncStickyOffsets();
  refreshEditors();
  updateStatusTooltip();
});

document.addEventListener("change", (event) => {
  if (event.target.id === "playground-version") {
    switchPlaygroundVersion(event.target.value);
  } else if (event.target.id === "input-type") {
    updateInputFormatState();
    prepareCurrentInput().catch((error) => {
      setStatus(error?.stack || String(error), true);
    });
    refreshCliOptions();
    scheduleAutoGenerate();
  } else if (event.target.matches("[data-option]")) {
    refreshCliOptions();
    scheduleAutoGenerate();
  }
});

document.addEventListener("input", (event) => {
  if (event.target.id === "option-filter") {
    filterOptions(event.target.value);
    return;
  }
  if (event.target.id === "schema" || event.target.matches("[data-option]")) {
    if (event.target.matches("[data-option]")) {
      refreshCliOptions();
    }
    scheduleAutoGenerate();
  }
});

let splitterDragging = false;

function endSplitterDrag() {
  if (!splitterDragging) {
    return;
  }
  splitterDragging = false;
  document.querySelector(".workspace-splitter")?.classList.remove("is-dragging");
  document.body.style.removeProperty("user-select");
  refreshEditors();
}

document.addEventListener("pointerdown", (event) => {
  if (!event.target.closest(".workspace-splitter")) {
    return;
  }
  splitterDragging = true;
  event.target.closest(".workspace-splitter").classList.add("is-dragging");
  document.body.style.userSelect = "none";
  event.preventDefault();
});

document.addEventListener("pointermove", (event) => {
  if (!splitterDragging) {
    return;
  }
  const workspace = document.querySelector(".workspace");
  if (!workspace) {
    return;
  }
  const rect = workspace.getBoundingClientRect();
  if (rect.width <= 0) {
    return;
  }
  const ratio = Math.min(0.85, Math.max(0.15, (event.clientX - rect.left) / rect.width));
  workspace.style.setProperty("--pane-left", `${ratio.toFixed(4)}fr`);
  workspace.style.setProperty("--pane-right", `${(1 - ratio).toFixed(4)}fr`);
  refreshEditors();
});

document.addEventListener("pointerup", endSplitterDrag);
document.addEventListener("pointercancel", endSplitterDrag);

document.addEventListener("dblclick", (event) => {
  if (!event.target.closest(".workspace-splitter")) {
    return;
  }
  const workspace = document.querySelector(".workspace");
  workspace?.style.removeProperty("--pane-left");
  workspace?.style.removeProperty("--pane-right");
  refreshEditors();
});
