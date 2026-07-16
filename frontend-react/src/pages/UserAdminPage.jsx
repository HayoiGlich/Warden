import { useEffect, useMemo, useRef, useState } from "react";
import {
  bulkCreateAdUsers,
  bulkUpdateAdUsers,
  createAdUser,
  getAdGroupsList,
  getAdOus,
  getAdOuUsers,
  getAdUserDetail,
  getFamStatus,
  searchAdUsers,
  syncFam,
  updateAdUser,
} from "../api";
import LoadingOverlay from "../components/LoadingOverlay";
import PageHero from "../components/PageHero";
import PickerModal from "../components/PickerModal";
import ToastStack from "../components/ToastStack";
import GroupTemplates from "../components/GroupTemplates";
import { hasPerm } from "../lib/perms";

const MODES = [
  { key: "create", icon: "bi-person-plus", label: "Создать", write: true },
  { key: "edit", icon: "bi-person-gear", label: "Редактировать" },
  { key: "bulk", icon: "bi-people", label: "Массовые операции", write: true },
];

/* ------------------------------------------------------------------ utils */

function splitGroups(value) {
  return String(value || "")
    .split(/[|;,\n]/)
    .map((part) => part.trim())
    .filter(Boolean);
}

// "OU=2fa,OU=UNV,DC=STAFF,..." -> "2fa"
function rdnValue(dn) {
  const first = String(dn || "").split(",")[0] || "";
  const eq = first.indexOf("=");
  return eq >= 0 ? first.slice(eq + 1).trim() : first.trim();
}

// Возвращает короткое имя OU по сохранённому значению (DN или имя).
function ouLabel(value, ous) {
  if (!value) return "";
  const found = ous.find((o) => o.dn === value);
  if (found) return found.name;
  if (/dc=/i.test(value) && value.includes(",")) return rdnValue(value);
  return value;
}

// Транслитерация кириллицы в латиницу для логина.
const TRANSLIT = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z",
  и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r",
  с: "s", т: "t", у: "u", ф: "f", х: "kh", ц: "ts", ч: "ch", ш: "sh",
  щ: "shch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
};

function translit(text) {
  return String(text || "")
    .toLowerCase()
    .split("")
    .map((ch) => (ch in TRANSLIT ? TRANSLIT[ch] : ch))
    .join("")
    .replace(/[^a-z0-9]/g, "");
}

// "Аточкин Александр Анатольевич" -> "a.a.atochkin"
function generateLogin({ lastName, firstName, middleName }) {
  const ln = translit(lastName);
  if (!ln) return "";
  const fi = translit(firstName).charAt(0);
  const mi = translit(middleName).charAt(0);
  const initials = [fi, mi].filter(Boolean).join(".");
  return initials ? `${initials}.${ln}` : ln;
}

// ------------------------------------------------------------ пароли

// Наборы символов без визуально похожих (0/O, 1/l/I) — меньше ошибок при вводе.
const PWD_SETS = {
  upper: "ABCDEFGHJKLMNPQRSTUVWXYZ",
  lower: "abcdefghijkmnpqrstuvwxyz",
  digits: "23456789",
  symbols: "!@#$%^&*()-_=+?",
};

// Криптостойкое случайное целое [0, max).
function randInt(max) {
  if (max <= 0) return 0;
  const buf = new Uint32Array(1);
  const limit = Math.floor(0xffffffff / max) * max;
  let x = 0;
  do {
    crypto.getRandomValues(buf);
    x = buf[0];
  } while (x >= limit);
  return x % max;
}

function pickChar(chars) {
  return chars.charAt(randInt(chars.length));
}

// Генерирует пароль с гарантией по одному символу каждого включённого набора.
function generatePassword({ length, upper, lower, digits, symbols }) {
  const sets = [];
  if (upper) sets.push(PWD_SETS.upper);
  if (lower) sets.push(PWD_SETS.lower);
  if (digits) sets.push(PWD_SETS.digits);
  if (symbols) sets.push(PWD_SETS.symbols);
  if (!sets.length) return "";

  const pool = sets.join("");
  const len = Math.max(Number(length) || 0, sets.length);
  const chars = sets.map((s) => pickChar(s)); // по одному из каждого набора
  while (chars.length < len) chars.push(pickChar(pool));

  // Перемешиваем (Fisher–Yates), чтобы обязательные символы не стояли в начале.
  for (let i = chars.length - 1; i > 0; i -= 1) {
    const j = randInt(i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}

// Приводит дату к ISO YYYY-MM-DD (для input type=date). Пустое -> "".
function normalizeDateToIso(value) {
  const v = String(value || "").trim();
  if (!v) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return v;
  const m = v.match(/^(\d{1,2})[.\/-](\d{1,2})[.\/-](\d{4})$/);
  if (m) {
    const dd = m[1].padStart(2, "0");
    const mm = m[2].padStart(2, "0");
    return `${m[3]}-${mm}-${dd}`;
  }
  return v;
}

// ISO YYYY-MM-DD -> DD.MM.YYYY для показа.
function isoToHuman(value) {
  const m = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : String(value || "");
}

// Отображаемое имя всегда «Фамилия Имя Отчество».
function composeDisplayName(lastName, firstName, middleName, fallback = "") {
  const parts = [lastName, firstName, middleName]
    .map((s) => String(s || "").trim())
    .filter(Boolean);
  if (parts.length) return parts.join(" ");
  return String(fallback || "").trim();
}

// Переставляет существующее имя в порядок «Фамилия Имя Отчество», используя
// sn/givenName из AD. Отчество — то, что осталось от displayName.
function reorderDisplayName(displayName, lastName, firstName) {
  const dn = String(displayName || "").trim();
  const ln = String(lastName || "").trim();
  const fn = String(firstName || "").trim();
  if (!dn) return [ln, fn].filter(Boolean).join(" ");
  if (!ln || !fn) return dn;

  const lnL = ln.toLowerCase();
  const fnL = fn.toLowerCase();
  let foundLn = false;
  let foundFn = false;
  const middle = [];
  for (const t of dn.split(/\s+/).filter(Boolean)) {
    const tl = t.toLowerCase();
    if (!foundLn && tl === lnL) {
      foundLn = true;
      continue;
    }
    if (!foundFn && tl === fnL) {
      foundFn = true;
      continue;
    }
    middle.push(t);
  }
  // Если в displayName не нашли и фамилию, и имя — не трогаем (чтобы не испортить).
  if (!foundLn || !foundFn) return dn;
  return [ln, fn, ...middle].join(" ");
}

// Если ФИО задано одной строкой (displayName), а полей нет — разбираем.
function fillNameParts(row) {
  if (row.lastName || row.firstName || row.middleName) return row;
  const parts = String(row.displayName || "").trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return {
      ...row,
      lastName: parts[0],
      firstName: parts[1],
      middleName: parts[2] || "",
    };
  }
  return row;
}

function parseCsv(text) {
  const clean = String(text || "").replace(/^﻿/, "");
  const firstLine = clean.split(/\r?\n/, 1)[0] || "";
  const semis = (firstLine.match(/;/g) || []).length;
  const commas = (firstLine.match(/,/g) || []).length;
  const tabs = (firstLine.match(/\t/g) || []).length;
  let delim = ",";
  if (semis > commas && semis >= tabs) delim = ";";
  else if (tabs > commas && tabs > semis) delim = "\t";

  const rows = [];
  let field = "";
  let row = [];
  let inQuotes = false;

  for (let i = 0; i < clean.length; i += 1) {
    const ch = clean[i];
    if (inQuotes) {
      if (ch === '"') {
        if (clean[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === delim) {
      row.push(field);
      field = "";
    } else if (ch === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (ch !== "\r") {
      field += ch;
    }
  }
  if (field.length || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => String(c).trim() !== ""));
}

const HEADER_ALIASES = {
  login: "login",
  логин: "login",
  samaccountname: "login",
  username: "login",
  firstname: "firstName",
  имя: "firstName",
  givenname: "firstName",
  lastname: "lastName",
  фамилия: "lastName",
  sn: "lastName",
  middlename: "middleName",
  отчество: "middleName",
  patronymic: "middleName",
  middle: "middleName",
  displayname: "displayName",
  фио: "displayName",
  отображаемоеимя: "displayName",
  email: "email",
  mail: "email",
  почта: "email",
  employeenumber: "employeeNumber",
  табельный: "employeeNumber",
  табельныйномер: "employeeNumber",
  номерсотрудника: "employeeNumber",
  табномер: "employeeNumber",
  accountexpires: "expires",
  expires: "expires",
  активнадо: "expires",
  срокдействия: "expires",
  действительнадо: "expires",
  password: "password",
  пароль: "password",
  newpassword: "password",
  новыйпароль: "password",
  ou: "ou",
  подразделение: "ou",
  groups: "groups",
  группы: "groups",
  setgroups: "groups",
  enabled: "enabled",
  активен: "enabled",
  статус: "enabled",
};

function normalizeHeader(name) {
  const key = String(name || "").trim().toLowerCase().replace(/[\s_-]/g, "");
  return HEADER_ALIASES[key] || "";
}

function parseBool(value) {
  const v = String(value || "").trim().toLowerCase();
  if (["1", "true", "да", "yes", "y", "вкл", "on"].includes(v)) return true;
  if (["0", "false", "нет", "no", "n", "выкл", "off"].includes(v)) return false;
  return null;
}

// Преобразует массив строк-массивов (из CSV или XLSX) в строки таблицы.
function rowsToObjects(rows, mode) {
  if (!rows || rows.length < 1) return [];
  const headers = rows[0].map((h) => normalizeHeader(h));
  const hasKnownHeader = headers.some(Boolean);
  const dataRows = hasKnownHeader ? rows.slice(1) : rows;
  const order = hasKnownHeader
    ? headers
    : mode === "create"
      ? ["login", "lastName", "firstName", "middleName", "displayName", "email", "employeeNumber", "password", "ou", "groups", "enabled", "expires"]
      : ["login", "lastName", "firstName", "displayName", "email", "employeeNumber", "password", "ou", "groups", "enabled", "expires"];

  return dataRows.map((cells) => {
    const obj = {};
    order.forEach((field, idx) => {
      if (field) obj[field] = cells[idx] !== undefined ? String(cells[idx]).trim() : "";
    });
    if (obj.expires) obj.expires = normalizeDateToIso(obj.expires);
    return emptyRow(mode, fillNameParts(obj));
  });
}

function csvToRows(text, mode) {
  return rowsToObjects(parseCsv(text), mode);
}

// Загружаем SheetJS динамически — он большой и нужен только при импорте файла.
async function xlsxToRows(arrayBuffer, mode) {
  const XLSX = await import("xlsx");
  const wb = XLSX.read(arrayBuffer, { type: "array" });
  const ws = wb.Sheets[wb.SheetNames[0]];
  if (!ws) return [];
  const rows = XLSX.utils.sheet_to_json(ws, { header: 1, raw: false, defval: "" });
  return rowsToObjects(rows, mode);
}

let rowSeq = 1;
function emptyRow(mode, preset = {}) {
  rowSeq += 1;
  const base = {
    _id: `row-${rowSeq}`,
    login: preset.login || "",
    firstName: preset.firstName || "",
    lastName: preset.lastName || "",
    middleName: preset.middleName || "",
    displayName: preset.displayName || "",
    email: preset.email || "",
    employeeNumber: preset.employeeNumber || "",
    ou: preset.ou || "",
    groups: preset.groups || "",
    enabled: preset.enabled !== undefined ? preset.enabled : "",
    password: preset.password || "",
    expires: preset.expires || "",
    created: preset.created || "",
  };
  return base;
}

/* --------------------------------------------------------------- toasts */

function useToasts() {
  const [toasts, setToasts] = useState([]);
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
    setToasts((cur) => [...cur, { id, message, type, title, icon }]);
    window.setTimeout(() => {
      setToasts((cur) => cur.filter((t) => t.id !== id));
    }, 4200);
  }
  const dismiss = (id) => setToasts((cur) => cur.filter((t) => t.id !== id));
  return { toasts, pushToast, dismiss };
}

/* ------------------------------------------------------------- OU pick */

function OuPicker({ ous, value, onChange, allowDefault = false }) {
  const [open, setOpen] = useState(false);
  const label = ouLabel(value, ous);

  return (
    <>
      <div className="field-with-action">
        <div className={`ou-display${value ? "" : " is-empty"}`}>
          {value ? (
            <span className="ou-display-name">{label}</span>
          ) : (
            <span>{allowDefault ? "Контейнер по умолчанию" : "OU не выбрана"}</span>
          )}
        </div>
        <button type="button" className="btn btn-outline-secondary btn-pick" onClick={() => setOpen(true)}>
          <i className="bi bi-diagram-3" />Выбрать
        </button>
        {value ? (
          <button
            type="button"
            className="btn btn-outline-secondary btn-pick"
            title="Сбросить"
            onClick={() => onChange("")}
          >
            <i className="bi bi-x-lg" />
          </button>
        ) : null}
      </div>

      <PickerModal
        open={open}
        title="Выбор подразделения (OU)"
        subtitle={`Доступно OU: ${ous.length}`}
        items={ous}
        keyOf={(ou) => ou.dn}
        labelOf={(ou) => ou.name}
        selectedKeys={value ? [value] : []}
        multi={false}
        searchPlaceholder="Поиск по названию…"
        confirmLabel="Выбрать OU"
        onClose={() => setOpen(false)}
        onConfirm={(chosen) => {
          onChange(chosen[0]?.dn || "");
          setOpen(false);
        }}
      />
    </>
  );
}

/* -------------------------------------------------------- group picker */

function GroupPicker({ value, onChange }) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [open, setOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [allGroups, setAllGroups] = useState([]);
  const [groupsLoading, setGroupsLoading] = useState(false);
  const timerRef = useRef(null);
  const hostRef = useRef(null);

  async function openModal() {
    setModalOpen(true);
    if (allGroups.length === 0) {
      setGroupsLoading(true);
      try {
        const data = await getAdGroupsList("", 5000);
        setAllGroups(Array.isArray(data?.groups) ? data.groups : []);
      } catch {
        setAllGroups([]);
      } finally {
        setGroupsLoading(false);
      }
    }
  }

  // Ключи (dn) уже выбранных групп — для предвыбора чекбоксов в модалке.
  const selectedKeys = useMemo(() => {
    const keys = [];
    value.forEach((chip) => {
      if (chip.dn) {
        keys.push(chip.dn);
      } else {
        const match = allGroups.find(
          (g) => g.name.toLowerCase() === chip.name.toLowerCase()
        );
        if (match) keys.push(match.dn);
      }
    });
    return keys;
  }, [value, allGroups]);

  function applyModal(chosen) {
    // Сохраняем вручную введённые группы, которых нет в общем списке.
    const chosenNames = new Set(chosen.map((g) => g.name.toLowerCase()));
    const preservedFreeText = value.filter(
      (chip) =>
        !chip.dn &&
        !allGroups.some((g) => g.name.toLowerCase() === chip.name.toLowerCase()) &&
        !chosenNames.has(chip.name.toLowerCase())
    );
    onChange([...chosen.map((g) => ({ name: g.name, dn: g.dn })), ...preservedFreeText]);
    setModalOpen(false);
  }

  useEffect(() => {
    function onDocClick(e) {
      if (!hostRef.current?.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  useEffect(() => {
    const q = query.trim();
    if (timerRef.current) window.clearTimeout(timerRef.current);
    if (q.length < 2) {
      setSuggestions([]);
      return undefined;
    }
    timerRef.current = window.setTimeout(async () => {
      try {
        const data = await getAdGroupsList(q, 30);
        setSuggestions(Array.isArray(data?.groups) ? data.groups : []);
        setOpen(true);
      } catch {
        setSuggestions([]);
      }
    }, 220);
    return () => timerRef.current && window.clearTimeout(timerRef.current);
  }, [query]);

  function addGroup(group) {
    const name = group?.name || group?.dn || "";
    if (!name) return;
    const exists = value.some(
      (g) => (g.dn && g.dn === group.dn) || g.name.toLowerCase() === name.toLowerCase()
    );
    if (!exists) onChange([...value, { name, dn: group.dn || "" }]);
    setQuery("");
    setSuggestions([]);
    setOpen(false);
  }

  function addFreeText() {
    const name = query.trim();
    if (!name) return;
    if (!value.some((g) => g.name.toLowerCase() === name.toLowerCase())) {
      onChange([...value, { name, dn: "" }]);
    }
    setQuery("");
  }

  function removeGroup(idx) {
    onChange(value.filter((_, i) => i !== idx));
  }

  return (
    <div className="group-picker" ref={hostRef}>
      <div className="field-with-action">
        <div className="field-control-wrap">
          <i className="bi bi-people field-icon" aria-hidden="true" />
          <input
            className="form-control"
            placeholder="Начните вводить имя группы…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onFocus={() => suggestions.length && setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (suggestions.length) addGroup(suggestions[0]);
                else addFreeText();
              }
            }}
          />
        </div>
        <button type="button" className="btn btn-outline-secondary btn-pick" onClick={openModal}>
          <i className="bi bi-list-check" />Список групп
        </button>
      </div>

      {open && suggestions.length ? (
        <div className="group-suggest">
          {suggestions.map((group) => (
            <button
              key={group.dn || group.name}
              type="button"
              className="group-suggest-item"
              onClick={() => addGroup(group)}
            >
              <span className="group-suggest-main">{group.name}</span>
              <span className="group-suggest-sub">{group.dn}</span>
            </button>
          ))}
        </div>
      ) : null}

      <div className="group-chips">
        {value.length === 0 ? (
          <span className="result-muted">Группы не выбраны</span>
        ) : (
          value.map((g, idx) => (
            <span key={`${g.name}-${idx}`} className="group-chip">
              {g.name}
              <button type="button" onClick={() => removeGroup(idx)} title="Убрать">
                <i className="bi bi-x" />
              </button>
            </span>
          ))
        )}
      </div>

      <PickerModal
        open={modalOpen}
        title="Выбор групп"
        loading={groupsLoading}
        items={allGroups}
        keyOf={(g) => g.dn}
        labelOf={(g) => g.name}
        selectedKeys={selectedKeys}
        multi
        searchPlaceholder="Поиск группы по имени…"
        confirmLabel="Применить выбор"
        onClose={() => setModalOpen(false)}
        onConfirm={applyModal}
      />
    </div>
  );
}

/* --------------------------------------------------------- result list */

function ResultReport({ summary }) {
  if (!summary) return null;
  const results = summary.results || [];
  return (
    <section className="surface surface-pad" style={{ marginTop: 18 }}>
      <div className="surface-head" style={{ marginBottom: 12 }}>
        <div>
          <span className="eyebrow is-primary">Отчёт</span>
          <h2 className="section-title">Результаты операции</h2>
          <p className="section-note">
            Обработано {summary.processed} · успешно {summary.succeeded} · ошибок {summary.failed}
          </p>
        </div>
      </div>
      <div className="events-container">
        <table className="result-table">
          <thead>
            <tr>
              <th style={{ width: 180 }}>Логин</th>
              <th style={{ width: 120 }}>Статус</th>
              <th>Детали</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r, idx) => (
              <tr key={`${r.login}-${idx}`}>
                <td><code className="result-code">{r.login || "—"}</code></td>
                <td>
                  <span className={`result-status ${r.success ? "is-ok" : "is-err"}`}>
                    <i className={`bi ${r.success ? "bi-check-circle-fill" : "bi-x-circle-fill"}`} />
                    {r.success ? r.action || "ок" : "ошибка"}
                  </span>
                </td>
                <td>
                  <div>{r.detail}</div>
                  {(r.warnings || []).map((w, i) => (
                    <div key={i} className="result-warn">
                      <i className="bi bi-exclamation-triangle me-1" />
                      {w}
                    </div>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/* -------------------------------------------------------- password gen */

function PasswordGenField({ value, onChange, placeholder }) {
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState({
    length: 14,
    upper: true,
    lower: true,
    digits: true,
    symbols: true,
  });
  const hostRef = useRef(null);

  useEffect(() => {
    function onDocClick(e) {
      if (!hostRef.current?.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  function setOpt(field, val) {
    setOpts((cur) => ({ ...cur, [field]: val }));
  }

  function generate() {
    const pwd = generatePassword(opts);
    if (pwd) onChange(pwd);
  }

  const CHECKS = [
    { key: "upper", label: "Верхний регистр (A–Z)" },
    { key: "lower", label: "Нижний регистр (a–z)" },
    { key: "digits", label: "Цифры (2–9)" },
    { key: "symbols", label: "Символы (!@#$…)" },
  ];
  const noneSelected = !opts.upper && !opts.lower && !opts.digits && !opts.symbols;

  return (
    <div className="pwd-gen" ref={hostRef}>
      <div className="field-with-action">
        <input
          className="form-control"
          type="text"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          className="btn btn-outline-secondary btn-pick"
          onClick={generate}
          title="Сгенерировать пароль"
        >
          <i className="bi bi-magic" />Сгенерировать
        </button>
        <button
          type="button"
          className="btn btn-outline-secondary btn-pick"
          title="Параметры пароля"
          onClick={() => setOpen((o) => !o)}
        >
          <i className="bi bi-sliders" />
        </button>
      </div>

      {open ? (
        <div className="pwd-gen-pop">
          <div className="pwd-gen-row">
            <label className="pwd-gen-label">Длина пароля</label>
            <input
              type="number"
              className="form-control pwd-gen-len"
              min="4"
              max="64"
              value={opts.length}
              onChange={(e) =>
                setOpt(
                  "length",
                  Math.max(4, Math.min(64, Number(e.target.value) || 0))
                )
              }
            />
          </div>
          {CHECKS.map((c) => (
            <label key={c.key} className="pwd-gen-check">
              <input
                type="checkbox"
                checked={opts[c.key]}
                onChange={(e) => setOpt(c.key, e.target.checked)}
              />
              {c.label}
            </label>
          ))}
          {noneSelected ? (
            <div className="pwd-gen-warn">
              <i className="bi bi-exclamation-triangle me-1" />
              Выберите хотя бы один набор символов.
            </div>
          ) : null}
          <button
            type="button"
            className="btn btn-primary btn-sm pwd-gen-apply"
            disabled={noneSelected}
            onClick={generate}
          >
            <i className="bi bi-arrow-repeat me-1" />Создать пароль
          </button>
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------ create */

function CreateForm({ ous, pushToast, setLoading }) {
  const blank = {
    login: "",
    lastName: "",
    firstName: "",
    middleName: "",
    displayName: "",
    email: "",
    employeeNumber: "",
    expires: "",
    password: "",
    ou: "",
    enabled: true,
  };
  const [form, setForm] = useState(blank);
  const [groups, setGroups] = useState([]);
  const [summary, setSummary] = useState(null);

  const autoDisplay = useMemo(
    () =>
      [form.lastName, form.firstName, form.middleName]
        .map((s) => s.trim())
        .filter(Boolean)
        .join(" "),
    [form.lastName, form.firstName, form.middleName]
  );

  function set(field, val) {
    setForm((cur) => ({ ...cur, [field]: val }));
  }

  // Ввод «Отображаемого имени» разбирается на Фамилию/Имя/Отчество.
  function setDisplayName(val) {
    const parts = String(val || "").trim().split(/\s+/).filter(Boolean);
    setForm((cur) => ({
      ...cur,
      displayName: val,
      lastName: parts[0] || "",
      firstName: parts[1] || "",
      middleName: parts.slice(2).join(" "),
    }));
  }

  function makeLogin() {
    const login = generateLogin({
      lastName: form.lastName,
      firstName: form.firstName,
      middleName: form.middleName,
    });
    if (login) {
      set("login", login);
    } else {
      pushToast("Для генерации логина нужна хотя бы фамилия", "info", "Логин");
    }
  }

  async function submit(e) {
    e.preventDefault();
    if (!form.login.trim()) {
      pushToast("Укажите логин", "warning", "Создание");
      return;
    }
    setLoading(true);
    try {
      const payload = {
        login: form.login.trim(),
        firstName: form.firstName.trim(),
        lastName: form.lastName.trim(),
        displayName: composeDisplayName(
          form.lastName,
          form.firstName,
          form.middleName,
          form.displayName
        ),
        email: form.email.trim(),
        employeeNumber: form.employeeNumber.trim(),
        accountExpires: form.expires.trim(),
        password: form.password,
        ou: form.ou,
        groups: groups.map((g) => g.dn || g.name),
        enabled: Boolean(form.enabled),
      };
      const data = await createAdUser(payload);
      const r = data.result;
      setSummary({ processed: 1, succeeded: r.success ? 1 : 0, failed: r.success ? 0 : 1, results: [r] });
      if (r.success) {
        pushToast(r.detail || "Учётка создана", "success", form.login);
        setForm(blank);
        setGroups([]);
      } else {
        pushToast(r.detail || "Не удалось создать", "danger", form.login);
      }
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Создание");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <form className="surface surface-pad" onSubmit={submit}>
        <div className="admin-grid">
          <div className="field-stack">
            <label className="field-label">Логин (sAMAccountName) *</label>
            <div className="field-with-action">
              <input className="form-control" value={form.login} placeholder="a.a.atochkin" onChange={(e) => set("login", e.target.value)} />
              <button type="button" className="btn btn-outline-secondary btn-pick" onClick={makeLogin} title="Сгенерировать из ФИО">
                <i className="bi bi-magic" />Сгенерировать
              </button>
            </div>
          </div>
          <div className="field-stack">
            <label className="field-label">Email</label>
            <input className="form-control" value={form.email} placeholder="ivanov@staff.local" onChange={(e) => set("email", e.target.value)} />
          </div>
          <div className="field-stack">
            <label className="field-label">Табельный номер (employeeNumber)</label>
            <input className="form-control" value={form.employeeNumber} placeholder="123456" onChange={(e) => set("employeeNumber", e.target.value)} />
          </div>
          <div className="field-stack">
            <label className="field-label">Активна до (срок действия)</label>
            <input className="form-control" type="date" value={form.expires} onChange={(e) => set("expires", e.target.value)} />
            <div className="admin-hint">Пусто — бессрочно.</div>
          </div>
          <div className="field-stack">
            <label className="field-label">Фамилия</label>
            <input className="form-control" value={form.lastName} placeholder="Аточкин" onChange={(e) => set("lastName", e.target.value)} />
          </div>
          <div className="field-stack">
            <label className="field-label">Имя</label>
            <input className="form-control" value={form.firstName} placeholder="Александр" onChange={(e) => set("firstName", e.target.value)} />
          </div>
          <div className="field-stack">
            <label className="field-label">Отчество</label>
            <input className="form-control" value={form.middleName} placeholder="Анатольевич" onChange={(e) => set("middleName", e.target.value)} />
          </div>
          <div className="field-stack">
            <label className="field-label">Отображаемое имя</label>
            <input className="form-control" value={form.displayName} placeholder={autoDisplay || "Аточкин Александр Анатольевич"} onChange={(e) => setDisplayName(e.target.value)} />
            <div className="admin-hint">Фамилия, имя и отчество подставятся автоматически.</div>
          </div>
          <div className="field-stack">
            <label className="field-label">Пароль</label>
            <PasswordGenField
              value={form.password}
              onChange={(v) => set("password", v)}
              placeholder="Начальный пароль"
            />
            <div className="admin-hint">Нужен LDAPS/StartTLS — иначе учётка создастся отключённой.</div>
          </div>
          <div className="field-stack admin-col-full">
            <label className="field-label">Подразделение (OU)</label>
            <OuPicker ous={ous} value={form.ou} onChange={(v) => set("ou", v)} allowDefault />
          </div>
          <div className="field-stack admin-col-full">
            <label className="field-label">Группы</label>
            <GroupTemplates value={groups} onChange={setGroups} pushToast={pushToast} />
            <GroupPicker value={groups} onChange={setGroups} />
          </div>
          <div className="field-stack admin-col-full">
            <label style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
              <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
              Учётная запись включена
            </label>
          </div>
        </div>

        <div className="search-toolbar" style={{ marginTop: 18 }}>
          <div />
          <div className="search-toolbar-actions">
            <button type="button" className="btn btn-outline-secondary" onClick={() => { setForm(blank); setGroups([]); }}>
              <i className="bi bi-eraser me-2" />Очистить
            </button>
            <button type="submit" className="btn btn-primary px-4">
              <i className="bi bi-person-plus me-2" />Создать пользователя
            </button>
          </div>
        </div>
      </form>
      <ResultReport summary={summary} />
    </>
  );
}

/* ----------------------------------------------------- Avanpost FAM */

// Визуальные признаки состояния учётки в Avanpost FAM.
const FAM_VIEW = {
  present: { tone: "ok", icon: "bi-check-circle-fill", title: "Учётка есть в Avanpost" },
  inactive: { tone: "warn", icon: "bi-pause-circle-fill", title: "Есть в Avanpost, но неактивна" },
  missing: { tone: "err", icon: "bi-x-circle-fill", title: "Не попала в Avanpost" },
  error: { tone: "err", icon: "bi-exclamation-triangle-fill", title: "Не удалось проверить Avanpost" },
  not_configured: { tone: "muted", icon: "bi-slash-circle", title: "Проверка Avanpost не настроена" },
};

function FamStatusCard({ status, loading, onSync, syncing, canWrite }) {
  const view = FAM_VIEW[status?.state] || FAM_VIEW.error;
  const reason = status?.detail || status?.summary || "";
  const busy = loading || syncing;

  return (
    <section className={`fam-card fam-${view.tone}`}>
      <div className="fam-card-main">
        <i className={`bi ${loading ? "bi-arrow-repeat fam-spin" : view.icon} fam-card-icon`} />
        <div className="fam-card-text">
          <div className="fam-card-title">
            Avanpost:{" "}
            {loading ? "проверяю…" : view.title}
          </div>
          {!loading && reason ? <div className="fam-card-reason">{reason}</div> : null}
          {!loading && status?.full_name ? (
            <div className="fam-card-sub">
              {status.full_name}
              {status.email ? ` · ${status.email}` : ""}
            </div>
          ) : null}
        </div>
      </div>
      {canWrite ? (
        <button
          type="button"
          className="btn btn-outline-secondary btn-sm fam-card-sync"
          onClick={onSync}
          disabled={busy}
          title="Синхронизировать с Avanpost"
        >
          <i className={`bi ${syncing ? "bi-arrow-repeat fam-spin" : "bi-arrow-repeat"} me-1`} />
          {syncing ? "Синхронизирую…" : "Синхронизировать"}
        </button>
      ) : null}
    </section>
  );
}

/* -------------------------------------------------------------- edit */

function EditForm({ ous, pushToast, setLoading, canWrite = true }) {
  const [login, setLogin] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [form, setForm] = useState(null);
  const [groups, setGroups] = useState([]);
  const [enabledChange, setEnabledChange] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [expiresNever, setExpiresNever] = useState(false);
  const [summary, setSummary] = useState(null);

  // Статус учётки в Avanpost FAM.
  const [fam, setFam] = useState(null);
  const [famLoading, setFamLoading] = useState(false);
  const [famSyncing, setFamSyncing] = useState(false);

  async function loadFam(loginArg) {
    const target = String(loginArg || "").trim();
    if (!target) return;
    setFamLoading(true);
    try {
      const data = await getFamStatus(target);
      setFam(data.status || null);
    } catch (err) {
      setFam({
        state: "error",
        summary: "Не удалось проверить пользователя в Avanpost.",
        detail: err.message || String(err),
      });
    } finally {
      setFamLoading(false);
    }
  }

  async function onSyncFam() {
    const target = String(form?.login || login || "").trim();
    if (!target) return;
    setFamSyncing(true);
    try {
      const data = await syncFam(target);
      setFam(data.status || null);
      const st = data.status || {};
      pushToast(
        st.summary || "Синхронизация выполнена",
        st.state === "present" ? "success" : st.state === "error" ? "danger" : "warning",
        "Avanpost"
      );
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Avanpost");
    } finally {
      setFamSyncing(false);
    }
  }

  // Единый поиск: логин / фамилия / ФИО с живыми подсказками (как в Анализаторе).
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [searching, setSearching] = useState(false);
  const [open, setOpen] = useState(false);
  const hostRef = useRef(null);
  const timerRef = useRef(null);

  function set(field, val) {
    setForm((cur) => ({ ...cur, [field]: val }));
  }

  // Живой поиск с дебаунсом: подсказки появляются прямо при печати.
  useEffect(() => {
    const q = query.trim();
    if (timerRef.current) window.clearTimeout(timerRef.current);
    if (q.length < 2) {
      setSuggestions([]);
      setOpen(false);
      return undefined;
    }
    timerRef.current = window.setTimeout(async () => {
      setSearching(true);
      try {
        const data = await searchAdUsers(q);
        const users = Array.isArray(data?.users) ? data.users : [];
        setSuggestions(users);
        setOpen(true);
      } catch {
        setSuggestions([]);
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => timerRef.current && window.clearTimeout(timerRef.current);
  }, [query]);

  // Закрытие выпадашки по клику вне поля поиска.
  useEffect(() => {
    function onDocClick(e) {
      if (!hostRef.current?.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  function pickSuggestion(user) {
    if (timerRef.current) window.clearTimeout(timerRef.current);
    setOpen(false);
    setSuggestions([]);
    setQuery("");
    setLogin(user.login || "");
    load(null, user.login || "");
  }

  async function load(e, loginArg) {
    e?.preventDefault();
    const target = String(loginArg ?? login).trim();
    if (!target) {
      pushToast("Укажите логин", "warning", "Загрузка");
      return;
    }
    setLogin(target);
    setLoading(true);
    setSummary(null);
    try {
      const data = await getAdUserDetail(target);
      setForm({
        login: data.login,
        firstName: data.firstName || "",
        lastName: data.lastName || "",
        displayName: reorderDisplayName(
          data.displayName,
          data.lastName,
          data.firstName
        ),
        email: data.email || "",
        employeeNumber: data.employeeNumber || "",
        ou: data.ou || "",
        enabled: data.enabled,
        expires: data.accountExpires || "",
        whenCreated: data.whenCreated || "",
      });
      setGroups((data.groups || []).map((g) => ({ name: g.name, dn: g.dn })));
      setEnabledChange("");
      setNewPassword("");
      setExpiresNever(!data.accountExpires);
      setLoaded(true);
      setFam(null);
      loadFam(data.login);
    } catch (err) {
      setLoaded(false);
      setForm(null);
      setFam(null);
      pushToast(err.message || String(err), "danger", "Загрузка");
    } finally {
      setLoading(false);
    }
  }

  async function submit(e) {
    e.preventDefault();
    setLoading(true);
    try {
      const payload = {
        login: form.login,
        firstName: form.firstName,
        lastName: form.lastName,
        displayName: form.displayName,
        email: form.email,
        employeeNumber: form.employeeNumber,
        ou: form.ou,
        setGroups: groups.map((g) => g.dn || g.name),
        newPassword,
      };
      if (enabledChange === "true") payload.enabled = true;
      if (enabledChange === "false") payload.enabled = false;
      if (expiresNever) payload.accountExpires = "never";
      else if (form.expires) payload.accountExpires = form.expires;

      const data = await updateAdUser(payload);
      const r = data.result;
      setSummary({ processed: 1, succeeded: r.success ? 1 : 0, failed: r.success ? 0 : 1, results: [r] });
      pushToast(r.detail || (r.success ? "Сохранено" : "Ошибка"), r.success ? "success" : "danger", form.login);
      if (r.success) setNewPassword("");
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Сохранение");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <form
        className="surface surface-pad"
        onSubmit={(e) => {
          e.preventDefault();
          if (suggestions.length) pickSuggestion(suggestions[0]);
          else load(null, query);
        }}
      >
        <div className="search-toolbar" style={{ marginTop: 0 }}>
          <div
            className="field-stack"
            style={{ flex: 1, minWidth: 240, position: "relative" }}
            ref={hostRef}
          >
            <label className="field-label">
              Поиск пользователя (логин, фамилия или ФИО)
            </label>
            <div className="field-control-wrap">
              <i className="bi bi-search field-icon" aria-hidden="true" />
              <input
                className="form-control"
                value={query}
                autoComplete="off"
                placeholder="Иванов · Иванов Иван Иванович · ivanov"
                onChange={(e) => setQuery(e.target.value)}
                onFocus={() => suggestions.length && setOpen(true)}
              />
            </div>
            {open && query.trim().length >= 2 ? (
              <div className="group-suggest">
                {searching ? (
                  <div className="result-muted" style={{ padding: "8px 10px" }}>
                    Ищу…
                  </div>
                ) : suggestions.length ? (
                  suggestions.map((u) => (
                    <button
                      type="button"
                      key={u.login}
                      className="group-suggest-item"
                      onClick={() => pickSuggestion(u)}
                    >
                      <span className="group-suggest-main">
                        {u.displayName || u.login}
                      </span>
                      <span className="group-suggest-sub">
                        {u.login}
                        {u.mail ? ` · ${u.mail}` : ""}
                      </span>
                    </button>
                  ))
                ) : (
                  <div className="result-muted" style={{ padding: "8px 10px" }}>
                    Ничего не найдено
                  </div>
                )}
              </div>
            ) : null}
          </div>
          <div className="search-toolbar-actions" style={{ alignSelf: "flex-end" }}>
            <button type="submit" className="btn btn-primary" disabled={searching}>
              <i className="bi bi-download me-2" />
              {searching ? "Ищу..." : "Загрузить"}
            </button>
          </div>
        </div>
      </form>

      {loaded && form ? (
        <FamStatusCard
          status={fam}
          loading={famLoading}
          syncing={famSyncing}
          onSync={onSyncFam}
          canWrite={canWrite}
        />
      ) : null}

      {loaded && form ? (
        <form className="surface surface-pad" style={{ marginTop: 16 }} onSubmit={submit}>
          <fieldset
            disabled={!canWrite}
            className={canWrite ? "" : "is-readonly"}
            style={{ border: "none", padding: 0, margin: 0, minWidth: 0 }}
          >
          <div className="admin-grid">
            <div className="field-stack">
              <label className="field-label">Логин</label>
              <input className="form-control" value={form.login} disabled />
            </div>
            <div className="field-stack">
              <label className="field-label">Email</label>
              <input className="form-control" value={form.email} onChange={(e) => set("email", e.target.value)} />
            </div>
            <div className="field-stack">
              <label className="field-label">Табельный номер (employeeNumber)</label>
              <input className="form-control" value={form.employeeNumber} onChange={(e) => set("employeeNumber", e.target.value)} />
            </div>
            <div className="field-stack">
              <label className="field-label">Фамилия</label>
              <input className="form-control" value={form.lastName} onChange={(e) => set("lastName", e.target.value)} />
            </div>
            <div className="field-stack">
              <label className="field-label">Имя</label>
              <input className="form-control" value={form.firstName} onChange={(e) => set("firstName", e.target.value)} />
            </div>
            <div className="field-stack">
              <label className="field-label">Отображаемое имя</label>
              <input className="form-control" value={form.displayName} onChange={(e) => set("displayName", e.target.value)} />
            </div>
            <div className="field-stack">
              <label className="field-label">Новый пароль</label>
              <PasswordGenField
                value={newPassword}
                onChange={setNewPassword}
                placeholder="Оставьте пустым, чтобы не менять"
              />
              <div className="admin-hint">Смена пароля требует LDAPS/StartTLS.</div>
            </div>
            <div className="field-stack admin-col-full">
              <label className="field-label">Подразделение (OU) — перенос при изменении</label>
              <OuPicker ous={ous} value={form.ou} onChange={(v) => set("ou", v)} />
            </div>
            <div className="field-stack admin-col-full">
              <label className="field-label">Группы (итоговый набор)</label>
              {canWrite ? (
                <GroupTemplates value={groups} onChange={setGroups} pushToast={pushToast} />
              ) : null}
              <GroupPicker value={groups} onChange={setGroups} />
              <div className="admin-hint">Добавленные группы назначатся, убранные — снимутся.</div>
            </div>
            <div className="field-stack">
              <label className="field-label">Статус учётной записи</label>
              <select className="form-select" value={enabledChange} onChange={(e) => setEnabledChange(e.target.value)}>
                <option value="">Не менять (сейчас: {form.enabled ? "включена" : "отключена"})</option>
                <option value="true">Включить</option>
                <option value="false">Отключить</option>
              </select>
            </div>
            <div className="field-stack">
              <label className="field-label">Активна до (срок действия)</label>
              <input
                className="form-control"
                type="date"
                value={form.expires || ""}
                disabled={expiresNever}
                onChange={(e) => set("expires", e.target.value)}
              />
              <label style={{ display: "inline-flex", alignItems: "center", gap: 6, marginTop: 6, cursor: "pointer" }}>
                <input type="checkbox" checked={expiresNever} onChange={(e) => setExpiresNever(e.target.checked)} />
                Бессрочно
              </label>
            </div>
            <div className="field-stack">
              <label className="field-label">Создана (дата добавления)</label>
              <input className="form-control" value={form.whenCreated || "—"} disabled />
            </div>
          </div>

          </fieldset>

          {canWrite ? (
            <div className="search-toolbar" style={{ marginTop: 18 }}>
              <div />
              <div className="search-toolbar-actions">
                <button type="submit" className="btn btn-primary px-4">
                  <i className="bi bi-save me-2" />Сохранить изменения
                </button>
              </div>
            </div>
          ) : null}
        </form>
      ) : null}

      <ResultReport summary={summary} />
    </>
  );
}

/* -------------------------------------------------------------- bulk */

function BulkPanel({ ous, pushToast, setLoading }) {
  const [bulkMode, setBulkMode] = useState("create");
  const [rows, setRows] = useState(() => [emptyRow("create")]);
  const [summary, setSummary] = useState(null);
  const [importInfo, setImportInfo] = useState(null);
  const fileRef = useRef(null);

  // Модалка выбора групп/OU для конкретной строки таблицы.
  const [allGroups, setAllGroups] = useState([]);
  const [groupsLoading, setGroupsLoading] = useState(false);
  const [picker, setPicker] = useState({ open: false, type: null, rowId: null });

  // Загрузка пользователей из OU (режим редактирования).
  const [loadOuOpen, setLoadOuOpen] = useState(false);
  const [ouUsers, setOuUsers] = useState([]);
  const [ouUsersOpen, setOuUsersOpen] = useState(false);
  const [ouUsersLoading, setOuUsersLoading] = useState(false);
  const [ouLoadName, setOuLoadName] = useState("");

  async function onPickLoadOu(chosen) {
    setLoadOuOpen(false);
    const ou = chosen[0];
    if (!ou) return;
    setOuLoadName(ou.name);
    setOuUsers([]);
    setOuUsersLoading(true);
    setOuUsersOpen(true);
    try {
      const data = await getAdOuUsers(ou.dn);
      setOuUsers(Array.isArray(data?.users) ? data.users : []);
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Загрузка из OU");
      setOuUsers([]);
    } finally {
      setOuUsersLoading(false);
    }
  }

  function onPickUsers(chosen) {
    setOuUsersOpen(false);
    if (!chosen.length) return;
    const newRows = chosen.map((u) =>
      emptyRow("edit", {
        login: u.login,
        firstName: u.firstName,
        lastName: u.lastName,
        displayName: reorderDisplayName(u.displayName, u.lastName, u.firstName),
        email: u.email,
        employeeNumber: u.employeeNumber,
        ou: u.ou,
        groups: (u.groups || []).join("|"),
        enabled: u.enabled ? "1" : "0",
        expires: u.accountExpires || "",
        created: u.whenCreated || "",
      })
    );
    const disabled = chosen.filter((u) => !u.enabled).length;
    const noPwd = chosen.filter((u) => u.passwordSet === false).length;

    setRows(newRows);
    setSummary(null);
    setImportInfo({ count: newRows.length, generated: 0, file: `OU: ${ouLoadName}`, disabled, noPwd });
    pushToast(`Загружено пользователей: ${newRows.length}`, "success", "Загрузка из OU");
    if (disabled || noPwd) {
      pushToast(
        `Проблемные учётки: отключено ${disabled}, без пароля ${noPwd}`,
        "warning",
        "Диагностика"
      );
    }
  }

  async function ensureGroups() {
    if (allGroups.length > 0) return;
    setGroupsLoading(true);
    try {
      const data = await getAdGroupsList("", 5000);
      setAllGroups(Array.isArray(data?.groups) ? data.groups : []);
    } catch {
      setAllGroups([]);
    } finally {
      setGroupsLoading(false);
    }
  }

  function openGroupPicker(rowId) {
    setPicker({ open: true, type: "groups", rowId });
    ensureGroups();
  }
  function openOuPicker(rowId) {
    setPicker({ open: true, type: "ou", rowId });
  }
  function closePicker() {
    setPicker({ open: false, type: null, rowId: null });
  }

  const pickerRow = rows.find((r) => r._id === picker.rowId) || null;

  const pickerSelectedKeys = useMemo(() => {
    if (!picker.open || !pickerRow) return [];
    if (picker.type === "ou") {
      return pickerRow.ou && ous.some((o) => o.dn === pickerRow.ou) ? [pickerRow.ou] : [];
    }
    const names = splitGroups(pickerRow.groups);
    const keys = [];
    names.forEach((nm) => {
      const m = allGroups.find((g) => g.name.toLowerCase() === nm.toLowerCase());
      if (m) keys.push(m.dn);
    });
    return keys;
  }, [picker, pickerRow, ous, allGroups]);

  function confirmPicker(chosen) {
    if (!pickerRow) {
      closePicker();
      return;
    }
    if (picker.type === "ou") {
      setCell(pickerRow._id, "ou", chosen[0]?.dn || "");
    } else {
      const chosenNames = chosen.map((g) => g.name);
      const chosenLower = new Set(chosenNames.map((n) => n.toLowerCase()));
      const preserved = splitGroups(pickerRow.groups).filter(
        (nm) =>
          !chosenLower.has(nm.toLowerCase()) &&
          !allGroups.some((g) => g.name.toLowerCase() === nm.toLowerCase())
      );
      setCell(pickerRow._id, "groups", [...chosenNames, ...preserved].join("|"));
    }
    closePicker();
  }

  function switchMode(mode) {
    setBulkMode(mode);
    setRows([emptyRow(mode)]);
    setSummary(null);
    setImportInfo(null);
  }

  // Заполняет пустые логины автогенерацией из ФИО.
  function fillEmptyLogins() {
    const next = rows.map((r) => {
      if (!r.login.trim()) {
        const login = generateLogin(r);
        if (login) return { ...r, login };
      }
      return r;
    });
    const generated = next.filter((r, i) => r.login !== rows[i].login).length;
    setRows(next);
    pushToast(
      generated
        ? `Сгенерировано логинов: ${generated}`
        : "Нет строк для генерации (нужна хотя бы фамилия)",
      generated ? "success" : "info",
      "Логины"
    );
  }

  function setCell(id, field, val) {
    setRows((cur) => cur.map((r) => (r._id === id ? { ...r, [field]: val } : r)));
  }
  function addRow() {
    setRows((cur) => [...cur, emptyRow(bulkMode)]);
  }
  function removeRow(id) {
    setRows((cur) => (cur.length > 1 ? cur.filter((r) => r._id !== id) : cur));
  }

  async function onFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const name = (file.name || "").toLowerCase();
    try {
      let parsed;
      if (name.endsWith(".xlsx") || name.endsWith(".xls")) {
        const buf = await file.arrayBuffer();
        parsed = await xlsxToRows(buf, bulkMode);
      } else {
        const text = await file.text();
        parsed = csvToRows(text, bulkMode);
      }

      if (!parsed.length) {
        pushToast("В файле не найдено строк", "warning", "Импорт");
        return;
      }

      // Автогенерация логинов для пустых (только при создании).
      let generated = 0;
      if (bulkMode === "create") {
        parsed = parsed.map((r) => {
          if (!r.login.trim()) {
            const login = generateLogin(r);
            if (login) {
              generated += 1;
              return { ...r, login };
            }
          }
          return r;
        });
      }

      setRows(parsed);
      setSummary(null);
      setImportInfo({ count: parsed.length, generated, file: file.name });
      pushToast(
        `Загружено строк: ${parsed.length}` +
          (generated ? `, сгенерировано логинов: ${generated}` : ""),
        "success",
        "Импорт"
      );
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Импорт");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  function downloadTemplate() {
    const header =
      bulkMode === "create"
        ? "login,lastName,firstName,middleName,displayName,email,employeeNumber,password,ou,groups,enabled,expires"
        : "login,lastName,firstName,displayName,email,employeeNumber,password,ou,groups,enabled,expires";
    const sample =
      bulkMode === "create"
        ? ',Аточкин,Александр,Анатольевич,,a.a.atochkin@staff.local,123456,P@ssw0rd!,,Группа1|Группа2,1,31.08.2026'
        : 'a.a.atochkin,Аточкин,Александр,,a.a.atochkin@staff.local,123456,B6U0dfNmzn0h9xI,,,1,31.08.2026';
    const blob = new Blob(["﻿" + header + "\n" + sample + "\n"], { type: "text/csv;charset=utf-8;" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `ad_${bulkMode}_template.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function fullDisplayName(row) {
    // Всегда «Фамилия Имя Отчество», если есть ФИО.
    return composeDisplayName(
      row.lastName,
      row.firstName,
      row.middleName,
      row.displayName
    );
  }

  function buildPayload(row) {
    const enabled = parseBool(row.enabled);
    if (bulkMode === "create") {
      return {
        login: row.login.trim(),
        firstName: row.firstName.trim(),
        lastName: row.lastName.trim(),
        displayName: fullDisplayName(row),
        email: row.email.trim(),
        employeeNumber: row.employeeNumber.trim(),
        accountExpires: (row.expires || "").trim(),
        password: row.password || "",
        ou: row.ou.trim(),
        groups: splitGroups(row.groups),
        enabled: enabled === null ? true : enabled,
      };
    }
    // Редактирование: меняем только заполненные ячейки, пустые не трогают
    // атрибуты и не снимают группы.
    const payload = { login: row.login.trim() };
    if (row.firstName.trim()) payload.firstName = row.firstName.trim();
    if (row.lastName.trim()) payload.lastName = row.lastName.trim();
    if (row.displayName.trim()) payload.displayName = row.displayName.trim();
    if (row.email.trim()) payload.email = row.email.trim();
    if (row.employeeNumber.trim()) payload.employeeNumber = row.employeeNumber.trim();
    if (row.password && row.password.trim()) payload.newPassword = row.password.trim();
    if (row.expires && row.expires.trim()) payload.accountExpires = row.expires.trim();
    if (row.ou.trim()) payload.ou = row.ou.trim();
    if (row.groups.trim()) payload.setGroups = splitGroups(row.groups);
    if (enabled !== null) payload.enabled = enabled;
    return payload;
  }

  async function submit() {
    const valid = rows.filter((r) => r.login.trim());
    if (!valid.length) {
      pushToast("Заполните хотя бы один логин", "warning", "Массовая операция");
      return;
    }
    setLoading(true);
    setSummary(null);
    try {
      const payloads = valid.map(buildPayload);
      const data =
        bulkMode === "create"
          ? await bulkCreateAdUsers(payloads)
          : await bulkUpdateAdUsers(payloads);
      setSummary(data);
      pushToast(
        `Готово: успешно ${data.succeeded} из ${data.processed}`,
        data.failed ? "warning" : "success",
        "Массовая операция"
      );
    } catch (err) {
      pushToast(err.message || String(err), "danger", "Массовая операция");
    } finally {
      setLoading(false);
    }
  }

  const cols =
    bulkMode === "create"
      ? ["login", "lastName", "firstName", "middleName", "displayName", "email", "employeeNumber", "password", "ou", "groups", "enabled", "expires"]
      : ["login", "lastName", "firstName", "displayName", "email", "employeeNumber", "password", "ou", "groups", "enabled", "expires", "created"];

  const COL_LABEL = {
    login: "Логин *",
    lastName: "Фамилия",
    firstName: "Имя",
    middleName: "Отчество",
    displayName: "Отобр. имя",
    email: "Email",
    employeeNumber: "Таб. №",
    password: bulkMode === "create" ? "Пароль" : "Новый пароль",
    ou: "OU",
    groups: "Группы (|)",
    enabled: "Вкл",
    expires: "Активна до",
    created: "Создан",
  };

  return (
    <>
      <section className="surface surface-pad">
        <div className="admin-modes" style={{ marginBottom: 14 }}>
          <button type="button" className={`admin-mode-btn${bulkMode === "create" ? " is-active" : ""}`} onClick={() => switchMode("create")}>
            <i className="bi bi-person-plus" />Создание
          </button>
          <button type="button" className={`admin-mode-btn${bulkMode === "edit" ? " is-active" : ""}`} onClick={() => switchMode("edit")}>
            <i className="bi bi-pencil-square" />Редактирование
          </button>
        </div>

        <div className="admin-banner">
          <i className="bi bi-info-circle" />
          <span>
            Заполните таблицу или импортируйте <b>Excel/CSV</b>. Группы — через{" "}
            <code>|</code>, пустой логин сгенерируется из ФИО.
          </span>
        </div>

        <div className="bulk-toolbar">
          <button type="button" className="btn btn-outline-secondary btn-sm" onClick={addRow}>
            <i className="bi bi-plus-lg me-1" />Добавить строку
          </button>
          <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => fileRef.current?.click()}>
            <i className="bi bi-upload me-1" />Импорт XLSX / CSV
          </button>
          {bulkMode === "create" ? (
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={fillEmptyLogins}>
              <i className="bi bi-magic me-1" />Сгенерировать логины
            </button>
          ) : null}
          {bulkMode === "edit" ? (
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setLoadOuOpen(true)}>
              <i className="bi bi-folder2-open me-1" />Загрузить из OU
            </button>
          ) : null}
          <button type="button" className="btn btn-outline-secondary btn-sm" onClick={downloadTemplate}>
            <i className="bi bi-download me-1" />Шаблон CSV
          </button>
          <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls,text/csv" hidden onChange={onFile} />
          <span className="status-pill">
            <i className="bi bi-list-ol" />Строк: {rows.length}
          </span>
        </div>

        {importInfo ? (
          <div className="admin-banner" style={{ background: "rgba(37,99,235,0.1)", color: "#1e3a8a" }}>
            <i className="bi bi-eye" />
            <span>
              Предпросмотр из «{importInfo.file}»: загружено <b>{importInfo.count}</b> записей
              {importInfo.generated ? <>, сгенерировано логинов: <b>{importInfo.generated}</b></> : null}.
              {importInfo.disabled || importInfo.noPwd ? (
                <>
                  {" "}
                  <span style={{ color: "#b45309" }}>
                    Проблемные: отключено <b>{importInfo.disabled || 0}</b>, без пароля <b>{importInfo.noPwd || 0}</b>.
                  </span>
                </>
              ) : null}
            </span>
          </div>
        ) : null}

        <div className="bulk-table-wrap">
          <table className="bulk-table">
            <thead>
              <tr>
                {cols.map((c) => (
                  <th key={c}>{COL_LABEL[c]}</th>
                ))}
                <th style={{ width: 36 }} />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row._id}>
                  {cols.map((c) => (
                    <td key={c}>
                      {c === "ou" ? (
                        <div className="cell-with-btn">
                          <button
                            type="button"
                            className={`cell-ou-display${row[c] ? "" : " is-empty"}`}
                            title={row[c] ? ouLabel(row[c], ous) : "Выбрать OU"}
                            onClick={() => openOuPicker(row._id)}
                          >
                            {row[c] ? ouLabel(row[c], ous) : "— выбрать —"}
                          </button>
                          {row[c] ? (
                            <button
                              type="button"
                              className="cell-pick-btn"
                              title="Сбросить"
                              onClick={() => setCell(row._id, c, "")}
                            >
                              <i className="bi bi-x" />
                            </button>
                          ) : null}
                        </div>
                      ) : c === "groups" ? (
                        <div className="cell-with-btn">
                          <input
                            value={row[c]}
                            placeholder="Группа1|Группа2"
                            onChange={(e) => setCell(row._id, c, e.target.value)}
                          />
                          <button
                            type="button"
                            className="cell-pick-btn"
                            title="Выбрать группы из списка"
                            onClick={() => openGroupPicker(row._id)}
                          >
                            <i className="bi bi-list-check" />
                          </button>
                        </div>
                      ) : c === "enabled" ? (
                        <select value={row[c]} onChange={(e) => setCell(row._id, c, e.target.value)}>
                          <option value="">—</option>
                          <option value="1">1</option>
                          <option value="0">0</option>
                        </select>
                      ) : c === "expires" ? (
                        <input
                          type="date"
                          value={row[c]}
                          title="Активна до (включительно). Пусто — бессрочно."
                          onChange={(e) => setCell(row._id, c, e.target.value)}
                        />
                      ) : c === "created" ? (
                        <span className="cell-readonly">{row[c] || "—"}</span>
                      ) : (
                        <input
                          value={row[c]}
                          type="text"
                          onChange={(e) => setCell(row._id, c, e.target.value)}
                        />
                      )}
                    </td>
                  ))}
                  <td>
                    <button type="button" className="bulk-row-del" title="Удалить строку" onClick={() => removeRow(row._id)}>
                      <i className="bi bi-trash" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>


        <div className="search-toolbar" style={{ marginTop: 16 }}>
          <div className="search-toolbar-info">
            <span className="status-pill"><i className="bi bi-shield-lock" />Операции выполняются построчно</span>
          </div>
          <div className="search-toolbar-actions">
            <button type="button" className="btn btn-primary px-4" onClick={submit}>
              <i className={`bi ${bulkMode === "create" ? "bi-people-fill" : "bi-pencil-fill"} me-2`} />
              {bulkMode === "create" ? "Создать всех" : "Применить изменения"}
            </button>
          </div>
        </div>
      </section>

      <PickerModal
        open={picker.open}
        title={picker.type === "ou" ? "Выбор подразделения (OU)" : "Выбор групп"}
        loading={picker.type === "groups" && groupsLoading}
        items={picker.type === "ou" ? ous : allGroups}
        keyOf={(it) => it.dn}
        labelOf={(it) => it.name}
        selectedKeys={pickerSelectedKeys}
        multi={picker.type === "groups"}
        searchPlaceholder="Поиск по имени или DN…"
        confirmLabel={picker.type === "ou" ? "Выбрать OU" : "Применить выбор"}
        onClose={closePicker}
        onConfirm={confirmPicker}
      />

      {/* Шаг 1: выбор OU для загрузки пользователей (режим редактирования) */}
      <PickerModal
        open={loadOuOpen}
        title="Выберите OU для загрузки пользователей"
        subtitle={`Доступно OU: ${ous.length}`}
        items={ous}
        keyOf={(ou) => ou.dn}
        labelOf={(ou) => ou.name}
        selectedKeys={[]}
        multi={false}
        searchPlaceholder="Поиск OU по названию…"
        confirmLabel="Показать пользователей"
        onClose={() => setLoadOuOpen(false)}
        onConfirm={onPickLoadOu}
      />

      {/* Шаг 2: выбор пользователей из OU */}
      <PickerModal
        open={ouUsersOpen}
        title={`Пользователи OU: ${ouLoadName}`}
        loading={ouUsersLoading}
        items={ouUsers}
        keyOf={(u) => u.login}
        labelOf={(u) => u.displayName || u.login}
        subOf={(u) => u.login}
        selectedKeys={[]}
        multi
        searchPlaceholder="Поиск по ФИО или логину…"
        confirmLabel="Загрузить выбранных"
        onClose={() => setOuUsersOpen(false)}
        onConfirm={onPickUsers}
      />

      <ResultReport summary={summary} />
    </>
  );
}

/* ------------------------------------------------------------- page */

export default function UserAdminPage({ user }) {
  const canWrite = hasPerm(user, "ad_write");
  const modes = canWrite ? MODES : MODES.filter((m) => !m.write);
  const [mode, setMode] = useState(canWrite ? "create" : "edit");
  const [ous, setOus] = useState([]);
  const [loading, setLoading] = useState(false);
  const { toasts, pushToast, dismiss } = useToasts();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getAdOus("");
        if (!cancelled) setOus(Array.isArray(data?.ous) ? data.ous : []);
      } catch {
        if (!cancelled) setOus([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <LoadingOverlay visible={loading} text="Обращаюсь к Active Directory…" />
      <ToastStack items={toasts} onDismiss={dismiss} />

      <PageHero
        icon="bi-person-badge"
        title="Управление пользователями AD"
        subtitle={
          canWrite
            ? "Учётные записи, группы и подразделения — по одной или массово."
            : "Просмотр учётных записей, групп и подразделений."
        }
        eyebrow="Active Directory"
        chips={
          canWrite
            ? [
                { label: "Создание и правка", icon: "bi-person-gear", tone: "primary" },
                { label: "Группы и OU", icon: "bi-diagram-3" },
                { label: "Массовые операции + CSV", icon: "bi-filetype-csv" },
              ]
            : [{ label: "Только просмотр", icon: "bi-eye", tone: "primary" }]
        }
      />

      {!canWrite ? (
        <div className="admin-banner" style={{ marginBottom: 12 }}>
          <i className="bi bi-eye" />
          <span>
            Ваша роль позволяет только просматривать данные AD. Изменение
            учётных записей недоступно.
          </span>
        </div>
      ) : null}

      {modes.length > 1 ? (
        <div className="admin-modes">
          {modes.map((m) => (
            <button
              key={m.key}
              type="button"
              className={`admin-mode-btn${mode === m.key ? " is-active" : ""}`}
              onClick={() => setMode(m.key)}
            >
              <i className={`bi ${m.icon}`} />
              {m.label}
            </button>
          ))}
        </div>
      ) : null}

      {mode === "create" && canWrite ? <CreateForm ous={ous} pushToast={pushToast} setLoading={setLoading} /> : null}
      {mode === "edit" ? <EditForm ous={ous} pushToast={pushToast} setLoading={setLoading} canWrite={canWrite} /> : null}
      {mode === "bulk" && canWrite ? <BulkPanel ous={ous} pushToast={pushToast} setLoading={setLoading} /> : null}
    </>
  );
}
