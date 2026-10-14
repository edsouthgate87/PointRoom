const DEFAULT_HOST_TOKEN_TTL_MS = 1000 * 60 * 60 * 12;

function parseRecord(rawValue) {
  if (!rawValue) {
    return null;
  }
  try {
    const parsed = JSON.parse(rawValue);
    if (
      typeof parsed === "object"
      && parsed !== null
      && typeof parsed.token === "string"
      && parsed.token
    ) {
      return {
        token: parsed.token,
        savedAt: Number.isFinite(parsed.saved_at) ? Number(parsed.saved_at) : null,
      };
    }
  } catch {
    return { token: rawValue, savedAt: null };
  }
  return null;
}

function isExpired(savedAt, ttlMs, nowMs) {
  if (!Number.isFinite(savedAt)) {
    return false;
  }
  return savedAt + ttlMs < nowMs;
}

function writeRecord(storage, key, token, nowMs) {
  storage.setItem(
    key,
    JSON.stringify({
      token,
      saved_at: nowMs,
    }),
  );
}

export function rememberHostToken(storage, roomScopedKey, token, nowMs = Date.now()) {
  if (!token) {
    return;
  }
  writeRecord(storage, roomScopedKey, token, nowMs);
}

export function clearHostToken(storage, roomScopedKey, legacyKeys = []) {
  storage.removeItem(roomScopedKey);
  for (const key of legacyKeys) {
    storage.removeItem(key);
  }
}

export function loadHostToken({
  storage,
  roomScopedKey,
  legacySources = [],
  legacyKeys = [],
  ttlMs = DEFAULT_HOST_TOKEN_TTL_MS,
  nowMs = Date.now(),
}) {
  const sources = [{ storage, key: roomScopedKey }, ...legacySources];
  let tokenRecord = null;
  let foundInLegacySource = false;

  for (const source of sources) {
    const rawValue = source.storage.getItem(source.key);
    tokenRecord = parseRecord(rawValue);
    if (!tokenRecord) {
      continue;
    }
    foundInLegacySource = source.key !== roomScopedKey || source.storage !== storage;
    break;
  }

  if (!tokenRecord || !tokenRecord.token) {
    return "";
  }
  if (isExpired(tokenRecord.savedAt, ttlMs, nowMs)) {
    clearHostToken(storage, roomScopedKey, legacyKeys);
    for (const source of legacySources) {
      source.storage.removeItem(source.key);
    }
    return "";
  }

  if (foundInLegacySource || tokenRecord.savedAt === null) {
    writeRecord(storage, roomScopedKey, tokenRecord.token, nowMs);
  }
  for (const key of legacyKeys) {
    storage.removeItem(key);
  }
  return tokenRecord.token;
}
