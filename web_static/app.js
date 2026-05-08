const configPanel = document.querySelector("#configPanel");
const configForm = document.querySelector("#configForm");
const mainLayout = document.querySelector("#mainLayout");
const toggleConfigButton = document.querySelector("#toggleConfigButton");
const saveConfigButton = document.querySelector("#saveConfigButton");
const llmModeSelect = document.querySelector("#llmModeSelect");
const llmModeHint = document.querySelector("#llmModeHint");
const apiKeyInput = document.querySelector("#apiKeyInput");
const toggleApiKeyButton = document.querySelector("#toggleApiKeyButton");
const codexCliPathInput = document.querySelector("#codexCliPathInput");
const detectCodexCliButton = document.querySelector("#detectCodexCliButton");
const pdfUpload = document.querySelector("#pdfUpload");
const uploadButton = document.querySelector("#uploadButton");
const pdfSelect = document.querySelector("#pdfSelect");
const outputName = document.querySelector("#outputName");
const startJobButton = document.querySelector("#startJobButton");
const deleteJobButton = document.querySelector("#deleteJobButton");
const downloadLink = document.querySelector("#downloadLink");
const connectionStatus = document.querySelector("#connectionStatus");
const apiKeyState = document.querySelector("#apiKeyState");
const jobStatus = document.querySelector("#jobStatus");
const jobStep = document.querySelector("#jobStep");
const progressBar = document.querySelector("#progressBar");
const progressText = document.querySelector("#progressText");
const logOutput = document.querySelector("#logOutput");
const toast = document.querySelector("#toast");
const llmModeSections = [...document.querySelectorAll("[data-llm-mode]")];

let pollTimer = null;
let isConfigOpen = false;

function showToast(message, isError = false) {
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.classList.remove("show");
  }, 3200);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const message = data?.detail || `请求失败：${response.status}`;
    throw new Error(message);
  }
  return data;
}

function fillConfig(values) {
  for (const [key, value] of Object.entries(values)) {
    const field = configForm.elements.namedItem(key);
    if (field) {
      field.value = value ?? "";
    }
  }
  renderLlmMode();
}

function fillPdfList(pdfs) {
  const currentValue = pdfSelect.value;
  pdfSelect.innerHTML = "";
  if (!pdfs.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "inputs 目录暂无 PDF";
    pdfSelect.append(option);
    return;
  }
  for (const pdf of pdfs) {
    const option = document.createElement("option");
    option.value = pdf;
    option.textContent = pdf;
    pdfSelect.append(option);
  }
  if (pdfs.includes(currentValue)) {
    pdfSelect.value = currentValue;
  }
}

function setConfigOpen(open) {
  isConfigOpen = open;
  configPanel.hidden = !open;
  mainLayout.classList.toggle("config-open", open);
  toggleConfigButton.textContent = open ? "收起配置" : "打开配置";
}

function toggleApiKeyVisibility() {
  const visible = apiKeyInput.type === "text";
  apiKeyInput.type = visible ? "password" : "text";
  toggleApiKeyButton.textContent = visible ? "显示" : "隐藏";
}

function renderLlmMode() {
  const mode = llmModeSelect.value || "api_key";
  for (const section of llmModeSections) {
    section.hidden = section.dataset.llmMode !== mode;
  }
  llmModeHint.textContent = mode === "codex_cli"
    ? "本地 Codex CLI 模式会调用本机 codex 命令，不使用远程 API Key。"
    : "API Key 模式会通过远程 LLM 服务执行请求。";
}

async function loadConfig() {
  const data = await requestJson("/api/config");
  connectionStatus.textContent = "已连接";
  fillConfig(data.values);
  fillPdfList(data.pdfs);
  updateLlmState(data);
}

function collectConfig() {
  const values = {};
  const data = new FormData(configForm);
  for (const [key, value] of data.entries()) {
    values[key] = String(value).trim();
  }
  return values;
}

async function saveConfig() {
  saveConfigButton.disabled = true;
  try {
    const data = await requestJson("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values: collectConfig() }),
    });
    fillConfig(data.values);
    fillPdfList(data.pdfs);
    updateLlmState(data);
    showToast("配置已保存");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    saveConfigButton.disabled = false;
  }
}

function updateLlmState(data) {
  const mode = data?.values?.PDF_CRAFT_LLM_MODE || llmModeSelect.value || "api_key";
  if (mode === "codex_cli") {
    apiKeyState.textContent = data.codex_cli_available
      ? "本地 Codex CLI 已配置"
      : "本地 Codex CLI 未配置或不可执行";
    return;
  }
  apiKeyState.textContent = data.has_api_key ? "API Key 已配置" : "API Key 未配置";
}

async function detectCodexCli() {
  detectCodexCliButton.disabled = true;
  try {
    const data = await requestJson("/api/config/detect-codex-cli", {
      method: "POST",
    });
    codexCliPathInput.value = data.path || "";
    showToast("已探测到 Codex CLI 路径");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    detectCodexCliButton.disabled = false;
  }
}

async function uploadPdf() {
  const file = pdfUpload.files[0];
  if (!file) {
    showToast("请先选择 PDF 文件", true);
    return;
  }
  uploadButton.disabled = true;
  const data = new FormData();
  data.append("file", file);
  try {
    const result = await requestJson("/api/upload", {
      method: "POST",
      body: data,
    });
    await loadConfig();
    pdfSelect.value = result.name;
    showToast("PDF 已上传");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    uploadButton.disabled = false;
  }
}

async function startJob() {
  if (!pdfSelect.value) {
    showToast("请先上传或选择 PDF", true);
    return;
  }
  startJobButton.disabled = true;
  downloadLink.hidden = true;
  try {
    const data = await requestJson("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pdf_name: pdfSelect.value,
        output_name: outputName.value.trim() || null,
      }),
    });
    renderJob(data);
    startPolling();
    showToast("任务已启动");
  } catch (error) {
    showToast(error.message, true);
    startJobButton.disabled = false;
  }
}

async function deleteJob() {
  if (!pdfSelect.value) {
    showToast("请先选择要删除的 PDF", true);
    return;
  }
  const confirmed = window.confirm("将删除输入 PDF、输出 EPUB 和中间处理目录，是否继续？");
  if (!confirmed) {
    return;
  }

  deleteJobButton.disabled = true;
  try {
    const data = await requestJson("/api/jobs", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pdf_name: pdfSelect.value,
        output_name: outputName.value.trim() || null,
      }),
    });
    fillPdfList(data.pdfs || []);
    outputName.value = "";
    downloadLink.hidden = true;
    await pollJob();
    showToast(data.message || "任务已删除");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    deleteJobButton.disabled = false;
  }
}

function renderJob(job) {
  const labels = {
    idle: "空闲",
    running: "运行中",
    done: "已完成",
    failed: "失败",
  };
  jobStatus.textContent = labels[job.status] || job.status;
  jobStep.textContent = job.step || "等待任务";
  logOutput.textContent = (job.logs || []).join("\n");
  logOutput.scrollTop = logOutput.scrollHeight;

  const total = job.progress_total;
  const current = job.progress_current || 0;
  const percent = total ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : 0;
  progressBar.style.width = `${percent}%`;
  progressText.textContent = total ? `${current} / ${total}` : `${current}`;

  const isRunning = job.status === "running";
  startJobButton.disabled = isRunning;
  deleteJobButton.disabled = isRunning;

  if (job.status === "done" && job.output_file) {
    downloadLink.href = `/api/download/${encodeURIComponent(job.output_file)}`;
    downloadLink.hidden = false;
  } else {
    downloadLink.hidden = true;
  }
  if (job.status === "failed" && job.error) {
    jobStep.textContent = job.error;
  }
}

async function pollJob() {
  try {
    const data = await requestJson("/api/jobs/current");
    renderJob(data);
    if (data.status !== "running") {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  } catch (error) {
    connectionStatus.textContent = "连接异常";
    showToast(error.message, true);
  }
}

function startPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer);
  }
  pollTimer = window.setInterval(pollJob, 1600);
}

toggleConfigButton.addEventListener("click", () => setConfigOpen(!isConfigOpen));
llmModeSelect.addEventListener("change", renderLlmMode);
toggleApiKeyButton.addEventListener("click", toggleApiKeyVisibility);
detectCodexCliButton.addEventListener("click", detectCodexCli);
saveConfigButton.addEventListener("click", saveConfig);
uploadButton.addEventListener("click", uploadPdf);
startJobButton.addEventListener("click", startJob);
deleteJobButton.addEventListener("click", deleteJob);

setConfigOpen(false);
renderLlmMode();

loadConfig()
  .then(pollJob)
  .catch((error) => {
    connectionStatus.textContent = "连接失败";
    showToast(error.message, true);
  });
