(function () {
  "use strict";

  const STATUS_OK = "ok";
  const EMPTY_CELL = "-";
  const MILLISECONDS_PER_SECOND = 1000;
  const WHOLE_MS_THRESHOLD = 100;
  const TENTH_MS_THRESHOLD = 10;
  const SAME_SPEED_TOLERANCE = 0.005;
  const DEFAULT_RELEASE_RANGE = "10";
  const ALL_RELEASES_RANGE = "all";
  const RELEASE_RANGE_OPTIONS = [
    { value: "10", label: "Latest 10" },
    { value: "25", label: "Latest 25" },
    { value: ALL_RELEASES_RANGE, label: "All releases" },
  ];
  const MAIN_VERSION = "main";
  const CASE_ORDER = ["small", "large"];
  const FORMATTERS = [
    { key: "default", label: "black/isort(Default)", color: "#f59e0b" },
    { key: "builtin", label: "Built-in", color: "#2563eb" },
    { key: "ruff", label: "Ruff", color: "#16a34a" },
  ];

  if (typeof document === "undefined") {
    if (typeof module !== "undefined" && module.exports) {
      module.exports = Object.freeze({
        chartSelection,
        mainSnapshotStatus,
        mainPanelBounds,
        DEFAULT_RELEASE_RANGE,
        RELEASE_RANGE_OPTIONS,
      });
    }
    return;
  }

  const scriptUrl = document.currentScript && document.currentScript.src ? document.currentScript.src : document.baseURI;
  const dataUrl = new URL("../../data/release-benchmarks.json", scriptUrl);
  const notesUrl = new URL("../../data/release-benchmark-notes.json", scriptUrl);
  const initializedCharts = new WeakSet();
  const initializedApps = new WeakSet();
  const observedCharts = new WeakSet();
  let benchmarkPayloadPromise = null;
  let benchmarkPayload = null;
  let resizeObserver = null;
  let globalEventsAttached = false;
  let instantNavigationHookAttached = false;
  let navigationEventsAttached = false;
  let mutationObserverAttached = false;
  let initializeScheduled = false;

  function chartContainers() {
    return Array.from(document.querySelectorAll("[data-release-benchmark-chart]"));
  }

  function benchmarkApps() {
    return Array.from(document.querySelectorAll("[data-release-benchmark-app]"));
  }

  function versionKey(version) {
    const normalizedVersion = String(version).replace(/^v/, "");
    if (normalizedVersion === MAIN_VERSION) {
      return [1, 0, 0, 0, 0];
    }
    const parts = String(version)
      .replace(/^v/, "")
      .split(/[.\-+_]/)
      .map((part) => (/^\d+$/.test(part) ? Number(part) : 0));
    while (parts.length < 4) {
      parts.push(0);
    }
    parts.unshift(0);
    return parts;
  }

  function compareVersions(left, right) {
    const leftKey = versionKey(left);
    const rightKey = versionKey(right);
    for (let index = 0; index < 5; index += 1) {
      if (leftKey[index] !== rightKey[index]) {
        return leftKey[index] - rightKey[index];
      }
    }
    return String(left).localeCompare(String(right));
  }

  function stringValue(value) {
    return typeof value === "string" ? value : "";
  }

  function numberValue(value) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim() !== "") {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
  }

  function benchmarkEntries(data) {
    if (!data || !Array.isArray(data.entries)) {
      return [];
    }
    return data.entries.filter((entry) => entry && typeof entry === "object");
  }

  function benchmarkMetadata(data) {
    if (!data || !data.metadata || typeof data.metadata !== "object" || Array.isArray(data.metadata)) {
      return {};
    }
    return data.metadata;
  }

  function metadataValue(data, key) {
    const value = benchmarkMetadata(data)[key];
    if (typeof value === "number") {
      return String(value);
    }
    return stringValue(value) || EMPTY_CELL;
  }

  function formatCount(value) {
    const number = numberValue(value);
    if (number === null) {
      return EMPTY_CELL;
    }
    return new Intl.NumberFormat("en-US").format(number);
  }

  function formatMs(value) {
    const number = numberValue(value);
    if (number === null) {
      return EMPTY_CELL;
    }
    if (number >= MILLISECONDS_PER_SECOND) {
      return `${(number / MILLISECONDS_PER_SECOND).toFixed(2)}s`;
    }
    if (number >= WHOLE_MS_THRESHOLD) {
      return `${number.toFixed(0)}ms`;
    }
    if (number >= TENTH_MS_THRESHOLD) {
      return `${number.toFixed(1)}ms`;
    }
    return `${number.toFixed(2)}ms`;
  }

  function inputLabel(inputType) {
    switch (inputType) {
      case "openapi":
        return "OpenAPI";
      case "jsonschema":
        return "JSON Schema";
      default:
        return inputType;
    }
  }

  function formatterLabel(formatter) {
    const knownFormatter = FORMATTERS.find((item) => item.key === formatter);
    return knownFormatter ? knownFormatter.label : formatter;
  }

  function caseSortKey(caseName) {
    const index = CASE_ORDER.indexOf(caseName);
    return [index === -1 ? CASE_ORDER.length : index, caseName];
  }

  function compareCaseNames(left, right) {
    const leftKey = caseSortKey(left);
    const rightKey = caseSortKey(right);
    if (leftKey[0] !== rightKey[0]) {
      return leftKey[0] - rightKey[0];
    }
    return String(leftKey[1]).localeCompare(String(rightKey[1]));
  }

  function compareFormatters(left, right) {
    const leftIndex = FORMATTERS.findIndex((formatter) => formatter.key === left);
    const rightIndex = FORMATTERS.findIndex((formatter) => formatter.key === right);
    const normalizedLeft = leftIndex === -1 ? FORMATTERS.length : leftIndex;
    const normalizedRight = rightIndex === -1 ? FORMATTERS.length : rightIndex;
    if (normalizedLeft !== normalizedRight) {
      return normalizedLeft - normalizedRight;
    }
    return String(left).localeCompare(String(right));
  }

  function findEntry(entries, version, formatter) {
    return entries.find(
      (entry) =>
        entry.version === version &&
        entry.formatter === formatter &&
        entry.status === STATUS_OK &&
        typeof entry.median_ms === "number",
    );
  }

  function scale(value, sourceMin, sourceMax, targetMin, targetMax) {
    if (sourceMax === sourceMin) {
      return (targetMin + targetMax) / 2;
    }
    return targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);
  }

  function maxVersionLabelWidth(context, versions) {
    if (versions.length === 0) {
      return 0;
    }
    return Math.max(...versions.map((version) => context.measureText(version).width));
  }

  function versionTickIndexes(context, versions, plotWidth) {
    if (versions.length <= 1) {
      return versions.length === 1 ? [0] : [];
    }
    const labelWidth = maxVersionLabelWidth(context, versions);
    const minimumSpacing = Math.max(44, Math.ceil(labelWidth) + 16);
    const minimumIndexGap = Math.max(
      1,
      Math.ceil((minimumSpacing / Math.max(plotWidth, 1)) * (versions.length - 1)),
    );
    const indexes = [];
    for (let index = 0; index < versions.length; index += minimumIndexGap) {
      indexes.push(index);
    }

    const lastIndex = versions.length - 1;
    if (indexes[indexes.length - 1] === lastIndex) {
      return indexes;
    }
    if (lastIndex - indexes[indexes.length - 1] < minimumIndexGap && indexes.length > 1) {
      indexes.pop();
    }
    indexes.push(lastIndex);
    return indexes;
  }

  function cssColor(element, name, fallback) {
    const value = getComputedStyle(element).getPropertyValue(name).trim();
    return value || fallback;
  }

  function scenarioEntries(data, inputType, caseName) {
    return benchmarkEntries(data).filter((entry) => entry.input_type === inputType && entry.case === caseName);
  }

  function normalizeNotes(payload) {
    if (!payload || !Array.isArray(payload.notes)) {
      return [];
    }
    return payload.notes.reduce((notesByVersion, note) => {
      if (note && typeof note.version === "string" && typeof note.summary === "string") {
        notesByVersion.push({
          version: note.version,
          summary: note.summary,
          details: typeof note.details === "string" ? note.details : "",
          inputType: typeof note.input_type === "string" ? note.input_type : "",
          caseName: typeof note.case === "string" ? note.case : "",
        });
      }
      return notesByVersion;
    }, []);
  }

  function noteText(note) {
    return note.details ? `${note.summary} ${note.details}` : note.summary;
  }

  function noteApplies(note, inputType, caseName) {
    if (note.inputType && note.inputType !== inputType) {
      return false;
    }
    if (note.caseName && note.caseName !== caseName) {
      return false;
    }
    return true;
  }

  function noteScopeLabel(note) {
    if (!note.inputType && !note.caseName) {
      return "";
    }
    if (note.inputType && !note.caseName) {
      return inputLabel(note.inputType);
    }
    if (!note.inputType) {
      return `${note.caseName[0].toUpperCase()}${note.caseName.slice(1)}`;
    }
    return `${note.caseName[0].toUpperCase()}${note.caseName.slice(1)} / ${inputLabel(note.inputType)}`;
  }

  function scenarioNotesByVersion(notes, inputType, caseName) {
    return notes.reduce((notesByVersion, note) => {
      if (!noteApplies(note, inputType, caseName)) {
        return notesByVersion;
      }
      if (!notesByVersion[note.version]) {
        notesByVersion[note.version] = [];
      }
      notesByVersion[note.version].push(note);
      return notesByVersion;
    }, {});
  }

  function visibleNotes(entries, notes) {
    return notes
      .filter((note) =>
        entries.some(
          (entry) =>
            entry.version === note.version &&
            noteApplies(note, stringValue(entry.input_type), stringValue(entry.case)),
        ),
      )
      .sort((left, right) => {
        const versionComparison = compareVersions(left.version, right.version);
        if (versionComparison !== 0) {
          return versionComparison;
        }
        const inputComparison = left.inputType.localeCompare(right.inputType);
        return inputComparison === 0 ? left.caseName.localeCompare(right.caseName) : inputComparison;
      });
  }

  function scenarios(entries) {
    const seen = new Set();
    const result = [];
    entries.forEach((entry) => {
      const inputType = stringValue(entry.input_type);
      const caseName = stringValue(entry.case);
      if (!inputType || !caseName) {
        return;
      }
      const key = `${inputType}\u0000${caseName}`;
      if (seen.has(key)) {
        return;
      }
      seen.add(key);
      result.push({ inputType, caseName });
    });
    return result.sort((left, right) => {
      const caseComparison = compareCaseNames(left.caseName, right.caseName);
      return caseComparison === 0 ? left.inputType.localeCompare(right.inputType) : caseComparison;
    });
  }

  function latestVersion(entries) {
    const versions = Array.from(new Set(entries.map((entry) => stringValue(entry.version)).filter(Boolean))).sort(
      compareVersions,
    );
    return versions.length === 0 ? "" : versions[versions.length - 1];
  }

  function releaseVersions(entries) {
    const versions = new Set();
    entries.forEach((entry) => {
      const version = stringValue(entry.version);
      if (version && version !== MAIN_VERSION) {
        versions.add(version);
      }
    });
    return Array.from(versions).sort(compareVersions);
  }

  function visibleReleaseVersions(versions, range) {
    const count = Number(range);
    if (!Number.isInteger(count) || count <= 0 || count >= versions.length) {
      return versions;
    }
    return versions.slice(-count);
  }

  function chartSelection(entries, range) {
    const allReleaseVersions = releaseVersions(entries);
    const versions = visibleReleaseVersions(allReleaseVersions, range);
    const selectedVersions = versions === allReleaseVersions ? null : new Set(versions);
    const visibleEntries = selectedVersions
      ? entries.filter((entry) => entry.version === MAIN_VERSION || selectedVersions.has(entry.version))
      : entries;
    const mainEntries = entries.filter((entry) => entry.version === MAIN_VERSION);
    let timingEntryCount = 0;
    let maxMs = 0;
    let hasMain = false;
    visibleEntries.forEach((entry) => {
      if (entry.status !== STATUS_OK || typeof entry.median_ms !== "number") {
        return;
      }
      timingEntryCount += 1;
      maxMs = Math.max(maxMs, entry.median_ms);
      hasMain ||= entry.version === MAIN_VERSION;
    });
    return {
      allReleaseVersions,
      versions,
      visibleEntries,
      mainEntries,
      hasMain,
      timingEntryCount,
      yMax: maxMs > 0 ? maxMs * 1.15 : 1,
    };
  }

  function mainPanelBounds(width, horizontalLabelPadding, hasMain) {
    if (!hasMain) {
      return null;
    }
    const left = width - Math.min(92, Math.max(72, Math.floor(width * 0.23)));
    return { left, right: width - 8, plotRight: left - horizontalLabelPadding };
  }

  function chartRange(container) {
    const range = container.dataset.releaseBenchmarkSelectedRange;
    return RELEASE_RANGE_OPTIONS.some((option) => option.value === range) ? range : DEFAULT_RELEASE_RANGE;
  }

  function rangeLabel(range) {
    const option = RELEASE_RANGE_OPTIONS.find((candidate) => candidate.value === range);
    return option ? option.label : RELEASE_RANGE_OPTIONS[0].label;
  }

  function timestampLabel(value) {
    const timestamp = stringValue(value);
    if (!timestamp) {
      return EMPTY_CELL;
    }
    const parsed = new Date(timestamp);
    if (Number.isNaN(parsed.getTime())) {
      return timestamp;
    }
    const iso = parsed.toISOString();
    return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
  }

  function mainSnapshotLabel(data) {
    const metadata = benchmarkMetadata(data);
    const measuredAt = timestampLabel(metadata.main_snapshot_generated_at);
    const sha = stringValue(metadata.main_snapshot_collector_sha);
    const shaLabel = sha ? ` · ${sha.slice(0, 12)}` : "";
    return measuredAt === EMPTY_CELL ? "Main snapshot time unavailable" : `Main measured ${measuredAt}${shaLabel}`;
  }

  function mainSnapshotStatus(data, hasMainSnapshot, hasMainTiming) {
    if (!hasMainSnapshot) {
      return "No main snapshot in this scenario";
    }
    const label = mainSnapshotLabel(data);
    return hasMainTiming ? label : `${label} · no successful timing`;
  }

  function versionRangeLabel(data, versions) {
    if (versions.length === 0) {
      return "No release versions";
    }
    const firstVersion = versions[0];
    const lastVersion = versions[versions.length - 1];
    const firstDate = releaseDate(data, firstVersion);
    const lastDate = releaseDate(data, lastVersion);
    if (firstDate === EMPTY_CELL || lastDate === EMPTY_CELL) {
      return firstVersion === lastVersion ? firstVersion : `${firstVersion}–${lastVersion}`;
    }
    return `${firstVersion} (${firstDate})–${lastVersion} (${lastDate})`;
  }

  function releaseDate(data, version) {
    const releaseDates = benchmarkMetadata(data).release_dates;
    if (!releaseDates || typeof releaseDates !== "object" || Array.isArray(releaseDates)) {
      return EMPTY_CELL;
    }
    const uploadedAt = stringValue(releaseDates[version]);
    if (!uploadedAt) {
      return EMPTY_CELL;
    }
    return timestampLabel(uploadedAt);
  }

  function ratioLabel(ratio) {
    if (Math.abs(ratio - 1) <= SAME_SPEED_TOLERANCE) {
      return "same speed";
    }
    if (ratio > 1) {
      return `${ratio.toFixed(2)}x faster`;
    }
    return `${(1 / ratio).toFixed(2)}x slower`;
  }

  function speedLabel(entry, baseline) {
    const entryMedian = numberValue(entry && entry.median_ms);
    const baselineMedian = numberValue(baseline && baseline.median_ms);
    if (entryMedian === null || baselineMedian === null || entryMedian <= 0 || baselineMedian <= 0) {
      return "";
    }
    return ratioLabel(baselineMedian / entryMedian);
  }

  function statusLabel(entry) {
    const status = stringValue(entry.status) || "unknown";
    let prefix = status;
    switch (status) {
      case STATUS_OK:
        return "OK";
      case "unsupported":
        prefix = "Unsupported";
        break;
      case "failed":
        prefix = "Failed";
        break;
      default:
        prefix = status ? `${status[0].toUpperCase()}${status.slice(1)}` : "Unknown";
        break;
    }
    const error = stringValue(entry.error);
    if (!error) {
      return prefix;
    }
    if (/timeout/i.test(error)) {
      return `${prefix}: timeout`;
    }
    if (/Failed to build|Failed to install|Could not install|No matching distribution/i.test(error)) {
      return `${prefix}: install`;
    }
    if (/unrecognized arguments|invalid choice|No such option|unsupported/i.test(error)) {
      return `${prefix}: unavailable`;
    }
    return `${prefix}: command`;
  }

  function createElement(tagName, className) {
    const element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    return element;
  }

  function appendText(parent, tagName, text, className) {
    const element = createElement(tagName, className);
    element.textContent = text;
    parent.append(element);
    return element;
  }

  function codeElement(text) {
    const element = document.createElement("code");
    element.textContent = text;
    return element;
  }

  function appendCell(row, value, tagName) {
    const cell = document.createElement(tagName);
    if (value instanceof Node) {
      cell.append(value);
    } else {
      cell.textContent = String(value);
    }
    row.append(cell);
    return cell;
  }

  function benchmarkTable(headers, rows) {
    const table = createElement("table", "release-benchmark-table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    headers.forEach((header) => appendCell(headRow, header, "th"));
    thead.append(headRow);
    const tbody = document.createElement("tbody");
    rows.forEach((values) => {
      const row = document.createElement("tr");
      values.forEach((value) => appendCell(row, value, "td"));
      tbody.append(row);
    });
    table.append(thead, tbody);
    return table;
  }

  function chartElement(inputType, caseName) {
    const container = createElement("div", "release-benchmark-chart");
    container.setAttribute("data-release-benchmark-chart", "");
    container.dataset.inputType = inputType;
    container.dataset.case = caseName;
    container.dataset.releaseBenchmarkSelectedRange = DEFAULT_RELEASE_RANGE;
    container.setAttribute("aria-label", `${caseName} / ${inputLabel(inputType)} release benchmark trend`);

    const controls = createElement("div", "release-benchmark-chart__controls");
    controls.setAttribute("role", "group");
    controls.setAttribute("aria-label", "Release range");
    appendText(controls, "span", "Show:", "release-benchmark-chart__controls-label");
    RELEASE_RANGE_OPTIONS.forEach((option) => {
      const button = createElement("button", "release-benchmark-chart__range-button");
      button.type = "button";
      button.dataset.releaseBenchmarkRangeOption = option.value;
      button.setAttribute("aria-pressed", String(option.value === DEFAULT_RELEASE_RANGE));
      button.textContent = option.label;
      controls.append(button);
    });
    const rangeInfo = createElement("span", "release-benchmark-chart__range-info");
    rangeInfo.setAttribute("aria-live", "polite");
    const mainSnapshot = createElement("span", "release-benchmark-chart__main-snapshot");
    const canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", "Median generation time by release version");
    const legend = createElement("span", "release-benchmark-chart__legend");
    legend.setAttribute("aria-hidden", "true");
    const status = createElement("span", "release-benchmark-chart__status");
    status.setAttribute("role", "status");
    status.textContent = "Loading benchmark chart...";
    const tooltip = createElement("span", "release-benchmark-chart__tooltip");
    tooltip.setAttribute("role", "status");
    tooltip.hidden = true;
    container.append(controls, rangeInfo, mainSnapshot, canvas, legend, status, tooltip);
    return container;
  }

  function versionElement(version, notesByVersion) {
    const versionNotes = notesByVersion[version];
    if (!versionNotes || versionNotes.length === 0) {
      return version;
    }
    const element = createElement("span", "release-benchmark-version-note");
    element.title = versionNotes.map((note) => note.summary).join(" ");
    element.textContent = `${version} *`;
    return element;
  }

  function formatterHistoryCell(entry, baseline) {
    if (!entry) {
      return EMPTY_CELL;
    }
    if (entry.status !== STATUS_OK) {
      return statusLabel(entry);
    }
    if (entry.formatter === "default") {
      return formatMs(entry.median_ms);
    }
    const speed = speedLabel(entry, baseline);
    return speed ? `${formatMs(entry.median_ms)} (${speed})` : formatMs(entry.median_ms);
  }

  function relativeSpeedCell(entry, baseline) {
    if (entry.status !== STATUS_OK) {
      return EMPTY_CELL;
    }
    if (entry.formatter === "default") {
      return "baseline";
    }
    return speedLabel(entry, baseline) || EMPTY_CELL;
  }

  function rangeCell(entry) {
    if (entry.status !== STATUS_OK) {
      return EMPTY_CELL;
    }
    return `${formatMs(entry.min_ms)}-${formatMs(entry.max_ms)}`;
  }

  function entriesByFormatter(entries) {
    return entries.reduce((grouped, entry) => {
      const formatter = stringValue(entry.formatter);
      if (formatter) {
        grouped[formatter] = entry;
      }
      return grouped;
    }, {});
  }

  function renderChartSection(fragment, entries) {
    const scenarioList = scenarios(entries);
    if (scenarioList.length === 0) {
      appendText(fragment, "p", "No benchmark data is available yet.", "release-benchmark-app__status");
      return;
    }
    const charts = createElement("div", "release-benchmark-chart-list");
    scenarioList.forEach(({ inputType, caseName }) => {
      appendText(charts, "h3", `${caseName[0].toUpperCase()}${caseName.slice(1)} / ${inputLabel(inputType)}`);
      charts.append(chartElement(inputType, caseName));
    });
    fragment.append(charts);
  }

  function renderBenchmarkNotes(fragment, entries, notes) {
    const noteList = visibleNotes(entries, notes);
    if (noteList.length === 0) {
      return;
    }
    appendText(fragment, "h2", "Benchmark Notes");
    appendText(
      fragment,
      "p",
      "Version markers in the charts and historical tables point to these benchmark interpretation notes.",
    );
    const list = document.createElement("ul");
    noteList.forEach((note) => {
      const item = document.createElement("li");
      item.append(codeElement(note.version));
      if (noteScopeLabel(note)) {
        item.append(` (${noteScopeLabel(note)})`);
      }
      item.append(`: ${noteText(note)}`);
      list.append(item);
    });
    fragment.append(list);
  }

  function selectedVersionCount(data, entries) {
    const selectedVersions = stringValue(benchmarkMetadata(data).selected_versions);
    if (!selectedVersions) {
      return new Set(entries.map((entry) => stringValue(entry.version)).filter(Boolean)).size;
    }
    return selectedVersions.split(",").filter(Boolean).length;
  }

  function entryPythonVersions(data, entries) {
    const versions = Array.from(new Set(entries.map((entry) => stringValue(entry.python_version)).filter(Boolean)));
    if (versions.length > 0) {
      return versions.sort().join(", ");
    }
    return metadataValue(data, "python_version");
  }

  function renderLatestDataset(fragment, data, entries) {
    appendText(fragment, "h2", "Latest Dataset");
    const list = document.createElement("ul");
    [
      ["Schema version", String(data && data.schema_version ? data.schema_version : 1)],
      ["Generated at", metadataValue(data, "generated_at")],
      ["Source workflow", metadataValue(data, "workflow")],
      ["Primary Python version", metadataValue(data, "python_version")],
      ["Entry Python versions", entryPythonVersions(data, entries)],
      ["Benchmark runs per case", metadataValue(data, "runs_per_case")],
      ["Version selection", metadataValue(data, "selection_strategy")],
      ["Selected versions", String(selectedVersionCount(data, entries))],
      ["Download source", metadataValue(data, "download_source")],
      ["Download window", `${metadataValue(data, "download_window_days")} days`],
      ["Downloads in window", formatCount(benchmarkMetadata(data).download_window_total)],
      ["PyPIStats last month", formatCount(benchmarkMetadata(data).pypistats_last_month)],
    ].forEach(([label, value]) => {
      const item = document.createElement("li");
      item.append(`${label}: `, codeElement(value));
      list.append(item);
    });
    fragment.append(list);
  }

  function renderLatestSummary(fragment, entries) {
    const version = latestVersion(entries);
    if (!version) {
      return;
    }
    const latestEntries = entries.filter((entry) => entry.version === version);
    appendText(fragment, "h2", "Latest Release Summary");
    appendText(
      fragment,
      "p",
      "Results below are medians. Built-in and Ruff ratios are relative to the black/isort(Default) formatter for the same scenario.",
    );
    appendText(fragment, "h3", version);
    Array.from(new Set(latestEntries.map((entry) => stringValue(entry.case)).filter(Boolean)))
      .sort(compareCaseNames)
      .forEach((caseName) => {
        const caseEntries = latestEntries.filter((entry) => entry.case === caseName);
        const rows = [];
        scenarios(caseEntries).forEach(({ inputType }) => {
          const scenarioEntriesForCase = caseEntries.filter((entry) => entry.input_type === inputType);
          const byFormatter = entriesByFormatter(scenarioEntriesForCase);
          Object.keys(byFormatter)
            .sort(compareFormatters)
            .forEach((formatter) => {
              const entry = byFormatter[formatter];
              rows.push([
                inputLabel(inputType),
                formatterLabel(formatter),
                formatMs(entry.median_ms),
                relativeSpeedCell(entry, byFormatter.default),
                rangeCell(entry),
                statusLabel(entry),
              ]);
            });
        });
        if (rows.length > 0) {
          appendText(fragment, "h4", `${caseName[0].toUpperCase()}${caseName.slice(1)}`);
          fragment.append(
            benchmarkTable(["Input", "Formatter", "Median", "vs black/isort(Default)", "Range", "Status"], rows),
          );
        }
      });
  }

  function renderHistoricalResults(fragment, data, entries, notes) {
    appendText(fragment, "h2", "Historical Results");
    appendText(
      fragment,
      "p",
      "Rows are release versions, newest first, with main shown before releases when present. Released is the PyPI upload timestamp in UTC. Formatter cells show median generation time and relative speed when available.",
    );
    scenarios(entries).forEach(({ inputType, caseName }) => {
      const scenarioEntriesForCase = entries.filter(
        (entry) => entry.input_type === inputType && entry.case === caseName,
      );
      const notesByVersion = scenarioNotesByVersion(notes, inputType, caseName);
      const rows = [];
      Array.from(new Set(scenarioEntriesForCase.map((entry) => stringValue(entry.version)).filter(Boolean)))
        .sort(compareVersions)
        .reverse()
        .forEach((version) => {
          const versionEntries = scenarioEntriesForCase.filter((entry) => entry.version === version);
          if (!versionEntries.some((entry) => entry.status === STATUS_OK)) {
            return;
          }
          const byFormatter = entriesByFormatter(versionEntries);
          rows.push([
            versionElement(version, notesByVersion),
            releaseDate(data, version),
            ...FORMATTERS.map((formatter) => formatterHistoryCell(byFormatter[formatter.key], byFormatter.default)),
          ]);
        });
      if (rows.length === 0) {
        return;
      }
      appendText(fragment, "h3", `${caseName[0].toUpperCase()}${caseName.slice(1)} / ${inputLabel(inputType)}`);
      fragment.append(
        benchmarkTable(["Version", "Released", ...FORMATTERS.map((formatter) => formatter.label)], rows),
      );
    });
  }

  function renderBenchmarkApp(container, data, notes) {
    const entries = benchmarkEntries(data);
    const fragment = document.createDocumentFragment();
    if (entries.length === 0) {
      appendText(fragment, "p", "No release benchmark data has been committed yet.", "release-benchmark-app__status");
      container.replaceChildren(fragment);
      return;
    }
    const backfillNote = stringValue(benchmarkMetadata(data).compatibility_backfill);
    if (backfillNote) {
      appendText(fragment, "p", `Compatibility backfill: ${backfillNote}`);
    }
    renderChartSection(fragment, entries);
    renderBenchmarkNotes(fragment, entries, notes);
    renderLatestDataset(fragment, data, entries);
    renderLatestSummary(fragment, entries);
    renderHistoricalResults(fragment, data, entries, notes);
    container.replaceChildren(fragment);
    scheduleInitializeCharts();
  }

  function renderLegend(container) {
    const legend = container.querySelector(".release-benchmark-chart__legend");
    if (!legend) {
      return;
    }
    const label = document.createElement("span");
    label.className = "release-benchmark-chart__legend-label";
    label.textContent = "Formatter:";
    const legendItems = FORMATTERS.map((formatter) => {
      const item = document.createElement("span");
      const swatch = document.createElement("span");
      item.className = "release-benchmark-chart__legend-item";
      swatch.className = "release-benchmark-chart__swatch";
      swatch.style.background = formatter.color;
      item.append(swatch, formatter.label);
      return item;
    });
    legend.replaceChildren(label, ...legendItems);
  }

  function setStatus(container, message) {
    const status = container.querySelector(".release-benchmark-chart__status");
    if (!status) {
      return;
    }
    status.textContent = message;
    status.hidden = message === "";
  }

  function updateChartControls(
    container,
    data,
    range,
    versions,
    allReleaseVersions,
    yMax,
    hasMainSnapshot,
    hasMainTiming,
  ) {
    const rangeInfo = container.querySelector(".release-benchmark-chart__range-info");
    if (rangeInfo) {
      rangeInfo.textContent =
        `Showing ${rangeLabel(range)} (${versionRangeLabel(data, versions)}) ` +
        `of ${allReleaseVersions.length} release versions · scale 0–${formatMs(yMax)}`;
    }
    const mainSnapshot = container.querySelector(".release-benchmark-chart__main-snapshot");
    if (mainSnapshot) {
      mainSnapshot.textContent = mainSnapshotStatus(data, hasMainSnapshot, hasMainTiming);
    }
    container.querySelectorAll("[data-release-benchmark-range-option]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.releaseBenchmarkRangeOption === range));
    });
  }

  function setChartRange(container, range) {
    if (!RELEASE_RANGE_OPTIONS.some((option) => option.value === range)) {
      return;
    }
    if (chartRange(container) === range) {
      return;
    }
    container.dataset.releaseBenchmarkSelectedRange = range;
    hideTooltip(container);
    if (benchmarkPayload) {
      drawChart(container, benchmarkPayload.data, benchmarkPayload.notes);
    }
  }

  function chartSize(canvas) {
    const rect = canvas.getBoundingClientRect();
    const style = getComputedStyle(canvas);
    const width = Math.max(320, Math.floor(rect.width));
    const height = Math.max(260, Number.parseFloat(style.height) || 420);
    return { width, height };
  }

  function clearCanvas(canvas) {
    const { width, height } = chartSize(canvas);
    const devicePixelRatio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(width * devicePixelRatio);
    canvas.height = Math.floor(height * devicePixelRatio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const context = canvas.getContext("2d");
    if (!context) {
      return null;
    }
    context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    context.clearRect(0, 0, width, height);
    return { context, width, height };
  }

  function isVisible(element) {
    return Boolean(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
  }

  function drawChart(container, data, notes) {
    const canvas = container.querySelector("canvas");
    if (!(canvas instanceof HTMLCanvasElement)) {
      return;
    }
    if (!isVisible(container)) {
      return;
    }

    const inputType = container.dataset.inputType || "";
    const caseName = container.dataset.case || "";
    const entries = scenarioEntries(data, inputType, caseName);
    const notesByVersion = scenarioNotesByVersion(notes, inputType, caseName);
    const range = chartRange(container);
    const { allReleaseVersions, versions, visibleEntries, mainEntries, hasMain, timingEntryCount, yMax } =
      chartSelection(entries, range);
    const hasMainSnapshot = mainEntries.length > 0;
    const chart = clearCanvas(canvas);
    if (!chart) {
      return;
    }
    const { context, width, height } = chart;

    if (timingEntryCount === 0) {
      updateChartControls(
        container,
        data,
        range,
        versions,
        allReleaseVersions,
        1,
        hasMainSnapshot,
        hasMain,
      );
      setStatus(container, "No benchmark data is available for this chart.");
      container.releaseBenchmarkPoints = [];
      container.releaseBenchmarkNotes = [];
      return;
    }
    setStatus(container, "");

    const foreground = cssColor(container, "--md-default-fg-color", "#111827");
    const muted = cssColor(container, "--md-default-fg-color--light", "#6b7280");
    const grid = cssColor(container, "--md-default-fg-color--lightest", "#d1d5db");
    const noteColor = cssColor(container, "--md-accent-fg-color", "#dc2626");
    context.font = "12px system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
    const horizontalLabelPadding = Math.ceil(maxVersionLabelWidth(context, versions) / 2) + 8;
    const mainPanel = mainPanelBounds(width, horizontalLabelPadding, hasMain);
    const plot = {
      left: Math.max(58, horizontalLabelPadding),
      top: 38,
      right: mainPanel ? mainPanel.plotRight : width - Math.max(18, horizontalLabelPadding),
      bottom: height - 58,
    };
    const points = [];
    updateChartControls(
      container,
      data,
      range,
      versions,
      allReleaseVersions,
      yMax,
      hasMainSnapshot,
      hasMain,
    );

    context.fillStyle = foreground;
    context.fillText(`${caseName[0].toUpperCase()}${caseName.slice(1)} / ${inputLabel(inputType)}`, plot.left, 20);

    if (mainPanel) {
      context.save();
      context.fillStyle = grid;
      context.globalAlpha = 0.25;
      context.fillRect(mainPanel.left, plot.top, mainPanel.right - mainPanel.left, plot.bottom - plot.top);
      context.globalAlpha = 1;
      context.strokeStyle = muted;
      context.setLineDash([4, 4]);
      context.beginPath();
      context.moveTo(mainPanel.left - 5, plot.top);
      context.lineTo(mainPanel.left - 5, plot.bottom);
      context.stroke();
      context.setLineDash([]);
      context.fillStyle = muted;
      context.textAlign = "center";
      context.fillText("main snapshot", (mainPanel.left + mainPanel.right) / 2, plot.top - 10);
      context.restore();
    }

    context.strokeStyle = grid;
    context.fillStyle = muted;
    context.lineWidth = 1;
    for (let index = 0; index <= 4; index += 1) {
      const value = (yMax * index) / 4;
      const y = scale(value, 0, yMax, plot.bottom, plot.top);
      context.beginPath();
      context.moveTo(plot.left, y);
      context.lineTo(plot.right, y);
      context.stroke();
      context.textAlign = "right";
      context.fillText(formatMs(value), plot.left - 10, y + 4);
    }

    context.textAlign = "center";
    versionTickIndexes(context, versions, plot.right - plot.left).forEach((index) => {
      const version = versions[index];
      const x = scale(index, 0, Math.max(versions.length - 1, 1), plot.left, plot.right);
      context.strokeStyle = grid;
      context.beginPath();
      context.moveTo(x, plot.top);
      context.lineTo(x, plot.bottom);
      context.stroke();
      context.fillStyle = muted;
      context.fillText(version, x, plot.bottom + 24);
    });
    if (mainPanel) {
      context.fillStyle = muted;
      context.fillText("main", (mainPanel.left + mainPanel.right) / 2, plot.bottom + 24);
    }

    context.strokeStyle = foreground;
    context.beginPath();
    context.moveTo(plot.left, plot.top);
    context.lineTo(plot.left, plot.bottom);
    context.lineTo(plot.right, plot.bottom);
    context.stroke();

    const noteMarkers = [];
    context.save();
    context.strokeStyle = noteColor;
    context.fillStyle = noteColor;
    context.textAlign = "center";
    context.setLineDash([4, 4]);
    versions.forEach((version, index) => {
      const versionNotes = notesByVersion[version];
      if (!versionNotes || versionNotes.length === 0) {
        return;
      }
      const x = scale(index, 0, Math.max(versions.length - 1, 1), plot.left, plot.right);
      noteMarkers.push({ x, version, notes: versionNotes, plotTop: plot.top, plotBottom: plot.bottom });
      context.beginPath();
      context.moveTo(x, plot.top);
      context.lineTo(x, plot.bottom);
      context.stroke();
      context.fillText("*", x, plot.top - 10);
    });
    context.restore();

    FORMATTERS.forEach((formatter) => {
      const releasePoints = [];
      versions.forEach((version, index) => {
        const entry = findEntry(visibleEntries, version, formatter.key);
        if (!entry) {
          return;
        }
        const x = scale(index, 0, Math.max(versions.length - 1, 1), plot.left, plot.right);
        const y = scale(entry.median_ms, 0, yMax, plot.bottom, plot.top);
        releasePoints.push({ x, y, entry, formatter });
        points.push({ x, y, entry, formatter });
      });
      if (releasePoints.length > 0) {
        context.strokeStyle = formatter.color;
        context.lineWidth = 2.4;
        context.beginPath();
        releasePoints.forEach((point, index) => {
          if (index === 0) {
            context.moveTo(point.x, point.y);
            return;
          }
          context.lineTo(point.x, point.y);
        });
        context.stroke();

        context.fillStyle = formatter.color;
        releasePoints.forEach((point) => {
          context.beginPath();
          context.arc(point.x, point.y, 3, 0, Math.PI * 2);
          context.fill();
        });
      }
      const mainEntry = findEntry(mainEntries, MAIN_VERSION, formatter.key);
      if (!mainEntry || !mainPanel) {
        return;
      }
      const x = (mainPanel.left + mainPanel.right) / 2;
      const y = scale(mainEntry.median_ms, 0, yMax, plot.bottom, plot.top);
      const point = { x, y, entry: mainEntry, formatter, mainSnapshot: true, snapshotLabel: mainSnapshotLabel(data) };
      points.push(point);
      context.save();
      context.fillStyle = formatter.color;
      context.translate(x, y);
      context.rotate(Math.PI / 4);
      context.fillRect(-3.5, -3.5, 7, 7);
      context.restore();
    });

    container.releaseBenchmarkPoints = points;
    container.releaseBenchmarkNotes = noteMarkers;
  }

  function showTooltip(container, event) {
    const tooltip = container.querySelector(".release-benchmark-chart__tooltip");
    const canvas = container.querySelector("canvas");
    if (!tooltip || !(canvas instanceof HTMLCanvasElement) || !container.releaseBenchmarkPoints) {
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    let nearest = null;
    let nearestDistance = 14;
    container.releaseBenchmarkPoints.forEach((point) => {
      const distance = Math.hypot(point.x - x, point.y - y);
      if (distance < nearestDistance) {
        nearest = point;
        nearestDistance = distance;
      }
    });
    let nearestNote = null;
    let nearestNoteDistance = 10;
    if (container.releaseBenchmarkNotes) {
      container.releaseBenchmarkNotes.forEach((marker) => {
        const isInsidePlot = y >= marker.plotTop - 24 && y <= marker.plotBottom + 30;
        const distance = Math.abs(marker.x - x);
        if (isInsidePlot && distance < nearestNoteDistance) {
          nearestNote = marker;
          nearestNoteDistance = distance;
        }
      });
    }
    if (nearestNote && (!nearest || nearestNoteDistance <= nearestDistance)) {
      tooltip.textContent = `${nearestNote.version}: ${nearestNote.notes.map(noteText).join(" ")}`;
      tooltip.style.left = `${canvas.offsetLeft + nearestNote.x}px`;
      tooltip.style.top = `${canvas.offsetTop + nearestNote.plotTop}px`;
      tooltip.hidden = false;
      return;
    }
    if (!nearest) {
      tooltip.hidden = true;
      return;
    }
    const snapshot = nearest.mainSnapshot ? ` · ${nearest.snapshotLabel}` : "";
    tooltip.textContent =
      `${nearest.entry.version} · ${nearest.formatter.label}: ` + `${formatMs(nearest.entry.median_ms)}${snapshot}`;
    tooltip.style.left = `${canvas.offsetLeft + nearest.x}px`;
    tooltip.style.top = `${canvas.offsetTop + nearest.y}px`;
    tooltip.hidden = false;
  }

  function hideTooltip(container) {
    const tooltip = container.querySelector(".release-benchmark-chart__tooltip");
    if (tooltip) {
      tooltip.hidden = true;
    }
  }

  function renderAll(data, notes, charts) {
    charts.forEach((container) => drawChart(container, data, notes));
  }

  function scheduleRender(data, notes, charts) {
    let rendered = false;
    const render = () => {
      if (rendered) {
        return;
      }
      rendered = true;
      renderAll(data, notes, charts);
    };
    window.requestAnimationFrame(render);
    window.setTimeout(render, 100);
  }

  function initializedChartContainers() {
    return chartContainers().filter((container) => initializedCharts.has(container));
  }

  function scheduleInitializedChartsRender() {
    if (!benchmarkPayload) {
      return;
    }
    scheduleRender(benchmarkPayload.data, benchmarkPayload.notes, initializedChartContainers());
  }

  function initializeBenchmarkApps() {
    const apps = benchmarkApps().filter((container) => !initializedApps.has(container));
    if (apps.length === 0) {
      return;
    }
    apps.forEach((container) => {
      initializedApps.add(container);
      const status = container.querySelector("[data-release-benchmark-status]");
      if (status) {
        status.textContent = "Loading benchmark data...";
      }
    });
    loadBenchmarkPayload()
      .then(({ data, notes }) => {
        apps.forEach((container) => renderBenchmarkApp(container, data, notes));
      })
      .catch((error) => {
        apps.forEach((container) => {
          container.replaceChildren();
          appendText(container, "p", error.message, "release-benchmark-app__status");
        });
      });
  }

  function loadBenchmarkPayload() {
    if (benchmarkPayloadPromise) {
      return benchmarkPayloadPromise;
    }
    benchmarkPayloadPromise = Promise.all([
      fetch(dataUrl).then((response) => {
        if (!response.ok) {
          throw new Error(`Could not load ${dataUrl.pathname}`);
        }
        return response.json();
      }),
      fetch(notesUrl)
        .then((response) => (response.ok ? response.json() : { notes: [] }))
        .then(normalizeNotes)
        .catch(() => []),
    ]).then((response) => {
      const [data, notes] = response;
      benchmarkPayload = { data, notes };
      return benchmarkPayload;
    });
    return benchmarkPayloadPromise;
  }

  function observeChart(container) {
    if (!("ResizeObserver" in window) || observedCharts.has(container)) {
      return;
    }
    if (!resizeObserver) {
      resizeObserver = new ResizeObserver(scheduleInitializedChartsRender);
    }
    resizeObserver.observe(container);
    observedCharts.add(container);
  }

  function attachGlobalEvents() {
    if (globalEventsAttached) {
      return;
    }
    globalEventsAttached = true;
    window.addEventListener("resize", scheduleInitializedChartsRender);
    document.addEventListener("click", (event) => {
      if (event.target instanceof Element && event.target.closest("[data-release-benchmark-range-option]")) {
        return;
      }
      window.setTimeout(scheduleInitializedChartsRender, 50);
    });
    document.addEventListener("change", () => window.setTimeout(scheduleInitializedChartsRender, 50));
    document.addEventListener("visibilitychange", scheduleInitializedChartsRender);
  }

  // Material instant navigation swaps page content without re-running destination extra scripts.
  function scheduleNavigationRetry() {
    [0, 50, 250, 750].forEach((delay) => {
      window.setTimeout(() => {
        initializeBenchmarkApps();
        scheduleInitializeCharts();
      }, delay);
    });
  }

  function attachNavigationEvents() {
    if (navigationEventsAttached) {
      return;
    }
    navigationEventsAttached = true;
    document.addEventListener(
      "click",
      (event) => {
        const anchor = event.target instanceof Element ? event.target.closest("a") : null;
        if (!(anchor instanceof HTMLAnchorElement) || anchor.target || anchor.origin !== window.location.origin) {
          return;
        }
        scheduleNavigationRetry();
      },
      true,
    );
    window.addEventListener("popstate", scheduleNavigationRetry);
  }

  function prepareChart(container) {
    if (initializedCharts.has(container)) {
      return;
    }
    initializedCharts.add(container);
    renderLegend(container);
    setStatus(container, "Loading benchmark chart...");
    container.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("[data-release-benchmark-range-option]")
        : null;
      if (!(button instanceof HTMLButtonElement)) {
        return;
      }
      setChartRange(container, button.dataset.releaseBenchmarkRangeOption || "");
    });
    container.addEventListener("mousemove", (event) => showTooltip(container, event));
    container.addEventListener("mouseleave", () => hideTooltip(container));
    observeChart(container);
  }

  function initializeCharts() {
    const charts = chartContainers();
    if (charts.length === 0) {
      return;
    }
    charts.forEach(prepareChart);
    attachGlobalEvents();
    loadBenchmarkPayload()
      .then(({ data, notes }) => {
        scheduleRender(data, notes, charts);
      })
      .catch((error) => {
        charts.forEach((container) => {
          setStatus(container, error.message);
          const tooltip = container.querySelector(".release-benchmark-chart__tooltip");
          if (tooltip) {
            tooltip.textContent = error.message;
            tooltip.hidden = false;
          }
        });
      });
  }

  function scheduleInitializeCharts() {
    if (initializeScheduled) {
      return;
    }
    initializeScheduled = true;
    window.setTimeout(() => {
      initializeScheduled = false;
      initializeCharts();
    }, 0);
  }

  function attachMutationObserver() {
    if (mutationObserverAttached || !("MutationObserver" in window) || !document.documentElement) {
      return;
    }
    mutationObserverAttached = true;
    const observer = new MutationObserver(() => {
      if (benchmarkApps().some((container) => !initializedApps.has(container))) {
        initializeBenchmarkApps();
      }
      if (chartContainers().some((container) => !initializedCharts.has(container))) {
        scheduleInitializeCharts();
      }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }

  function instantNavigationDocument() {
    if (window.document$ && typeof window.document$.subscribe === "function") {
      return window.document$;
    }
    if (typeof document$ !== "undefined" && typeof document$.subscribe === "function") {
      return document$;
    }
    return null;
  }

  function attachInstantNavigationHook() {
    if (instantNavigationHookAttached) {
      return;
    }
    const navigationDocument = instantNavigationDocument();
    if (!navigationDocument) {
      return;
    }
    instantNavigationHookAttached = true;
    try {
      navigationDocument.subscribe(start);
    } catch {
      // Initial rendering and navigation retry still cover environments without this observable.
    }
  }

  function start() {
    attachNavigationEvents();
    attachMutationObserver();
    initializeBenchmarkApps();
    scheduleInitializeCharts();
    attachInstantNavigationHook();
  }

  if (document.readyState === "loading") {
    document.addEventListener(
      "DOMContentLoaded",
      start,
      { once: true },
    );
  }
  start();
  window.addEventListener("load", start, { once: true });
  window.setTimeout(start, 0);
})();
