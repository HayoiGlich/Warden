import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import {
  getAdGroups,
  getCollectors,
  getSystemInfo,
  reconnectAd,
  searchAdUsers,
  searchEvents,
} from "../api";
import GroupModal from "../components/GroupModal";
import LoadingOverlay from "../components/LoadingOverlay";
import PageHero from "../components/PageHero";
import ToastStack from "../components/ToastStack";
import { HelpHint } from "../components/HelpHint";

const PAGE_SIZE = 50;
const PERIODS = [
  { value: "1d", label: "Последние 24 часа" },
  { value: "7d", label: "Последние 7 дней" },
  { value: "30d", label: "Последние 30 дней" },
  { value: "60d", label: "Последние 60 дней" }
];

function emptyAdDetails(username = "") {
  return {
    success: false,
    username,
    displayName: "",
    container: { name: "", type: "", dn: "", description: "" },
    groups: []
  };
}

function escapeCsv(value) {
  return String(value ?? "").replace(/"/g, '""').replace(/\n/g, " ");
}

function canQueryAdSuggestions(value) {
  return String(value ?? "").trim().length >= 2;
}

function getLogonTypeDescription(type) {
  const map = {
    0: "Система",
    2: "Интерактивный локальный вход",
    3: "Сетевой вход",
    4: "Пакетный вход",
    5: "Вход службы",
    7: "Разблокировка рабочего места",
    8: "Сетевой вход с открытым текстом",
    9: "Новые учетные данные",
    10: "Удаленный интерактивный вход по RDP",
    11: "Кэшированный интерактивный вход"
  };
  const numeric = Number(type);
  return map[numeric] || `Тип ${type || "неизвестен"}`;
}

function getLogonTypeBadge(type) {
  const value = String(type ?? "");
  if (value === "10") return { tone: "is-blue", icon: "bi-display", label: "RDP" };
  if (value === "3" || value === "8") return { tone: "is-ink", icon: "bi-diagram-3", label: "Сеть" };
  if (value === "2" || value === "7") return { tone: "is-green", icon: "bi-person-check", label: "Локально" };
  if (value === "4" || value === "5") return { tone: "is-sand", icon: "bi-gear", label: "Служба" };
  if (value === "0") return { tone: "is-dark", icon: "bi-cpu", label: "Система" };
  return { tone: "is-neutral", icon: "bi-question-circle", label: "Другое" };
}

function getEventState(eventId) {
  const value = Number(eventId);
  if (value === 4624) return { tone: "is-success", label: "Успешный вход" };
  if (value === 4625) return { tone: "is-danger", label: "Ошибка входа" };
  return { tone: "is-neutral", label: "Системное событие" };
}

function parseToDate(value) {
  if (!value) return null;
  const direct = new Date(value);
  if (!Number.isNaN(direct.getTime())) return direct;
  const fallback = new Date(String(value).trim().replace(" ", "T"));
  if (!Number.isNaN(fallback.getTime())) return fallback;
  return null;
}

function formatHumanDateTime(value) {
  const date = parseToDate(value);
  if (!date) return String(value ?? "");
  return new Intl.DateTimeFormat("ru-RU", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(date);
}

function renderGroupBadges(groups, limit = 4) {
  if (!groups || groups.length === 0) {
    return <span className="result-muted">Нет данных AD</span>;
  }
  return (
    <>
      {groups.slice(0, limit).map((group) => (
        <span key={group} className="group-badge">
          {group}
        </span>
      ))}
      {groups.length > limit ? <span className="result-muted">+{groups.length - limit}</span> : null}
    </>
  );
}

export default function WinlogPage() {
  const [filters, setFilters] = useState({ username: "", computer: "", period: "1d" });
  const [lastSearch, setLastSearch] = useState({ username: "", computer: "", period: "1d" });
  const [events, setEvents] = useState([]);
  const [stats, setStats] = useState(null);
  const [adConnected, setAdConnected] = useState(false);
  const [systemLine, setSystemLine] = useState("Подключаю сервисы...");
  const [loading, setLoading] = useState(false);
  const [loadingText, setLoadingText] = useState("Подождите");
  const [currentOffset, setCurrentOffset] = useState(0);
  const [suggestions, setSuggestions] = useState([]);
  const [autocompleteOpen, setAutocompleteOpen] = useState(false);
  const [autocompleteIndex, setAutocompleteIndex] = useState(-1);
  const [toasts, setToasts] = useState([]);
  const [adUsersCache, setAdUsersCache] = useState({});
  const [adDetailsCache, setAdDetailsCache] = useState({});
  const [modalLogin, setModalLogin] = useState("");
  const [modalLoading, setModalLoading] = useState(false);
  const [selectedRow, setSelectedRow] = useState(-1);
  const [hasSearched, setHasSearched] = useState(false);
  const [adReconnecting, setAdReconnecting] = useState(false);
  const [collectors, setCollectors] = useState([]);

  const deferredUsername = useDeferredValue(filters.username);
  const resultsRef = useRef(null);
  const autocompleteTimerRef = useRef(null);
  const autocompleteHostRef = useRef(null);

  const page = Math.floor(currentOffset / PAGE_SIZE) + 1;
  const total = Number(stats?.total ?? 0);
  const totalPages = Math.max(1, Math.ceil((total || 0) / PAGE_SIZE));
  const rangeStart = total > 0 && events.length > 0 ? currentOffset + 1 : 0;
  const rangeEnd = total > 0 && events.length > 0 ? currentOffset + events.length : 0;

  function pushToast(message, type = "info", title = "") {
    const id = `${Date.now()}-${Math.random()}`;
    const icon =
      type === "success"
        ? "bi-check-circle"
        : type === "danger"
          ? "bi-exclamation-triangle"
          : type === "warning"
            ? "bi-exclamation-circle"
            : "bi-info-circle";
    setToasts((current) => [...current, { id, message, type, title, icon }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, 3500);
  }

  async function handleReconnectAd() {
    if (adReconnecting) return;
    setAdReconnecting(true);
    try {
      const result = await reconnectAd();
      const ok = Boolean(result?.connected || result?.success);
      setAdConnected(ok);
      pushToast(
        result?.detail || (ok ? "AD подключён" : "AD по-прежнему недоступен"),
        ok ? "success" : "danger",
        "Active Directory"
      );
      await loadSystemInfo();
    } catch (error) {
      pushToast(error.message || String(error), "danger", "Active Directory");
    } finally {
      setAdReconnecting(false);
    }
  }

  async function loadSystemInfo() {
    try {
      const system = await getSystemInfo();
      const ad = String(system?.active_directory || "unknown");
      const host = system?.system_info?.hostname || "host";
      const ip = system?.system_info?.ip_address || "-";
      const adLabel =
        ad === "available" || ad === "connected" || ad === "ok" ? "подключен" : ad;
      setSystemLine(`${host} · ${ip} · Active Directory: ${adLabel}`);
    } catch {
      setSystemLine("Сервис запущен, системная сводка временно недоступна");
    }
  }

  async function loadCollectors() {
    try {
      const data = await getCollectors();
      setCollectors(Array.isArray(data?.collectors) ? data.collectors : []);
    } catch {
      // не критично — индикатор просто не покажем
    }
  }

  async function loadAdDetails(login) {
    const safeLogin = String(login || "").trim().toLowerCase();
    if (!safeLogin) return emptyAdDetails();
    if (adDetailsCache[safeLogin]) return adDetailsCache[safeLogin];

    const details = await getAdGroups(safeLogin);
    setAdDetailsCache((current) => ({ ...current, [safeLogin]: details }));
    return details;
  }

  async function runSearch({
    nextFilters = lastSearch,
    offset = currentOffset,
    keepScroll = false
  } = {}) {
    const statusSnapshot = systemLine;
    setLoading(true);
    setLoadingText("Загружаю события входа...");
    setAutocompleteOpen(false);
    setSystemLine("Выполняю запрос к журналам и каталогу...");

    try {
      const data = await searchEvents({
        username: nextFilters.username,
        computer: nextFilters.computer,
        period: nextFilters.period,
        limit: PAGE_SIZE,
        offset
      });

      setEvents(Array.isArray(data?.events) ? data.events : []);
      setStats(data?.stats || null);
      setAdConnected(Boolean(data?.ad_connected));
      if (Array.isArray(data?.collectors)) setCollectors(data.collectors);
      setLastSearch(nextFilters);
      setCurrentOffset(offset);
      setSelectedRow(-1);
      setHasSearched(true);
      setSystemLine(statusSnapshot || "Запрос выполнен");

      if (!keepScroll) {
        window.requestAnimationFrame(() => {
          resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      }
    } catch (error) {
      setSystemLine("Ошибка запроса. Проверьте доступность API, БД и AD.");
      pushToast(error.message || String(error), "danger", "Поиск");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadSystemInfo();
    loadCollectors();
  }, []);

  useEffect(() => {
    function handleDocumentClick(event) {
      if (!autocompleteHostRef.current?.contains(event.target)) {
        setAutocompleteOpen(false);
      }
    }
    document.addEventListener("mousedown", handleDocumentClick);
    return () => document.removeEventListener("mousedown", handleDocumentClick);
  }, []);

  useEffect(() => {
    const value = String(deferredUsername || "").trim();
    if (autocompleteTimerRef.current) {
      window.clearTimeout(autocompleteTimerRef.current);
    }

    if (!canQueryAdSuggestions(value)) {
      setAutocompleteOpen(false);
      setSuggestions([]);
      setAutocompleteIndex(-1);
      return undefined;
    }

    autocompleteTimerRef.current = window.setTimeout(async () => {
      const cacheKey = value.toLowerCase();

      if (adUsersCache[cacheKey]) {
        setSuggestions(adUsersCache[cacheKey]);
        setAutocompleteOpen(true);
        setAutocompleteIndex(-1);
        return;
      }

      try {
        const data = await searchAdUsers(value);
        const users = Array.isArray(data?.users) ? data.users : [];
        setAdUsersCache((current) => ({ ...current, [cacheKey]: users }));
        setSuggestions(users);
        setAutocompleteOpen(true);
        setAutocompleteIndex(-1);
      } catch {
        setSuggestions([]);
        setAutocompleteOpen(true);
        setAutocompleteIndex(-1);
      }
    }, 220);

    return () => {
      if (autocompleteTimerRef.current) {
        window.clearTimeout(autocompleteTimerRef.current);
      }
    };
  }, [deferredUsername, adUsersCache]);

  const resultSummary = useMemo(() => {
    if (!stats) return null;
    return {
      returned: Number(stats.returned ?? 0),
      successful: Number(stats.successful ?? 0),
      failed: Number(stats.failed ?? 0)
    };
  }, [stats]);

  const searchMeta = useMemo(
    () => [
      {
        label: "Найдено всего",
        value: total,
        note: "по текущему запросу",
        tone: "is-primary",
        icon: "bi-search"
      },
      {
        label: "На странице",
        value: resultSummary?.returned ?? 0,
        note: rangeStart && rangeEnd ? `${rangeStart}–${rangeEnd}` : "—",
        tone: "is-info",
        icon: "bi-collection"
      },
      {
        label: "Успешные",
        value: resultSummary?.successful ?? 0,
        note: "Event ID 4624",
        tone: "is-success",
        icon: "bi-check2-circle"
      },
      {
        label: "Ошибки",
        value: resultSummary?.failed ?? 0,
        note: "Event ID 4625",
        tone: "is-danger",
        icon: "bi-exclamation-octagon"
      }
    ],
    [rangeEnd, rangeStart, resultSummary, total]
  );

  const activeFiltersCount = useMemo(() => {
    let count = 0;
    if (String(filters.username).trim()) count += 1;
    if (String(filters.computer).trim()) count += 1;
    if (filters.period !== "1d") count += 1;
    return count;
  }, [filters]);

  function handleExport() {
    if (!events.length) {
      pushToast("Нет данных для экспорта", "warning", "Экспорт");
      return;
    }

    let csv = "Время;EventID;Логин;Компьютер;ТипВхода;IPАдрес;Сообщение\n";
    events.forEach((event) => {
      csv += `"${escapeCsv(formatHumanDateTime(event.time_created))}";`;
      csv += `"${escapeCsv(event.event_id)}";`;
      csv += `"${escapeCsv(event.username)}";`;
      csv += `"${escapeCsv(event.computer)}";`;
      csv += `"${escapeCsv(getLogonTypeDescription(event.logon_type))}";`;
      csv += `"${escapeCsv(event.ip_address)}";`;
      csv += `"${escapeCsv(event.message)}"\n`;
    });

    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8;" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `logins_page${page}_${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
    pushToast(`Сохранено ${events.length} записей в CSV`, "success", "Экспорт");
  }

  async function openModal(login) {
    const safeLogin = String(login || "").trim();
    if (!safeLogin) return;

    setModalLogin(safeLogin);
    setModalLoading(true);

    try {
      await loadAdDetails(safeLogin);
    } catch (error) {
      pushToast(error.message || String(error), "danger", "AD");
    } finally {
      setModalLoading(false);
    }
  }

  function closeModal() {
    setModalLogin("");
    setModalLoading(false);
  }

  function changePage(nextPage) {
    const normalized = Math.min(Math.max(1, nextPage), totalPages);
    runSearch({
      nextFilters: lastSearch,
      offset: (normalized - 1) * PAGE_SIZE,
      keepScroll: true
    });
  }

  function resetSearch() {
    setFilters({ username: "", computer: "", period: "1d" });
    setLastSearch({ username: "", computer: "", period: "1d" });
    setEvents([]);
    setStats(null);
    setCurrentOffset(0);
    setSuggestions([]);
    setAutocompleteOpen(false);
    setAutocompleteIndex(-1);
    setSelectedRow(-1);
    setHasSearched(false);
    loadSystemInfo();
  }

  function pickSuggestion(user) {
    setFilters((current) => ({ ...current, username: user.login || "" }));
    setAutocompleteOpen(false);
    setAutocompleteIndex(-1);
  }

  function handleAutocompleteKeys(event) {
    if (!autocompleteOpen || !suggestions.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setAutocompleteIndex((current) => (current + 1) % suggestions.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setAutocompleteIndex((current) => (current <= 0 ? suggestions.length - 1 : current - 1));
    } else if (event.key === "Enter" && autocompleteIndex >= 0) {
      event.preventDefault();
      pickSuggestion(suggestions[autocompleteIndex]);
    } else if (event.key === "Escape") {
      setAutocompleteOpen(false);
    }
  }

  // Колонку «Коллектор» показываем только когда событий из разных коллекторов
  // больше одного — иначе она дублирует одно и то же имя.
  const showCollector =
    new Set(events.map((event) => event.collector).filter(Boolean)).size > 1;

  return (
    <>
      <LoadingOverlay visible={loading} text={loadingText} />
      <ToastStack
        items={toasts}
        onDismiss={(id) => setToasts((current) => current.filter((toast) => toast.id !== id))}
      />

      <PageHero
        icon="bi-shield-check"
        title="Анализатор журналов Windows"
        subtitle={systemLine}
        eyebrow="Windows Security Events"
        chips={[
          adConnected
            ? {
                label: adReconnecting ? "Переподключаю..." : "AD на связи",
                icon: adReconnecting ? "bi-arrow-repeat" : "bi-diagram-3",
                tone: "success",
                title: "Переподключиться к Active Directory",
                onClick: handleReconnectAd,
                disabled: adReconnecting
              }
            : {
                label: adReconnecting ? "Переподключаю..." : "AD недоступен — переподключиться",
                icon: adReconnecting ? "bi-arrow-repeat" : "bi-plug",
                tone: "warning",
                title: "Попытаться восстановить связь с Active Directory",
                onClick: handleReconnectAd,
                disabled: adReconnecting
              }
        ]}
        actions={
          <>
            <button
              className="btn btn-outline-secondary"
              type="button"
              onClick={() =>
                runSearch({ nextFilters: lastSearch, offset: currentOffset, keepScroll: true })
              }
              disabled={!hasSearched}
              title="Повторить запрос"
            >
              <i className="bi bi-arrow-clockwise me-1" />
              Обновить
            </button>
            <button
              className="btn btn-primary"
              type="button"
              disabled={!events.length}
              onClick={handleExport}
            >
              <i className="bi bi-download me-1" />
              Экспорт CSV
            </button>
          </>
        }
      />

      {collectors.length > 0 && (
        <div className="collectors-strip">
          <span className="collectors-strip-label">
            <i className="bi bi-hdd-stack" />
            Базы коллекторов: подключено{" "}
            {collectors.filter((item) => item.connected).length} из {collectors.length}
          </span>
          <div className="collectors-strip-list">
            {collectors.map((item) => (
              <span
                key={`${item.name}-${item.host}`}
                className={`collector-pill ${item.connected ? "is-on" : "is-off"}`}
                title={
                  item.connected
                    ? `${item.host} — подключена`
                    : `${item.host} — недоступна${item.error ? `: ${item.error}` : ""}`
                }
              >
                <span className="collector-dot" />
                <span className="collector-name">{item.name || item.host}</span>
                <span className="collector-host">{item.host}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="stat-grid">
        {searchMeta.map((item) => (
          <article key={item.label} className={`stat-tile ${item.tone}`}>
            <div className="stat-tile-label">
              <i className={`bi ${item.icon}`} style={{ marginRight: 6 }} />
              {item.label}
            </div>
            <div className="stat-tile-value">{item.value}</div>
            <div className="stat-tile-note">{item.note}</div>
          </article>
        ))}
      </div>

      <section className="surface surface-pad search-panel">
        <div className="surface-head" style={{ marginBottom: 18 }}>
          <div>
            <span className="eyebrow is-primary">Фильтры</span>
            <h2 className="section-title">Поиск по событиям входа</h2>
          </div>
          <div className="surface-head-side">
            <span className="status-pill">
              <i className="bi bi-sliders2" />
              Активных фильтров: {activeFiltersCount}
            </span>
            <button
              type="button"
              className={`status-pill ${adConnected ? "is-positive" : "is-warning"}`}
              onClick={handleReconnectAd}
              disabled={adReconnecting}
              title="Переподключиться к Active Directory"
              style={{ cursor: adReconnecting ? "wait" : "pointer", border: "none" }}
            >
              <span className="dot" />
              {adReconnecting
                ? "Переподключаю к AD..."
                : adConnected
                  ? "Active Directory подключен"
                  : "Active Directory недоступен — переподключиться"}
            </button>
          </div>
        </div>

        <form
          autoComplete="off"
          onSubmit={(event) => {
            event.preventDefault();
            runSearch({ nextFilters: filters, offset: 0 });
          }}
        >
          <div className="row g-3">
            <div className="col-12 col-xl-5 position-relative" ref={autocompleteHostRef}>
              <div className="field-stack">
                <span className="field-label-row">
                  <label className="field-label" htmlFor="winlog-username">
                    Пользователь или ФИО
                  </label>
                  <HelpHint text="Логин (ivanov), фамилия или полное ФИО. Подсказки из AD появляются от 2 символов; выбирайте стрелками ↑↓ и Enter." />
                </span>
                <div className="field-control-wrap">
                  <i className="bi bi-person field-icon" aria-hidden="true" />
                  <input
                    id="winlog-username"
                    className="form-control form-control-lg"
                    value={filters.username}
                    placeholder="ivanov или Иванов Иван Иванович"
                    onChange={(event) =>
                      setFilters((current) => ({ ...current, username: event.target.value }))
                    }
                    onFocus={() => {
                      if (suggestions.length) setAutocompleteOpen(true);
                    }}
                    onKeyDown={handleAutocompleteKeys}
                  />
                </div>
              </div>

              {autocompleteOpen ? (
                <div className="autocomplete-list" role="listbox">
                  {suggestions.length ? (
                    suggestions.slice(0, 10).map((user, idx) => (
                      <button
                        key={`${user.login}-${user.displayName}-${idx}`}
                        type="button"
                        className={`autocomplete-item${autocompleteIndex === idx ? " is-active" : ""}`}
                        onMouseEnter={() => setAutocompleteIndex(idx)}
                        onClick={() => pickSuggestion(user)}
                      >
                        <span className="autocomplete-main">{user.login || "-"}</span>
                        <span className="autocomplete-sub">
                          {user.displayName || "Без displayName"}
                        </span>
                      </button>
                    ))
                  ) : (
                    <div className="autocomplete-empty">Пользователи не найдены</div>
                  )}
                </div>
              ) : null}
            </div>

            <div className="col-12 col-md-7 col-xl-4">
              <div className="field-stack">
                <span className="field-label-row">
                  <label className="field-label" htmlFor="winlog-computer">
                    Компьютер
                  </label>
                  <HelpHint text="Имя хоста, откуда был вход (например BS-WS-001). Можно ввести часть имени — найдёт по совпадению." />
                </span>
                <div className="field-control-wrap">
                  <i className="bi bi-pc-display field-icon" aria-hidden="true" />
                  <input
                    id="winlog-computer"
                    className="form-control form-control-lg"
                    value={filters.computer}
                    placeholder="BS-WS-001"
                    onChange={(event) =>
                      setFilters((current) => ({ ...current, computer: event.target.value }))
                    }
                  />
                </div>
              </div>
            </div>

            <div className="col-12 col-md-5 col-xl-3">
              <div className="field-stack">
                <span className="field-label-row">
                  <label className="field-label" htmlFor="winlog-period">
                    Период
                  </label>
                  <HelpHint text="За какой промежуток показывать события. Чем шире период — тем больше записей и дольше запрос." />
                </span>
                <div className="field-control-wrap">
                  <i className="bi bi-calendar-range field-icon" aria-hidden="true" />
                  <select
                    id="winlog-period"
                    className="form-select form-select-lg"
                    value={filters.period}
                    onChange={(event) =>
                      setFilters((current) => ({ ...current, period: event.target.value }))
                    }
                  >
                    {PERIODS.map((period) => (
                      <option key={period.value} value={period.value}>
                        {period.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          </div>

          <div className="search-toolbar">
            <div className="search-toolbar-info" />

            <div className="search-toolbar-actions">
              <button
                className="btn btn-outline-secondary btn-lg"
                type="button"
                onClick={resetSearch}
              >
                <i className="bi bi-eraser me-2" />
                Очистить
              </button>
              <button className="btn btn-primary btn-lg px-4" type="submit">
                <i className="bi bi-search me-2" />
                Найти события
              </button>
            </div>
          </div>
        </form>
      </section>

      <section className="surface result-shell" ref={resultsRef} style={{ marginTop: 22 }}>
        <div className="result-shell-head">
          <div>
            <span className="eyebrow is-primary">Результаты</span>
            <h2 className="section-title">Журнал входов</h2>
            <p className="section-note">
              {hasSearched
                ? `Показано ${rangeStart}–${rangeEnd} из ${total}.`
                : "Результаты появятся после поиска."}
            </p>
          </div>

          <div className="result-shell-actions">
            <div className="pagination-row">
              <button
                type="button"
                className="pager-btn"
                onClick={() => changePage(1)}
                disabled={page <= 1}
                aria-label="Первая страница"
                title="Первая страница"
              >
                <i className="bi bi-chevron-double-left" />
              </button>
              <button
                type="button"
                className="pager-btn"
                onClick={() => changePage(page - 1)}
                disabled={page <= 1}
                aria-label="Назад"
              >
                <i className="bi bi-chevron-left" />
              </button>
              <span className="pager-info">
                Стр. {page} / {totalPages}
              </span>
              <button
                type="button"
                className="pager-btn"
                onClick={() => changePage(page + 1)}
                disabled={page >= totalPages}
                aria-label="Вперед"
              >
                <i className="bi bi-chevron-right" />
              </button>
              <button
                type="button"
                className="pager-btn"
                onClick={() => changePage(totalPages)}
                disabled={page >= totalPages}
                aria-label="Последняя страница"
                title="Последняя страница"
              >
                <i className="bi bi-chevron-double-right" />
              </button>
            </div>
          </div>
        </div>

        {events.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon" aria-hidden="true">
              <i className={`bi ${hasSearched ? "bi-inboxes" : "bi-search"}`} />
            </div>
            <div className="empty-state-title">
              {hasSearched ? "По запросу ничего не найдено" : "События пока не загружены"}
            </div>
            <div className="empty-state-text">
              {hasSearched
                ? "Попробуйте изменить фильтры или расширить период."
                : "Здесь появятся события входа за выбранный период."}
            </div>
            {!hasSearched ? (
              <div className="empty-state-cta">
                <button
                  className="btn btn-primary"
                  type="button"
                  onClick={() => runSearch({ nextFilters: filters, offset: 0 })}
                >
                  <i className="bi bi-search me-2" />
                  Показать события
                </button>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="events-container">
            <table className="result-table">
              <thead>
                <tr>
                  <th style={{ width: 200 }}>Время</th>
                  {showCollector && <th style={{ width: 150 }}>Коллектор</th>}
                  <th style={{ width: 200 }}>Событие</th>
                  <th style={{ width: 280 }}>Пользователь</th>
                  <th style={{ width: 170 }}>Компьютер</th>
                  <th style={{ width: 240 }}>Тип входа</th>
                  <th style={{ width: 170 }}>IP</th>
                  <th>Группы AD</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event, index) => {
                  const login = String(event.username || "").trim();
                  const state = getEventState(event.event_id);
                  const logonBadge = getLogonTypeBadge(event.logon_type);
                  const cachedDetails = adDetailsCache[login.toLowerCase()] || emptyAdDetails(login);

                  return (
                    <tr
                      key={`${event.id}-${index}`}
                      className={`result-row ${state.tone} ${selectedRow === index ? "is-active" : ""}`}
                      onClick={() => setSelectedRow(index)}
                    >
                      <td className="result-time">{formatHumanDateTime(event.time_created)}</td>
                      {showCollector && (
                        <td>
                          <code className="result-code">{event.collector || "-"}</code>
                        </td>
                      )}
                      <td>
                        <span className={`event-pill ${state.tone}`}>
                          <span className="event-pill-dot" />
                          <span>{state.label}</span>
                          <span className="event-pill-code">{event.event_id}</span>
                        </span>
                      </td>
                      <td>
                        <div className="result-user-cell">
                          <button
                            type="button"
                            className="result-user-button"
                            onClick={(clickEvent) => {
                              clickEvent.stopPropagation();
                              openModal(login);
                            }}
                            title="Показать профиль AD"
                          >
                            {login || "-"}
                          </button>
                          <div className="result-subline">
                            {cachedDetails.displayName ||
                              "Открыть карточку пользователя из Active Directory"}
                          </div>
                        </div>
                      </td>
                      <td>
                        <code className="result-code">{event.computer || "-"}</code>
                      </td>
                      <td>
                        <span className={`type-pill ${logonBadge.tone}`}>
                          <i className={`bi ${logonBadge.icon}`} />
                          <span>{logonBadge.label}</span>
                        </span>
                        <div className="result-subline">
                          {getLogonTypeDescription(event.logon_type)}
                        </div>
                      </td>
                      <td>
                        {event.ip_address && event.ip_address !== "-" ? (
                          <code className="result-code">{event.ip_address}</code>
                        ) : (
                          <span className="result-muted">Нет IP</span>
                        )}
                      </td>
                      <td>
                        <div className="groups-cell">
                          {cachedDetails.groups?.length ? (
                            <>
                              <div className="groups-preview">
                                {renderGroupBadges(cachedDetails.groups, 4)}
                              </div>
                              <button
                                type="button"
                                className="btn-soft"
                                onClick={(clickEvent) => {
                                  clickEvent.stopPropagation();
                                  openModal(login);
                                }}
                                title="Открыть профиль"
                              >
                                <i className="bi bi-arrows-fullscreen" />
                                Все
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              className="btn-soft"
                              onClick={async (clickEvent) => {
                                clickEvent.stopPropagation();
                                try {
                                  await loadAdDetails(login);
                                } catch (error) {
                                  pushToast(
                                    error.message || String(error),
                                    "warning",
                                    "AD"
                                  );
                                }
                              }}
                            >
                              <i className="bi bi-people" />
                              Загрузить группы
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <GroupModal
        open={Boolean(modalLogin)}
        login={modalLogin}
        loading={modalLoading}
        details={
          adDetailsCache[String(modalLogin || "").toLowerCase()] || emptyAdDetails(modalLogin)
        }
        onClose={closeModal}
      />
    </>
  );
}
