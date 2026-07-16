import { useEffect, useMemo, useState } from "react";

function renderDirectoryValue(value, fallback) {
  const text = String(value ?? "").trim();
  if (!text) {
    return <div className="user-ad-meta-value is-muted">{fallback}</div>;
  }
  return <div className="user-ad-meta-value">{text}</div>;
}

export default function GroupModal({ open, login, details, loading, onClose }) {
  const [filter, setFilter] = useState("");

  useEffect(() => {
    if (open) setFilter("");
  }, [open, login]);

  useEffect(() => {
    if (!open) return undefined;
    function handleKey(event) {
      if (event.key === "Escape") onClose?.();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  const groups = Array.isArray(details?.groups) ? details.groups : [];
  const filteredGroups = useMemo(() => {
    const query = filter.trim().toLowerCase();
    if (!query) return groups;
    return groups.filter((group) => String(group).toLowerCase().includes(query));
  }, [groups, filter]);

  if (!open) return null;

  const displayName = String(details?.displayName ?? "").trim();
  const container = details?.container || {};
  const containerName = String(container.name ?? "").trim();
  const containerType = String(container.type ?? "").trim();
  const containerDescription = String(container.description ?? "").trim();
  const containerDn = String(container.dn ?? "").trim();
  const containerTitle = [containerType, containerName].filter(Boolean).join(": ");

  return (
    <div className="modal-shell" onClick={onClose}>
      <div
        className="modal-dialog-react"
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header-react">
          <div>
            <div className="modal-overline">Профиль из Active Directory</div>
            <div className="modal-title-react">{login}</div>
            <div className="modal-sub">
              {loading
                ? "Загружаю данные пользователя..."
                : `Найдено групп: ${groups.length}${displayName ? ` · ${displayName}` : ""}`}
            </div>
          </div>
          <button
            type="button"
            className="modal-close"
            aria-label="Закрыть"
            onClick={onClose}
          >
            <i className="bi bi-x-lg" />
          </button>
        </div>

        <div className="modal-body-react">
          <div className="user-ad-meta">
            <div className="user-ad-meta-card">
              <div className="user-ad-meta-label">Полное ФИО</div>
              {renderDirectoryValue(displayName, "DisplayName пустой")}
            </div>
            <div className="user-ad-meta-card">
              <div className="user-ad-meta-label">OU / контейнер</div>
              {renderDirectoryValue(containerTitle || containerName, "Контейнер не найден")}
              {containerDn ? <code>{containerDn}</code> : null}
            </div>
            <div className="user-ad-meta-card">
              <div className="user-ad-meta-label">Описание OU</div>
              {renderDirectoryValue(containerDescription, "Описание не заполнено")}
            </div>
          </div>

          <div className="surface-head" style={{ marginTop: 24, marginBottom: 12 }}>
            <div>
              <div className="eyebrow">Группы</div>
              <div className="section-title" style={{ fontSize: "1.05rem" }}>
                Рекурсивный список групп пользователя
              </div>
            </div>
            <div style={{ flex: 1, maxWidth: 280 }}>
              <div className="field-control-wrap">
                <i className="bi bi-search field-icon" aria-hidden="true" />
                <input
                  type="search"
                  className="form-control"
                  placeholder="Фильтр по имени группы"
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                  disabled={loading || !groups.length}
                />
              </div>
            </div>
          </div>

          <div className="groups-container groups-container-modern">
            {loading ? (
              <span className="result-muted">
                <span className="loading-spinner" style={{ display: "inline-block", marginRight: 8, verticalAlign: "middle" }} />
                Подождите...
              </span>
            ) : filteredGroups.length > 0 ? (
              filteredGroups.map((group) => (
                <span key={group} className="group-badge">
                  {group}
                </span>
              ))
            ) : groups.length === 0 ? (
              <span className="result-muted">Группы не найдены</span>
            ) : (
              <span className="result-muted">Нет групп, соответствующих фильтру</span>
            )}
          </div>
        </div>

        <div className="modal-footer-react">
          <button type="button" className="btn btn-outline-secondary" onClick={onClose}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
