const configPanel = document.querySelector("#configPanel");
const configForm = document.querySelector("#configForm");
const mainLayout = document.querySelector("#mainLayout");
const toggleConfigButton = document.querySelector("#toggleConfigButton");
const saveConfigButton = document.querySelector("#saveConfigButton");
const runPreflightButton = document.querySelector("#runPreflightButton");
const llmModeSelect = document.querySelector("#llmModeSelect");
const llmModeHint = document.querySelector("#llmModeHint");
const apiKeyInput = document.querySelector("#apiKeyInput");
const toggleApiKeyButton = document.querySelector("#toggleApiKeyButton");
const codexCliPathInput = document.querySelector("#codexCliPathInput");
const detectCodexCliButton = document.querySelector("#detectCodexCliButton");
const codexModelSelect = document.querySelector("#codexModelSelect");
const pdfUpload = document.querySelector("#pdfUpload");
const uploadButton = document.querySelector("#uploadButton");
const pdfSelect = document.querySelector("#pdfSelect");
const outputName = document.querySelector("#outputName");
const startJobButton = document.querySelector("#startJobButton");
const pauseJobButton = document.querySelector("#pauseJobButton");
const resumeJobButton = document.querySelector("#resumeJobButton");
const cancelJobButton = document.querySelector("#cancelJobButton");
const retryJobButton = document.querySelector("#retryJobButton");
const deleteJobButton = document.querySelector("#deleteJobButton");
const refreshJobsButton = document.querySelector("#refreshJobsButton");
const downloadLink = document.querySelector("#downloadLink");
const connectionStatus = document.querySelector("#connectionStatus");
const apiKeyState = document.querySelector("#apiKeyState");
const jobStatus = document.querySelector("#jobStatus");
const jobStep = document.querySelector("#jobStep");
const progressBar = document.querySelector("#progressBar");
const progressText = document.querySelector("#progressText");
const logOutput = document.querySelector("#logOutput");
const jobList = document.querySelector("#jobList");
const preflightSummary = document.querySelector("#preflightSummary");
const preflightSummaryCards = document.querySelector("#preflightSummaryCards");
const preflightList = document.querySelector("#preflightList");
const preflightPanel = document.querySelector(".preflight-panel");
const runtimeSnapshotTitle = document.querySelector("#runtimeSnapshotTitle");
const runtimeSnapshot = document.querySelector("#runtimeSnapshot");
const guideTabs = document.querySelector("#guideTabs");
const guideDetail = document.querySelector("#guideDetail");
const guideSection = document.querySelector(".guide-section");
const toast = document.querySelector("#toast");
const llmModeSections = [...document.querySelectorAll("[data-llm-mode]")];
const configTabButtons = [...document.querySelectorAll("[data-config-tab]")];
const configSections = [...document.querySelectorAll("[data-config-panel]")];

const statusLabels = {
  idle: "空闲",
  queued: "排队中",
  running: "运行中",
  pausing: "正在暂停",
  paused: "已暂停",
  canceling: "正在取消",
  canceled: "已取消",
  done: "已完成",
  failed: "失败",
};

let pollTimer = null;
let isConfigOpen = false;
let selectedJobId = null;
let jobs = [];
let lastPreflight = null;
let selectedGuideId = null;
let selectedConfigTab = "access";
let selectedPreflightItemId = null;

const preflightTargets = {
  llm_key: { tab: "access", focus: () => apiKeyInput },
  llm_url: { tab: "access", focus: () => configForm.elements.namedItem("PDF_CRAFT_LLM_BASE_URL") },
  codex_cli: { tab: "access", focus: () => codexCliPathInput },
  codex_model: { tab: "access", focus: () => codexModelSelect },
  codex_reasoning: { tab: "access", focus: () => configForm.elements.namedItem("PDF_CRAFT_CODEX_MODEL_REASONING_EFFORT") },
  llm_mode: { tab: "access", focus: () => llmModeSelect },
  input_dir: { tab: "runtime", focus: () => configForm.elements.namedItem("PDF_CRAFT_INPUT_DIR") },
  work_dir: { tab: "runtime", focus: () => configForm.elements.namedItem("PDF_CRAFT_WORK_DIR") },
  dist_dir: { tab: "runtime", focus: () => configForm.elements.namedItem("PDF_CRAFT_DIST_DIR") },
  model_dir: { tab: "runtime", focus: () => configForm.elements.namedItem("PDF_CRAFT_MODEL_DIR") },
  device: { tab: "runtime", focus: () => configForm.elements.namedItem("PDF_CRAFT_DEVICE") },
  cuda: { tab: "runtime", focus: () => configForm.elements.namedItem("PDF_CRAFT_DEVICE"), guide: "cuda" },
  table_format: { tab: "quality", focus: () => configForm.elements.namedItem("PDF_CRAFT_EXTRACT_TABLE_FORMAT") },
  latex: { tab: "quality", focus: () => configForm.elements.namedItem("PDF_CRAFT_EXTRACT_FORMULA"), guide: "latex" },
  threads_count: { tab: "advanced", focus: () => configForm.elements.namedItem("PDF_CRAFT_THREADS_COUNT") },
  retry_times: { tab: "advanced", focus: () => configForm.elements.namedItem("PDF_CRAFT_LLM_RETRY_TIMES") },
  retry_interval: { tab: "advanced", focus: () => configForm.elements.namedItem("PDF_CRAFT_LLM_RETRY_INTERVAL_SECONDS") },
  top_p: { tab: "advanced", focus: () => configForm.elements.namedItem("PDF_CRAFT_LLM_TOP_P") },
  temperature: { tab: "advanced", focus: () => configForm.elements.namedItem("PDF_CRAFT_LLM_TEMPERATURE") },
  source_pdf: { focus: () => pdfSelect },
  output_file: { focus: () => outputName },
};

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
    const detail = data?.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message || `请求失败：${response.status}`;
    throw new Error(message);
  }
  return data;
}

async function copyText(text, successMessage = "命令已复制") {
  try {
    await navigator.clipboard.writeText(text);
    showToast(successMessage);
  } catch (error) {
    showToast(`复制失败，请手动复制。${error.message}`, true);
  }
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

function fillCodexModelList(models, selectedValue) {
  if (!codexModelSelect) {
    return;
  }

  const current = selectedValue ?? codexModelSelect.value;
  codexModelSelect.innerHTML = "";

  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "Use Codex default model";
  codexModelSelect.append(defaultOption);

  for (const model of models || []) {
    const option = document.createElement("option");
    option.value = model.slug;
    option.textContent = model.display_name;
    option.title = model.description || model.slug;
    codexModelSelect.append(option);
  }

  codexModelSelect.value = current ?? "";
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

function focusElement(element) {
  if (!element) {
    return;
  }
  element.scrollIntoView({ behavior: "smooth", block: "center" });
  if (typeof element.focus === "function") {
    window.setTimeout(() => element.focus({ preventScroll: true }), 60);
  }
}

function renderConfigTab(tabId = selectedConfigTab) {
  selectedConfigTab = tabId;

  for (const button of configTabButtons) {
    const active = button.dataset.configTab === tabId;
    button.dataset.selected = String(active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  }

  for (const section of configSections) {
    section.hidden = section.dataset.configPanel !== tabId;
  }
}

function selectGuide(guideId) {
  if (!lastPreflight?.guides?.length) {
    return;
  }
  selectedGuideId = guideId;
  renderGuides(lastPreflight.guides || [], lastPreflight.runtime || {});
  guideSection?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function navigateToPreflightTarget(itemId) {
  const target = preflightTargets[itemId];
  if (!target) {
    preflightPanel?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }

  selectedPreflightItemId = itemId;
  if (lastPreflight) {
    renderPreflight(lastPreflight);
  }

  if (target.tab) {
    setConfigOpen(true);
    renderConfigTab(target.tab);
  }
  if (target.guide) {
    selectGuide(target.guide);
  }
  const element = target.focus?.();
  if (element) {
    focusElement(element);
  } else if (!target.tab) {
    preflightPanel?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function toggleApiKeyVisibility() {
  const visible = apiKeyInput.type === "text";
  apiKeyInput.type = visible ? "password" : "text";
  toggleApiKeyButton.textContent = visible ? "显示" : "隐藏";
}

function renderLlmMode() {
  const mode = llmModeSelect.value || "api_key";
  for (const section of llmModeSections) {
    const active = section.dataset.llmMode === mode;
    section.hidden = !active;
    for (const field of section.querySelectorAll("input, select, textarea, button")) {
      field.disabled = !active;
    }
  }
  llmModeHint.textContent = mode === "codex_cli"
    ? "本地 Codex CLI 模式会调用本机 codex 命令，不使用远程 API Key。"
    : "API Key 模式会通过远程 LLM 服务执行请求。";
}

async function loadConfig() {
  const data = await requestJson("/api/config");
  fillCodexModelList(data.codex_models || [], data.values?.PDF_CRAFT_CODEX_MODEL || "");
  connectionStatus.textContent = "已连接";
  fillConfig(data.values);
  fillPdfList(data.pdfs);
  updateLlmState(data);
  await runPreflight({ silent: true });
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
    fillCodexModelList(data.codex_models || [], data.values?.PDF_CRAFT_CODEX_MODEL || "");
    fillConfig(data.values);
    fillPdfList(data.pdfs);
    updateLlmState(data);
    await runPreflight({ silent: true });
    showToast("配置已保存");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    saveConfigButton.disabled = false;
  }
}

async function savePartialConfig(changes, successMessage) {
  const values = collectConfig();
  Object.assign(values, changes);
  const data = await requestJson("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values }),
  });
  fillCodexModelList(data.codex_models || [], data.values?.PDF_CRAFT_CODEX_MODEL || "");
  fillConfig(data.values);
  fillPdfList(data.pdfs);
  updateLlmState(data);
  await runPreflight({ silent: true });
  if (successMessage) {
    showToast(successMessage);
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
    const data = await requestJson("/api/config/detect-codex-cli", { method: "POST" });
    codexCliPathInput.value = data.path || "";
    showToast("已检测到 Codex CLI 路径");
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
    await runPreflight({ silent: true });
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
  const preflight = await runPreflight({ silent: true });
  if (!preflight.can_start) {
    showToast("预检未通过，请先处理阻断项", true);
    return;
  }
  startJobButton.disabled = true;
  downloadLink.hidden = true;
  try {
    const job = await requestJson("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pdf_name: pdfSelect.value,
        output_name: outputName.value.trim() || null,
      }),
    });
    selectedJobId = job.id;
    renderJob(job);
    await loadJobs();
    startPolling();
    showToast("任务已加入队列");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    startJobButton.disabled = false;
  }
}

async function runPreflight({ silent = false } = {}) {
  const params = new URLSearchParams();
  if (pdfSelect.value) {
    params.set("pdf_name", pdfSelect.value);
  }
  if (outputName.value.trim()) {
    params.set("output_name", outputName.value.trim());
  }
  runPreflightButton.disabled = true;
  try {
    const query = params.toString();
    const data = await requestJson(`/api/preflight${query ? `?${query}` : ""}`);
    lastPreflight = data;
    renderPreflight(data);
    if (!silent) {
      showToast(data.can_start ? "预检通过" : "预检存在阻断项", !data.can_start);
    }
    return data;
  } catch (error) {
    preflightSummary.textContent = "预检失败";
    preflightList.innerHTML = "";
    showToast(error.message, true);
    return { can_start: false, items: [] };
  } finally {
    runPreflightButton.disabled = false;
  }
}

function renderPreflight(data) {
  const errors = data.summary?.errors || 0;
  const warnings = data.summary?.warnings || 0;
  preflightSummary.textContent = errors
    ? `${errors} 个阻断项，${warnings} 个提醒`
    : warnings
      ? `可开始，${warnings} 个提醒`
      : "可以开始转换";
  preflightSummary.dataset.status = errors ? "error" : warnings ? "warn" : "ok";
  renderPreflightSummaryCards(data);
  renderGuides(data.guides || [], data.runtime || {});

  preflightList.innerHTML = "";
  for (const item of data.items || []) {
    const row = document.createElement("div");
    row.className = "preflight-item";
    row.dataset.status = item.status;
    row.dataset.selected = item.id === selectedPreflightItemId ? "true" : "false";

    const badge = document.createElement("span");
    badge.className = "preflight-badge";
    badge.textContent = item.status === "ok" ? "通过" : item.status === "warn" ? "提醒" : "阻断";

    const content = document.createElement("span");
    content.className = "preflight-content";

    const title = document.createElement("strong");
    title.textContent = item.label;

    const message = document.createElement("span");
    message.textContent = item.message;

    content.append(title, message);
    row.append(badge, content);

    const action = createPreflightAction(item);
    if (action) {
      const actionButton = document.createElement("button");
      actionButton.type = "button";
      actionButton.className = "preflight-action";
      actionButton.textContent = action.label;
      actionButton.addEventListener("click", async (event) => {
        event.stopPropagation();
        selectedPreflightItemId = item.id;
        try {
          await action.run();
        } catch (error) {
          showToast(error.message, true);
        }
      });
      row.append(actionButton);
      row.dataset.clickable = "true";
    } else if (preflightTargets[item.id]) {
      row.dataset.clickable = "true";
    }

    if (preflightTargets[item.id]) {
      row.addEventListener("click", () => navigateToPreflightTarget(item.id));
    }

    preflightList.append(row);
  }

  if (selectedPreflightItemId) {
    const selected = preflightList.querySelector(`[data-selected="true"]`);
    selected?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function createPreflightAction(item) {
  if (item.id === "codex_cli" && item.status !== "ok") {
    return {
      label: "检测 CLI",
      run: async () => {
        navigateToPreflightTarget(item.id);
        await detectCodexCli();
        await runPreflight({ silent: true });
      },
    };
  }

  if (item.id === "cuda" && item.status === "error") {
    return {
      label: "切到 CPU",
      run: async () => {
        navigateToPreflightTarget(item.id);
        await savePartialConfig({ PDF_CRAFT_DEVICE: "cpu" }, "已切换到 CPU");
      },
    };
  }

  if (item.id === "latex" && item.status !== "ok") {
    return {
      label: "查看引导",
      run: async () => {
        navigateToPreflightTarget(item.id);
      },
    };
  }

  if (preflightTargets[item.id] && item.status !== "ok") {
    return {
      label: "去处理",
      run: async () => {
        navigateToPreflightTarget(item.id);
      },
    };
  }

  return null;
}

function renderPreflightSummaryCards(data) {
  const total = data.summary?.total || 0;
  const errors = data.summary?.errors || 0;
  const warnings = data.summary?.warnings || 0;
  const ok = Math.max(0, total - errors - warnings);
  const cards = [
    { label: "通过", value: ok, status: "ok" },
    { label: "提醒", value: warnings, status: "warn" },
    { label: "阻断", value: errors, status: "error" },
  ];

  preflightSummaryCards.innerHTML = "";
  for (const cardData of cards) {
    const card = document.createElement("div");
    card.className = "summary-card";
    card.dataset.status = cardData.status;

    const value = document.createElement("strong");
    value.textContent = String(cardData.value);

    const label = document.createElement("span");
    label.textContent = cardData.label;

    card.append(value, label);
    preflightSummaryCards.append(card);
  }
}

function renderGuides(guides, runtime) {
  const providers = Array.isArray(runtime.providers) && runtime.providers.length
    ? runtime.providers.join(", ")
    : "未检测到 provider";
  const runtimeName = runtime.onnxruntime_gpu
    ? "GPU 环境"
    : runtime.onnxruntime
      ? "CPU 环境"
      : "未识别环境";
  runtimeSnapshotTitle.textContent = runtimeName;
  runtimeSnapshot.textContent = runtime.python
    ? `${runtime.python} · ${providers}`
    : "未获取到当前服务环境";

  if (!guides.length) {
    guideTabs.innerHTML = "";
    guideDetail.innerHTML = "";
    return;
  }

  const preferredGuide = guides.find((guide) => guide.status === "attention")
    || guides.find((guide) => guide.status === "active")
    || guides[0];
  if (!guides.some((guide) => guide.id === selectedGuideId)) {
    selectedGuideId = preferredGuide.id;
  }

  guideTabs.innerHTML = "";
  for (const guide of guides) {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "guide-tab";
    tab.dataset.status = guide.status;
    tab.dataset.selected = guide.id === selectedGuideId ? "true" : "false";
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", guide.id === selectedGuideId ? "true" : "false");
    tab.textContent = guide.title;
    tab.addEventListener("click", () => {
      selectedGuideId = guide.id;
      renderGuides(guides, runtime);
    });
    guideTabs.append(tab);
  }

  const guide = guides.find((item) => item.id === selectedGuideId) || guides[0];
  const card = document.createElement("section");
  card.className = "guide-card";
  card.dataset.status = guide.status;

  const header = document.createElement("div");
  header.className = "guide-card-head";

  const titleGroup = document.createElement("div");
  titleGroup.className = "guide-title-group";

  const title = document.createElement("strong");
  title.textContent = guide.title;

  const summary = document.createElement("span");
  summary.className = "guide-summary";
  summary.textContent = guide.summary;

  titleGroup.append(title, summary);

  const badge = document.createElement("span");
  badge.className = "guide-badge";
  badge.textContent = guide.status === "active"
    ? "当前使用"
    : guide.status === "attention"
      ? "需要处理"
      : guide.status === "optional"
        ? "按需安装"
        : "可切换";

  header.append(titleGroup, badge);

  const body = document.createElement("div");
  body.className = "guide-detail-grid";

  const details = document.createElement("ul");
  details.className = "guide-details";
  for (const line of guide.details || []) {
    const item = document.createElement("li");
    item.textContent = line;
    details.append(item);
  }

  const commands = document.createElement("div");
  commands.className = "guide-commands";
  for (const entry of guide.commands || []) {
    const row = document.createElement("div");
    row.className = "guide-command";

    const label = document.createElement("span");
    label.className = "guide-command-label";
    label.textContent = entry.label;

    const code = document.createElement("code");
    code.textContent = entry.command;

    const button = document.createElement("button");
    button.type = "button";
    button.className = "guide-copy-button";
    button.textContent = "复制";
    button.addEventListener("click", () => copyText(entry.command, `${entry.label}命令已复制`));

    row.append(label, code, button);
    commands.append(row);
  }

  body.append(details, commands);
  card.append(header, body);
  guideDetail.innerHTML = "";
  guideDetail.append(card);
}

async function jobAction(endpoint, successMessage) {
  if (!selectedJobId) {
    showToast("请先选择一个历史任务", true);
    return;
  }
  try {
    const job = await requestJson(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: selectedJobId }),
    });
    renderJob(job);
    await loadJobs();
    startPolling();
    showToast(successMessage);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function deleteSelectedJob() {
  if (!selectedJobId) {
    showToast("请先选择一个历史任务", true);
    return;
  }
  const confirmed = window.confirm("将删除该任务记录、输入 PDF、输出 EPUB 和中间处理目录，是否继续？");
  if (!confirmed) {
    return;
  }

  deleteJobButton.disabled = true;
  try {
    const data = await requestJson("/api/jobs/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: selectedJobId,
        delete_files: true,
      }),
    });
    selectedJobId = null;
    downloadLink.hidden = true;
    await loadConfig();
    renderJobs(data.jobs || []);
    await pollJob();
    showToast("任务与相关文件已删除");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    deleteJobButton.disabled = false;
  }
}

function renderJob(job) {
  const label = statusLabels[job.status] || job.status || "空闲";
  jobStatus.textContent = label;
  jobStatus.dataset.status = job.status || "idle";
  jobStep.textContent = job.error || job.step || "等待任务";
  logOutput.textContent = (job.logs || []).join("\n");
  logOutput.scrollTop = logOutput.scrollHeight;

  const total = job.progress_total;
  const current = job.progress_current || 0;
  const percent = total ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : 0;
  progressBar.style.width = `${percent}%`;
  progressText.textContent = total ? `${current} / ${total}` : `${current}`;

  pauseJobButton.disabled = !job.can_pause;
  resumeJobButton.disabled = !job.can_resume;
  cancelJobButton.disabled = !job.can_cancel;
  retryJobButton.disabled = !job.can_retry;
  deleteJobButton.disabled = !job.can_delete;

  if (job.status === "done" && job.output_file) {
    downloadLink.href = `/api/download/${encodeURIComponent(job.output_file)}`;
    downloadLink.hidden = false;
  } else {
    downloadLink.hidden = true;
  }
}

function renderJobs(nextJobs) {
  jobs = nextJobs;
  if (!selectedJobId && jobs.length) {
    const active = jobs.find((job) => ["running", "pausing", "canceling", "queued"].includes(job.status));
    selectedJobId = active?.id || jobs[0].id;
  }
  if (selectedJobId && !jobs.some((job) => job.id === selectedJobId)) {
    selectedJobId = jobs[0]?.id || null;
  }

  jobList.innerHTML = "";
  if (!jobs.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "还没有任务。上传 PDF 后开始转换，会在这里保留历史记录。";
    jobList.append(empty);
    renderJob({ status: "idle", step: "等待任务", logs: [] });
    return;
  }

  for (const job of jobs) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "job-item";
    item.classList.toggle("selected", job.id === selectedJobId);
    item.dataset.status = job.status;
    item.addEventListener("click", () => {
      selectedJobId = job.id;
      renderJob(job);
      renderJobs(jobs);
    });

    const title = document.createElement("span");
    title.className = "job-title";
    title.textContent = job.pdf_name;

    const meta = document.createElement("span");
    meta.className = "job-meta";
    const output = job.output_file || job.output_name || "等待输出";
    meta.textContent = `${statusLabels[job.status] || job.status} · ${output}`;

    const time = document.createElement("span");
    time.className = "job-time";
    time.textContent = job.finished_at || job.started_at || job.created_at || "";

    item.append(title, meta, time);
    jobList.append(item);
  }

  const selected = jobs.find((job) => job.id === selectedJobId);
  if (selected) {
    renderJob(selected);
  }
}

async function loadJobs() {
  const data = await requestJson("/api/jobs");
  renderJobs(data.jobs || []);
  return data.jobs || [];
}

async function pollJob() {
  try {
    const data = await requestJson("/api/jobs/current");
    if (!selectedJobId && data.id) {
      selectedJobId = data.id;
    }
    await loadJobs();
    const hasActiveJob = jobs.some((job) => ["queued", "running", "pausing", "canceling"].includes(job.status));
    if (!hasActiveJob) {
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
runPreflightButton.addEventListener("click", () => runPreflight());
uploadButton.addEventListener("click", uploadPdf);
startJobButton.addEventListener("click", startJob);
pdfSelect.addEventListener("change", () => runPreflight({ silent: true }));
outputName.addEventListener("input", () => {
  window.clearTimeout(outputName.preflightTimer);
  outputName.preflightTimer = window.setTimeout(() => runPreflight({ silent: true }), 350);
});
pauseJobButton.addEventListener("click", () => jobAction("/api/jobs/pause", "已请求暂停"));
resumeJobButton.addEventListener("click", () => jobAction("/api/jobs/resume", "任务已继续"));
cancelJobButton.addEventListener("click", () => jobAction("/api/jobs/cancel", "已请求取消"));
retryJobButton.addEventListener("click", () => jobAction("/api/jobs/retry", "任务已重新加入队列"));
deleteJobButton.addEventListener("click", deleteSelectedJob);
refreshJobsButton.addEventListener("click", () => loadJobs().then(() => showToast("任务列表已刷新")));
for (const button of configTabButtons) {
  button.addEventListener("click", () => renderConfigTab(button.dataset.configTab || "access"));
}

setConfigOpen(false);
renderConfigTab(selectedConfigTab);
renderLlmMode();

Promise.all([loadConfig(), loadJobs()])
  .then(() => {
    const hasActiveJob = jobs.some((job) => ["queued", "running", "pausing", "canceling"].includes(job.status));
    if (hasActiveJob) {
      startPolling();
    }
  })
  .catch((error) => {
    connectionStatus.textContent = "连接失败";
    showToast(error.message, true);
  });
