import { useEffect, useState } from "react";
import { changePassword, login } from "../api";

function initialAuthError() {
  try {
    const p = new URLSearchParams(window.location.search);
    return p.get("auth_error") || "";
  } catch {
    return "";
  }
}

function passwordScore(pw) {
  let score = 0;
  if (pw.length >= 6) score += 1;
  if (pw.length >= 10) score += 1;
  if (/[A-ZА-Я]/.test(pw) && /[a-zа-я]/.test(pw)) score += 1;
  if (/\d/.test(pw) && /[^A-Za-zА-Яа-я0-9]/.test(pw)) score += 1;
  return Math.min(score, 4);
}
const SCORE_LABEL = ["", "Слабый", "Так себе", "Хороший", "Надёжный"];

export default function LoginPage({ initialStep = "login", onAuthenticated, oidc }) {
  const [step, setStep] = useState(initialStep); // login | change
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState(initialAuthError());
  const [busy, setBusy] = useState(false);

  // Убираем ?auth_error из URL после прочтения, чтобы не залипал.
  useEffect(() => {
    if (initialAuthError()) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  const score = passwordScore(newPw);

  async function submitLogin(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const data = await login(username.trim(), password);
      if (data?.must_change_password) {
        setOldPw(password);
        setPassword("");
        setStep("change");
      } else {
        onAuthenticated();
      }
    } catch (err) {
      setError(err.message || "Ошибка входа");
    } finally {
      setBusy(false);
    }
  }

  async function submitChange(event) {
    event.preventDefault();
    setError("");
    if (newPw.length < 6) {
      setError("Новый пароль — минимум 6 символов");
      return;
    }
    if (newPw !== confirmPw) {
      setError("Пароли не совпадают");
      return;
    }
    setBusy(true);
    try {
      await changePassword(oldPw, newPw);
      onAuthenticated();
    } catch (err) {
      setError(err.message || "Не удалось сменить пароль");
    } finally {
      setBusy(false);
    }
  }

  const isChange = step === "change";

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-hero">
          <div className="auth-brand">
            <span className="auth-brand-mark">M</span>
            <div>
              <div className="auth-brand-name">MID</div>
              <div className="auth-brand-sub">Консоль администратора</div>
            </div>
          </div>
          <div className="auth-step-dots" aria-hidden="true">
            <span className={`auth-step-dot${!isChange ? " is-active" : ""}`} />
            <span className={`auth-step-dot${isChange ? " is-active" : ""}`} />
          </div>
          <div className="auth-hero-title">
            {isChange ? "Почти готово 🔐" : "С возвращением 👋"}
          </div>
          <div className="auth-hero-sub">
            {isChange
              ? "Задайте новый пароль — и сразу к работе."
              : "Локальная или доменная учётная запись."}
          </div>
        </div>

        <div className="auth-body">
          {step === "login" ? (
            <form className="auth-form" onSubmit={submitLogin}>
              <label className="auth-field">
                <span>Логин</span>
                <input
                  type="text"
                  autoFocus
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="admin или доменный логин"
                />
              </label>
              <label className="auth-field">
                <span>Пароль</span>
                <div className="pw-field">
                  <input
                    type={showPw ? "text" : "password"}
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Ваш пароль"
                  />
                  <button
                    type="button"
                    className="pw-toggle"
                    onClick={() => setShowPw((v) => !v)}
                    title={showPw ? "Скрыть пароль" : "Показать пароль"}
                    aria-label={showPw ? "Скрыть пароль" : "Показать пароль"}
                  >
                    <i className={`bi ${showPw ? "bi-eye-slash" : "bi-eye"}`} />
                  </button>
                </div>
              </label>

              {error ? (
                <div className="auth-error">
                  <i className="bi bi-exclamation-triangle me-1" />
                  {error}
                </div>
              ) : null}

              <button className="auth-submit" type="submit" disabled={busy}>
                {busy ? "Проверяю..." : "Войти"}
              </button>

              {oidc ? (
                <>
                  <div className="auth-divider">
                    <span>или</span>
                  </div>
                  <a className="auth-sso" href="/api/auth/oidc/login">
                    <i className="bi bi-shield-lock me-2" />
                    {oidc.label}
                  </a>
                </>
              ) : null}
            </form>
          ) : (
            <form className="auth-form" onSubmit={submitChange}>
              <label className="auth-field">
                <span>Текущий пароль</span>
                <input
                  type="password"
                  autoComplete="current-password"
                  value={oldPw}
                  onChange={(e) => setOldPw(e.target.value)}
                />
              </label>
              <label className="auth-field">
                <span>Новый пароль</span>
                <div className="pw-field">
                  <input
                    type={showPw ? "text" : "password"}
                    autoFocus
                    autoComplete="new-password"
                    value={newPw}
                    onChange={(e) => setNewPw(e.target.value)}
                    placeholder="Минимум 6 символов"
                  />
                  <button
                    type="button"
                    className="pw-toggle"
                    onClick={() => setShowPw((v) => !v)}
                    title={showPw ? "Скрыть пароль" : "Показать пароль"}
                    aria-label={showPw ? "Скрыть пароль" : "Показать пароль"}
                  >
                    <i className={`bi ${showPw ? "bi-eye-slash" : "bi-eye"}`} />
                  </button>
                </div>
                {newPw ? (
                  <div className={`pw-meter is-${score}`}>
                    <div className="pw-meter-track">
                      <span className="pw-meter-seg" />
                      <span className="pw-meter-seg" />
                      <span className="pw-meter-seg" />
                      <span className="pw-meter-seg" />
                    </div>
                    <span className="pw-meter-label">
                      Надёжность: {SCORE_LABEL[score] || "—"}
                    </span>
                  </div>
                ) : null}
              </label>
              <label className="auth-field">
                <span>Повторите новый пароль</span>
                <input
                  type={showPw ? "text" : "password"}
                  autoComplete="new-password"
                  value={confirmPw}
                  onChange={(e) => setConfirmPw(e.target.value)}
                  placeholder="Ещё раз тот же пароль"
                />
              </label>

              {error ? (
                <div className="auth-error">
                  <i className="bi bi-exclamation-triangle me-1" />
                  {error}
                </div>
              ) : null}

              <button className="auth-submit" type="submit" disabled={busy}>
                {busy ? "Сохраняю..." : "Сохранить и войти"}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
