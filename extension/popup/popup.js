const statusEl = document.querySelector("#status");
const statusBadgeEl = document.querySelector("#status-badge");
const accountEl = document.querySelector("#account");
const creditsPillEl = document.querySelector("#credits-pill");
const creditsValueEl = document.querySelector("#credits-value");
const versionEl = document.querySelector("#version");
const errorEl = document.querySelector("#error");
const jobEl = document.querySelector("#job");
const logsEl = document.querySelector("#logs");
const statActiveEl = document.querySelector("#stat-active");
const statCompletedEl = document.querySelector("#stat-completed");
const statErrorEl = document.querySelector("#stat-error");
const copyLogsEl = document.querySelector("#copy-logs");
const clearLogsEl = document.querySelector("#clear-logs");
const flowBtnEl = document.querySelector("#flow");

// Enhanced UI Elements
const promptInputEl = document.querySelector("#prompt-input");
const promptCountEl = document.querySelector("#prompt-count");
const toggleMultiEl = document.querySelector("#toggle-multi");
const btnGenerateEl = document.querySelector("#btn-generate");
const btnPromptAssistantEl = document.querySelector("#btn-prompt-assistant");
const btnImportTxtEl = document.querySelector("#btn-import-txt");
const btnSavePromptEl = document.querySelector("#btn-save-prompt");
const popoutTabBtnEl = document.querySelector("#popout-tab");
const bannerAlertEl = document.querySelector("#banner-alert");
const bannerCloseEl = document.querySelector("#banner-close");
const drawerToggleEl = document.querySelector("#drawer-toggle");
const activityDrawerEl = document.querySelector("#activity-drawer");
const btnToggleLogsEl = document.querySelector("#btn-toggle-logs");
const tabTaskBadgeEl = document.querySelector("#tab-task-badge");
const stepperDecEl = document.querySelector("#stepper-dec");
const stepperIncEl = document.querySelector("#stepper-inc");
const stepperValEl = document.querySelector("#stepper-val");
const modelSelectEl = document.querySelector("#model-select");

const send = (message) => chrome.runtime.sendMessage(message);

let lastLogsFingerprint = null;
let lastActiveCount = null;
let lastCompletedCount = null;
let lastErrorCount = null;
let currentQuantity = 1;

function timeLabel(value) {
  if (!value) return "";
  const d = new Date(value);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function getBadgeChar(status) {
  if (status === "done") return "✓";
  if (status === "error") return "✕";
  if (status === "running") return "●";
  return "•";
}

function renderActivity(activity = {}) {
  const active = Number(activity.activeCount || 0);
  const completed = Number(activity.completedCount || 0);
  const errorCount = Number(activity.errorCount || 0);
  const logs = Array.isArray(activity.logs) ? activity.logs : [];

  // Update stats counters
  if (statActiveEl && lastActiveCount !== active) {
    statActiveEl.textContent = String(active);
    if (tabTaskBadgeEl) tabTaskBadgeEl.textContent = String(active);
  }
  if (statCompletedEl && lastCompletedCount !== completed) {
    lastCompletedCount = completed;
    statCompletedEl.textContent = String(completed);
  }
  if (statErrorEl && lastErrorCount !== errorCount) {
    lastErrorCount = errorCount;
    statErrorEl.textContent = String(errorCount);
  }

  // Update job status
  if (lastActiveCount !== active) {
    lastActiveCount = active;
    if (jobEl) {
      jobEl.textContent = active ? activity.current?.label || `${active} active` : "Idle";
      jobEl.classList.toggle("idle", !active);
    }
  }

  // Fast fingerprint to avoid DOM churn
  const topLog = logs[0];
  const fingerprint = `${active}:${completed}:${errorCount}:${logs.length}:${topLog ? `${topLog.at}_${topLog.status}_${topLog.detail}` : "empty"}`;
  if (fingerprint === lastLogsFingerprint) {
    return;
  }
  lastLogsFingerprint = fingerprint;

  if (!logsEl) return;

  if (!logs.length) {
    logsEl.innerHTML = '<div class="empty-state">Chưa có hoạt động nào</div>';
    return;
  }

  // Build rows efficiently using DocumentFragment
  const fragment = document.createDocumentFragment();
  const maxRender = Math.min(logs.length, 30);

  for (let i = 0; i < maxRender; i++) {
    const item = logs[i];
    const row = document.createElement("div");
    row.className = "log-row";

    const time = document.createElement("span");
    time.className = "log-time";
    time.textContent = timeLabel(item.at);

    const badge = document.createElement("span");
    const status = item.status || "info";
    badge.className = `log-badge ${status}`;
    badge.textContent = getBadgeChar(status);

    const body = document.createElement("div");
    body.className = "log-body";

    const label = document.createElement("span");
    label.className = "log-label";
    label.textContent = item.label || "Activity";
    body.append(label);

    if (item.detail) {
      const detail = document.createElement("span");
      detail.className = "log-detail";
      detail.textContent = `· ${item.detail}`;
      body.append(detail);
    }

    row.append(time, badge, body);
    fragment.append(row);
  }

  logsEl.replaceChildren(fragment);
}

function updateStatus(connected, accountReady) {
  if (!statusBadgeEl || !statusEl) return;
  if (connected && accountReady) {
    statusBadgeEl.className = "status-pill connected";
    statusEl.textContent = "Ready";
    if (bannerAlertEl && !bannerAlertEl.dataset.userDismissed) {
      bannerAlertEl.hidden = true;
    }
  } else if (connected) {
    statusBadgeEl.className = "status-pill syncing";
    statusEl.textContent = "Syncing";
  } else {
    statusBadgeEl.className = "status-pill disconnected";
    statusEl.textContent = "Disconnected";
    if (bannerAlertEl && !bannerAlertEl.dataset.userDismissed) {
      bannerAlertEl.hidden = false;
    }
  }
}

async function refresh() {
  try {
    const state = await send({ type: "FLOW_PROVIDER_GET_STATE" });
    if (!state) return;

    if (versionEl && state.version) {
      versionEl.textContent = `v${state.version}`;
    }

    updateStatus(state.connected, state.account?.ready);

    if (accountEl) {
      if (state.account?.email) {
        accountEl.textContent = state.account.email;
        accountEl.title = state.account.email;
      } else {
        accountEl.textContent = "Chưa đăng nhập Flow";
        accountEl.title = "Mở Google Flow để đăng nhập tài khoản";
      }
    }

    if (creditsPillEl && creditsValueEl) {
      if (Number.isFinite(state.account?.credits)) {
        creditsValueEl.textContent = `${state.account.credits} cr`;
        creditsPillEl.hidden = false;
      } else {
        creditsPillEl.hidden = true;
      }
    }

    renderActivity(state.activity);
  } catch (_) {
    updateStatus(false, false);
  }
}

// Prompt line count updates
function updatePromptCount() {
  if (!promptInputEl || !promptCountEl) return;
  const text = promptInputEl.value.trim();
  if (!text) {
    promptCountEl.textContent = "0 prompt(s)";
    return;
  }
  if (toggleMultiEl?.checked) {
    const lines = text.split("\n").filter((l) => l.trim().length > 0);
    promptCountEl.textContent = `${lines.length} prompt(s)`;
  } else {
    promptCountEl.textContent = "1 prompt";
  }
}

if (promptInputEl) {
  promptInputEl.addEventListener("input", updatePromptCount);
}
if (toggleMultiEl) {
  toggleMultiEl.addEventListener("change", updatePromptCount);
}

// Prompt Assistant (adds high-fidelity enhancers)
if (btnPromptAssistantEl) {
  btnPromptAssistantEl.onclick = () => {
    if (!promptInputEl) return;
    const current = promptInputEl.value.trim();
    const additions = "cinematic lighting, ultra detailed 8k, photorealistic, masterpiece, depth of field";
    if (!current) {
      promptInputEl.value = `cyberpunk city at dusk, neon rain reflections, ${additions}`;
    } else {
      promptInputEl.value = `${current}, ${additions}`;
    }
    updatePromptCount();
  };
}

// Provider tabs selection
document.querySelectorAll(".provider-pill").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".provider-pill").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const provider = btn.dataset.provider;
    if (modelSelectEl) {
      if (provider === "chatgpt") {
        modelSelectEl.innerHTML = '<option value="gpt-4o">GPT-4o Vision</option><option value="o1-preview">o1 Preview</option>';
      } else if (provider === "gemini") {
        modelSelectEl.innerHTML = '<option value="gemini-1.5-pro">Gemini 1.5 Pro</option><option value="imagen-3">Imagen 3</option>';
      } else {
        modelSelectEl.innerHTML = '<option value="imagen-3">Nano Banana 2</option><option value="imagen-3-pro">Flow Imagen 3 Pro</option><option value="flow-v2">Flow V2 Ultra</option><option value="veo-video">Veo 2 Video Motion</option>';
      }
    }
  };
});

// Feature nav tabs
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
  };
});

// Segmented buttons (Ratio, Media type)
document.querySelectorAll("#ratio-group .seg-btn").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll("#ratio-group .seg-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
  };
});

document.querySelectorAll("#media-type-group .seg-btn").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll("#media-type-group .seg-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
  };
});

// Stepper
if (stepperDecEl && stepperIncEl && stepperValEl) {
  stepperDecEl.onclick = () => {
    if (currentQuantity > 1) {
      currentQuantity--;
      stepperValEl.textContent = String(currentQuantity);
    }
  };
  stepperIncEl.onclick = () => {
    if (currentQuantity < 8) {
      currentQuantity++;
      stepperValEl.textContent = String(currentQuantity);
    }
  };
}

// Drawer collapsible toggle
if (drawerToggleEl && activityDrawerEl) {
  drawerToggleEl.onclick = () => {
    activityDrawerEl.classList.toggle("collapsed");
    drawerToggleEl.textContent = activityDrawerEl.classList.contains("collapsed") ? "▼" : "▲";
  };
}
if (btnToggleLogsEl && activityDrawerEl) {
  btnToggleLogsEl.onclick = () => {
    activityDrawerEl.classList.toggle("collapsed");
    btnToggleLogsEl.classList.toggle("active", !activityDrawerEl.classList.contains("collapsed"));
  };
}

// Popout to tab
if (popoutTabBtnEl) {
  popoutTabBtnEl.onclick = () => {
    chrome.tabs.create({ url: chrome.runtime.getURL("popup/popup.html") });
  };
}

// Close Banner
if (bannerCloseEl && bannerAlertEl) {
  bannerCloseEl.onclick = () => {
    bannerAlertEl.hidden = true;
    bannerAlertEl.dataset.userDismissed = "true";
  };
}

// Generate CTA Action
if (btnGenerateEl) {
  btnGenerateEl.onclick = async () => {
    hideError();
    const prompt = promptInputEl?.value?.trim();
    if (!prompt) {
      showError("Vui lòng nhập nội dung prompt trước khi tạo.");
      promptInputEl?.focus();
      return;
    }

    btnGenerateEl.style.transform = "scale(0.98)";
    setTimeout(() => { btnGenerateEl.style.transform = ""; }, 150);

    // If Google Flow is active, make sure Flow tab is available
    const activeProvider = document.querySelector(".provider-pill.active")?.dataset.provider;
    if (activeProvider === "chatgpt") {
      const resp = await send({ type: "CHATGPT_OPEN_TAB" }).catch(() => null);
      if (!resp) showError("Không thể mở tab ChatGPT");
    } else {
      const resp = await send({ type: "FLOW_PROVIDER_OPEN_FLOW" }).catch(() => null);
      if (!resp?.ok) showError(resp?.error || "Không thể mở Google Flow");
    }
  };
}

if (flowBtnEl) {
  flowBtnEl.onclick = async () => {
    hideError();
    const result = await send({ type: "FLOW_PROVIDER_OPEN_FLOW" });
    if (!result?.ok) {
      showError(result?.error || "Không thể mở Google Flow");
    }
  };
}

if (copyLogsEl) {
  copyLogsEl.onclick = async () => {
    hideError();
    try {
      const state = await send({ type: "FLOW_PROVIDER_GET_STATE" });
      const lines = [
        `Flow Provider ${state?.version || "unknown"}`,
        `Connected: ${Boolean(state?.connected)}`,
        `Account ready: ${Boolean(state?.account?.ready)}`,
        `Account email: ${state?.account?.email || "none"}`,
        `Credits: ${state?.account?.credits ?? "unknown"}`,
        `Stats: ${state?.activity?.activeCount || 0} active, ${state?.activity?.completedCount || 0} completed, ${state?.activity?.errorCount || 0} errors`,
        "--- Activity Logs ---",
        ...((state?.activity?.logs || []).slice().reverse().map((item) => (
          `[${new Date(item.at).toLocaleTimeString()}] [${String(item.status || "info").toUpperCase()}] ${item.label || "Activity"}${item.detail ? ` · ${item.detail}` : ""}`
        ))),
      ];
      await navigator.clipboard.writeText(lines.join("\n"));
      const prevText = copyLogsEl.textContent;
      copyLogsEl.textContent = "✓ Copied";
      setTimeout(() => { copyLogsEl.textContent = prevText; }, 1200);
    } catch (err) {
      showError(err?.message || "Không thể sao chép nhật ký");
    }
  };
}

if (clearLogsEl) {
  clearLogsEl.onclick = async () => {
    hideError();
    try {
      await send({ type: "FLOW_PROVIDER_CLEAR_LOGS" });
      lastLogsFingerprint = null;
      renderActivity({ logs: [], activeCount: 0 });
    } catch (err) {
      showError(err?.message || "Không thể xóa nhật ký");
    }
  };
}

function showError(msg) {
  if (!errorEl) return;
  errorEl.textContent = msg;
  errorEl.hidden = false;
  setTimeout(() => {
    hideError();
  }, 4000);
}

function hideError() {
  if (!errorEl) return;
  errorEl.textContent = "";
  errorEl.hidden = true;
}

// Initial fetch
refresh();

// Lightweight polling interval
const timer = setInterval(() => {
  refresh().catch(() => { });
}, 1000);

window.addEventListener("unload", () => {
  clearInterval(timer);
});
