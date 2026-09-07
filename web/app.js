const form = document.querySelector("#build-form");
const fileInput = document.querySelector("#apk-file");
const dropzone = document.querySelector("#dropzone");
const fileLabel = document.querySelector("#file-label");
const fileDetail = document.querySelector("#file-detail");
const urlInput = document.querySelector("#apk-url");
const clearUrl = document.querySelector("#clear-url");
const buildButton = document.querySelector("#build-button");
const buildButtonLabel = document.querySelector("#build-button-label");
const terminal = document.querySelector("#terminal");
const statusPill = document.querySelector("#status-pill");
const logLive = document.querySelector("#log-live");
const emptyResult = document.querySelector("#empty-result");
const buildResult = document.querySelector("#build-result");
const resultName = document.querySelector("#result-name");
const resultMeta = document.querySelector("#result-meta");
const downloadButton = document.querySelector("#download-button");
const healthLabel = document.querySelector("#health-label");
const storeQuery = document.querySelector("#store-query");
const storeSearchButton = document.querySelector("#store-search-button");
const storeStatus = document.querySelector("#store-status");
const storeResults = document.querySelector("#store-results");
const storeSelection = document.querySelector("#store-selection");
const storeSelectionIcon = document.querySelector("#store-selection-icon");
const storeSelectionTitle = document.querySelector("#store-selection-title");
const storeSelectionDetail = document.querySelector("#store-selection-detail");
const storeClear = document.querySelector("#store-clear");
const uptodownAppUrl = document.querySelector("#uptodown-app-url");
const sourceName = document.querySelector("#source-name");

let activeJob = null;
let pollTimer = null;

function formatBytes(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function setSelectedFile(file) {
  if (!file) {
    fileLabel.textContent = "Drop an APK here";
    fileDetail.textContent = "or click to browse from your device";
    return;
  }
  fileLabel.textContent = file.name;
  fileDetail.textContent = `${formatBytes(file.size)} · ready to patch`;
  urlInput.value = "";
  clearStoreSelection();
}

function setStoreStatus(message, kind = "") {
  storeStatus.textContent = message;
  storeStatus.className = `store-status ${kind}`.trim();
}

function renderStoreIcon(container, iconUrl, fallback = "APK") {
  container.replaceChildren();
  if (iconUrl) {
    const image = document.createElement("img");
    image.src = iconUrl;
    image.alt = "";
    image.loading = "lazy";
    image.addEventListener("error", () => { container.textContent = fallback; }, { once: true });
    container.append(image);
    return;
  }
  container.textContent = fallback;
}

function clearStoreSelection() {
  uptodownAppUrl.value = "";
  sourceName.value = "";
  storeSelection.classList.add("hidden");
  renderStoreIcon(storeSelectionIcon, "", "APK");
  setStoreStatus("");
}

function renderStoreResults(results) {
  storeResults.replaceChildren();
  results.forEach((app) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "store-result";
    button.innerHTML = `
      <span class="store-result-icon">APK</span>
      <span class="store-result-copy"><strong></strong><small></small></span>
      <span class="store-result-arrow">→</span>
    `;
    button.querySelector("strong").textContent = app.title;
    button.querySelector("small").textContent = app.summary || app.url;
    renderStoreIcon(button.querySelector(".store-result-icon"), app.icon, "APK");
    button.addEventListener("click", () => selectStoreApp(app));
    storeResults.append(button);
  });
}

async function searchStore() {
  const query = storeQuery.value.trim();
  if (query.length < 2) {
    setStoreStatus("Enter at least 2 characters.", "error");
    return;
  }
  storeSearchButton.disabled = true;
  storeResults.replaceChildren();
  setStoreStatus("Searching Uptodown…");
  try {
    const response = await fetch(`/api/uptodown/search?q=${encodeURIComponent(query)}`, { cache: "no-store" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Uptodown search failed");
    renderStoreResults(result.results || []);
    setStoreStatus(
      result.results?.length ? `${result.results.length} result${result.results.length === 1 ? "" : "s"} found` : "No matching apps found.",
      result.results?.length ? "success" : "",
    );
  } catch (error) {
    setStoreStatus(error.message, "error");
  } finally {
    storeSearchButton.disabled = false;
  }
}

async function selectStoreApp(app) {
  storeSearchButton.disabled = true;
  setStoreStatus(`Resolving ${app.title} APK…`);
  try {
    const response = await fetch(`/api/uptodown/app?url=${encodeURIComponent(app.url)}`, { cache: "no-store" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load this Uptodown app");
    if (!result.apkUrl) throw new Error("Uptodown did not expose a direct APK for this app");
    fileInput.value = "";
    setSelectedFile(null);
    uptodownAppUrl.value = result.url;
    sourceName.value = result.title;
    urlInput.value = result.apkUrl;
    storeSelection.classList.remove("hidden");
    storeSelectionTitle.textContent = result.title;
    storeSelectionDetail.textContent = "Uptodown APK resolved · ready to build";
    renderStoreIcon(storeSelectionIcon, result.icon, "APK");
    setStoreStatus("Store source selected. Start the build when ready.", "success");
    storeResults.replaceChildren();
  } catch (error) {
    setStoreStatus(error.message, "error");
  } finally {
    storeSearchButton.disabled = false;
  }
}

function setStatus(status, label) {
  statusPill.className = `status-pill ${status}`;
  statusPill.textContent = label;
  logLive.className = `log-live ${status}`;
  logLive.innerHTML = `<i></i> ${status === "running" ? "Streaming" : label}`;
}

function renderLogs(logs) {
  terminal.replaceChildren();
  if (!logs?.length) {
    const empty = document.createElement("div");
    empty.className = "terminal-line muted";
    empty.textContent = "Waiting for build output…";
    terminal.append(empty);
    return;
  }
  logs.forEach((line) => {
    const item = document.createElement("div");
    item.className = `terminal-line${line.startsWith("ERROR") ? " error-line" : ""}`;
    item.textContent = line;
    terminal.append(item);
  });
  terminal.scrollTop = terminal.scrollHeight;
}

function showResult(job) {
  emptyResult.classList.add("hidden");
  buildResult.classList.remove("hidden");
  resultName.textContent = job.outputName;
  resultMeta.textContent = `${job.signingMode === "test" ? "Test" : "Debug"}-signed artifact · ready to download`;
  downloadButton.href = job.downloadUrl;
}

async function pollJob(jobId) {
  try {
    const response = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
    if (!response.ok) throw new Error("Could not read build status");
    const job = await response.json();
    renderLogs(job.logs);
    if (job.status === "queued" || job.status === "running") {
      setStatus("running", job.phase || "Building");
      window.setTimeout(() => pollJob(jobId), 900);
      return;
    }
    if (job.status === "succeeded") {
      setStatus("success", "Ready");
      showResult(job);
      buildButton.disabled = false;
      buildButtonLabel.textContent = "Patch & build APK";
      activeJob = null;
      return;
    }
    setStatus("error", "Failed");
    buildButton.disabled = false;
    buildButtonLabel.textContent = "Try again";
    activeJob = null;
  } catch (error) {
    setStatus("error", "Connection lost");
    buildButton.disabled = false;
    buildButtonLabel.textContent = "Try again";
    renderLogs([`ERROR: ${error.message}`]);
    activeJob = null;
  }
}

fileInput.addEventListener("change", () => setSelectedFile(fileInput.files[0]));
["dragenter", "dragover"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropzone.classList.add("dragging");
}));
["dragleave", "drop"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropzone.classList.remove("dragging");
}));
dropzone.addEventListener("drop", (event) => {
  const file = [...event.dataTransfer.files].find((candidate) => candidate.name.toLowerCase().endsWith(".apk"));
  if (!file) {
    renderLogs(["ERROR: Please choose an Android APK file."]);
    return;
  }
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  setSelectedFile(file);
});
clearUrl.addEventListener("click", () => {
  urlInput.value = "";
  if (uptodownAppUrl.value) clearStoreSelection();
  urlInput.focus();
});
storeSearchButton.addEventListener("click", searchStore);
storeQuery.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    searchStore();
  }
});
storeClear.addEventListener("click", () => {
  clearStoreSelection();
  urlInput.value = "";
  storeQuery.focus();
});
urlInput.addEventListener("input", () => {
  if (urlInput.value.trim() && uptodownAppUrl.value) clearStoreSelection();
});
document.querySelectorAll(".sign-option input").forEach((input) => input.addEventListener("change", () => {
  document.querySelectorAll(".sign-option").forEach((option) => option.classList.toggle("selected", option.querySelector("input").checked));
}));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (activeJob) return;
  const file = fileInput.files[0];
  if (!file && !urlInput.value.trim() && !uptodownAppUrl.value) {
    setStatus("error", "Source needed");
    renderLogs(["ERROR: Choose an APK file, search Uptodown, or paste a public APK URL."]);
    document.querySelector("#dropzone").focus();
    return;
  }
  if (file && !file.name.toLowerCase().endsWith(".apk")) {
    setStatus("error", "Invalid file");
    renderLogs(["ERROR: The selected file must have an .apk extension."]);
    return;
  }
  const payload = new FormData(form);
  buildButton.disabled = true;
  buildButtonLabel.textContent = "Building…";
  setStatus("running", "Starting");
  emptyResult.classList.remove("hidden");
  buildResult.classList.add("hidden");
  renderLogs(["Starting a new APK patch job…"]);
  try {
    const response = await fetch("/api/jobs", { method: "POST", body: payload });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not start build");
    activeJob = result.id;
    pollJob(activeJob);
  } catch (error) {
    setStatus("error", "Failed");
    renderLogs([`ERROR: ${error.message}`]);
    buildButton.disabled = false;
    buildButtonLabel.textContent = "Try again";
  }
});

fetch("/api/health", { cache: "no-store" })
  .then((response) => response.json())
  .then((health) => { if (health.ok) healthLabel.textContent = "Toolchain standing by"; })
  .catch(() => { healthLabel.textContent = "Server needs attention"; });