function buildQuery(params = {}) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    search.set(key, String(value));
  });
  const query = search.toString();
  return query ? `?${query}` : "";
}

async function readJson(response) {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    // Сессия истекла / нет авторизации — сообщаем приложению, чтобы показать логин.
    if (response.status === 401) {
      window.dispatchEvent(new Event("auth:expired"));
    }
    const detail = body?.detail ? String(body.detail) : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return body;
}

// ---- Авторизация ----

export async function getMe() {
  const response = await fetch("/api/auth/me", { cache: "no-store" });
  return readJson(response);
}

export async function login(username, password) {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return readJson(response);
}

export async function logout() {
  const response = await fetch("/api/auth/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return readJson(response);
}

export async function changePassword(oldPassword, newPassword) {
  const response = await fetch("/api/auth/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  return readJson(response);
}

// ---- Настройки (админ) ----

export async function getSettings() {
  const response = await fetch("/api/settings", { cache: "no-store" });
  return readJson(response);
}

export async function updateSettings(payload) {
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

// ---- LDAP-провайдер (вкладка настроек) ----

export async function getLdapSettings() {
  const response = await fetch("/api/settings/ldap", { cache: "no-store" });
  return readJson(response);
}

export async function updateLdapSettings(payload) {
  const response = await fetch("/api/settings/ldap", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function testLdapConnection(payload) {
  const response = await fetch("/api/settings/ldap/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

// ---- LDAP-провайдеры (список, вкладка настроек) ----

export async function getLdapProviders() {
  const response = await fetch("/api/settings/ldap/providers", {
    cache: "no-store",
  });
  return readJson(response);
}

export async function updateLdapProviders(providers) {
  const response = await fetch("/api/settings/ldap/providers", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ providers }),
  });
  return readJson(response);
}

export async function testLdapProvider(payload) {
  const response = await fetch("/api/settings/ldap/providers/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

// ---- Доступ: роли и маппинг групп AD (вкладка настроек) ----

export async function getRoleMappings() {
  const response = await fetch("/api/settings/roles", { cache: "no-store" });
  return readJson(response);
}

export async function updateRoleMappings(payload) {
  const response = await fetch("/api/settings/roles", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function previewRole(login) {
  const response = await fetch("/api/settings/roles/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login }),
  });
  return readJson(response);
}

// ---- Сервисы (хаб ссылок) ----

export async function getServices() {
  const response = await fetch("/api/services", { cache: "no-store" });
  return readJson(response);
}

export async function updateServices(services) {
  const response = await fetch("/api/settings/services", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ services }),
  });
  return readJson(response);
}

// ---- Маппинг атрибутов AD → профиль (вкладка настроек) ----

export async function getAttrMap() {
  const response = await fetch("/api/settings/attributes", { cache: "no-store" });
  return readJson(response);
}

export async function updateAttrMap(mappings) {
  const response = await fetch("/api/settings/attributes", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mappings }),
  });
  return readJson(response);
}

// ---- Коллекторы (вкладка настроек) ----

export async function getCollectorSettings() {
  const response = await fetch("/api/settings/collectors", { cache: "no-store" });
  return readJson(response);
}

export async function updateCollectorSettings(collectors) {
  const response = await fetch("/api/settings/collectors", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ collectors }),
  });
  return readJson(response);
}

export async function testCollector(payload) {
  const response = await fetch("/api/settings/collectors/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function getSystemInfo() {
  const response = await fetch("/api/system");
  return readJson(response);
}

export async function searchEvents(params) {
  const response = await fetch(`/api/search${buildQuery(params)}`);
  return readJson(response);
}

export async function getCollectors() {
  const response = await fetch("/api/collectors", { cache: "no-store" });
  return readJson(response);
}

export async function searchAdUsers(query) {
  const response = await fetch(`/api/ad-search${buildQuery({ q: query })}`);
  return readJson(response);
}

export async function getAdGroups(username) {
  const response = await fetch(`/api/ad-groups${buildQuery({ username })}`);
  return readJson(response);
}

export async function reconnectAd() {
  const response = await fetch("/api/ad/reconnect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return readJson(response);
}

// ---- Шаблоны групп (свои у каждого пользователя) ----

export async function getGroupTemplates() {
  const response = await fetch("/api/ad/templates", { cache: "no-store" });
  return readJson(response);
}

export async function createGroupTemplate(name, groups) {
  const response = await fetch("/api/ad/templates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, groups }),
  });
  return readJson(response);
}

export async function updateGroupTemplate(id, name, groups) {
  const response = await fetch(`/api/ad/templates/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, groups }),
  });
  return readJson(response);
}

export async function deleteGroupTemplate(id) {
  const response = await fetch(`/api/ad/templates/${id}`, { method: "DELETE" });
  return readJson(response);
}

// ---- AD admin (создание/редактирование пользователей) ----

export async function getAdOus(query = "") {
  const response = await fetch(`/api/ad/ous${buildQuery({ q: query })}`);
  return readJson(response);
}

export async function getAdGroupsList(query = "", limit = 200) {
  const response = await fetch(`/api/ad/groups-list${buildQuery({ q: query, limit })}`);
  return readJson(response);
}

export async function getAdUserDetail(login) {
  const response = await fetch(`/api/ad/user${buildQuery({ login })}`);
  return readJson(response);
}

export async function getAdOuUsers(ou, limit = 3000) {
  const response = await fetch(`/api/ad/ou-users${buildQuery({ ou, limit })}`);
  return readJson(response);
}

export async function createAdUser(payload) {
  const response = await fetch("/api/ad/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function updateAdUser(payload) {
  const response = await fetch("/api/ad/users", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function bulkCreateAdUsers(users) {
  const response = await fetch("/api/ad/users/bulk-create", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ users }),
  });
  return readJson(response);
}

export async function bulkUpdateAdUsers(users) {
  const response = await fetch("/api/ad/users/bulk-update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ users }),
  });
  return readJson(response);
}

// ---- Avanpost FAM (попадание учётки + синхронизация) ----

export async function getFamStatus(login) {
  const response = await fetch(`/api/ad/fam-status${buildQuery({ login })}`);
  return readJson(response);
}

export async function syncFam(login) {
  const response = await fetch("/api/ad/fam-sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login }),
  });
  return readJson(response);
}

// ---- Yandex Cloud: отчёт по ВМ ----

export async function getYcVms(refresh = false) {
  const response = await fetch(`/api/yc/vms${refresh ? "?refresh=1" : ""}`, {
    cache: "no-store",
  });
  return readJson(response);
}

export async function getYcTariff() {
  const response = await fetch("/api/yc/tariff", { cache: "no-store" });
  return readJson(response);
}

export async function saveYcTariff(tariff) {
  const response = await fetch("/api/yc/tariff", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(tariff),
  });
  return readJson(response);
}

// Скачивает xlsx-отчёт по выбранным машинам (rows — массив выбранных ВМ).
export async function downloadYcReport(rows) {
  const response = await fetch("/api/yc/report/xlsx", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows }),
  });
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("auth:expired"));
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* тело не JSON — оставляем статус */
    }
    throw new Error(detail);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : `yc_vm_report.xlsx`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return filename;
}

