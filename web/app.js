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
const openBrowserButton = document.querySelector("#open-browser-button");
const savedAppSelect = document.querySelector("#saved-app-select");
const savedAppId = document.querySelector("#saved-app-id");
const remoteBrowser = document.querySelector("#remote-browser");
const browserImage = document.querySelector("#browser-image");
const browserPlaceholder = document.querySelector("#browser-placeholder");
const browserStatus = document.querySelector("#browser-status");
const browserAuto = document.querySelector("#browser-auto");
const browserRefresh = document.querySelector("#browser-refresh");
const browserClose = document.querySelector("#browser-close");
const uptodownAppUrl = document.querySelector("#uptodown-app-url");
const sourceName = document.querySelector("#source-name");

let activeJob = null;
let pollTimer = null;
let browserPollTimer = null;
let browserSessionId = null;
let savedApps = [];
let browserAutoAttempted = false;

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
  stopRemoteBrowser();
  uptodownAppUrl.value = "";
  sourceName.value = "";
  savedAppId.value = "";
  savedAppSelect.value = "";
  storeSelection.classList.add("hidden");
  remoteBrowser.classList.add("hidden");
  browserImage.removeAttribute("src");
  browserPlaceholder.textContent = "Starting Chromium on the server…";
  browserPlaceholder.classList.remove("hidden");
  renderStoreIcon(storeSelectionIcon, "", "APK");
  setStoreStatus("");
}

function renderSavedApps() {
  const selected = savedAppId.value;
  savedAppSelect.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = savedApps.length ? "Select a downloaded app" : "No apps saved yet";
  savedAppSelect.append(empty);
  savedApps.forEach((app) => {
    const option = document.createElement("option");
    option.value = app.id;
    option.textContent = `${app.title} · ${formatBytes(app.size)}`;
    savedAppSelect.append(option);
  });
  savedAppSelect.value = selected;
}

async function loadSavedApps() {
  try {
    const response = await fetch("/api/saved-apps", { cache: "no-store" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load saved apps");
    savedApps = result.apps || [];
    renderSavedApps();
  } catch (error) {
    setStoreStatus(error.message, "error");
  }
}

function selectSavedApp(app) {
  stopRemoteBrowser();
  fileInput.value = "";
  urlInput.value = "";
  savedAppId.value = app.id;
  uptodownAppUrl.value = app.sourceUrl || "";
  sourceName.value = app.fileName || app.title;
  savedAppSelect.value = app.id;
  storeSelection.classList.remove("hidden");
  remoteBrowser.classList.add("hidden");
  storeSelectionTitle.textContent = app.title;
  storeSelectionDetail.textContent = `Saved on server · ${formatBytes(app.size)} · ready to build`;
  openBrowserButton.textContent = "Open again";
  renderStoreIcon(storeSelectionIcon, "", "APK");
  setStoreStatus("Saved server APK selected. Start the build when ready.", "success");
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

function selectStoreApp(app) {
  clearStoreSelection();
  fileInput.value = "";
  urlInput.value = "";
  uptodownAppUrl.value = app.url;
  sourceName.value = app.title;
  storeSelection.classList.remove("hidden");
  storeSelectionTitle.textContent = app.title;
  storeSelectionDetail.textContent = "Open the server browser to download this app";
  openBrowserButton.textContent = "Open";
  renderStoreIcon(storeSelectionIcon, app.icon, "APK");
  setStoreStatus("App selected. Open the server browser to continue.", "warning");
  storeResults.replaceChildren();
}

async function openRemoteBrowser() {
  if (!uptodownAppUrl.value) return;
  openBrowserButton.disabled = true;
  remoteBrowser.classList.remove("hidden");
  browserPlaceholder.classList.remove("hidden");
  browserPlaceholder.textContent = "Starting Chromium on the server…";
  setStoreStatus("Opening the server browser…");
  try {
    const response = await fetch("/api/uptodown/browser", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: uptodownAppUrl.value, title: sourceName.value }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not open the server browser");
    browserSessionId = result.id;
    browserAutoAttempted = false;
    renderBrowserSnapshot(result);
    pollBrowser(result.id);
  } catch (error) {
    browserPlaceholder.textContent = error.message;
    setStoreStatus(error.message, "error");
  } finally {
    openBrowserButton.disabled = false;
  }
}

function renderBrowserSnapshot(snapshot) {
  if (snapshot.image) {
    browserImage.src = snapshot.image;
    browserPlaceholder.classList.add("hidden");
  }
  browserStatus.textContent = snapshot.status === "downloaded"
    ? "APK saved on server"
    : snapshot.status === "error"
      ? "Browser error"
      : "Interactive · click the page to continue";
  if (snapshot.savedApp) {
    const saved = snapshot.savedApp;
    if (!savedApps.some((app) => app.id === saved.id)) savedApps.unshift(saved);
    renderSavedApps();
    selectSavedApp(saved);
    remoteBrowser.classList.remove("hidden");
    setStoreStatus("APK downloaded and saved on the server. It is selected for this build.", "success");
  } else if (snapshot.status === "ready") {
    setStoreStatus("Server browser ready. Click the Uptodown download button in the view above.", "warning");
  }
}

async function pollBrowser(sessionId) {
  window.clearTimeout(browserPollTimer);
  try {
    const response = await fetch(`/api/uptodown/browser/${sessionId}`, { cache: "no-store" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not read remote browser");
    renderBrowserSnapshot(result);
    if (result.status === "ready" && !browserAutoAttempted) {
      browserAutoAttempted = true;
      autoFindDownload();
      return;
    }
    if (result.status === "starting" || result.status === "ready") {
      browserPollTimer = window.setTimeout(() => pollBrowser(sessionId), 350);
    }
  } catch (error) {
    browserStatus.textContent = "Browser disconnected";
    setStoreStatus(error.message, "error");
  }
}

async function autoFindDownload() {
  if (!browserSessionId) return;
  browserAuto.disabled = true;
  browserStatus.textContent = "Looking for a download control…";
  try {
    const response = await fetch(`/api/uptodown/browser/${browserSessionId}/auto`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not automate the remote browser");
    renderBrowserSnapshot(result);
    setStoreStatus(
      result.automation?.clicked
        ? `Clicked “${result.automation.label || "download"}”. Watching for the server file…`
        : "No visible download control was found. Complete the browser check, then try again or click the page.",
      result.automation?.clicked ? "success" : "warning",
    );
    if (browserSessionId) {
      browserPollTimer = window.setTimeout(() => pollBrowser(browserSessionId), 150);
    }
  } catch (error) {
    setStoreStatus(error.message, "error");
  } finally {
    browserAuto.disabled = false;
  }
}

async function clickRemoteBrowser(event) {
  if (!browserSessionId || !browserImage.complete || !browserImage.naturalWidth) return;
  const bounds = browserImage.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;
  const x = Math.max(
    0,
    Math.min(browserImage.naturalWidth, (event.clientX - bounds.left) * browserImage.naturalWidth / bounds.width),
  );
  const y = Math.max(
    0,
    Math.min(browserImage.naturalHeight, (event.clientY - bounds.top) * browserImage.naturalHeight / bounds.height),
  );
  try {
    const response = await fetch(`/api/uptodown/browser/${browserSessionId}/click`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        x,
        y,
        imageWidth: browserImage.naturalWidth,
        imageHeight: browserImage.naturalHeight,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not click the remote browser");
    renderBrowserSnapshot(result);
    if (browserSessionId) {
      browserPollTimer = window.setTimeout(() => pollBrowser(browserSessionId), 150);
    }
  } catch (error) {
    setStoreStatus(error.message, "error");
  }
}

function stopRemoteBrowser() {
  window.clearTimeout(browserPollTimer);
  if (!browserSessionId) return;
  const sessionId = browserSessionId;
  browserSessionId = null;
  fetch(`/api/uptodown/browser/${sessionId}`, { method: "DELETE", keepalive: true }).catch(() => {});
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
openBrowserButton.addEventListener("click", openRemoteBrowser);
browserAuto.addEventListener("click", autoFindDownload);
browserImage.addEventListener("click", clickRemoteBrowser);
browserRefresh.addEventListener("click", () => {
  if (browserSessionId) pollBrowser(browserSessionId);
});
browserClose.addEventListener("click", () => {
  stopRemoteBrowser();
  remoteBrowser.classList.add("hidden");
});
savedAppSelect.addEventListener("change", () => {
  const app = savedApps.find((candidate) => candidate.id === savedAppSelect.value);
  if (app) selectSavedApp(app);
  else if (savedAppId.value) clearStoreSelection();
});
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
  if (urlInput.value.trim() && (uptodownAppUrl.value || savedAppId.value)) clearStoreSelection();
});
document.querySelectorAll(".sign-option input").forEach((input) => input.addEventListener("change", () => {
  document.querySelectorAll(".sign-option").forEach((option) => option.classList.toggle("selected", option.querySelector("input").checked));
}));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (activeJob) return;
  const file = fileInput.files[0];
  if (!file && !urlInput.value.trim() && !uptodownAppUrl.value && !savedAppId.value) {
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
  .then((health) => {
    if (health.ok) {
      healthLabel.textContent = health.chromium ? "Toolchain + browser standing by" : "Toolchain standing by";
      loadSavedApps();
    }
  })
  .catch(() => { healthLabel.textContent = "Server needs attention"; });