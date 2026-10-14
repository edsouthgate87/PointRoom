const PRESET_AVATAR_COLORS = {
  yellow: "#f1c963",
  mint: "#7bd09f",
  blue: "#4f7184",
  coral: "#ed725e",
};

export function voteTone(value) {
  if (value === "break") {
    return "break";
  }
  if (value === "?") {
    return "question";
  }
  return "number";
}

export function initials(name) {
  return name.split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase();
}

export function avatarColorValue(value) {
  const presetColor = PRESET_AVATAR_COLORS[value];
  if (presetColor) {
    return presetColor;
  }
  return /^#[0-9a-f]{6}$/i.test(value || "") ? value.toLowerCase() : "#f1c963";
}

export function avatarTextColor(value) {
  const red = Number.parseInt(value.slice(1, 3), 16);
  const green = Number.parseInt(value.slice(3, 5), 16);
  const blue = Number.parseInt(value.slice(5, 7), 16);
  const luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255;
  return luminance < 0.58 ? "#fffdf6" : "#102126";
}

export function avatarStyle(value) {
  const fill = avatarColorValue(value || "yellow");
  return `--avatar-fill: ${escapeHtml(fill)}; --avatar-ink: ${avatarTextColor(fill)};`;
}

export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
