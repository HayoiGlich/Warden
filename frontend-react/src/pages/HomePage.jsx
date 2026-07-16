import { Link } from "react-router-dom";

const services = [
  {
    to: "/winlog",
    icon: "bi-shield-check",
    title: "Анализатор журналов",
    description: "События входа Windows: поиск по пользователю, компьютеру и периоду.",
    bullets: [
      { icon: "bi-person-vcard", label: "AD-подсказки" },
      { icon: "bi-filetype-csv", label: "CSV-экспорт" }
    ],
    tone: "is-primary",
    cta: "Открыть"
  },
  {
    to: "/ad-users",
    icon: "bi-person-badge",
    title: "Пользователи AD",
    description: "Учётные записи, группы и OU — по одной или массово из Excel/CSV.",
    bullets: [
      { icon: "bi-person-gear", label: "Создание и правка" },
      { icon: "bi-people", label: "Массовые операции" }
    ],
    tone: "is-accent",
    cta: "Открыть"
  }
];

const quickTips = [
  { title: "Найдите вход", text: "Введите логин или фамилию — подсказки появятся сами." },
  { title: "Откройте профиль", text: "Клик по пользователю покажет его группы AD." },
  { title: "Управляйте учётками", text: "Правьте по одной или массово из Excel/CSV." }
];

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Доброй ночи";
  if (h < 12) return "Доброе утро";
  if (h < 18) return "Добрый день";
  return "Добрый вечер";
}

function firstName(user) {
  const raw = String(user?.display_name || user?.username || "").trim();
  if (!raw) return "";
  const noDomain = raw.includes("\\") ? raw.split("\\").pop() : raw;
  // ФИО «Фамилия Имя Отчество» -> берём среднее слово как имя, иначе первое.
  const parts = noDomain.split(/\s+/).filter(Boolean);
  if (user?.display_name && parts.length >= 2) return parts[1];
  return parts[0] || noDomain;
}

export default function HomePage({ user }) {
  const name = firstName(user);
  const profile = Array.isArray(user?.profile) ? user.profile : [];
  return (
    <div>
      <section className="home-welcome">
        <span className="home-welcome-kicker">
          <i className="bi bi-stars" />
          MID
        </span>
        <h1 className="home-welcome-title">
          {greeting()}
          {name ? `, ${name}` : ""}! <span className="wave">👋</span>
        </h1>
        <p className="home-welcome-sub">
          Входы Windows и учётные записи Active Directory — в одном месте.
        </p>
      </section>

      {profile.length ? (
        <section className="profile-card">
          <div className="profile-card-head">
            <span className="profile-card-avatar" aria-hidden="true">
              {String(user?.display_name || user?.username || "?")
                .charAt(0)
                .toUpperCase()}
            </span>
            <div>
              <div className="profile-card-name">
                {user?.display_name || user?.username}
              </div>
              <div className="profile-card-login">
                <i className="bi bi-person-badge me-1" />
                {user?.username}
                {user?.role_label ? ` · ${user.role_label}` : ""}
              </div>
            </div>
          </div>
          <div className="profile-card-grid">
            {profile.map((f, i) => (
              <div key={i} className="profile-field">
                <span className="profile-field-label">{f.label}</span>
                <span className="profile-field-value">{f.value}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="quick-tips">
        {quickTips.map((tip, idx) => (
          <div key={tip.title} className="quick-tip">
            <span className="quick-tip-num">{idx + 1}</span>
            <div>
              <div className="quick-tip-title">{tip.title}</div>
              <div className="quick-tip-text">{tip.text}</div>
            </div>
          </div>
        ))}
      </section>

      <section className="home-grid">
        {services.map((service) => (
          <Link key={service.to} to={service.to} className={`home-service ${service.tone}`}>
            <div className="home-service-top">
              <div className="home-service-icon" aria-hidden="true">
                <i className={`bi ${service.icon}`} />
              </div>
              <div className="home-service-arrow" aria-hidden="true">
                <i className="bi bi-arrow-up-right" />
              </div>
            </div>

            <div className="home-service-title">{service.title}</div>
            <div className="home-service-text">{service.description}</div>

            <div className="home-service-tags">
              {service.bullets.map((bullet) => (
                <span key={bullet.label} className="home-service-tag">
                  <i className={`bi ${bullet.icon}`} />
                  {bullet.label}
                </span>
              ))}
            </div>

            <div className="home-service-action">
              {service.cta}
              <i className="bi bi-arrow-right" />
            </div>
          </Link>
        ))}
      </section>
    </div>
  );
}
