/**
 * Подсказки для дружелюбного интерфейса.
 *
 * <HelpHint> — маленький «?» рядом с подписью поля: при наведении/фокусе
 * показывает поясняющий текст. <Tooltip> — обёртка вокруг любого элемента
 * (например, иконочной кнопки), показывающая подсказку тем же способом.
 */

export function HelpHint({ text, side = "top" }) {
  const label = typeof text === "string" ? text : "Подсказка";
  return (
    <span
      className={`help-hint help-hint-${side}`}
      tabIndex={0}
      role="note"
      aria-label={label}
    >
      <i className="bi bi-question-circle" aria-hidden="true" />
      <span className="help-hint-bubble" role="tooltip">
        {text}
      </span>
    </span>
  );
}

export function Tooltip({ text, side = "top", children, className = "" }) {
  return (
    <span className={`tooltip-host tooltip-${side} ${className}`.trim()}>
      {children}
      <span className="tooltip-bubble" role="tooltip">
        {text}
      </span>
    </span>
  );
}

/**
 * Подпись поля со встроенной подсказкой:
 *   <FieldLabel text="Порт" hint="Пусто = авто (636/389)" htmlFor="port" />
 */
export function FieldLabel({ text, hint, htmlFor, required = false, className = "" }) {
  return (
    <span className={`field-label-row ${className}`.trim()}>
      <label className="field-label" htmlFor={htmlFor}>
        {text}
        {required ? <span className="field-req" title="Обязательное поле"> *</span> : null}
      </label>
      {hint ? <HelpHint text={hint} /> : null}
    </span>
  );
}

export default HelpHint;
