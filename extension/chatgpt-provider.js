const CHATGPT_HOME_URL = "https://chatgpt.com/";
const CHATGPT_SESSION_URL = "https://chatgpt.com/api/auth/session";
const CHATGPT_TAB_ID_KEY = "flow-provider-chatgpt-tab-id-v1";

function isChatGPTUrl(url) {
  if (!url || typeof url !== "string") return false;
  return /^https:\/\/(?:[a-z0-9-]+\.)*(?:chatgpt\.com|chat\.openai\.com)(\/|$)/i.test(url);
}

async function waitForChatGPTTab(tabId, timeoutMs = 25000) {
  const deadline = Date.now() + timeoutMs;
  let reloaded = false;
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    if (!tab) throw new Error("chatgpt_tab_closed");
    if (tab.discarded && !reloaded && typeof chrome?.tabs?.reload === "function") {
      reloaded = true;
      await chrome.tabs.reload(tabId).catch(() => {});
    }
    const currentUrl = tab.url || tab.pendingUrl || "";
    if (isChatGPTUrl(currentUrl)) {
      if (tab.status === "complete") return tab;
      if (chrome?.scripting?.executeScript) {
        try {
          const ping = await chrome.scripting.executeScript({
            target: { tabId },
            func: () => document.readyState,
          }).catch(() => null);
          const state = ping?.[0]?.result;
          if (state === "interactive" || state === "complete") return tab;
        } catch (_) {}
      }
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error("chatgpt_tab_timeout");
}

async function findOrOpenChatGPTTab() {
  const tabs = await chrome.tabs.query({
    url: ["https://chatgpt.com/*", "https://*.chatgpt.com/*", "https://chat.openai.com/*"],
  }).catch(() => []);

  const existing = tabs
    .filter((tab) => tab.id && isChatGPTUrl(tab.url || tab.pendingUrl))
    .sort((left, right) => Number(right.status === "complete") - Number(left.status === "complete")
      || Number(right.active) - Number(left.active)
      || Number(right.lastAccessed || 0) - Number(left.lastAccessed || 0))[0];

  if (existing?.id) {
    await chrome.storage.local.set({ [CHATGPT_TAB_ID_KEY]: existing.id });
    appendActivity("ChatGPT tab reused", "done", `tab ${existing.id}`);
    await waitForChatGPTTab(existing.id);
    return { tabId: existing.id, isNew: false };
  }

  const stored = await chrome.storage.local.get(CHATGPT_TAB_ID_KEY);
  const trackedTabId = Number(stored?.[CHATGPT_TAB_ID_KEY]);
  if (Number.isInteger(trackedTabId) && trackedTabId > 0) {
    const tracked = await chrome.tabs.get(trackedTabId).catch(() => null);
    if (tracked && isChatGPTUrl(tracked.url || tracked.pendingUrl)) {
      appendActivity("ChatGPT tab reused", "done", `tab ${trackedTabId}`);
      await waitForChatGPTTab(trackedTabId);
      return { tabId: trackedTabId, isNew: false };
    }
    await chrome.storage.local.remove(CHATGPT_TAB_ID_KEY);
  }

  const normalWindows = await chrome.windows.getAll({ windowTypes: ["normal"] }).catch(() => []);
  const targetWindow = normalWindows
    .filter((window) => window.id)
    .sort((left, right) => Number(right.focused) - Number(left.focused))[0];

  let tab;
  if (targetWindow?.id) {
    tab = await chrome.tabs.create({
      windowId: targetWindow.id,
      url: CHATGPT_HOME_URL,
      active: false,
    });
  } else {
    const createdWindow = await chrome.windows.create({
      url: CHATGPT_HOME_URL,
      focused: false,
      type: "normal",
    });
    tab = createdWindow.tabs?.[0];
  }
  if (!tab?.id) throw new Error("chatgpt_tab_create_failed");
  await chrome.storage.local.set({ [CHATGPT_TAB_ID_KEY]: tab.id });
  appendActivity("ChatGPT tab opened", "done", `tab ${tab.id}`);
  await waitForChatGPTTab(tab.id);
  return { tabId: tab.id, isNew: true };
}

async function getChatGPTSession(tabId = null) {
  let targetTabId = tabId;
  if (!targetTabId) {
    const info = await findOrOpenChatGPTTab();
    targetTabId = info.tabId;
  }
  await waitForChatGPTTab(targetTabId);

  const results = await chrome.scripting.executeScript({
    target: { tabId: targetTabId },
    world: "MAIN",
    func: async () => {
      try {
        const resp = await fetch("https://chatgpt.com/api/auth/session", {
          method: "GET",
          credentials: "include",
          headers: { "accept": "application/json" },
        });
        if (!resp.ok) {
          return { ok: false, status: resp.status, error: `session_http_${resp.status}` };
        }
        const data = await resp.json().catch(() => null);
        if (!data || !data.accessToken) {
          return { ok: false, error: "not_logged_in_or_no_access_token" };
        }
        return {
          ok: true,
          accessToken: data.accessToken,
          user: data.user || null,
          expires: data.expires || null,
        };
      } catch (err) {
        return { ok: false, error: err?.message || String(err) };
      }
    },
  });

  const res = results?.[0]?.result;
  if (!res?.ok) {
    throw new Error(res?.error || "get_chatgpt_session_failed");
  }
  return res;
}

async function chatGPTFetch(tabId, spec) {
  let targetTabId = tabId;
  if (!targetTabId) {
    const info = await findOrOpenChatGPTTab();
    targetTabId = info.tabId;
  }
  await waitForChatGPTTab(targetTabId);

  const results = await chrome.scripting.executeScript({
    target: { tabId: targetTabId },
    world: "MAIN",
    args: [{ spec }],
    func: async ({ spec }) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), spec.timeoutMs || 60000);
      try {
        const resp = await fetch(spec.url, {
          method: spec.method || "GET",
          headers: spec.headers || {},
          body: spec.body,
          credentials: "include",
          signal: controller.signal,
        });
        const contentType = resp.headers.get("content-type") || "";
        const isJson = contentType.includes("json");
        const out = {
          ok: resp.ok,
          status: resp.status,
          headers: Object.fromEntries(resp.headers.entries()),
          finalUrl: resp.url,
        };
        if (isJson) {
          out.data = await resp.json().catch(() => null);
        } else {
          out.text = await resp.text().catch(() => "");
        }
        return { ok: true, data: out };
      } catch (err) {
        return { ok: false, error: err?.message || String(err) };
      } finally {
        clearTimeout(timer);
      }
    },
  });

  const res = results?.[0]?.result;
  if (!res?.ok) {
    throw new Error(res?.error || "chatgpt_fetch_failed");
  }
  return res.data;
}

async function handleChatGPTRpc(msg) {
  switch (msg.type) {
    case "CHATGPT_OPEN_TAB":
      return await findOrOpenChatGPTTab();
    case "CHATGPT_GET_SESSION":
      return await getChatGPTSession(msg.tabId);
    case "CHATGPT_FETCH":
      return await chatGPTFetch(msg.tabId, msg.spec);
    default:
      throw new Error(`unknown_chatgpt_rpc:${msg.type}`);
  }
}
