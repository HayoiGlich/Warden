export default function ToastStack({ items, onDismiss }) {
  if (!items?.length) return null;

  return (
    <div className="toast-stack" aria-live="polite">
      {items.map((toast) => (
        <div key={toast.id} className={`toast is-${toast.type || "info"}`} role="status">
          <div className="toast-icon" aria-hidden="true">
            <i className={`bi ${toast.icon || "bi-info-circle"}`} />
          </div>
          <div className="toast-body">
            {toast.title ? <div className="toast-title">{toast.title}</div> : null}
            <div className="toast-message">{toast.message}</div>
          </div>
          <button
            type="button"
            className="toast-close"
            aria-label="Закрыть уведомление"
            onClick={() => onDismiss(toast.id)}
          >
            <i className="bi bi-x-lg" />
          </button>
        </div>
      ))}
    </div>
  );
}
