import { expose } from "https://cdn.jsdelivr.net/npm/comlink@4.4.2/dist/esm/comlink.mjs";
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.0/full/pyodide.mjs";

const PYODIDE_VERSION = "314.0.0";
const PYODIDE_INDEX = "https://cdn.jsdelivr.net/pyodide/v314.0.0/full/";
const STANDARD_RUNTIME_PACKAGES = [
  "inflect>=4.1,<8",
  "pydantic>=2.12,<3",
  "pyyaml>=6.0.1",
];
const LEGACY_APP_PACKAGES = ["tdom"];
const INPUT_TYPE_PACKAGES = {
  csv: ["genson>=1.2.1,<2"],
  dict: ["genson>=1.2.1,<2"],
  graphql: ["graphql-core>=3.2.3"],
  json: ["genson>=1.2.1,<2"],
  yaml: ["genson>=1.2.1,<2"],
};

let pyodideReadyPromise = null;
let bootInfo = null;
let statusCallback = null;
let activeVersion = null;
const packageInstallPromises = new Map();
let packageInstallQueue = Promise.resolve();

function postStatus(message) {
  statusCallback?.(message);
}

async function fetchText(url) {
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) {
    throw new Error(`Could not load ${url}: ${response.status}`);
  }
  return response.text();
}

function withDefaultVersionConfig(versionConfig = {}) {
  const config = versionConfig || {};
  const assetBaseUrl = config.assetBaseUrl || new URL("./generated/", self.location.href).toString();
  return {
    id: config.id || "current",
    label: config.label || "Current build",
    assetBaseUrl,
    metadataUrl: config.metadataUrl || new URL("codegen-ui-metadata.json", assetBaseUrl).toString(),
    appUrl: config.appUrl || new URL("runtime.py", assetBaseUrl).toString(),
    install: config.install || null,
    runtimePackages: Array.isArray(config.runtimePackages) ? config.runtimePackages : [],
  };
}

function packageInstallFromMetadata(metadata, versionConfig) {
  const install = versionConfig.install;
  if (install?.type === "wheel" && install.url) {
    return {
      source: install.url,
      deps: install.deps === true,
    };
  }
  if (install?.type === "requirement" && install.requirement) {
    return {
      source: install.requirement,
      deps: install.deps === true,
    };
  }
  if (metadata.package_wheel) {
    return {
      source: new URL(metadata.package_wheel, versionConfig.assetBaseUrl).toString(),
      deps: false,
    };
  }
  return {
    source: "datamodel-code-generator",
    deps: false,
  };
}

async function installPythonPackages(pyodide, packages, message) {
  const missingPackages = packages.filter((name) => !packageInstallPromises.has(name));
  if (missingPackages.length === 0) {
    return Promise.all(packages.map((name) => packageInstallPromises.get(name)));
  }

  const installPromise = packageInstallQueue.then(async () => {
    postStatus(message);
    pyodide.globals.set("packages_json", JSON.stringify(missingPackages));
    try {
      await pyodide.runPythonAsync(`
import json
import micropip
await micropip.install(json.loads(packages_json))
`);
    } finally {
      pyodide.globals.delete("packages_json");
    }
  });
  packageInstallQueue = installPromise.catch(() => {});
  missingPackages.forEach((name) => packageInstallPromises.set(name, installPromise));

  return Promise.all(packages.map((name) => packageInstallPromises.get(name)));
}

function packagesForInputType(inputType) {
  return INPUT_TYPE_PACKAGES[inputType] || [];
}

function packagesForRuntimeApp(versionConfig) {
  const appPath = new URL(versionConfig.appUrl).pathname;
  return appPath.endsWith("/app.py") ? LEGACY_APP_PACKAGES : [];
}

async function initPyodide(versionConfig) {
  activeVersion = withDefaultVersionConfig(versionConfig);
  const metadataJson = await fetchText(activeVersion.metadataUrl);
  const metadata = JSON.parse(metadataJson);

  postStatus("Loading Pyodide...");
  const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX });

  postStatus("Loading micropip...");
  await pyodide.loadPackage("micropip");

  await installPythonPackages(
    pyodide,
    [...STANDARD_RUNTIME_PACKAGES, ...activeVersion.runtimePackages, ...packagesForRuntimeApp(activeVersion)],
    "Installing generator runtime dependencies...",
  );

  const packageInstall = packageInstallFromMetadata(metadata, activeVersion);
  postStatus(`Installing datamodel-code-generator (${activeVersion.label})...`);
  pyodide.globals.set("package_source", packageInstall.source);
  pyodide.globals.set("package_deps", packageInstall.deps);
  await pyodide.runPythonAsync(`
import micropip
await micropip.install(package_source, deps=package_deps)
`);
  pyodide.globals.delete("package_source");
  pyodide.globals.delete("package_deps");

  const appSource = await fetchText(activeVersion.appUrl);
  pyodide.runPython(appSource);
  pyodide.globals.get("set_ui_metadata")(metadataJson);

  bootInfo = {
    metadataJson,
    codegenVersion: pyodide.runPython(
      "from importlib.metadata import version\nversion('datamodel-code-generator')",
    ),
    selectedVersion: activeVersion.id,
    selectedVersionLabel: activeVersion.label,
    pyodideVersion: PYODIDE_VERSION,
    pythonVersion: pyodide.runPython("import sys; sys.version.split()[0]"),
  };
  return pyodide;
}

async function ensurePyodide(onStatus = null, versionConfig = null) {
  statusCallback = onStatus;
  pyodideReadyPromise ??= initPyodide(versionConfig);
  return pyodideReadyPromise;
}

const api = {
  async init(onStatus, versionConfig) {
    await ensurePyodide(onStatus, versionConfig);
    return bootInfo;
  },

  async sample(inputType) {
    const pyodide = await ensurePyodide();
    return pyodide.globals.get("sample_schema")(inputType || "");
  },

  async prepare(inputType) {
    const pyodide = await ensurePyodide();
    await installPythonPackages(
      pyodide,
      packagesForInputType(inputType || ""),
      `Installing ${inputType || "selected"} input dependencies...`,
    );
  },

  async buildCliOptions(options, inputType) {
    const pyodide = await ensurePyodide();
    return pyodide.globals.get("build_cli_options")(JSON.stringify(options || {}), inputType || "");
  },

  async exportConfigToml(options, inputType) {
    const pyodide = await ensurePyodide();
    return pyodide.globals.get("export_config_toml")(JSON.stringify(options || {}), inputType || "");
  },

  async importConfigToml(configToml) {
    const pyodide = await ensurePyodide();
    return JSON.parse(pyodide.globals.get("import_config_toml")(configToml || ""));
  },

  async generate(schema, inputType, options) {
    const pyodide = await ensurePyodide();
    await installPythonPackages(
      pyodide,
      packagesForInputType(inputType || ""),
      `Installing ${inputType || "selected"} input dependencies...`,
    );
    const resultJson = pyodide.globals.get("generate_in_browser")(
      schema,
      inputType,
      JSON.stringify(options || {}),
    );
    return JSON.parse(resultJson);
  },
};

expose(api);
