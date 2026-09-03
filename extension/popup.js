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

const send = (message) => chrome.runtime.sendMessage(message);

let lastLogsFingerprint = null;
let lastActiveCount = null;
let lastCompletedCount = null;
let lastErrorCount = null;

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
    jobEl.textContent = active ? activity.current?.label || `${active} active` : "Idle";
    jobEl.classList.toggle("idle", !active);
  }

  // Fast fingerprint to avoid needless DOM churn and UI freezes
  const topLog = logs[0];
  const fingerprint = `${active}:${completed}:${errorCount}:${logs.length}:${topLog ? `${topLog.at}_${topLog.status}_${topLog.detail}` : "empty"}`;
  if (fingerprint === lastLogsFingerprint) {
    return;
  }
  lastLogsFingerprint = fingerprint;

  if (!logs.length) {
    logsEl.innerHTML = '<div class="empty-state">Chưa có hoạt động nào</div>';
    return;
  }

  // Build rows efficiently using DocumentFragment
  const fragment = document.createDocumentFragment();
  const maxRender = Math.min(logs.length, 40);

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
  if (connected && accountReady) {
    statusBadgeEl.className = "status-badge connected";
    statusEl.textContent = "Ready";
  } else if (connected) {
    statusBadgeEl.className = "status-badge syncing";
    statusEl.textContent = "Syncing";
  } else {
    statusBadgeEl.className = "status-badge disconnected";
    statusEl.textContent = "Disconnected";
  }
}

async function refresh() {
  try {
    const state = await send({ type: "FLOW_PROVIDER_GET_STATE" });
    if (!state) return;

    if (versionEl && state.version) {
      versionEl.textContent = `v${state.version}`;
    }

    const ready = Boolean(state.connected && state.account?.ready);
    updateStatus(state.connected, state.account?.ready);

    if (state.account?.email) {
      accountEl.textContent = state.account.email;
      accountEl.title = state.account.email;
    } else {
      accountEl.textContent = "Chưa đăng nhập Google Flow";
      accountEl.title = "Mở Google Flow để đăng nhập tài khoản";
    }

    if (Number.isFinite(state.account?.credits)) {
      creditsValueEl.textContent = `${state.account.credits} credits`;
      creditsPillEl.hidden = false;
    } else {
      creditsPillEl.hidden = true;
    }

    renderActivity(state.activity);
  } catch (_) {
    updateStatus(false, false);
  }
}

flowBtnEl.onclick = async () => {
  hideError();
  const result = await send({ type: "FLOW_PROVIDER_OPEN_FLOW" });
  if (!result?.ok) {
    showError(result?.error || "Không thể mở Google Flow");
  }
};

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

function showError(msg) {
  errorEl.textContent = msg;
  errorEl.hidden = false;
}

function hideError() {
  errorEl.textContent = "";
  errorEl.hidden = true;
}

// Initial fetch
refresh();

// Lightweight polling interval
const timer = setInterval(() => {
  refresh().catch(() => {});
}, 1000);

window.addEventListener("unload", () => {
  clearInterval(timer);
});
