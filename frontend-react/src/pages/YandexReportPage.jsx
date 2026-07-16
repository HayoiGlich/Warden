import { useEffect, useMemo, useState } from "react";
import PageHero from "../components/PageHero";
import ToastStack from "../components/ToastStack";
import { downloadYcReport, getYcTariff, getYcVms, saveYcTariff } from "../api";

/* ------------------------------------------------------------------ utils */

function fmtNum(value, digits = 0) {
  const n = Number(value || 0);
  return n.toLocaleString("ru-RU", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtMoney(value) {
  const n = Number(value || 0);
  return n.toLocaleString("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

// epoch-секунды -> «ДД.ММ HH:MM:SS» (данные из кэша сервера).
function fmtCachedAt(epochSeconds) {
  const ts = Number(epochSeconds || 0);
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

const STATUS_TONE = {
  Running: "ok",
  Stopped: "muted",
  Stopping: "warn",
  Starting: "warn",
  Error: "err",
  Crashed: "err",
};

/* -------------------------------------------------------------- toasts */

function useToasts() {
  const [toasts, setToasts] = useState([]);
  function pushToast(message, type = "info", title = "") {
    const id = `${Date.now()}-${Math.random()}`;
    const icon =
      type === "success"
        ? "bi-check-circle"
        : type === "danger"
          ? "bi-exclamation-triangle"
          : "bi-info-circle";
    setToasts((c) => [...c, { id, message, type, title, icon }]);
    window.setTimeout(() => setToasts((c) => c.filter((t) => t.id !== id)), 4200);
  }
  const dismiss = (id) => setToasts((c) => c.filter((t) => t.id !== id));
  return { toasts, pushToast, dismiss };
}

/* -------------------------------------------------------- tariff editor */

// Поля тарифа (цена за час, ₽). Ключи совпадают с backend.
const TARIFF_FIELDS = [
  { key: "cpu_100", label: "ЦПУ обычный, 100%" },
  { key: "cpu_50", label: "ЦПУ обычный, 50%" },
  { key: "cpu_hi", label: "ЦПУ Compute Optimized" },
  { key: "ram", label: "ОЗУ обычное" },
  { key: "ram_hi", label: "ОЗУ Compute Optimized" },
  { key: "ssd", label: "SSD" },
  { key: "ssd_io", label: "SSD IO" },
  { key: "hdd", label: "HDD" },
];

function TariffEditor({ pushToast, onSaved }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState(() =>
    Object.fromEntries(TARIFF_FIELDS.map((f) => [f.key, ""]))
  );
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getYcTariff();
        if (cancelled) return;
        const t = data.tariff || {};
        setValues(
          Object.fromEntries(
            TARIFF_FIELDS.map((f) => [f.key, t[f.key] === undefined ? "" : String(t[f.key])])
          )
        );
      } catch (err) {
        if (!cancelled) pushToast(err.message || String(err), "danger", "Тарифы");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pushToast]);

  function setField(key, val) {
    const clean = val.replace(",", ".").replace(/[^0-9.]/g, "");
    setValues((c) => ({ ...c, [key]: clean }));
  }

  async function onSave() {
    setSaving(true);
    try {
      const payload = Object.fromEntries(
        TARIFF_FIELDS.map((f) => [f.key, Number(values[f.key]) || 0])
      );
      const data = await saveYcTariff(payload);
      const t = data.tariff || {};
      setValues(Object.fromEntries(TARIFF_FIELDS.map((f) => [f.key, String(t[f.key] ?? 0)])));
      // Сигнал другим открытым вкладкам «Отчёт» + пересчёт в этой.
      if ("BroadcastChannel" in window) {
        const channel = new BroadcastChannel("yc-report");
        channel.postMessage("tariff-saved");
        channel.close();
      }
      pushToast("Тарифы сохранены — цены в отчёте пересчитаны", "success", "Тарифы");
      onSaved?.();
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Тарифы");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="surface surface-pad" style={{ marginBottom: 16 }}>
      <button type="button" className="yc-collapse-head" onClick={() => setOpen((o) => !o)}>
        <span>
          <i className="bi bi-cash-coin me-2" />
          Тарифы (цена за час, ₽)
        </span>
        <i className={`bi bi-chevron-${open ? "up" : "down"}`} />
      </button>

      {open ? (
        loading ? (
          <div className="admin-hint" style={{ marginTop: 12 }}>Загрузка тарифов…</div>
        ) : (
          <>
            <div className="admin-hint" style={{ margin: "10px 0 4px" }}>
              Цены за час. В отчёте переводятся в сутки (×24) и год. Стоимость ВМ =
              ЦПУ + ОЗУ (по типу платформы) + SSD + HDD.
            </div>
            <div className="admin-grid">
              {TARIFF_FIELDS.map((f) => (
                <div className="field-stack" key={f.key}>
                  <label className="field-label">{f.label}</label>
                  <div className="field-control-wrap">
                    <i className="bi bi-currency-exchange field-icon" aria-hidden="true" />
                    <input
                      className="form-control"
                      inputMode="decimal"
                      value={values[f.key]}
                      placeholder="0"
                      onChange={(e) => setField(f.key, e.target.value)}
                    />
                  </div>
                </div>
              ))}
            </div>
            <div className="search-toolbar" style={{ marginTop: 16 }}>
              <div />
              <div className="search-toolbar-actions">
                <button type="button" className="btn btn-primary px-4" onClick={onSave} disabled={saving}>
                  <i className={`bi ${saving ? "fam-spin bi-arrow-repeat" : "bi-save"} me-2`} />
                  {saving ? "Сохраняю…" : "Сохранить тарифы"}
                </button>
              </div>
            </div>
          </>
        )
      ) : null}
    </section>
  );
}

/* --------------------------------------------------------------- page */

export default function YandexReportPage() {
  const [vms, setVms] = useState([]);
  const [selected, setSelected] = useState(() => new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [cachedAt, setCachedAt] = useState(0);
  const { toasts, pushToast, dismiss } = useToasts();

  // force=false — из кэша сервера (быстро, переживает перезаход); true — заново
  // опросить облако (кнопка «Обновить»).
  async function load(force = false) {
    if (force) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const data = await getYcVms(force);
      const list = Array.isArray(data?.vms) ? data.vms : [];
      setVms(list);
      setCachedAt(Number(data?.cached_at) || 0);
      // По умолчанию включаем в отчёт все машины.
      setSelected(new Set(list.map((v) => v.id)));
      if (force) pushToast(`Данные обновлены: ${list.length} машин`, "success", "Yandex Cloud");
    } catch (err) {
      if (!force) {
        setVms([]);
        setSelected(new Set());
      }
      setError(err.message || String(err));
      if (force) pushToast(err.message || String(err), "danger", "Обновление");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Тихо перечитывает кэш сервера (без похода в облако) и сохраняет выбор —
  // цены могли пересчитаться после изменения тарифов.
  async function reloadCached() {
    try {
      const data = await getYcVms(false);
      const list = Array.isArray(data?.vms) ? data.vms : [];
      setVms(list);
      setCachedAt(Number(data?.cached_at) || 0);
      setSelected((cur) => {
        const ids = new Set(list.map((v) => v.id));
        const next = new Set();
        cur.forEach((id) => ids.has(id) && next.add(id));
        return next;
      });
    } catch {
      /* фоновое обновление — молча игнорируем ошибки */
    }
  }

  // Автоподхват новых тарифов: сигнал из вкладки «Тарифы» (в т.ч. из другой
  // вкладки браузера) + возврат фокуса на страницу.
  useEffect(() => {
    function onWake() {
      if (document.visibilityState === "visible") reloadCached();
    }
    let channel;
    if ("BroadcastChannel" in window) {
      channel = new BroadcastChannel("yc-report");
      channel.onmessage = (e) => {
        if (e?.data === "tariff-saved") reloadCached();
      };
    }
    window.addEventListener("focus", onWake);
    document.addEventListener("visibilitychange", onWake);
    return () => {
      if (channel) channel.close();
      window.removeEventListener("focus", onWake);
      document.removeEventListener("visibilitychange", onWake);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return vms;
    return vms.filter(
      (v) =>
        String(v.name || "").toLowerCase().includes(q) ||
        String(v.os || "").toLowerCase().includes(q) ||
        String(v.status || "").toLowerCase().includes(q)
    );
  }, [vms, query]);

  const selectedVms = useMemo(
    () => vms.filter((v) => selected.has(v.id)),
    [vms, selected]
  );

  const totals = useMemo(() => {
    return selectedVms.reduce(
      (acc, v) => {
        acc.cores += Number(v.cores || 0);
        acc.ram += Number(v.ram_gb || 0);
        acc.ssd += Number(v.ssd_gb || 0);
        acc.hdd += Number(v.hdd_gb || 0);
        acc.snap += Number(v.snapshots_gb || 0);
        acc.day += Number(v.cost_day || 0);
        acc.year += Number(v.cost_year || 0);
        return acc;
      },
      { cores: 0, ram: 0, ssd: 0, hdd: 0, snap: 0, day: 0, year: 0 }
    );
  }, [selectedVms]);

  function toggle(id) {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAllVisible() {
    setSelected((cur) => {
      const next = new Set(cur);
      filtered.forEach((v) => next.add(v.id));
      return next;
    });
  }

  function clearAll() {
    setSelected(new Set());
  }

  const allVisibleSelected =
    filtered.length > 0 && filtered.every((v) => selected.has(v.id));

  async function onDownload() {
    if (!selectedVms.length) {
      pushToast("Выберите хотя бы одну машину", "warning", "Отчёт");
      return;
    }
    setDownloading(true);
    try {
      const rows = selectedVms.map((v) => ({
        name: v.name,
        platform: v.platform,
        cpu_type: v.cpu_type,
        cores: v.cores,
        ram_gb: v.ram_gb,
        ssd_gb: v.ssd_gb,
        hdd_gb: v.hdd_gb,
        snapshots_gb: v.snapshots_gb,
      }));
      const filename = await downloadYcReport(rows);
      pushToast(`Отчёт сформирован: ${filename}`, "success", "Скачивание");
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Отчёт");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <>
      <ToastStack items={toasts} onDismiss={dismiss} />

      <PageHero
        icon="bi-hdd-stack"
        title="Отчёт по ВМ Yandex Cloud"
        subtitle="Выберите машины — отчёт по стоимости соберётся в браузере, готовый файл можно скачать."
        eyebrow="Yandex Cloud"
        chips={[
          { label: "Расчёт стоимости", icon: "bi-cash-coin", tone: "primary" },
          { label: "Выгрузка в XLSX", icon: "bi-file-earmark-excel" },
        ]}
        actions={
          <button
            type="button"
            className="btn btn-outline-secondary"
            onClick={() => load(true)}
            disabled={loading || refreshing}
            title="Заново опросить Yandex Cloud"
          >
            <i className={`bi bi-arrow-clockwise me-1${refreshing ? " fam-spin" : ""}`} />
            {refreshing ? "Обновляю…" : "Обновить"}
          </button>
        }
      />

      {error ? (
        <div className="admin-banner" style={{ marginBottom: 12 }}>
          <i className="bi bi-exclamation-triangle" />
          <span>{error}</span>
        </div>
      ) : null}

      <TariffEditor pushToast={pushToast} onSaved={reloadCached} />

      {loading ? (
        <div className="surface surface-pad admin-hint">
          <span className="loading-spinner" style={{ marginRight: 8, verticalAlign: "middle" }} />
          Запрашиваю список машин в Yandex Cloud…
        </div>
      ) : !error ? (
        <>
          {/* -------- выбор машин -------- */}
          <section className="surface surface-pad">
            <div className="yc-toolbar">
              <div className="field-control-wrap" style={{ flex: 1, minWidth: 200 }}>
                <i className="bi bi-search field-icon" aria-hidden="true" />
                <input
                  className="form-control"
                  placeholder="Поиск по имени, ОС или статусу…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
              <button type="button" className="btn btn-outline-secondary btn-sm" onClick={selectAllVisible}>
                <i className="bi bi-check-all me-1" />Выбрать все
              </button>
              <button type="button" className="btn btn-outline-secondary btn-sm" onClick={clearAll}>
                <i className="bi bi-eraser me-1" />Снять
              </button>
              <span className="status-pill">
                <i className="bi bi-check2-square" />Выбрано: {selected.size} из {vms.length}
              </span>
              {cachedAt ? (
                <span className="status-pill" title="Время последнего опроса Yandex Cloud">
                  <i className="bi bi-clock-history" />Данные от {fmtCachedAt(cachedAt)}
                </span>
              ) : null}
            </div>

            <div className="bulk-table-wrap" style={{ marginTop: 12 }}>
              <table className="bulk-table yc-table">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}>
                      <input
                        type="checkbox"
                        checked={allVisibleSelected}
                        onChange={(e) => (e.target.checked ? selectAllVisible() : clearAll())}
                        title="Выбрать всё / снять"
                      />
                    </th>
                    <th>Имя ВМ</th>
                    <th>Статус</th>
                    <th>Платформа</th>
                    <th>Тип ЦПУ</th>
                    <th>ОС</th>
                    <th className="yc-num">vCPU</th>
                    <th className="yc-num">ОЗУ, Гб</th>
                    <th className="yc-num">SSD, Гб</th>
                    <th className="yc-num">HDD, Гб</th>
                    <th className="yc-num">Снимки, Гб</th>
                    <th className="yc-num">Цена/сутки, ₽</th>
                    <th className="yc-num">Цена/год, ₽</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.length === 0 ? (
                    <tr>
                      <td colSpan={12} className="admin-hint" style={{ textAlign: "center" }}>
                        {vms.length ? "Ничего не найдено." : "В фолдере нет виртуальных машин."}
                      </td>
                    </tr>
                  ) : (
                    filtered.map((v) => {
                      const checked = selected.has(v.id);
                      const tone = STATUS_TONE[v.status] || "muted";
                      return (
                        <tr
                          key={v.id}
                          className={checked ? "yc-row-on" : ""}
                          onClick={() => toggle(v.id)}
                          style={{ cursor: "pointer" }}
                        >
                          <td onClick={(e) => e.stopPropagation()}>
                            <input type="checkbox" checked={checked} onChange={() => toggle(v.id)} />
                          </td>
                          <td>{v.name}</td>
                          <td>
                            <span className={`yc-status yc-${tone}`}>{v.status}</span>
                          </td>
                          <td>{v.platform}</td>
                          <td>{v.cpu_type}</td>
                          <td>{v.os}</td>
                          <td className="yc-num">{fmtNum(v.cores)}</td>
                          <td className="yc-num">{fmtNum(v.ram_gb)}</td>
                          <td className="yc-num">{fmtNum(v.ssd_gb)}</td>
                          <td className="yc-num">{fmtNum(v.hdd_gb)}</td>
                          <td className="yc-num">{fmtNum(v.snapshots_gb)}</td>
                          <td className="yc-num">{fmtMoney(v.cost_day)}</td>
                          <td className="yc-num">{fmtMoney(v.cost_year)}</td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </section>

          {/* -------- отчёт по выбранным -------- */}
          <section className="surface surface-pad" style={{ marginTop: 16 }}>
            <div className="surface-head" style={{ marginBottom: 12 }}>
              <div>
                <span className="eyebrow is-primary">Отчёт</span>
                <h2 className="section-title">Стоимость выбранных машин</h2>
                <p className="section-note">
                  В отчёте {selectedVms.length}{" "}
                  {selectedVms.length === 1 ? "машина" : "машин"} · итог за год{" "}
                  <b>{fmtMoney(totals.year)} ₽</b>
                </p>
              </div>
              <button
                type="button"
                className="btn btn-primary"
                onClick={onDownload}
                disabled={downloading || !selectedVms.length}
              >
                <i className={`bi ${downloading ? "fam-spin bi-arrow-repeat" : "bi-file-earmark-excel"} me-1`} />
                {downloading ? "Формирую…" : "Скачать XLSX"}
              </button>
            </div>

            {selectedVms.length === 0 ? (
              <div className="admin-hint">Не выбрано ни одной машины — отметьте нужные в таблице выше.</div>
            ) : (
              <div className="bulk-table-wrap">
                <table className="bulk-table yc-table">
                  <thead>
                    <tr>
                      <th>Имя ВМ</th>
                      <th>Платформа</th>
                      <th>Тип ЦПУ</th>
                      <th className="yc-num">ЦПУ, шт.</th>
                      <th className="yc-num">ОЗУ, Гб</th>
                      <th className="yc-num">SSD, Гб</th>
                      <th className="yc-num">HDD, Гб</th>
                      <th className="yc-num">Снимки, Гб</th>
                      <th className="yc-num">Итого за ВМ в день, ₽</th>
                      <th className="yc-num">Итого за ВМ в год, ₽</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedVms.map((v) => (
                      <tr key={v.id}>
                        <td>{v.name}</td>
                        <td>{v.platform}</td>
                        <td>{v.cpu_type}</td>
                        <td className="yc-num">{fmtNum(v.cores)}</td>
                        <td className="yc-num">{fmtNum(v.ram_gb)}</td>
                        <td className="yc-num">{fmtNum(v.ssd_gb)}</td>
                        <td className="yc-num">{fmtNum(v.hdd_gb)}</td>
                        <td className="yc-num">{fmtNum(v.snapshots_gb)}</td>
                        <td className="yc-num">{fmtMoney(v.cost_day)}</td>
                        <td className="yc-num">{fmtMoney(v.cost_year)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="yc-total-row">
                      <td colSpan={3}>ИТОГО</td>
                      <td className="yc-num">{fmtNum(totals.cores)}</td>
                      <td className="yc-num">{fmtNum(totals.ram)}</td>
                      <td className="yc-num">{fmtNum(totals.ssd)}</td>
                      <td className="yc-num">{fmtNum(totals.hdd)}</td>
                      <td className="yc-num">{fmtNum(totals.snap)}</td>
                      <td className="yc-num">{fmtMoney(totals.day)}</td>
                      <td className="yc-num">{fmtMoney(totals.year)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </section>
        </>
      ) : null}
    </>
  );
}
