export default function PageHero({
  icon,
  title,
  subtitle,
  eyebrow = "MID",
  chips = [],
  actions = null
}) {
  return (
    <section className="page-hero">
      <div className="page-hero-grid">
        <div className="page-hero-row">
          <div className="page-hero-icon" aria-hidden="true">
            <i className={`bi ${icon}`} />
          </div>
          <div className="page-hero-copy">
            <span className="page-hero-eyebrow">{eyebrow}</span>
            <h1 className="page-hero-title">{title}</h1>
            {subtitle ? <div className="page-hero-subtitle">{subtitle}</div> : null}
            {chips.length ? (
              <div className="page-hero-chips">
                {chips.map((chip, idx) => {
                  if (!chip) return null;
                  const isObject = typeof chip === "object";
                  const text = isObject ? chip.label : chip;
                  const tone = isObject ? chip.tone : null;
                  const icon = isObject ? chip.icon : null;
                  const onClick = isObject ? chip.onClick : null;
                  const title = isObject ? chip.title : null;
                  const disabled = isObject ? chip.disabled : false;
                  const className = `chip${tone ? ` is-${tone}` : ""}${onClick ? " is-clickable" : ""}`;
                  if (onClick) {
                    return (
                      <button
                        key={`${text}-${idx}`}
                        type="button"
                        className={className}
                        onClick={onClick}
                        title={title || ""}
                        disabled={disabled}
                      >
                        {icon ? <i className={`bi ${icon}`} /> : null}
                        {text}
                      </button>
                    );
                  }
                  return (
                    <span key={`${text}-${idx}`} className={className} title={title || ""}>
                      {icon ? <i className={`bi ${icon}`} /> : null}
                      {text}
                    </span>
                  );
                })}
              </div>
            ) : null}
          </div>
        </div>

        {actions ? <div className="page-hero-actions">{actions}</div> : null}
      </div>
    </section>
  );
}
