const statusEl = document.querySelector("#status");
const accountEl = document.querySelector("#account");
const errorEl = document.querySelector("#error");
const send = (message) => chrome.runtime.sendMessage(message);

async function refresh() {
  const state = await send({ type: "FLOW_PROVIDER_GET_STATE" });
  statusEl.textContent = state.connected ? "Connected" : "Disconnected";
  statusEl.classList.toggle("connected", Boolean(state.connected));
  accountEl.textContent = state.account?.email
    ? `${state.account.email}${Number.isFinite(state.account.credits) ? ` · ${state.account.credits} credits` : ""}`
    : "Open Google Flow and sign in.";
}

document.querySelector("#flow").onclick = async () => {
  errorEl.textContent = "";
  const result = await send({ type: "FLOW_PROVIDER_OPEN_FLOW" });
  if (!result?.ok) errorEl.textContent = result?.error || "Cannot open Google Flow";
};

refresh().catch(() => {
  statusEl.textContent = "Disconnected";
});
