import { useEffect, useState } from "react";
import {
  getGroupTemplates,
  createGroupTemplate,
  updateGroupTemplate,
  deleteGroupTemplate,
} from "../api";

function mergeGroups(current, incoming) {
  const seen = new Set(current.map((g) => String(g.name || "").toLowerCase()));
  const merged = [...current];
  (incoming || []).forEach((g) => {
    const key = String(g.name || "").toLowerCase();
    if (key && !seen.has(key)) {
      seen.add(key);
      merged.push({ name: g.name, dn: g.dn || "" });
    }
  });
  return merged;
}

function byName(a, b) {
  return String(a.name).localeCompare(String(b.name), "ru");
}

/**
 * Шаблоны быстрого назначения групп (свои у каждого пользователя).
 * value — текущие группы [{name,dn}]; onChange — установить группы.
 */
export default function GroupTemplates({ value, onChange, pushToast }) {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState("");
  const [manage, setManage] = useState(false);
  const [busy, setBusy] = useState(false);
  const [renameId, setRenameId] = useState(0);
  const [renameVal, setRenameVal] = useState("");

  const notify = (m, t, title) => pushToast?.(m, t, title || "Шаблоны");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getGroupTemplates();
        if (!cancelled) setTemplates((data?.templates || []).slice().sort(byName));
      } catch (err) {
        if (!cancelled) notify(err.message || String(err), "danger");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const currentPayload = () =>
    value.map((g) => ({ name: g.name, dn: g.dn || "" }));

  function apply(t) {
    onChange(mergeGroups(value, t.groups || []));
    notify(`Применён «${t.name}» (+${(t.groups || []).length} гр.)`, "info");
  }

  async function saveNew() {
    const name = newName.trim();
    if (!name || !value.length) return;
    setBusy(true);
    try {
      const data = await createGroupTemplate(name, currentPayload());
      setTemplates((cur) => [...cur, data.template].sort(byName));
      setNewName("");
      notify(`Шаблон «${name}» сохранён`, "success");
    } catch (err) {
      notify(err.message || String(err), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function overwrite(t) {
    setBusy(true);
    try {
      const data = await updateGroupTemplate(t.id, t.name, currentPayload());
      setTemplates((cur) =>
        cur.map((x) => (x.id === t.id ? data.template : x)).sort(byName)
      );
      notify(`«${t.name}»: сохранены текущие ${value.length} гр.`, "success");
    } catch (err) {
      notify(err.message || String(err), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function saveRename(t) {
    const name = renameVal.trim();
    if (!name) return;
    setBusy(true);
    try {
      const data = await updateGroupTemplate(t.id, name, t.groups || []);
      setTemplates((cur) =>
        cur.map((x) => (x.id === t.id ? data.template : x)).sort(byName)
      );
      setRenameId(0);
      setRenameVal("");
    } catch (err) {
      notify(err.message || String(err), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove(t) {
    setBusy(true);
    try {
      await deleteGroupTemplate(t.id);
      setTemplates((cur) => cur.filter((x) => x.id !== t.id));
      notify(`«${t.name}» удалён`, "info");
    } catch (err) {
      notify(err.message || String(err), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="tpl-box">
      <div className="tpl-head">
        <span className="tpl-head-title">
          <i className="bi bi-bookmark-star me-1" />
          Шаблоны групп
        </span>
        <button
          type="button"
          className="tpl-manage-toggle"
          onClick={() => setManage((v) => !v)}
        >
          <i className={`bi ${manage ? "bi-x-lg" : "bi-gear"} me-1`} />
          {manage ? "Готово" : "Управление"}
        </button>
      </div>

      {loading ? (
        <div className="tpl-empty">Загрузка...</div>
      ) : (
        <>
          <div className="tpl-chips">
            {templates.length ? (
              templates.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className="tpl-chip"
                  onClick={() => apply(t)}
                  title={`Применить: ${(t.groups || [])
                    .map((g) => g.name)
                    .join(", ") || "нет групп"}`}
                >
                  <i className="bi bi-collection" />
                  {t.name}
                  <span className="tpl-chip-n">{(t.groups || []).length}</span>
                </button>
              ))
            ) : (
              <span className="tpl-empty">
                Пока нет шаблонов — соберите группы ниже и сохраните.
              </span>
            )}
          </div>

          <div className="tpl-new">
            <input
              className="form-control"
              placeholder="Название нового шаблона"
              value={newName}
              maxLength={150}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  saveNew();
                }
              }}
            />
            <button
              type="button"
              className="btn btn-outline-secondary btn-sm"
              onClick={saveNew}
              disabled={busy || !newName.trim() || !value.length}
              title={
                value.length
                  ? "Сохранить текущий набор групп как шаблон"
                  : "Сначала выберите группы"
              }
            >
              <i className="bi bi-plus-lg me-1" />
              Сохранить текущие ({value.length})
            </button>
          </div>

          {manage && templates.length ? (
            <div className="tpl-manage-list">
              {templates.map((t) => (
                <div key={t.id} className="tpl-manage-row">
                  {renameId === t.id ? (
                    <input
                      className="form-control tpl-rename-input"
                      value={renameVal}
                      autoFocus
                      onChange={(e) => setRenameVal(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          saveRename(t);
                        }
                        if (e.key === "Escape") setRenameId(0);
                      }}
                      onBlur={() => saveRename(t)}
                    />
                  ) : (
                    <span className="tpl-manage-name">
                      {t.name}
                      <span className="tpl-manage-count">
                        {(t.groups || []).length} гр.
                      </span>
                    </span>
                  )}
                  <div className="tpl-manage-actions">
                    <button
                      type="button"
                      className="btn btn-outline-secondary btn-sm"
                      onClick={() => apply(t)}
                    >
                      Применить
                    </button>
                    <button
                      type="button"
                      className="btn btn-outline-secondary btn-sm"
                      onClick={() => overwrite(t)}
                      disabled={busy}
                      title="Заменить группы шаблона текущим набором из формы"
                    >
                      Обновить текущими
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      title="Переименовать"
                      onClick={() => {
                        setRenameId(t.id);
                        setRenameVal(t.name);
                      }}
                    >
                      <i className="bi bi-pencil" />
                    </button>
                    <button
                      type="button"
                      className="bulk-row-del"
                      title="Удалить"
                      onClick={() => remove(t)}
                      disabled={busy}
                    >
                      <i className="bi bi-trash" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
