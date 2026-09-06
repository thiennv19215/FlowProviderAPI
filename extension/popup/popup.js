const statusEl = document.querySelector('#status');
const statusBadgeEl = document.querySelector('#status-badge');
const accountEl = document.querySelector('#account');
const creditsPillEl = document.querySelector('#credits-pill');
const creditsValueEl = document.querySelector('#credits-value');
const versionEl = document.querySelector('#version');
const errorEl = document.querySelector('#error');
const jobEl = document.querySelector('#job');
const logsEl = document.querySelector('#logs');
const statActiveEl = document.querySelector('#stat-active');
const statCompletedEl = document.querySelector('#stat-completed');
const statErrorEl = document.querySelector('#stat-error');
const copyLogsEl = document.querySelector('#copy-logs');
const clearLogsEl = document.querySelector('#clear-logs');
const flowBtnEl = document.querySelector('#flow');
const refreshBtnEl = document.querySelector('#refresh');
const bannerTextEl = document.querySelector('#banner-text');
const backendStatusEl = document.querySelector('#backend-status');
const backendProvidersEl = document.querySelector('#backend-providers');
const backendQueuedEl = document.querySelector('#backend-queued');
const backendDispatchingEl = document.querySelector('#backend-dispatching');
const backendRunningEl = document.querySelector('#backend-running');
const backendCapacityEl = document.querySelector('#backend-capacity');

const send = (message) => chrome.runtime.sendMessage(message);
let lastLogsFingerprint = null;
let lastBackendHealth = null;

function timeLabel(value) {
  if (!value) return '';
  const date = new Date(value);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function updateStatus(connected, accountReady) {
  if (!statusBadgeEl || !statusEl) return;
  if (connected && accountReady) {
    statusBadgeEl.className = 'fp-status-pill connected';
    statusEl.textContent = 'Ready';
    if (bannerTextEl) bannerTextEl.textContent = 'Provider và Google Flow đã sẵn sàng nhận job từ backend.';
    return;
  }
  if (connected) {
    statusBadgeEl.className = 'fp-status-pill syncing';
    statusEl.textContent = 'Syncing';
    if (bannerTextEl) bannerTextEl.textContent = 'Đã nối Provider, đang đồng bộ phiên Google Flow.';
    return;
  }
  statusBadgeEl.className = 'fp-status-pill disconnected';
  statusEl.textContent = 'Disconnected';
  if (bannerTextEl) bannerTextEl.textContent = 'Chưa kết nối Provider. Extension sẽ tự reconnect.';
}

function renderActivity(activity = {}) {
  const active = Number(activity.activeCount || 0);
  const completed = Number(activity.completedCount || 0);
  const errors = Number(activity.errorCount || 0);
  const logs = Array.isArray(activity.logs) ? activity.logs : [];

  if (statActiveEl) statActiveEl.textContent = String(active);
  if (statCompletedEl) statCompletedEl.textContent = String(completed);
  if (statErrorEl) statErrorEl.textContent = String(errors);
  if (jobEl) {
    jobEl.textContent = active ? activity.current?.label || `${active} in flight` : 'Idle';
    jobEl.classList.toggle('idle', !active);
  }

  const top = logs[0];
  const fingerprint = `${active}:${completed}:${errors}:${logs.length}:${top ? `${top.at}:${top.status}:${top.detail}` : 'empty'}`;
  if (fingerprint === lastLogsFingerprint) return;
  lastLogsFingerprint = fingerprint;

  if (!logsEl) return;
  if (!logs.length) {
    logsEl.innerHTML = '<div class="empty-state">Chưa có hoạt động. Job được tạo và quản lý bởi FlowProviderAPI.</div>';
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const item of logs.slice(0, 30)) {
    const row = document.createElement('div');
    row.className = 'log-row';

    const time = document.createElement('span');
    time.className = 'log-time';
    time.textContent = timeLabel(item.at);

    const badge = document.createElement('span');
    badge.className = `log-badge ${item.status || 'info'}`;
    badge.textContent = item.status === 'done' ? '✓' : item.status === 'error' ? '✕' : item.status === 'running' ? '●' : '•';

    const body = document.createElement('div');
    body.className = 'log-body';
    const label = document.createElement('span');
    label.className = 'log-label';
    label.textContent = item.label || 'Activity';
    body.append(label);
    if (item.detail) {
      const detail = document.createElement('span');
      detail.className = 'log-detail';
      detail.textContent = ` · ${item.detail}`;
      body.append(detail);
    }

    row.append(time, badge, body);
    fragment.append(row);
  }
  logsEl.replaceChildren(fragment);
}

function renderBackendHealth(health) {
  lastBackendHealth = health && typeof health === 'object' ? health : null;
  if (!lastBackendHealth) {
    if (backendStatusEl) backendStatusEl.textContent = 'Unavailable';
    if (backendProvidersEl) backendProvidersEl.textContent = '--';
    if (backendQueuedEl) backendQueuedEl.textContent = '--';
    if (backendDispatchingEl) backendDispatchingEl.textContent = '--';
    if (backendRunningEl) backendRunningEl.textContent = '--';
    if (backendCapacityEl) backendCapacityEl.textContent = 'Queue -- / --';
    return;
  }

  const jobs = lastBackendHealth.jobs || {};
  const active = Number.isFinite(lastBackendHealth.active_jobs) ? lastBackendHealth.active_jobs : 0;
  const capacity = Number.isFinite(lastBackendHealth.job_queue_capacity) ? lastBackendHealth.job_queue_capacity : 0;
  if (backendStatusEl) backendStatusEl.textContent = String(lastBackendHealth.status || 'unknown');
  if (backendProvidersEl) backendProvidersEl.textContent = String(Number(lastBackendHealth.provider_accounts || 0));
  if (backendQueuedEl) backendQueuedEl.textContent = String(Number(jobs.queued || 0));
  if (backendDispatchingEl) backendDispatchingEl.textContent = String(Number(jobs.dispatching || 0));
  if (backendRunningEl) backendRunningEl.textContent = String(Number(jobs.running || 0));
  if (backendCapacityEl) {
    backendCapacityEl.textContent = capacity > 0 ? `Queue ${active} / ${capacity}` : `Active ${active}`;
    backendCapacityEl.classList.toggle('idle', active === 0);
  }
}

async function fetchBackendHealth() {
  try {
    const response = await send({ type: 'FLOW_PROVIDER_GET_BACKEND_HEALTH' });
    return response?.ok && response.health && typeof response.health === 'object'
      ? response.health
      : null;
  } catch (_) {
    return null;
  }
}

async function refresh() {
  try {
    const state = await send({ type: 'FLOW_PROVIDER_GET_STATE' });
    if (!state) return;
    if (versionEl) versionEl.textContent = state.version ? `v${state.version}` : 'unknown';
    updateStatus(Boolean(state.connected), Boolean(state.account?.ready));
    if (accountEl) {
      accountEl.textContent = state.account?.email || 'Chưa có phiên Google Flow';
      accountEl.title = state.account?.email || 'Mở Google Flow và đăng nhập để đồng bộ phiên';
    }
    if (creditsPillEl && creditsValueEl) {
      const hasCredits = Number.isFinite(state.account?.credits);
      creditsPillEl.hidden = !hasCredits;
      if (hasCredits) creditsValueEl.textContent = `${state.account.credits} cr`;
    }
    renderActivity(state.activity);
    renderBackendHealth(await fetchBackendHealth());
  } catch (error) {
    updateStatus(false, false);
    renderBackendHealth(null);
    showError(error?.message || 'Không đọc được trạng thái connector.');
  }
}

if (flowBtnEl) {
  flowBtnEl.onclick = async () => {
    hideError();
    const result = await send({ type: 'FLOW_PROVIDER_OPEN_FLOW' });
    if (!result?.ok) showError(result?.error || 'Không thể mở Google Flow.');
  };
}

if (refreshBtnEl) refreshBtnEl.onclick = () => refresh();

if (copyLogsEl) {
  copyLogsEl.onclick = async () => {
    hideError();
    try {
      const state = await send({ type: 'FLOW_PROVIDER_GET_STATE' });
      const backend = lastBackendHealth;
      const lines = [
        `Flow Provider ${state?.version || 'unknown'}`,
        `Connected: ${Boolean(state?.connected)}`,
        `Account ready: ${Boolean(state?.account?.ready)}`,
        `Account: ${state?.account?.email || 'none'}`,
        `Credits: ${state?.account?.credits ?? 'unknown'}`,
        `Backend: ${backend?.status || 'unavailable'}`,
        `Providers ready: ${backend?.provider_accounts ?? 'unknown'}`,
        `Queue: ${backend?.active_jobs ?? 'unknown'} / ${backend?.job_queue_capacity ?? 'unknown'}`,
        `Jobs: ${backend?.jobs?.queued ?? 'unknown'} queued, ${backend?.jobs?.dispatching ?? 'unknown'} dispatching, ${backend?.jobs?.running ?? 'unknown'} running`,
        `Connector activity: ${state?.activity?.activeCount || 0} active, ${state?.activity?.completedCount || 0} completed, ${state?.activity?.errorCount || 0} errors`,
        '--- Activity Logs ---',
        ...((state?.activity?.logs || []).slice().reverse().map((item) => (
          `[${new Date(item.at).toLocaleTimeString()}] [${String(item.status || 'info').toUpperCase()}] ${item.label || 'Activity'}${item.detail ? ` · ${item.detail}` : ''}`
        ))),
      ];
      await navigator.clipboard.writeText(lines.join('\n'));
      const previous = copyLogsEl.textContent;
      copyLogsEl.textContent = '✓ Copied';
      setTimeout(() => { copyLogsEl.textContent = previous; }, 1200);
    } catch (error) {
      showError(error?.message || 'Không thể sao chép nhật ký.');
    }
  };
}

if (clearLogsEl) {
  clearLogsEl.onclick = async () => {
    hideError();
    try {
      await send({ type: 'FLOW_PROVIDER_CLEAR_LOGS' });
      lastLogsFingerprint = null;
      await refresh();
    } catch (error) {
      showError(error?.message || 'Không thể xóa nhật ký.');
    }
  };
}

function showError(message) {
  if (!errorEl) return;
  errorEl.textContent = message;
  errorEl.hidden = false;
  setTimeout(hideError, 4000);
}

function hideError() {
  if (!errorEl) return;
  errorEl.textContent = '';
  errorEl.hidden = true;
}

void refresh();
const timer = setInterval(() => { void refresh(); }, 3000);
window.addEventListener('unload', () => clearInterval(timer));
