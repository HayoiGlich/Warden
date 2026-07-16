import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import HomePage from "./pages/HomePage";
import WinlogPage from "./pages/WinlogPage";
import UserAdminPage from "./pages/UserAdminPage";
import ServicesPage from "./pages/ServicesPage";
import YandexReportPage from "./pages/YandexReportPage";
import SettingsPage from "./pages/SettingsPage";
import LoginPage from "./pages/LoginPage";
import { hasPerm } from "./lib/perms";
import { getMe, getSystemInfo, logout as apiLogout } from "./api";

function deriveStatus(system) {
  if (!system) {
    return { tone: "offline", label: "Сервис недоступен", title: "Не удалось получить системную сводку" };
  }
  const ad = String(system.active_directory || "").toLowerCase();
  const adOk = ["available", "connected", "ok"].includes(ad);
  const host = system?.system_info?.hostname || "host";
  return {
    tone: adOk ? "online" : "offline",
    label: adOk ? "AD на связи" : "AD недоступен",
    title: `${host} · AD ${adOk ? "подключен" : ad || "нет данных"}`
  };
}

export default function App() {
  const [auth, setAuth] = useState({ checking: true, authed: false, user: null, mustChange: false, oidc: null });
  const [status, setStatus] = useState({ tone: "", label: "Подключение..." });

  const refreshAuth = useCallback(async () => {
    try {
      const me = await getMe();
      setAuth({
        checking: false,
        authed: Boolean(me?.authenticated),
        user: me?.user || null,
        mustChange: Boolean(me?.must_change_password),
        oidc: me?.oidc_enabled ? { label: me?.oidc_label || "Войти через Avanpost" } : null
      });
    } catch {
      setAuth({ checking: false, authed: false, user: null, mustChange: false, oidc: null });
    }
  }, []);

  useEffect(() => {
    refreshAuth();
  }, [refreshAuth]);

  // Сессия истекла в фоне (любой запрос вернул 401) — просим войти заново.
  useEffect(() => {
    function onExpired() {
      setAuth((current) => ({ ...current, authed: false }));
    }
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, []);

  useEffect(() => {
    if (!auth.authed) return undefined;
    let cancelled = false;
    async function load() {
      try {
        const data = await getSystemInfo();
        if (!cancelled) setStatus(deriveStatus(data));
      } catch {
        if (!cancelled) setStatus(deriveStatus(null));
      }
    }
    load();
    const timer = window.setInterval(load, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [auth.authed]);

  const handleLogout = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      /* ignore */
    }
    refreshAuth();
  }, [refreshAuth]);

  if (auth.checking) {
    return (
      <div className="auth-screen">
        <span className="loading-spinner" role="status" aria-label="Загрузка" />
      </div>
    );
  }

  if (!auth.authed || auth.mustChange) {
    return (
      <LoginPage
        initialStep={auth.authed && auth.mustChange ? "change" : "login"}
        onAuthenticated={refreshAuth}
        oidc={auth.oidc}
      />
    );
  }

  return (
    <AppShell status={status} user={auth.user} onLogout={handleLogout}>
      <div className="container">
        <Routes>
          <Route path="/" element={<HomePage user={auth.user} />} />
          <Route path="/winlog" element={<WinlogPage />} />
          <Route path="/ad-users" element={<UserAdminPage user={auth.user} />} />
          <Route path="/services" element={<ServicesPage user={auth.user} />} />
          {hasPerm(auth.user, "settings") ? (
            <Route path="/yc-report" element={<YandexReportPage />} />
          ) : null}
          {hasPerm(auth.user, "settings") ? (
            <Route path="/settings" element={<SettingsPage />} />
          ) : null}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </AppShell>
  );
}
