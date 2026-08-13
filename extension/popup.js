const statusEl = document.querySelector("#status");
const accountEl = document.querySelector("#account");
const errorEl = document.querySelector("#error");
const jobEl = document.querySelector("#job");
const logsEl = document.querySelector("#logs");
const send = (message) => chrome.runtime.sendMessage(message);

function timeLabel(value) {
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function renderActivity(activity = {}) {
  const active = Number(activity.activeCount || 0);
  jobEl.textContent = active ? activity.current?.label || `${active} active` : "Idle";
  jobEl.classList.toggle("idle", !active);
  const logs = Array.isArray(activity.logs) ? activity.logs : [];
  logsEl.replaceChildren();
  if (!logs.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No activity yet";
    logsEl.append(empty);
    return;
  }
  for (const item of logs) {
    const row = document.createElement("div");
    row.className = "log";
    const time = document.createElement("span");
    time.textContent = timeLabel(item.at);
    const mark = document.createElement("span");
    mark.className = `mark ${item.status}`;
    mark.textContent = item.status === "done" ? "✓" : item.status === "error" ? "×" : "•";
    const message = document.createElement("span");
    message.className = "message";
    message.textContent = item.label || "Activity";
    if (item.detail) {
      const detail = document.createElement("span");
      detail.className = "detail";
      detail.textContent = ` · ${item.detail}`;
      message.append(detail);
    }
    row.append(time, mark, message);
    logsEl.append(row);
  }
}

async function refresh() {
  const state = await send({ type: "FLOW_PROVIDER_GET_STATE" });
  const ready = Boolean(state.connected && state.account?.ready);
  statusEl.textContent = ready ? "Ready" : state.connected ? "Syncing" : "Disconnected";
  statusEl.classList.toggle("connected", ready);
  accountEl.textContent = state.account?.email
    ? `${state.account.email}${Number.isFinite(state.account.credits) ? ` · ${state.account.credits} credits` : ""}`
    : "Open Google Flow and sign in.";
  renderActivity(state.activity);
}

document.querySelector("#flow").onclick = async () => {
  errorEl.textContent = "";
  const result = await send({ type: "FLOW_PROVIDER_OPEN_FLOW" });
  if (!result?.ok) errorEl.textContent = result?.error || "Cannot open Google Flow";
};

refresh().catch(() => {
  statusEl.textContent = "Disconnected";
});

setInterval(() => refresh().catch(() => {}), 1000);
