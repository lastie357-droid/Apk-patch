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
clearUrl.addEventListener("click", () => { urlInput.value = ""; urlInput.focus(); });
document.querySelectorAll(".sign-option input").forEach((input) => input.addEventListener("change", () => {
  document.querySelectorAll(".sign-option").forEach((option) => option.classList.toggle("selected", option.querySelector("input").checked));
}));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (activeJob) return;
  const file = fileInput.files[0];
  if (!file && !urlInput.value.trim()) {
    setStatus("error", "Source needed");
    renderLogs(["ERROR: Choose an APK file or paste a public APK URL."]);
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