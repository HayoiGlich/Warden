import { useEffect, useState } from "react";
import PageHero from "../components/PageHero";
import ToastStack from "../components/ToastStack";
import { HelpHint } from "../components/HelpHint";
import {
  getLdapProviders,
  updateLdapProviders,
  testLdapProvider,
  getRoleMappings,
  updateRoleMappings,
  previewRole,
  getAttrMap,
  updateAttrMap,
  getCollectorSettings,
  updateCollectorSettings,
  testCollector,
} from "../api";

/* --------------------------------------------------- Avanpost-style rows */

function Row({ label, hint, full, children }) {
  return (
    <div className={`av-row${full ? " av-row-full" : ""}`}>
      <div className="av-label">
        <span>{label}</span>
        {hint ? <HelpHint text={hint} /> : null}
      </div>
      <div className="av-field">{children}</div>
    </div>
  );
}

function Toggle({ checked, onChange, label, disabled }) {
  return (
    <label className={`av-toggle${disabled ? " is-disabled" : ""}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={onChange}
      />
      <span className="av-toggle-track">
        <span className="av-toggle-thumb" />
      </span>
      {label ? <span className="av-toggle-text">{label}</span> : null}
    </label>
  );
}

/* ------------------------------------------------------------- providers */

let provSeq = 0;
function blankProvider() {
  provSeq += 1;
  return {
    _id: `prov-${provSeq}`,
    id: "",
    name: "Новый провайдер",
    enabled: true,
    active: false,
    vendor: "ad",
    host: "",
    port: "",
    use_ssl: true,
    failover: [],
    start_tls: false,
    tls_validate: false,
    use_pooling: false,
    connect_timeout: "",
    bind_type: "simple",
    bind_dn: "",
    bind_password: "",
    bind_password_set: false,
    domain: "",
    base_dn: "",
    user_filter: "",
    group_filter: "",
    upn_suffix: "",
    default_user_ou: "",
    login_group: "",
    attr_login: "sAMAccountName",
    attr_email: "mail",
    attr_display: "displayName",
    attr_first: "givenName",
    attr_last: "sn",
  };
}

function providersToRows(list) {
  const rows = (Array.isArray(list) ? list : []).map((p) => {
    provSeq += 1;
    const failover = Array.isArray(p.failover) ? p.failover : [];
    return {
      _id: `prov-${provSeq}`,
      id: p.id || "",
      name: p.name ?? "LDAP",
      enabled: p.enabled !== false,
      active: Boolean(p.active),
      vendor: p.vendor || "ad",
      host: p.host ?? "",
      port: p.port ?? "",
      use_ssl: Boolean(p.use_ssl),
      failover: [...failover],
      start_tls: Boolean(p.start_tls),
      tls_validate: Boolean(p.tls_validate),
      use_pooling: Boolean(p.use_pooling),
      connect_timeout: p.connect_timeout ?? "",
      bind_type: p.bind_type || "simple",
      bind_dn: p.bind_dn ?? "",
      bind_password: "",
      bind_password_set: Boolean(p.bind_password_set),
      domain: p.domain ?? "",
      base_dn: p.base_dn ?? "",
      user_filter: p.user_filter ?? "",
      group_filter: p.group_filter ?? "",
      upn_suffix: p.upn_suffix ?? "",
      default_user_ou: p.default_user_ou ?? "",
      login_group: p.login_group ?? "",
      attr_login: p.attr_login || "sAMAccountName",
      attr_email: p.attr_email || "mail",
      attr_display: p.attr_display || "displayName",
      attr_first: p.attr_first || "givenName",
      attr_last: p.attr_last || "sn",
    };
  });
  return rows.length ? rows : [blankProvider()];
}

function provPayload(p) {
  const anon = p.bind_type === "anonymous";
  return {
    id: p.id || undefined,
    name: String(p.name || "").trim() || "LDAP",
    enabled: Boolean(p.enabled),
    active: Boolean(p.active),
    vendor: p.vendor,
    host: String(p.host || "").trim(),
    port: String(p.port).trim() === "" ? null : Number(p.port),
    use_ssl: p.use_ssl,
    failover: (p.failover || []).map((s) => String(s).trim()).filter(Boolean),
    start_tls: p.start_tls,
    tls_validate: p.tls_validate,
    use_pooling: p.use_pooling,
    connect_timeout:
      String(p.connect_timeout).trim() === "" ? null : Number(p.connect_timeout),
    bind_type: p.bind_type,
    bind_dn: anon ? "" : p.bind_dn,
    bind_password: p.bind_password, // "" = не менять
    domain: p.domain,
    base_dn: p.base_dn,
    user_filter: p.user_filter,
    group_filter: p.group_filter,
    upn_suffix: p.upn_suffix,
    default_user_ou: p.default_user_ou,
    login_group: p.login_group,
    attr_login: p.attr_login,
    attr_email: p.attr_email,
    attr_display: p.attr_display,
    attr_first: p.attr_first,
    attr_last: p.attr_last,
  };
}

/* ------------------------------------------------------------ collectors */

let colSeq = 0;
function blankCollector() {
  colSeq += 1;
  return {
    _id: `col-${colSeq}`,
    name: "",
    host: "",
    port: "",
    database: "",
    user: "",
    password: "",
    password_set: false,
    enabled: true,
  };
}

function collectorsToRows(list) {
  const rows = (Array.isArray(list) ? list : []).map((c) => {
    colSeq += 1;
    return {
      _id: `col-${colSeq}`,
      name: c.name ?? "",
      host: c.host ?? "",
      port: c.port ?? "",
      database: c.database ?? "",
      user: c.user ?? "",
      password: "",
      password_set: Boolean(c.password_set),
      enabled: c.enabled !== false,
    };
  });
  return rows.length ? rows : [blankCollector()];
}

/* ----------------------------------------------------------------- roles */

let ruleSeq = 0;
function blankRule() {
  ruleSeq += 1;
  return { _id: `rule-${ruleSeq}`, group: "", role: "viewer" };
}

function rulesToRows(list) {
  return (Array.isArray(list) ? list : []).map((m) => {
    ruleSeq += 1;
    return {
      _id: `rule-${ruleSeq}`,
      group: m.group ?? "",
      role: m.role || "viewer",
    };
  });
}

/* ------------------------------------------------------- attr mapping */

let attrSeq = 0;
function blankAttr() {
  attrSeq += 1;
  return { _id: `attr-${attrSeq}`, attr: "", label: "", primary: false };
}

function attrsToRows(list) {
  const rows = (Array.isArray(list) ? list : []).map((m) => {
    attrSeq += 1;
    return {
      _id: `attr-${attrSeq}`,
      attr: m.attr ?? "",
      label: m.label ?? "",
      primary: Boolean(m.primary),
    };
  });
  return rows.length ? rows : [blankAttr()];
}

/* ================================================================= page */

export default function SettingsPage() {
  const [tab, setTab] = useState("providers");
  const [toasts, setToasts] = useState([]);
  const [loading, setLoading] = useState(true);

  const [providers, setProviders] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [provSaving, setProvSaving] = useState(false);
  const [provTesting, setProvTesting] = useState(false);
  const [provTestResult, setProvTestResult] = useState(null);

  const [collectors, setCollectors] = useState([]);
  const [collectorStatus, setCollectorStatus] = useState([]);
  const [collectorsSaving, setCollectorsSaving] = useState(false);
  const [testingHost, setTestingHost] = useState("");

  const [roleCatalog, setRoleCatalog] = useState([]);
  const [defaultRole, setDefaultRole] = useState("viewer");
  const [rules, setRules] = useState([]);
  const [rolesSaving, setRolesSaving] = useState(false);
  const [previewLogin, setPreviewLogin] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [previewResult, setPreviewResult] = useState(null);

  const [attrRows, setAttrRows] = useState([]);
  const [attrSaving, setAttrSaving] = useState(false);

  function pushToast(message, type = "info", title = "") {
    const id = `${Date.now()}-${Math.random()}`;
    const icon =
      type === "success"
        ? "bi-check-circle"
        : type === "danger"
          ? "bi-exclamation-triangle"
          : "bi-info-circle";
    setToasts((current) => [...current, { id, message, type, title, icon }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((t) => t.id !== id));
    }, 4200);
  }

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [provData, colData, roleData, attrData] = await Promise.all([
          getLdapProviders(),
          getCollectorSettings(),
          getRoleMappings(),
          getAttrMap(),
        ]);
        if (cancelled) return;
        const rows = providersToRows(provData?.providers);
        setProviders(rows);
        setSelectedId(rows.find((r) => r.active)?._id || rows[0]?._id || "");
        setCollectors(collectorsToRows(colData?.collectors));
        setCollectorStatus(Array.isArray(colData?.status) ? colData.status : []);
        setRoleCatalog(Array.isArray(roleData?.roles) ? roleData.roles : []);
        setDefaultRole(roleData?.default_role || "viewer");
        setRules(rulesToRows(roleData?.mappings));
        setAttrRows(attrsToRows(attrData?.mappings));
      } catch (err) {
        pushToast(err.message || String(err), "danger", "Настройки");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  /* -------- providers ops -------- */

  const selected = providers.find((p) => p._id === selectedId) || null;

  function setProv(id, key, value) {
    setProviders((cur) =>
      cur.map((p) => (p._id === id ? { ...p, [key]: value } : p))
    );
  }
  function setFailover(id, idx, value) {
    setProviders((cur) =>
      cur.map((p) =>
        p._id === id
          ? { ...p, failover: p.failover.map((s, i) => (i === idx ? value : s)) }
          : p
      )
    );
  }
  function addFailover(id) {
    setProviders((cur) =>
      cur.map((p) => (p._id === id ? { ...p, failover: [...p.failover, ""] } : p))
    );
  }
  function removeFailover(id, idx) {
    setProviders((cur) =>
      cur.map((p) =>
        p._id === id
          ? { ...p, failover: p.failover.filter((_, i) => i !== idx) }
          : p
      )
    );
  }
  function addProvider() {
    const p = blankProvider();
    setProviders((cur) => [...cur, p]);
    setSelectedId(p._id);
    setProvTestResult(null);
  }
  function removeProvider(id) {
    setProviders((cur) => {
      const next = cur.filter((p) => p._id !== id);
      const rows = next.length ? next : [blankProvider()];
      if (id === selectedId) setSelectedId(rows[0]._id);
      return rows;
    });
  }
  function makeActive(id) {
    setProviders((cur) =>
      cur.map((p) => ({ ...p, active: p._id === id }))
    );
  }

  async function onSaveProviders() {
    setProvSaving(true);
    try {
      const data = await updateLdapProviders(providers.map(provPayload));
      const rows = providersToRows(data?.providers);
      setProviders(rows);
      setSelectedId(
        rows.find((r) => r.active)?._id ||
          rows.find((r) => r.id === selected?.id)?._id ||
          rows[0]?._id ||
          ""
      );
      pushToast(
        data?.ad_connected
          ? "Сохранено. Активный провайдер подключён."
          : "Сохранено. Активный провайдер не подключился — проверьте параметры.",
        data?.ad_connected ? "success" : "warning",
        "LDAP-провайдеры"
      );
    } catch (err) {
      pushToast(err.message || String(err), "danger", "LDAP-провайдеры");
    } finally {
      setProvSaving(false);
    }
  }

  async function onTestProvider(p) {
    setProvTesting(true);
    setProvTestResult(null);
    try {
      const anon = p.bind_type === "anonymous";
      const data = await testLdapProvider({
        id: p.id || undefined,
        host: String(p.host || "").trim(),
        port: String(p.port).trim() === "" ? null : Number(p.port),
        use_ssl: p.use_ssl,
        bind_type: p.bind_type,
        bind_dn: anon ? "" : p.bind_dn,
        bind_credentials: anon ? "" : p.bind_password,
        start_tls: p.start_tls,
        tls_validate: p.tls_validate,
        domain: p.domain,
        connect_timeout:
          String(p.connect_timeout).trim() === ""
            ? null
            : Number(p.connect_timeout),
      });
      setProvTestResult(data);
    } catch (err) {
      setProvTestResult({ ok: false, error: err.message || String(err) });
    } finally {
      setProvTesting(false);
    }
  }

  /* -------- collectors ops -------- */

  function setCol(id, key, value) {
    setCollectors((cur) =>
      cur.map((r) => (r._id === id ? { ...r, [key]: value } : r))
    );
  }
  function addCollector() {
    setCollectors((cur) => [...cur, blankCollector()]);
  }
  function removeCollector(id) {
    setCollectors((cur) =>
      cur.length > 1 ? cur.filter((r) => r._id !== id) : cur
    );
  }
  function collectorStatusFor(row) {
    return collectorStatus.find(
      (s) => s.name === row.name || s.host === row.host
    );
  }

  async function onSaveCollectors(event) {
    event.preventDefault();
    const rows = collectors.filter((r) => String(r.host).trim());
    if (!rows.length) {
      pushToast("Добавьте хотя бы один коллектор с host", "warning", "Коллекторы");
      return;
    }
    setCollectorsSaving(true);
    try {
      const payload = rows.map((r) => ({
        name: String(r.name || "").trim(),
        host: String(r.host).trim(),
        port: String(r.port).trim() === "" ? null : Number(r.port),
        database: String(r.database || "").trim(),
        user: String(r.user || "").trim(),
        password: r.password,
        enabled: Boolean(r.enabled),
      }));
      const data = await updateCollectorSettings(payload);
      setCollectors(collectorsToRows(data?.collectors));
      const status = Array.isArray(data?.status) ? data.status : [];
      setCollectorStatus(status);
      const ok = status.filter((s) => s.connected).length;
      pushToast(
        `Сохранено. Подключено ${ok} из ${status.length}.`,
        ok === status.length ? "success" : "warning",
        "Коллекторы"
      );
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Коллекторы");
    } finally {
      setCollectorsSaving(false);
    }
  }

  async function onTestCollector(row) {
    setTestingHost(row._id);
    try {
      const data = await testCollector({
        host: String(row.host).trim(),
        port: String(row.port).trim() === "" ? null : Number(row.port),
        database: String(row.database || "").trim(),
        user: String(row.user || "").trim(),
        password: row.password,
      });
      pushToast(
        data?.connected
          ? `Подключение к «${row.host}» успешно`
          : `Не удалось: ${data?.error || "нет соединения"}`,
        data?.connected ? "success" : "danger",
        "Проверка коллектора"
      );
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Проверка коллектора");
    } finally {
      setTestingHost("");
    }
  }

  /* -------- roles ops -------- */

  function setRule(id, key, value) {
    setRules((cur) =>
      cur.map((r) => (r._id === id ? { ...r, [key]: value } : r))
    );
  }
  function addRule() {
    setRules((cur) => [...cur, blankRule()]);
  }
  function removeRule(id) {
    setRules((cur) => cur.filter((r) => r._id !== id));
  }

  async function onSaveRoles(event) {
    event.preventDefault();
    setRolesSaving(true);
    try {
      const mappings = rules
        .map((r) => ({ group: String(r.group || "").trim(), role: r.role }))
        .filter((r) => r.group);
      const data = await updateRoleMappings({
        default_role: defaultRole,
        mappings,
      });
      setDefaultRole(data?.default_role || "viewer");
      setRules(rulesToRows(data?.mappings));
      pushToast(
        "Права сохранены. Применятся при следующем входе пользователей.",
        "success",
        "Доступ"
      );
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Доступ");
    } finally {
      setRolesSaving(false);
    }
  }

  async function onPreviewRole(event) {
    event.preventDefault();
    const login = previewLogin.trim();
    if (!login) return;
    setPreviewing(true);
    setPreviewResult(null);
    try {
      const data = await previewRole(login);
      setPreviewResult(data);
    } catch (err) {
      setPreviewResult({ error: err.message || String(err) });
    } finally {
      setPreviewing(false);
    }
  }

  const roleName = (value) =>
    roleCatalog.find((r) => r.value === value)?.label || value;

  /* -------- attr mapping ops -------- */

  function setAttr(id, key, value) {
    setAttrRows((cur) =>
      cur.map((r) => (r._id === id ? { ...r, [key]: value } : r))
    );
  }
  function setAttrPrimary(id) {
    setAttrRows((cur) => cur.map((r) => ({ ...r, primary: r._id === id })));
  }
  function addAttr() {
    setAttrRows((cur) => [...cur, blankAttr()]);
  }
  function removeAttr(id) {
    setAttrRows((cur) => cur.filter((r) => r._id !== id));
  }

  async function onSaveAttrs(event) {
    event.preventDefault();
    setAttrSaving(true);
    try {
      const mappings = attrRows
        .map((r) => ({
          attr: String(r.attr || "").trim(),
          label: String(r.label || "").trim(),
          primary: Boolean(r.primary),
        }))
        .filter((r) => r.attr);
      const data = await updateAttrMap(mappings);
      setAttrRows(attrsToRows(data?.mappings));
      pushToast(
        "Маппинг сохранён. Применится при следующем входе пользователей.",
        "success",
        "Атрибуты"
      );
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Атрибуты");
    } finally {
      setAttrSaving(false);
    }
  }

  const anon = selected?.bind_type === "anonymous";

  return (
    <>
      <ToastStack
        items={toasts}
        onDismiss={(id) => setToasts((c) => c.filter((t) => t.id !== id))}
      />
      <PageHero
        icon="bi-gear"
        title="Настройки"
        subtitle="Изменения применяются сразу — без перезапуска."
        eyebrow="Администрирование"
      />

      {loading ? (
        <div className="admin-hint">Загрузка настроек...</div>
      ) : (
        <>
          <ul className="nav nav-tabs settings-tabs">
            <li className="nav-item">
              <button
                type="button"
                className={`nav-link${tab === "providers" ? " active" : ""}`}
                onClick={() => setTab("providers")}
              >
                <i className="bi bi-diagram-3 me-1" />
                LDAP-провайдеры
              </button>
            </li>
            <li className="nav-item">
              <button
                type="button"
                className={`nav-link${tab === "collectors" ? " active" : ""}`}
                onClick={() => setTab("collectors")}
              >
                <i className="bi bi-hdd-stack me-1" />
                Коллекторы
              </button>
            </li>
            <li className="nav-item">
              <button
                type="button"
                className={`nav-link${tab === "roles" ? " active" : ""}`}
                onClick={() => setTab("roles")}
              >
                <i className="bi bi-shield-lock me-1" />
                Доступ
              </button>
            </li>
            <li className="nav-item">
              <button
                type="button"
                className={`nav-link${tab === "attributes" ? " active" : ""}`}
                onClick={() => setTab("attributes")}
              >
                <i className="bi bi-person-vcard me-1" />
                Атрибуты
              </button>
            </li>
          </ul>

          {tab === "providers" ? (
            <div className="prov-layout">
              <aside className="prov-list">
                {providers.map((p) => (
                  <button
                    key={p._id}
                    type="button"
                    className={`prov-item${p._id === selectedId ? " is-selected" : ""}`}
                    onClick={() => {
                      setSelectedId(p._id);
                      setProvTestResult(null);
                    }}
                  >
                    <span className="prov-item-icon">
                      <i className={`bi ${p.vendor === "ad" ? "bi-windows" : "bi-diagram-3"}`} />
                    </span>
                    <span className="prov-item-main">
                      <span className="prov-item-name">{p.name || "LDAP"}</span>
                      <span className="prov-item-sub">
                        {p.host
                          ? `${p.host}${p.port ? `:${p.port}` : ""}`
                          : "адрес не задан"}
                      </span>
                    </span>
                    <span className="prov-item-badges">
                      {p.active ? (
                        <span className="prov-badge is-active">Активный</span>
                      ) : null}
                      {!p.enabled ? (
                        <span className="prov-badge is-off">выкл</span>
                      ) : null}
                    </span>
                  </button>
                ))}
                <button type="button" className="prov-add" onClick={addProvider}>
                  <i className="bi bi-plus-lg" />
                  Добавить провайдера
                </button>
              </aside>

              {selected ? (
                <div className="surface surface-pad prov-editor">
                  <div className="prov-editor-head">
                    <div className="prov-editor-title">
                      <input
                        className="form-control prov-name-input"
                        value={selected.name}
                        onChange={(e) => setProv(selected._id, "name", e.target.value)}
                        placeholder="Название провайдера"
                      />
                    </div>
                    <div className="prov-editor-head-actions">
                      {selected.active ? (
                        <span className="prov-badge is-active">
                          <i className="bi bi-check-circle-fill me-1" />
                          Активный
                        </span>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-outline-secondary btn-sm"
                          onClick={() => makeActive(selected._id)}
                          disabled={!selected.enabled}
                          title={
                            selected.enabled
                              ? "Использовать этот провайдер для входа и AD"
                              : "Сначала включите провайдер"
                          }
                        >
                          <i className="bi bi-star me-1" />
                          Сделать активным
                        </button>
                      )}
                      <label className="admin-mode-btn" style={{ cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={selected.enabled}
                          onChange={(e) =>
                            setProv(selected._id, "enabled", e.target.checked)
                          }
                        />
                        Включён
                      </label>
                    </div>
                  </div>

                  {/* ============ Настройки подключения ============ */}
                  <section className="av-section">
                    <div className="av-section-head">
                      <span>Настройки подключения</span>
                    </div>
                    <div className="av-section-body">
                      <Row label="Служба каталогов">
                        <select
                          className="av-input"
                          value={selected.vendor}
                          onChange={(e) =>
                            setProv(selected._id, "vendor", e.target.value)
                          }
                        >
                          <option value="ad">Active Directory</option>
                          <option value="other">Другой LDAP</option>
                        </select>
                      </Row>

                      <Row
                        label="Хост"
                        hint="Имя или IP контроллера домена. Без ldap://, только адрес. Пример: 172.16.110.221 или dc01.staff.local"
                      >
                        <input
                          className="av-input"
                          placeholder="172.16.110.221"
                          value={selected.host}
                          onChange={(e) =>
                            setProv(selected._id, "host", e.target.value)
                          }
                        />
                      </Row>

                      <Row
                        label="Порт"
                        hint="Пусто = авто: 636 при включённом LDAPS, иначе 389."
                      >
                        <input
                          type="number"
                          min="1"
                          className="av-input"
                          placeholder={selected.use_ssl ? "636" : "389"}
                          value={selected.port}
                          onChange={(e) =>
                            setProv(selected._id, "port", e.target.value)
                          }
                        />
                      </Row>

                      <Row
                        label="LDAPS (SSL/TLS)"
                        hint="Шифрованное соединение (порт 636). Для Active Directory обычно включено. Отдельно от StartTLS."
                      >
                        <Toggle
                          checked={selected.use_ssl}
                          onChange={(e) =>
                            setProv(selected._id, "use_ssl", e.target.checked)
                          }
                          label="Использовать защищённое соединение"
                        />
                      </Row>

                      <Row
                        label="StartTLS"
                        hint="Апгрейд обычного соединения (389) до шифрованного. Не включайте одновременно с LDAPS."
                      >
                        <Toggle
                          checked={selected.start_tls}
                          onChange={(e) =>
                            setProv(selected._id, "start_tls", e.target.checked)
                          }
                          label="Повышать до TLS (StartTLS)"
                        />
                      </Row>

                      <Row
                        label="Резервные серверы"
                        hint="Необязательно. Хост или host:порт, по одному в строке — перебираются, если основной недоступен. SSL как у основного."
                      >
                        <div className="av-servers">
                          {selected.failover.map((s, idx) => (
                            <div className="av-server-row" key={idx}>
                              <input
                                className="av-input"
                                placeholder="dc02.staff.local"
                                value={s}
                                onChange={(e) =>
                                  setFailover(selected._id, idx, e.target.value)
                                }
                              />
                              <button
                                type="button"
                                className="bulk-row-del"
                                title="Убрать адрес"
                                onClick={() => removeFailover(selected._id, idx)}
                              >
                                <i className="bi bi-x-lg" />
                              </button>
                            </div>
                          ))}
                          <button
                            type="button"
                            className="av-add-btn"
                            onClick={() => addFailover(selected._id)}
                          >
                            <i className="bi bi-plus-lg me-1" />
                            Резервный сервер
                          </button>
                        </div>
                      </Row>

                      <Row label="Проверка сертификата">
                        <Toggle
                          checked={selected.tls_validate}
                          onChange={(e) =>
                            setProv(selected._id, "tls_validate", e.target.checked)
                          }
                          label="Проверять сертификат сервера"
                        />
                      </Row>

                      <Row label="Пул соединений">
                        <Toggle
                          checked={selected.use_pooling}
                          onChange={(e) =>
                            setProv(selected._id, "use_pooling", e.target.checked)
                          }
                          label="Переиспользовать соединения"
                        />
                      </Row>

                      <Row
                        label="Тип аутентификации"
                        hint="simple — логин/пароль (обычно для LDAPS); ntlm — доменная (DOMAIN\\user); anonymous — без пароля (только чтение)."
                      >
                        <select
                          className="av-input"
                          value={selected.bind_type}
                          onChange={(e) =>
                            setProv(selected._id, "bind_type", e.target.value)
                          }
                        >
                          <option value="simple">simple</option>
                          <option value="ntlm">ntlm</option>
                          <option value="anonymous">anonymous</option>
                        </select>
                      </Row>

                      <Row
                        label="Домен (NetBIOS)"
                        hint="Короткое имя домена (STAFF для staff.local). Нужно для NTLM и подстановки к «голому» логину."
                      >
                        <input
                          className="av-input"
                          placeholder="STAFF"
                          value={selected.domain}
                          onChange={(e) =>
                            setProv(selected._id, "domain", e.target.value)
                          }
                        />
                      </Row>

                      <Row
                        label={`Имя пользователя${anon ? " (не требуется)" : ""}`}
                        hint="Служебная учётка. Операции AD выполняются под вошедшим пользователем (делегирование); эта учётка нужна лишь для анализатора, вычисления роли и локального админа. Формат: кратко (eventsreader), STAFF\\eventsreader, UPN или полный DN."
                      >
                        <input
                          className="av-input"
                          placeholder="eventsreader"
                          value={selected.bind_dn}
                          onChange={(e) =>
                            setProv(selected._id, "bind_dn", e.target.value)
                          }
                          disabled={anon}
                        />
                      </Row>

                      <Row
                        label={`Пароль${
                          anon
                            ? " (не требуется)"
                            : selected.bind_password_set
                              ? " (задан)"
                              : ""
                        }`}
                        hint="Пустое поле — оставить прежний пароль без изменений."
                      >
                        <input
                          type="password"
                          className="av-input"
                          autoComplete="new-password"
                          placeholder={
                            selected.bind_password_set
                              ? "•••••• (без изменений)"
                              : ""
                          }
                          value={selected.bind_password}
                          onChange={(e) =>
                            setProv(selected._id, "bind_password", e.target.value)
                          }
                          disabled={anon}
                        />
                      </Row>

                      <Row label="Тайм-аут подключения, сек">
                        <input
                          type="number"
                          min="1"
                          className="av-input"
                          placeholder="по умолчанию"
                          value={selected.connect_timeout}
                          onChange={(e) =>
                            setProv(selected._id, "connect_timeout", e.target.value)
                          }
                        />
                      </Row>
                    </div>
                  </section>

                  {/* ============ Настройки интеграции ============ */}
                  <section className="av-section">
                    <div className="av-section-head">
                      <span>Настройки интеграции</span>
                    </div>
                    <div className="av-section-body">
                      <Row
                        label="BaseDN"
                        hint="Ветка каталога, где искать. Пусто = автоопределение (defaultNamingContext). Пример: OU=Users,DC=staff,DC=local"
                      >
                        <input
                          className="av-input"
                          placeholder="пусто = авто (defaultNamingContext)"
                          value={selected.base_dn}
                          onChange={(e) =>
                            setProv(selected._id, "base_dn", e.target.value)
                          }
                        />
                      </Row>

                      <div className="av-divider">
                        <span>Настройки импорта пользователей</span>
                      </div>

                      <Row
                        label="Фильтр поиска пользователя"
                        hint="LDAP-фрагмент, добавляемый к поиску пользователей. Пример: (objectCategory=person). Пусто — без ограничений."
                      >
                        <input
                          className="av-input"
                          placeholder="(objectCategory=person)"
                          value={selected.user_filter}
                          onChange={(e) =>
                            setProv(selected._id, "user_filter", e.target.value)
                          }
                        />
                      </Row>

                      <Row
                        label="LDAP атрибут, содержащий логин пользователя"
                        hint="Для Active Directory — sAMAccountName. Менять только для нестандартных каталогов."
                      >
                        <input
                          className="av-input"
                          value={selected.attr_login}
                          onChange={(e) =>
                            setProv(selected._id, "attr_login", e.target.value)
                          }
                        />
                      </Row>

                      <Row
                        label="Группа для входа"
                        hint="Если указать — входить смогут только участники этой группы AD. Пусто — любой прошедший проверку."
                      >
                        <input
                          className="av-input"
                          placeholder="пусто = любой"
                          value={selected.login_group}
                          onChange={(e) =>
                            setProv(selected._id, "login_group", e.target.value)
                          }
                        />
                      </Row>

                      <Row label="UPN-суффикс в домене">
                        <input
                          className="av-input"
                          placeholder="staff.local"
                          value={selected.upn_suffix}
                          onChange={(e) =>
                            setProv(selected._id, "upn_suffix", e.target.value)
                          }
                        />
                      </Row>

                      <Row label="OU по умолчанию для новых учётных записей (DN)">
                        <input
                          className="av-input"
                          value={selected.default_user_ou}
                          onChange={(e) =>
                            setProv(selected._id, "default_user_ou", e.target.value)
                          }
                        />
                      </Row>

                      <div className="av-divider">
                        <span>Настройки импорта групп</span>
                      </div>

                      <Row
                        label="Фильтр поиска групп"
                        hint="LDAP-фрагмент к поиску групп. Пусто — без ограничений."
                      >
                        <input
                          className="av-input"
                          value={selected.group_filter}
                          onChange={(e) =>
                            setProv(selected._id, "group_filter", e.target.value)
                          }
                        />
                      </Row>

                      <div className="av-divider">
                        <span>Сопоставление атрибутов каталога</span>
                      </div>

                      <Row label="Email">
                        <input
                          className="av-input"
                          value={selected.attr_email}
                          onChange={(e) =>
                            setProv(selected._id, "attr_email", e.target.value)
                          }
                        />
                      </Row>
                      <Row label="Отображаемое имя">
                        <input
                          className="av-input"
                          value={selected.attr_display}
                          onChange={(e) =>
                            setProv(selected._id, "attr_display", e.target.value)
                          }
                        />
                      </Row>
                      <Row label="Имя">
                        <input
                          className="av-input"
                          value={selected.attr_first}
                          onChange={(e) =>
                            setProv(selected._id, "attr_first", e.target.value)
                          }
                        />
                      </Row>
                      <Row label="Фамилия">
                        <input
                          className="av-input"
                          value={selected.attr_last}
                          onChange={(e) =>
                            setProv(selected._id, "attr_last", e.target.value)
                          }
                        />
                      </Row>
                    </div>
                  </section>

                  {provTestResult ? (
                    <div
                      className={`admin-hint settings-test-result ${
                        provTestResult.ok ? "is-ok" : "is-err"
                      }`}
                    >
                      {provTestResult.ok ? (
                        <>
                          <i className="bi bi-check-circle me-1" />
                          Успешно ({provTestResult.channel || "?"})
                          {provTestResult.base_dn
                            ? ` · Base DN: ${provTestResult.base_dn}`
                            : ""}
                        </>
                      ) : (
                        <>
                          <i className="bi bi-exclamation-triangle me-1" />
                          Ошибка: {provTestResult.error || "не удалось подключиться"}
                        </>
                      )}
                    </div>
                  ) : null}

                  <div className="prov-editor-foot">
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={onSaveProviders}
                      disabled={provSaving}
                    >
                      <i className="bi bi-save me-1" />
                      {provSaving ? "Сохраняю..." : "Сохранить всё и подключить"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-outline-secondary"
                      onClick={() => onTestProvider(selected)}
                      disabled={provTesting}
                    >
                      <i className="bi bi-plug me-1" />
                      {provTesting ? "Проверяю..." : "Проверить подключение"}
                    </button>
                    <div className="prov-editor-foot-spacer" />
                    <button
                      type="button"
                      className="btn btn-outline-secondary"
                      onClick={() => removeProvider(selected._id)}
                      disabled={selected.active}
                      title={
                        selected.active
                          ? "Нельзя удалить активный провайдер"
                          : "Удалить провайдер"
                      }
                    >
                      <i className="bi bi-trash me-1" />
                      Удалить
                    </button>
                  </div>
                  <p className="admin-hint">
                    «Сохранить» применяет весь список; активный провайдер сразу
                    переподключается. Пустой пароль — оставить прежний.
                  </p>
                </div>
              ) : (
                <div className="surface surface-pad dash-empty">
                  Выберите или добавьте провайдера
                </div>
              )}
            </div>
          ) : tab === "roles" ? (
            <form className="surface surface-pad" onSubmit={onSaveRoles}>
              <div className="admin-banner">
                <i className="bi bi-info-circle" />
                <span>
                  Роль пользователя определяется его группами в AD при входе.
                  Совпало несколько правил — берётся старшая роль. Не совпало
                  ничего — роль по умолчанию.
                </span>
              </div>

              <div className="admin-grid" style={{ marginTop: 12 }}>
                <label className="field-stack">
                  <span className="field-label-row">
                    <span className="field-label">Роль по умолчанию</span>
                    <HelpHint text="Назначается любому вошедшему через AD, чьи группы не совпали ни с одним правилом ниже." />
                  </span>
                  <select
                    className="form-select"
                    value={defaultRole}
                    onChange={(e) => setDefaultRole(e.target.value)}
                  >
                    {roleCatalog.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="prov-section-title" style={{ marginTop: 18 }}>
                <i className="bi bi-diagram-3" />
                Группа AD → роль
              </div>

              <div className="bulk-table-wrap">
                <table className="bulk-table">
                  <thead>
                    <tr>
                      <th>Группа AD (имя или DN)</th>
                      <th style={{ width: 220 }}>Роль</th>
                      <th style={{ width: 60 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {rules.length ? (
                      rules.map((r) => (
                        <tr key={r._id}>
                          <td>
                            <input
                              value={r.group}
                              placeholder="MID-Admins  ·  CN=Helpdesk,OU=…,DC=…"
                              onChange={(e) =>
                                setRule(r._id, "group", e.target.value)
                              }
                            />
                          </td>
                          <td>
                            <select
                              className="form-select"
                              value={r.role}
                              onChange={(e) =>
                                setRule(r._id, "role", e.target.value)
                              }
                            >
                              {roleCatalog.map((rc) => (
                                <option key={rc.value} value={rc.value}>
                                  {rc.label}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td>
                            <button
                              type="button"
                              className="bulk-row-del"
                              title="Удалить правило"
                              onClick={() => removeRule(r._id)}
                            >
                              <i className="bi bi-trash" />
                            </button>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={3} className="result-muted">
                          Правил нет — все входящие через AD получают роль по
                          умолчанию «{roleName(defaultRole)}».
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              <div className="bulk-toolbar" style={{ marginTop: 12 }}>
                <button
                  type="button"
                  className="btn btn-outline-secondary btn-sm"
                  onClick={addRule}
                >
                  <i className="bi bi-plus-lg me-1" />
                  Добавить правило
                </button>
              </div>

              <button
                className="btn btn-primary"
                type="submit"
                disabled={rolesSaving}
                style={{ marginTop: 12 }}
              >
                <i className="bi bi-save me-1" />
                {rolesSaving ? "Сохраняю..." : "Сохранить права"}
              </button>
              <p className="admin-hint">
                Уровни: <b>Администратор</b> — всё, включая настройки;{" "}
                <b>Оператор</b> — правка пользователей AD и журналы;{" "}
                <b>Просмотр</b> — только чтение. Изменения вступают в силу при
                следующем входе пользователя.
              </p>

              <div className="prov-section-title" style={{ marginTop: 20 }}>
                <i className="bi bi-search" />
                Проверить роль по логину
              </div>
              <div className="search-toolbar" style={{ marginTop: 0 }}>
                <div
                  className="field-stack"
                  style={{ flex: 1, minWidth: 240 }}
                >
                  <span className="field-label-row">
                    <span className="field-label">Логин пользователя AD</span>
                    <HelpHint text="Покажет группы этого пользователя в AD и какая роль ему достанется по текущим правилам (сохранять не обязательно — проверка идёт по актуальным сохранённым правилам)." />
                  </span>
                  <input
                    className="form-control"
                    value={previewLogin}
                    placeholder="ivanov"
                    onChange={(e) => setPreviewLogin(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") onPreviewRole(e);
                    }}
                  />
                </div>
                <div
                  className="search-toolbar-actions"
                  style={{ alignSelf: "flex-end" }}
                >
                  <button
                    type="button"
                    className="btn btn-outline-secondary"
                    onClick={onPreviewRole}
                    disabled={previewing || !previewLogin.trim()}
                  >
                    <i className="bi bi-person-check me-1" />
                    {previewing ? "Проверяю..." : "Проверить"}
                  </button>
                </div>
              </div>

              {previewResult ? (
                previewResult.error ? (
                  <div className="admin-hint settings-test-result is-err">
                    <i className="bi bi-exclamation-triangle me-1" />
                    {previewResult.error}
                  </div>
                ) : (
                  <div className="role-preview">
                    <div className="role-preview-head">
                      <span className="role-preview-login">
                        {previewResult.login}
                      </span>
                      <span
                        className={`prov-badge is-active role-badge is-${previewResult.role}`}
                      >
                        {previewResult.role_label}
                      </span>
                      {previewResult.used_default ? (
                        <span className="role-preview-note">
                          по умолчанию (ни одно правило не совпало)
                        </span>
                      ) : (
                        <span className="role-preview-note">
                          совпало правил: {previewResult.matched?.length || 0}
                        </span>
                      )}
                    </div>
                    {previewResult.matched?.length ? (
                      <div className="role-preview-matched">
                        {previewResult.matched.map((m, i) => (
                          <span className="role-preview-chip" key={i}>
                            {m.group} → {roleName(m.role)}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    <div className="role-preview-groups">
                      <span className="role-preview-sub">
                        Группы в AD ({previewResult.groups?.length || 0}):
                      </span>
                      {previewResult.groups?.length ? (
                        <div className="role-preview-grouplist">
                          {previewResult.groups.map((g, i) => (
                            <span className="role-preview-group" key={i}>
                              {g}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="result-muted"> нет</span>
                      )}
                    </div>
                  </div>
                )
              ) : null}
            </form>
          ) : tab === "attributes" ? (
            <form className="surface surface-pad" onSubmit={onSaveAttrs}>
              <div className="admin-banner">
                <i className="bi bi-info-circle" />
                <span>
                  Какие атрибуты AD подтягивать в профиль пользователя после
                  входа. «Основной» — что показывать как имя (в шапке/сайдбаре).
                  Читаются правами самого пользователя при входе.
                </span>
              </div>

              <div className="bulk-table-wrap" style={{ marginTop: 12 }}>
                <table className="bulk-table">
                  <thead>
                    <tr>
                      <th>Атрибут AD</th>
                      <th>Подпись в профиле</th>
                      <th style={{ width: 90, textAlign: "center" }}>Основной</th>
                      <th style={{ width: 48 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {attrRows.map((r) => (
                      <tr key={r._id}>
                        <td>
                          <input
                            value={r.attr}
                            placeholder="displayName"
                            onChange={(e) => setAttr(r._id, "attr", e.target.value)}
                          />
                        </td>
                        <td>
                          <input
                            value={r.label}
                            placeholder="ФИО"
                            onChange={(e) => setAttr(r._id, "label", e.target.value)}
                          />
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <input
                            type="radio"
                            name="attr-primary"
                            checked={r.primary}
                            onChange={() => setAttrPrimary(r._id)}
                          />
                        </td>
                        <td>
                          <button
                            type="button"
                            className="bulk-row-del"
                            title="Удалить"
                            onClick={() => removeAttr(r._id)}
                          >
                            <i className="bi bi-trash" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="bulk-toolbar" style={{ marginTop: 12 }}>
                <button
                  type="button"
                  className="btn btn-outline-secondary btn-sm"
                  onClick={addAttr}
                >
                  <i className="bi bi-plus-lg me-1" />
                  Добавить атрибут
                </button>
              </div>

              <button
                className="btn btn-primary"
                type="submit"
                disabled={attrSaving}
                style={{ marginTop: 12 }}
              >
                <i className="bi bi-save me-1" />
                {attrSaving ? "Сохраняю..." : "Сохранить маппинг"}
              </button>
              <p className="admin-hint">
                Примеры атрибутов AD: displayName, mail, title, department,
                telephoneNumber, company, physicalDeliveryOfficeName. Изменения
                применяются при следующем входе пользователя.
              </p>
            </form>
          ) : (
            <form className="surface surface-pad" onSubmit={onSaveCollectors}>
              <div className="admin-banner">
                <i className="bi bi-info-circle" />
                <span>
                  Поиск идёт по всем включённым коллекторам. Пустой пароль —
                  оставить прежний.
                </span>
              </div>

              <div className="bulk-table-wrap" style={{ marginTop: 12 }}>
                <table className="bulk-table">
                  <thead>
                    <tr>
                      <th>Имя</th>
                      <th>Host / IP *</th>
                      <th style={{ width: 90 }}>Порт</th>
                      <th>База</th>
                      <th>Пользователь</th>
                      <th>Пароль</th>
                      <th style={{ width: 70 }}>Вкл</th>
                      <th style={{ width: 130 }}>Статус</th>
                      <th style={{ width: 150 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {collectors.map((row) => {
                      const st = collectorStatusFor(row);
                      return (
                        <tr key={row._id}>
                          <td>
                            <input
                              value={row.name}
                              placeholder="av-sv-event"
                              onChange={(e) => setCol(row._id, "name", e.target.value)}
                            />
                          </td>
                          <td>
                            <input
                              value={row.host}
                              placeholder="192.168.31.225"
                              onChange={(e) => setCol(row._id, "host", e.target.value)}
                            />
                          </td>
                          <td>
                            <input
                              type="number"
                              value={row.port}
                              placeholder="5432"
                              onChange={(e) => setCol(row._id, "port", e.target.value)}
                            />
                          </td>
                          <td>
                            <input
                              value={row.database}
                              placeholder="logs"
                              onChange={(e) =>
                                setCol(row._id, "database", e.target.value)
                              }
                            />
                          </td>
                          <td>
                            <input
                              value={row.user}
                              placeholder="postgres"
                              onChange={(e) => setCol(row._id, "user", e.target.value)}
                            />
                          </td>
                          <td>
                            <input
                              type="password"
                              autoComplete="new-password"
                              value={row.password}
                              placeholder={
                                row.password_set ? "•••• (без изм.)" : "пароль"
                              }
                              onChange={(e) =>
                                setCol(row._id, "password", e.target.value)
                              }
                            />
                          </td>
                          <td style={{ textAlign: "center" }}>
                            <input
                              type="checkbox"
                              checked={row.enabled}
                              onChange={(e) =>
                                setCol(row._id, "enabled", e.target.checked)
                              }
                            />
                          </td>
                          <td>
                            {st ? (
                              <span
                                className={`result-status ${
                                  st.connected ? "is-ok" : "is-err"
                                }`}
                                title={st.error || ""}
                              >
                                <i
                                  className={`bi ${
                                    st.connected
                                      ? "bi-check-circle-fill"
                                      : "bi-x-circle-fill"
                                  }`}
                                />
                                {st.connected ? "на связи" : "нет связи"}
                              </span>
                            ) : (
                              <span className="result-muted">—</span>
                            )}
                          </td>
                          <td>
                            <div className="cell-with-btn">
                              <button
                                type="button"
                                className="btn btn-outline-secondary btn-sm"
                                onClick={() => onTestCollector(row)}
                                disabled={testingHost === row._id}
                              >
                                <i className="bi bi-plug me-1" />
                                {testingHost === row._id ? "…" : "Тест"}
                              </button>
                              <button
                                type="button"
                                className="bulk-row-del"
                                title="Удалить"
                                onClick={() => removeCollector(row._id)}
                              >
                                <i className="bi bi-trash" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="bulk-toolbar" style={{ marginTop: 12 }}>
                <button
                  type="button"
                  className="btn btn-outline-secondary btn-sm"
                  onClick={addCollector}
                >
                  <i className="bi bi-plus-lg me-1" />
                  Добавить коллектор
                </button>
                <span className="status-pill">
                  <i className="bi bi-hdd-stack" />
                  Всего: {collectors.length}
                </span>
              </div>

              <button
                className="btn btn-primary"
                type="submit"
                disabled={collectorsSaving}
                style={{ marginTop: 12 }}
              >
                <i className="bi bi-save me-1" />
                {collectorsSaving ? "Сохраняю..." : "Сохранить и переподключить"}
              </button>
            </form>
          )}
        </>
      )}
    </>
  );
}
