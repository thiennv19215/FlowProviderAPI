(() => {
  let adminKey = "";
  let clients = [];
  const $ = (selector) => document.querySelector(selector);
  const authPanel = $("#auth-panel");
  const dashboard = $("#dashboard");
  const createDialog = $("#create-dialog");
  const keyDialog = $("#key-dialog");

  function toast(message, error = false) {
    const node = $("#toast");
    node.textContent = message;
    node.className = `toast show${error ? " error" : ""}`;
    clearTimeout(node.timer);
    node.timer = setTimeout(() => node.className = "toast", 2600);
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {"X-Admin-Key": adminKey, ...(options.body ? {"Content-Type": "application/json"} : {}), ...options.headers},
    });
    if (response.status === 204) return null;
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error?.message || `Request failed (${response.status})`);
    return body;
  }

  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
  const formatDate = (value) => new Intl.DateTimeFormat("vi-VN", {dateStyle:"medium", timeStyle:"short"}).format(new Date(value));

  function render() {
    const query = $("#search").value.trim().toLowerCase();
    const filtered = clients.filter((item) => `${item.name} ${item.id} ${item.key_prefix}`.toLowerCase().includes(query));
    $("#total-count").textContent = clients.length;
    $("#active-count").textContent = clients.filter((item) => item.enabled).length;
    $("#revoked-count").textContent = clients.filter((item) => !item.enabled).length;
    $("#empty-state").hidden = filtered.length !== 0;
    $("#client-list").innerHTML = filtered.map((item) => `
      <tr>
        <td class="client-name"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.id)}</small></td>
        <td><code class="prefix">${escapeHtml(item.key_prefix)}…</code></td>
        <td class="limits">${item.max_concurrent_jobs} jobs đồng thời<br>${item.rate_limit_per_minute} req/phút · P${item.priority}</td>
        <td>${formatDate(item.created_at)}</td>
        <td><span class="badge ${item.enabled ? "active" : "revoked"}">${item.enabled ? "Hoạt động" : "Đã thu hồi"}</span></td>
        <td><div class="row-actions"><button class="rotate" data-rotate="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}">Cấp lại key</button>${item.enabled ? `<button class="revoke" data-revoke="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}">Thu hồi</button>` : ""}</div></td>
      </tr>`).join("");
  }

  async function loadClients() {
    const body = await request("/v1/api-clients");
    clients = body.data;
    render();
  }

  $("#auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    adminKey = $("#admin-key").value;
    try {
      await loadClients();
      $("#admin-key").value = "";
      authPanel.hidden = true;
      dashboard.hidden = false;
      toast("Đã kết nối control plane");
    } catch (error) {
      adminKey = "";
      toast(error.message, true);
    } finally { button.disabled = false; }
  });

  $("#toggle-key").addEventListener("click", () => {
    const input = $("#admin-key");
    input.type = input.type === "password" ? "text" : "password";
    $("#toggle-key").textContent = input.type === "password" ? "Hiện" : "Ẩn";
  });
  $("#new-client-button").addEventListener("click", () => adminKey ? createDialog.showModal() : $("#admin-key").focus());
  document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => createDialog.close()));
  $("#search").addEventListener("input", render);
  $("#refresh-button").addEventListener("click", async () => { try { await loadClients(); toast("Danh sách đã được cập nhật"); } catch (error) { toast(error.message, true); } });
  $("#disconnect-button").addEventListener("click", () => { adminKey = ""; clients = []; dashboard.hidden = true; authPanel.hidden = false; toast("Đã ngắt kết nối"); });

  $("#create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter;
    const data = Object.fromEntries(new FormData(event.currentTarget));
    ["priority", "max_concurrent_jobs", "rate_limit_per_minute"].forEach((key) => data[key] = Number(data[key]));
    button.disabled = true;
    try {
      const created = await request("/v1/api-clients", {method:"POST", body:JSON.stringify(data)});
      createDialog.close();
      event.currentTarget.reset();
      $("#issued-key").value = created.api_key;
      $("#copy-key").textContent = "Sao chép key";
      $("#key-dialog-message").textContent = "Hãy sao chép và gửi qua kênh an toàn. Key này sẽ không hiển thị lại.";
      keyDialog.showModal();
      await loadClients();
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  });

  $("#client-list").addEventListener("click", async (event) => {
    const rotateButton = event.target.closest("[data-rotate]");
    if (rotateButton) {
      if (!confirm(`Cấp key mới cho “${rotateButton.dataset.name}”? Key hiện tại sẽ ngừng hoạt động ngay.`)) return;
      rotateButton.disabled = true;
      try {
        const rotated = await request(`/v1/api-clients/${encodeURIComponent(rotateButton.dataset.rotate)}/rotate`, {method:"POST"});
        $("#issued-key").value = rotated.api_key;
        $("#copy-key").textContent = "Sao chép key";
        $("#key-dialog-message").textContent = "Key cũ đã ngừng hoạt động. Hãy sao chép key mới ngay vì nó sẽ không hiển thị lại.";
        keyDialog.showModal();
        await loadClients();
      } catch (error) { toast(error.message, true); rotateButton.disabled = false; }
      return;
    }
    const button = event.target.closest("[data-revoke]");
    if (!button || !confirm(`Thu hồi API key của “${button.dataset.name}”?`)) return;
    button.disabled = true;
    try { await request(`/v1/api-clients/${encodeURIComponent(button.dataset.revoke)}`, {method:"DELETE"}); await loadClients(); toast("API key đã được thu hồi"); }
    catch (error) { toast(error.message, true); button.disabled = false; }
  });

  $("#copy-key").addEventListener("click", async (event) => {
    const input = $("#issued-key");
    let copied = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(input.value);
        copied = true;
      }
    } catch { copied = false; }
    if (!copied) {
      input.focus();
      input.select();
      input.setSelectionRange(0, input.value.length);
      try { copied = document.execCommand("copy"); } catch { copied = false; }
    }
    if (copied) {
      event.currentTarget.textContent = "✓ Đã sao chép";
      toast("Đã sao chép API key");
    } else {
      input.focus();
      input.select();
      toast("Key đã được chọn; nhấn Ctrl+C để sao chép.", true);
    }
  });
  $("#key-done").addEventListener("click", () => { $("#issued-key").value = ""; keyDialog.close(); });
})();
