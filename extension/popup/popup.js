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

// Console controls
const promptInputEl = document.querySelector("#prompt-input");
const promptCountEl = document.querySelector("#prompt-count");
const toggleMultiEl = document.querySelector("#toggle-multi");
const btnGenerateEl = document.querySelector("#btn-generate");
const btnPromptAssistantEl = document.querySelector("#btn-prompt-assistant");
const btnClearPromptEl = document.querySelector("#btn-clear-prompt");
const popoutTabBtnEl = document.querySelector("#popout-tab");
const bannerAlertEl = document.querySelector("#banner-alert");
const bannerCloseEl = document.querySelector("#banner-close");
const drawerToggleEl = document.querySelector("#drawer-toggle");
const activityDrawerEl = document.querySelector("#activity-drawer");
const btnToggleActivityEl = document.querySelector("#btn-toggle-activity");
const tabTaskBadgeEl = document.querySelector("#tab-task-badge");
const stepperDecEl = document.querySelector("#stepper-dec");
const stepperIncEl = document.querySelector("#stepper-inc");
const stepperValEl = document.querySelector("#stepper-val");
const modelSelectEl = document.querySelector("#model-select");
const projectScopeLabelEl = document.querySelector("#project-scope-label");

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

  // Update telemetry stats counters
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

  // Update job indicator
  if (lastActiveCount !== active) {
    lastActiveCount = active;
    if (jobEl) {
      jobEl.textContent = active ? activity.current?.label || `${active} in flight` : "Idle";
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
    logsEl.innerHTML = '<div class="empty-state">Sẵn sàng tiếp nhận lệnh tạo từ API hoặc MCP Adapter</div>';
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
    statusBadgeEl.className = "fp-status-pill connected";
    statusEl.textContent = "Ready";
    if (bannerAlertEl && !bannerAlertEl.dataset.userDismissed) {
      bannerAlertEl.hidden = true;
    }
  } else if (connected) {
    statusBadgeEl.className = "fp-status-pill syncing";
    statusEl.textContent = "Syncing";
  } else {
    statusBadgeEl.className = "fp-status-pill disconnected";
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
        accountEl.textContent = "Chưa kết nối Google Flow";
        accountEl.title = "Mở Google Flow để xác thực phiên";
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
    promptCountEl.textContent = "Ready";
    return;
  }
  if (toggleMultiEl?.checked) {
    const lines = text.split("\n").filter((l) => l.trim().length > 0);
    promptCountEl.textContent = `${lines.length} lines`;
  } else {
    promptCountEl.textContent = `${text.length} chars`;
  }
}

if (promptInputEl) {
  promptInputEl.addEventListener("input", updatePromptCount);
  promptInputEl.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "Enter") {
      btnGenerateEl?.click();
    }
  });
}
if (toggleMultiEl) {
  toggleMultiEl.addEventListener("change", updatePromptCount);
}

// Clear prompt
if (btnClearPromptEl && promptInputEl) {
  btnClearPromptEl.onclick = () => {
    promptInputEl.value = "";
    updatePromptCount();
    promptInputEl.focus();
  };
}

// Style Booster / Prompt Assistant
if (btnPromptAssistantEl && promptInputEl) {
  btnPromptAssistantEl.onclick = () => {
    const current = promptInputEl.value.trim();
    const styleModifiers = "volumetric lighting, cinematic composition, photorealistic 8k, highly detailed";
    if (!current) {
      promptInputEl.value = `Futuristic architectural glass pavilion on a misty mountain lake, ${styleModifiers}`;
    } else {
      promptInputEl.value = `${current}, ${styleModifiers}`;
    }
    updatePromptCount();
    promptInputEl.focus();
  };
}

// Provider pill selector
document.querySelectorAll(".route-pill").forEach((pill) => {
  pill.onclick = () => {
    document.querySelectorAll(".route-pill").forEach((p) => p.classList.remove("active"));
    pill.classList.add("active");
    const provider = pill.dataset.provider;
    if (modelSelectEl) {
      if (provider === "chatgpt") {
        modelSelectEl.innerHTML = '<option value="gpt-4o">ChatGPT-4o (Vision)</option><option value="o1-preview">o1 Reasoning Model</option>';
      } else if (provider === "gemini") {
        modelSelectEl.innerHTML = '<option value="gemini-1.5-pro">Gemini 1.5 Pro</option><option value="imagen-3">Imagen 3</option>';
      } else {
        modelSelectEl.innerHTML = '<option value="imagen-3">Imagen 3 Pro (Flow Engine)</option><option value="flow-v2">Flow V2 Ultra Fast</option><option value="veo-video">Veo 2 Cinematic Video</option>';
      }
    }
    if (projectScopeLabelEl) {
      projectScopeLabelEl.textContent = provider === "flow" ? "Workspace: Default" : `Target: ${provider.toUpperCase()}`;
    }
  };
});

// Segmented buttons (Ratio, Media type)
document.querySelectorAll("#ratio-group .segment-btn").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll("#ratio-group .segment-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
  };
});

document.querySelectorAll("#media-type-group .segment-btn").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll("#media-type-group .segment-btn").forEach((b) => b.classList.remove("active"));
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
    if (currentQuantity < 4) {
      currentQuantity++;
      stepperValEl.textContent = String(currentQuantity);
    }
  };
}

// Subnav item switching
document.querySelectorAll(".subnav-item").forEach((item) => {
  item.onclick = () => {
    document.querySelectorAll(".subnav-item").forEach((i) => i.classList.remove("active"));
    item.classList.add("active");
  };
});

// Drawer collapsible toggle
if (drawerToggleEl && activityDrawerEl) {
  drawerToggleEl.onclick = () => {
    activityDrawerEl.classList.toggle("collapsed");
    drawerToggleEl.textContent = activityDrawerEl.classList.contains("collapsed") ? "▼" : "▲";
  };
}
if (btnToggleActivityEl && activityDrawerEl) {
  btnToggleActivityEl.onclick = () => {
    activityDrawerEl.classList.toggle("collapsed");
    btnToggleActivityEl.classList.toggle("active", !activityDrawerEl.classList.contains("collapsed"));
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

// Result showcase elements
const resultGalleryEl = document.querySelector("#result-gallery");
const resultGridEl = document.querySelector("#result-grid");
const resultCountEl = document.querySelector("#result-count");
const btnCloseResultEl = document.querySelector("#btn-close-result");

if (btnCloseResultEl && resultGalleryEl) {
  btnCloseResultEl.onclick = () => {
    resultGalleryEl.hidden = true;
  };
}

// Generate CTA Action - Directly triggers image creation!
if (btnGenerateEl) {
  btnGenerateEl.onclick = async () => {
    hideError();
    const prompt = promptInputEl?.value?.trim();
    if (!prompt) {
      showError("Vui lòng nhập nội dung prompt trước khi phát lệnh.");
      promptInputEl?.focus();
      return;
    }

    const activeProvider = document.querySelector(".route-pill.active")?.dataset.provider || "flow";
    const activeRatio = document.querySelector("#ratio-group .segment-btn.active")?.dataset.ratio || "9:16";
    const activeMediaType = document.querySelector("#media-type-group .segment-btn.active")?.dataset.type || "image";
    const selectedModel = modelSelectEl?.value || "imagen-3";

    // Set UI to loading state
    const originalContent = btnGenerateEl.innerHTML;
    btnGenerateEl.disabled = true;
    btnGenerateEl.style.opacity = "0.8";
    btnGenerateEl.innerHTML = `
      <span class="cta-content">
        <span class="cta-spin">⚙</span>
        <span>Đang tạo ảnh trên ${activeProvider === "flow" ? "Google Flow" : "ChatGPT"}...</span>
      </span>
    `;

    try {
      const resp = await send({
        type: "FLOW_PROVIDER_CREATE_GENERATION",
        payload: {
          prompt,
          provider: activeProvider,
          aspectRatio: activeRatio,
          mediaType: activeMediaType,
          model: selectedModel,
          count: currentQuantity
        }
      });

      if (!resp?.ok) {
        throw new Error(resp?.error || "Tác vụ tạo ảnh không thành công.");
      }

      if (resp.images && resp.images.length > 0) {
        if (resultGalleryEl && resultGridEl) {
          resultGridEl.innerHTML = "";
          const ratioClass = activeRatio === "16:9" ? "ratio-16-9" : (activeRatio === "1:1" ? "ratio-1-1" : "");

          resp.images.forEach((img, idx) => {
            const item = document.createElement("div");
            item.className = `result-item ${ratioClass}`;
            item.innerHTML = `
              <img src="${img.url}" alt="Output ${idx + 1}" loading="lazy">
              <div class="result-overlay">
                <a href="${img.url}" target="_blank" class="result-btn-view" title="Xem ảnh gốc">↗ Xem</a>
                <a href="${img.url}" download="flow_generated_${idx + 1}.png" class="result-btn-download" title="Tải về">↓ Lưu</a>
              </div>
            `;
            resultGridEl.appendChild(item);
          });

          if (resultCountEl) {
            resultCountEl.textContent = `${resp.images.length} ảnh`;
          }
          resultGalleryEl.hidden = false;
          resultGalleryEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      } else if (resp.message) {
        appendActivity("Hoàn tất", "done", resp.message);
      }
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      btnGenerateEl.disabled = false;
      btnGenerateEl.style.opacity = "";
      btnGenerateEl.innerHTML = originalContent;
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
