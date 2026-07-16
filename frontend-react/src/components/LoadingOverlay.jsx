export default function LoadingOverlay({ visible, text = "Подождите" }) {
  if (!visible) return null;

  return (
    <div className="loading-overlay-react" aria-hidden={!visible}>
      <div className="loading-card">
        <span className="loading-spinner" role="status" aria-label="Загрузка" />
        <div className="loading-text">
          <span className="loading-title">Загрузка</span>
          <span className="loading-sub">{text}</span>
        </div>
      </div>
    </div>
  );
}
