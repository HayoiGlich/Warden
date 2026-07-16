import { useEffect, useRef, useState } from "react";
import PageHero from "../components/PageHero";
import ToastStack from "../components/ToastStack";
import { getServices, updateServices } from "../api";
import { hasPerm } from "../lib/perms";

let seq = 0;
function blankLink() {
  seq += 1;
  return {
    _id: `svc-${seq}`,
    id: "",
    title: "",
    url: "",
    description: "",
    icon: "bi-box-arrow-up-right",
    category: "",
  };
}

function toRows(list) {
  return (Array.isArray(list) ? list : []).map((s) => {
    seq += 1;
    return {
      _id: `svc-${seq}`,
      id: s.id || "",
      title: s.title ?? "",
      url: s.url ?? "",
      description: s.description ?? "",
      icon: s.icon || "bi-box-arrow-up-right",
      category: s.category ?? "",
    };
  });
}

function safeUrl(url) {
  const u = String(url || "").trim();
  if (!u) return "#";
  return /^https?:\/\//i.test(u) ? u : `https://${u}`;
}

export default function ServicesPage({ user }) {
  const canEdit = hasPerm(user, "settings");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toasts, setToasts] = useState([]);
  // Не обновлять список во время правки (чтобы не затереть несохранённое)
  // и держать «сырое» значение с сервера для сравнения при опросе.
  const editingRef = useRef(false);
  const rawRef = useRef("[]");
  editingRef.current = editing;

  function pushToast(message, type = "info", title = "") {
    const id = `${Date.now()}-${Math.random()}`;
    const icon =
      type === "success"
        ? "bi-check-circle"
        : type === "danger"
          ? "bi-exclamation-triangle"
          : "bi-info-circle";
    setToasts((c) => [...c, { id, message, type, title, icon }]);
    window.setTimeout(() => setToasts((c) => c.filter((t) => t.id !== id)), 4200);
  }

  useEffect(() => {
    let cancelled = false;

    // Тихое обновление: подтягиваем чужие изменения, но не мешаем правке
    // и перерисовываем, только если список реально изменился.
    async function refresh({ silent = true } = {}) {
      if (editingRef.current) return;
      try {
        const data = await getServices();
        if (cancelled) return;
        const list = Array.isArray(data?.services) ? data.services : [];
        const sig = JSON.stringify(list);
        if (sig !== rawRef.current) {
          rawRef.current = sig;
          setRows(toRows(list));
        }
      } catch (err) {
        if (!silent && !cancelled) {
          pushToast(err.message || String(err), "danger", "Сервисы");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    refresh({ silent: false });

    // Периодический опрос — живая синхронизация между пользователями.
    const timer = window.setInterval(() => refresh(), 20000);
    // Мгновенно, когда пользователь возвращается на вкладку.
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  function setField(id, key, value) {
    setRows((c) => c.map((r) => (r._id === id ? { ...r, [key]: value } : r)));
  }
  function addRow() {
    setRows((c) => [...c, blankLink()]);
  }
  function removeRow(id) {
    setRows((c) => c.filter((r) => r._id !== id));
  }

  async function onSave() {
    setSaving(true);
    try {
      const payload = rows
        .filter((r) => String(r.title).trim() && String(r.url).trim())
        .map((r) => ({
          id: r.id || undefined,
          title: r.title.trim(),
          url: r.url.trim(),
          description: r.description.trim(),
          icon: r.icon.trim() || "bi-box-arrow-up-right",
          category: r.category.trim(),
        }));
      const data = await updateServices(payload);
      const list = Array.isArray(data?.services) ? data.services : [];
      rawRef.current = JSON.stringify(list);
      setRows(toRows(list));
      setEditing(false);
      pushToast("Сохранено — видно всем пользователям", "success", "Сервисы");
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Сервисы");
    } finally {
      setSaving(false);
    }
  }

  const visible = rows.filter((r) => String(r.title).trim() && String(r.url).trim());

  return (
    <>
      <ToastStack
        items={toasts}
        onDismiss={(id) => setToasts((c) => c.filter((t) => t.id !== id))}
      />
      <PageHero
        icon="bi-grid-3x3-gap"
        title="Сервисы"
        subtitle="Все ссылки на внутренние сервисы в одном месте."
        eyebrow="Быстрый доступ"
        actions={
          canEdit ? (
            <button
              type="button"
              className={`btn ${editing ? "btn-outline-secondary" : "btn-primary"}`}
              onClick={() => setEditing((v) => !v)}
            >
              <i className={`bi ${editing ? "bi-eye" : "bi-pencil"} me-1`} />
              {editing ? "Просмотр" : "Управление"}
            </button>
          ) : null
        }
      />

      {loading ? (
        <div className="admin-hint">Загрузка...</div>
      ) : editing ? (
        <div className="surface surface-pad">
          <div className="bulk-table-wrap">
            <table className="bulk-table">
              <thead>
                <tr>
                  <th style={{ width: 150 }}>Иконка</th>
                  <th>Название *</th>
                  <th>URL *</th>
                  <th>Описание</th>
                  <th>Категория</th>
                  <th style={{ width: 48 }} />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r._id}>
                    <td>
                      <div className="svc-icon-cell">
                        <i className={`bi ${r.icon || "bi-link-45deg"}`} />
                        <input
                          value={r.icon}
                          placeholder="bi-graph-up"
                          onChange={(e) => setField(r._id, "icon", e.target.value)}
                        />
                      </div>
                    </td>
                    <td>
                      <input
                        value={r.title}
                        placeholder="Мониторинг"
                        onChange={(e) => setField(r._id, "title", e.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        value={r.url}
                        placeholder="https://grafana.local"
                        onChange={(e) => setField(r._id, "url", e.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        value={r.description}
                        placeholder="Дашборды и метрики"
                        onChange={(e) =>
                          setField(r._id, "description", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        value={r.category}
                        placeholder="Инфраструктура"
                        onChange={(e) => setField(r._id, "category", e.target.value)}
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="bulk-row-del"
                        title="Удалить"
                        onClick={() => removeRow(r._id)}
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
              onClick={addRow}
            >
              <i className="bi bi-plus-lg me-1" />
              Добавить сервис
            </button>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            onClick={onSave}
            disabled={saving}
            style={{ marginTop: 12 }}
          >
            <i className="bi bi-save me-1" />
            {saving ? "Сохраняю..." : "Сохранить"}
          </button>
          <p className="admin-hint">
            Иконка — класс из Bootstrap Icons (напр. bi-graph-up, bi-hdd-rack,
            bi-camera-video). Пустые строки не сохраняются.
          </p>
        </div>
      ) : visible.length ? (
        <div className="svc-grid">
          {visible.map((s) => (
            <a
              key={s._id}
              className="svc-card"
              href={safeUrl(s.url)}
              target="_blank"
              rel="noreferrer noopener"
            >
              <span className="svc-card-icon">
                <i className={`bi ${s.icon || "bi-box-arrow-up-right"}`} />
              </span>
              <span className="svc-card-body">
                <span className="svc-card-title">{s.title}</span>
                {s.description ? (
                  <span className="svc-card-desc">{s.description}</span>
                ) : null}
                {s.category ? (
                  <span className="svc-card-cat">{s.category}</span>
                ) : null}
              </span>
              <i className="bi bi-arrow-up-right svc-card-go" />
            </a>
          ))}
        </div>
      ) : (
        <div className="surface surface-pad dash-empty">
          {canEdit
            ? "Пока нет ни одного сервиса. Нажмите «Управление», чтобы добавить."
            : "Список сервисов пуст."}
        </div>
      )}
    </>
  );
}
