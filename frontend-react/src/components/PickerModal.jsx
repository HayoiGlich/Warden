import { useEffect, useMemo, useState } from "react";

/**
 * Универсальное модальное окно выбора из списка с галочками и поиском.
 * Используется для выбора групп (multi) и OU (single).
 *
 * props:
 *  - open, title, subtitle, loading
 *  - items: массив объектов
 *  - keyOf(item) -> уникальный ключ (dn/name)
 *  - labelOf(item) -> основная подпись
 *  - subOf(item) -> вторичная подпись (dn/описание)
 *  - selectedKeys: массив ключей уже выбранных
 *  - multi: множественный выбор (по умолчанию true)
 *  - onClose(), onConfirm(chosenItems)
 */
export default function PickerModal({
  open,
  title,
  subtitle = "",
  loading = false,
  items = [],
  keyOf,
  labelOf,
  subOf,
  selectedKeys = [],
  multi = true,
  searchPlaceholder = "Поиск по названию…",
  confirmLabel = "Применить",
  onClose,
  onConfirm,
}) {
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState(() => new Set());

  useEffect(() => {
    if (open) {
      setSelected(new Set(selectedKeys || []));
      setFilter("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === "Escape") onClose?.();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) => {
      const label = String(labelOf(it) || "").toLowerCase();
      const sub = String((subOf && subOf(it)) || "").toLowerCase();
      return label.includes(q) || sub.includes(q);
    });
  }, [items, filter, labelOf, subOf]);

  const MAX_RENDER = 5000;
  const visible = filtered.slice(0, MAX_RENDER);
  const hiddenCount = filtered.length - visible.length;

  if (!open) return null;

  function toggle(key) {
    setSelected((cur) => {
      if (!multi) {
        const next = new Set();
        if (!cur.has(key)) next.add(key);
        return next;
      }
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function selectAllVisible() {
    setSelected((cur) => {
      const next = new Set(cur);
      filtered.forEach((it) => next.add(keyOf(it)));
      return next;
    });
  }

  function clearAll() {
    setSelected(new Set());
  }

  function confirm() {
    const chosen = items.filter((it) => selected.has(keyOf(it)));
    onConfirm?.(chosen, selected);
  }

  return (
    <div className="modal-shell" onClick={onClose}>
      <div
        className="modal-dialog-react picker-modal"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header-react">
          <div>
            <div className="modal-overline">{multi ? "Множественный выбор" : "Выбор одного значения"}</div>
            <div className="modal-title-react">{title}</div>
            <div className="modal-sub">
              {loading
                ? "Загружаю список…"
                : subtitle || `Доступно: ${items.length} · выбрано: ${selected.size}`}
            </div>
          </div>
          <button type="button" className="modal-close" aria-label="Закрыть" onClick={onClose}>
            <i className="bi bi-x-lg" />
          </button>
        </div>

        <div className="modal-body-react">
          <div className="picker-toolbar">
            <div className="field-control-wrap" style={{ flex: 1 }}>
              <i className="bi bi-search field-icon" aria-hidden="true" />
              <input
                type="search"
                className="form-control"
                placeholder={searchPlaceholder}
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                autoFocus
              />
            </div>
            {multi ? (
              <div className="picker-toolbar-actions">
                <button type="button" className="btn btn-outline-secondary btn-sm" onClick={selectAllVisible}>
                  <i className="bi bi-check-all me-1" />Выбрать всё
                </button>
                <button type="button" className="btn btn-outline-secondary btn-sm" onClick={clearAll}>
                  <i className="bi bi-eraser me-1" />Сброс
                </button>
              </div>
            ) : null}
          </div>

          <div className="picker-list">
            {loading ? (
              <div className="result-muted" style={{ padding: 16 }}>
                <span className="loading-spinner" style={{ display: "inline-block", marginRight: 8, verticalAlign: "middle" }} />
                Загрузка…
              </div>
            ) : filtered.length === 0 ? (
              <div className="result-muted" style={{ padding: 16 }}>Ничего не найдено</div>
            ) : (
              <>
                {visible.map((it) => {
                  const key = keyOf(it);
                  const checked = selected.has(key);
                  return (
                    <label key={key} className={`picker-item${checked ? " is-checked" : ""}`}>
                      <input
                        type={multi ? "checkbox" : "radio"}
                        checked={checked}
                        onChange={() => toggle(key)}
                      />
                      <span className="picker-item-text">
                        <span className="picker-item-main">{labelOf(it)}</span>
                        {subOf && subOf(it) ? <span className="picker-item-sub">{subOf(it)}</span> : null}
                      </span>
                    </label>
                  );
                })}
                {hiddenCount > 0 ? (
                  <div className="result-muted" style={{ padding: "10px 12px" }}>
                    Показаны первые {MAX_RENDER}. Ещё {hiddenCount} — уточните поиск.
                  </div>
                ) : null}
              </>
            )}
          </div>
        </div>

        <div className="modal-footer-react">
          <span className="status-pill" style={{ marginRight: "auto" }}>
            <i className="bi bi-check2-square" />Выбрано: {selected.size}
          </span>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose}>
            Отмена
          </button>
          <button type="button" className="btn btn-primary" onClick={confirm}>
            <i className="bi bi-check-lg me-1" />{confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
