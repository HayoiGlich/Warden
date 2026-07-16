import { useState } from "react";

/**
 * Дружелюбная подсказка-карточка. Если задан `id`, кнопка «скрыть»
 * запоминает выбор пользователя в localStorage, чтобы не мозолить глаза.
 */
export default function TipCard({
  id,
  icon = "bi-lightbulb",
  title,
  children,
  tone = "primary",
}) {
  const storeKey = id ? `tip-dismissed:${id}` : null;
  const [hidden, setHidden] = useState(() => {
    if (!storeKey) return false;
    try {
      return window.localStorage.getItem(storeKey) === "1";
    } catch {
      return false;
    }
  });

  if (hidden) return null;

  function dismiss() {
    if (storeKey) {
      try {
        window.localStorage.setItem(storeKey, "1");
      } catch {
        /* ignore */
      }
    }
    setHidden(true);
  }

  return (
    <div className={`tip-card is-${tone}`} role="note">
      <span className="tip-card-icon" aria-hidden="true">
        <i className={`bi ${icon}`} />
      </span>
      <div className="tip-card-body">
        {title ? <div className="tip-card-title">{title}</div> : null}
        <div className="tip-card-text">{children}</div>
      </div>
      {storeKey ? (
        <button
          type="button"
          className="tip-card-close"
          onClick={dismiss}
          title="Больше не показывать"
          aria-label="Скрыть подсказку"
        >
          <i className="bi bi-x-lg" />
        </button>
      ) : null}
    </div>
  );
}
