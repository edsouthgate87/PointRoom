import {
  clearHostToken,
  loadHostToken,
  rememberHostToken,
} from "./host-credentials.js";

const PARTICIPANT_ID_KEY = "point-room-participant-id";
const PARTICIPANT_ID_PREFIX = "point-room-participant-id-room";
const PARTICIPANT_TOKEN_PREFIX = "point-room-participant-token-room";
const DISPLAY_NAME_KEY = "point-room-display-name";
const HOST_TOKEN_KEY = "point-room-host-token";
const HOST_TOKEN_PREFIX = "point-room-host-token-room";
const HOST_TOKEN_MAX_AGE_MS = 1000 * 60 * 60 * 12;

export function createClientStorage(localStorage, sessionStorage) {
  function roomKey(prefix, roomId) {
    return `${prefix}:${roomId.toUpperCase()}`;
  }

  function getOrCreateParticipantId() {
    const storedId = localStorage.getItem(PARTICIPANT_ID_KEY);
    if (storedId) {
      return storedId;
    }
    const generatedId = window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : `person-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    localStorage.setItem(PARTICIPANT_ID_KEY, generatedId);
    return generatedId;
  }

  function migrateStoredCredentials() {
    for (let index = localStorage.length - 1; index >= 0; index -= 1) {
      const key = localStorage.key(index);
      if (!key || !key.startsWith(`${PARTICIPANT_TOKEN_PREFIX}:`)) {
        continue;
      }
      const value = localStorage.getItem(key);
      if (value && !sessionStorage.getItem(key)) {
        sessionStorage.setItem(key, value);
      }
      localStorage.removeItem(key);
    }
  }

  function loadRoomCredentials(roomId) {
    const normalizedRoomId = roomId.toUpperCase();
    const hostTokenKey = roomKey(HOST_TOKEN_PREFIX, normalizedRoomId);
    return {
      participantId: localStorage.getItem(
        roomKey(PARTICIPANT_ID_PREFIX, normalizedRoomId),
      ) || getOrCreateParticipantId(),
      participantToken: sessionStorage.getItem(
        roomKey(PARTICIPANT_TOKEN_PREFIX, normalizedRoomId),
      ) || "",
      hostToken: loadHostToken({
        storage: localStorage,
        roomScopedKey: hostTokenKey,
        legacySources: [
          { storage: sessionStorage, key: hostTokenKey },
          { storage: localStorage, key: HOST_TOKEN_KEY },
          { storage: sessionStorage, key: HOST_TOKEN_KEY },
        ],
        legacyKeys: [HOST_TOKEN_KEY],
        ttlMs: HOST_TOKEN_MAX_AGE_MS,
      }),
    };
  }

  function rememberParticipantCredentials(roomId, participantId, participantToken) {
    localStorage.setItem(
      roomKey(PARTICIPANT_ID_PREFIX, roomId),
      participantId,
    );
    sessionStorage.setItem(
      roomKey(PARTICIPANT_TOKEN_PREFIX, roomId),
      participantToken,
    );
  }

  function clearParticipantToken(roomId) {
    sessionStorage.removeItem(roomKey(PARTICIPANT_TOKEN_PREFIX, roomId));
  }

  function clearRoomCredentials(roomId) {
    localStorage.removeItem(roomKey(PARTICIPANT_ID_PREFIX, roomId));
    const participantTokenKey = roomKey(PARTICIPANT_TOKEN_PREFIX, roomId);
    const hostTokenKey = roomKey(HOST_TOKEN_PREFIX, roomId);
    sessionStorage.removeItem(participantTokenKey);
    localStorage.removeItem(participantTokenKey);
    sessionStorage.removeItem(hostTokenKey);
    clearHostToken(localStorage, hostTokenKey, [HOST_TOKEN_KEY]);
    sessionStorage.removeItem(HOST_TOKEN_KEY);
  }

  return {
    clearParticipantToken,
    clearRoomCredentials,
    getOrCreateParticipantId,
    loadDisplayName: () => localStorage.getItem(DISPLAY_NAME_KEY) || "",
    loadRoomCredentials,
    migrateStoredCredentials,
    rememberDisplayName: (name) => localStorage.setItem(DISPLAY_NAME_KEY, name),
    rememberHostToken: (roomId, token) => rememberHostToken(
      localStorage,
      roomKey(HOST_TOKEN_PREFIX, roomId),
      token,
    ),
    rememberParticipantCredentials,
  };
}
