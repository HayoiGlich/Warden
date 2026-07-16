// Права и роли на фронте. Источник истины — backend (user.perms из /api/auth/me).
// Здесь только удобные хелперы и подписи для отображения.

export const ROLE_LABELS = {
  admin: "Администратор",
  operator: "Оператор",
  viewer: "Просмотр",
};

// Запасной вариант, если у старой сессии нет perms: выводим из is_admin.
function fallbackPerms(user) {
  return user?.is_admin
    ? ["ad_read", "ad_write", "logs", "settings"]
    : ["ad_read", "logs"];
}

export function permsOf(user) {
  const p = user?.perms;
  return Array.isArray(p) ? p : fallbackPerms(user);
}

export function hasPerm(user, perm) {
  return permsOf(user).includes(perm);
}

export function roleLabel(user) {
  return user?.role_label || ROLE_LABELS[user?.role] || "Пользователь";
}
