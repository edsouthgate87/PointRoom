import { AVATAR_PALETTE, CARD_SIZES } from "./app/constants.js";
import {
  avatarColorValue,
  avatarStyle,
  avatarTextColor,
  escapeHtml,
  initials,
  voteTone,
} from "./app/ui-utils.js";
import { createClientStorage } from "./app/client-storage.js";

(() => {
  const body = document.body;
  const initialRoomId = (body.dataset.roomId || "").toUpperCase();
  const adoWorkItemBaseUrl = body.dataset.adoWorkItemBaseUrl || "";
  const publicBaseUrl = (body.dataset.publicBaseUrl || window.location.origin).replace(/\/$/, "");
  const cardSizeStorageKey = "point-room-card-size";
  const clientStorage = createClientStorage(
    window.localStorage,
    window.sessionStorage,
  );
  const hostCredentialStorage = window.localStorage;
  const storedCardSize = hostCredentialStorage.getItem(cardSizeStorageKey);
  let cardSize = CARD_SIZES.includes(storedCardSize) ? storedCardSize : "medium";

  const entryView = document.getElementById("entryView");
  const roomView = document.getElementById("roomView");
  const entryForm = document.getElementById("entryForm");
  const displayNameInput = document.getElementById("displayName");
  const roomCodeInput = document.getElementById("roomCodeInput");
  const issueInputEntry = document.getElementById("issueInputEntry");
  const deckInput = document.getElementById("deckInput");
  const joinButton = document.getElementById("joinButton");
  const entryError = document.getElementById("entryError");
  const entryAvatarPalette = document.getElementById("entryAvatarPalette");
  const entryAvatarColorInput = document.getElementById("entryAvatarColorInput");
  const entryColorStatus = document.getElementById("entryColorStatus");
  const toggleEntryAvatarPalette = document.getElementById("toggleEntryAvatarPalette");
  const connectionState = document.getElementById("connectionState");
  const connectionLabel = document.getElementById("connectionLabel");
  const roomCodeDisplay = document.getElementById("roomCodeDisplay");
  const roundStatus = document.getElementById("roundStatus");
  const roundStatusText = document.getElementById("roundStatusText");
  const issueForm = document.getElementById("issueForm");
  const issueInput = document.getElementById("issueInput");
  const storyPreview = document.getElementById("storyPreview");
  const storySaveStatus = document.getElementById("storySaveStatus");
  const hostNote = document.getElementById("hostNote");
  const voteProgress = document.getElementById("voteProgress");
  const roundTag = document.getElementById("roundTag");
  const participantTable = document.getElementById("participantTable");
  const resultsPanel = document.getElementById("resultsPanel");
  const averageValue = document.getElementById("averageValue");
  const averageCaption = document.getElementById("averageCaption");
  const distribution = document.getElementById("distribution");
  const historySection = document.getElementById("historySection");
  const historyCount = document.getElementById("historyCount");
  const historyList = document.getElementById("historyList");
  const selectionCaption = document.getElementById("selectionCaption");
  const selectedCard = document.getElementById("selectedCard");
  const deck = document.getElementById("deck");
  const cardSizeControls = Array.from(document.querySelectorAll(".deck-size-option"));
  const peopleCount = document.getElementById("peopleCount");
  const peopleList = document.getElementById("peopleList");
  const hostControls = document.getElementById("hostControls");
  const suggestionsEnabledInput = document.getElementById("suggestionsEnabledInput");
  const suggestionsSettingCaption = document.getElementById("suggestionsSettingCaption");
  const roomDeckInput = document.getElementById("roomDeckInput");
  const storyQueueCount = document.getElementById("storyQueueCount");
  const storyQueueCaption = document.getElementById("storyQueueCaption");
  const suggestStoryButton = document.getElementById("suggestStoryButton");
  const suggestionGroup = document.getElementById("suggestionGroup");
  const suggestionCount = document.getElementById("suggestionCount");
  const suggestionsList = document.getElementById("suggestionsList");
  const backlogGroup = document.getElementById("backlogGroup");
  const backlogCount = document.getElementById("backlogCount");
  const backlogList = document.getElementById("backlogList");
  const profileControls = document.getElementById("profileControls");
  const profileForm = document.getElementById("profileForm");
  const profileAvatar = document.getElementById("profileAvatar");
  const profileNameInput = document.getElementById("profileNameInput");
  const saveProfileButton = document.getElementById("saveProfileButton");
  const avatarPalette = document.getElementById("avatarPalette");
  const toggleAvatarPalette = document.getElementById("toggleAvatarPalette");
  const avatarColorInput = document.getElementById("avatarColorInput");
  const revealButton = document.getElementById("revealButton");
  const revealCountdown = document.getElementById("revealCountdown");
  const revealCountdownValue = document.getElementById("revealCountdownValue");
  const revealCountdownInput = document.getElementById("revealCountdownInput");
  const startStoryButton = document.getElementById("startStoryButton");
  const resetButton = document.getElementById("resetButton");
  const deleteSessionButton = document.getElementById("deleteSessionButton");
  const storyModal = document.getElementById("storyModal");
  const newStoryForm = document.getElementById("newStoryForm");
  const newStoryInput = document.getElementById("newStoryInput");
  const storyModalEyebrow = document.getElementById("storyModalEyebrow");
  const storyModalTitle = document.getElementById("storyModalTitle");
  const storyModalInputLabel = document.getElementById("storyModalInputLabel");
  const storyModalSubmitLabel = document.getElementById("storyModalSubmitLabel");
  const cancelStoryButton = document.getElementById("cancelStoryButton");
  const storyModalError = document.getElementById("storyModalError");
  const storyModalPanel = storyModal.querySelector(".story-modal-panel");
  const shareLink = document.getElementById("shareLink");
  const copyRoomButton = document.getElementById("copyRoomButton");
  const copyShareButton = document.getElementById("copyShareButton");
  const leaveRoomLink = document.querySelector(".leave-link");
  const toast = document.getElementById("toast");
  const newStoryButton = newStoryForm.querySelector('button[type="submit"]');

  const browserParticipantId = clientStorage.getOrCreateParticipantId();
  let participantId = browserParticipantId;
  let participantToken = "";
  let hostToken = "";
  let displayName = clientStorage.loadDisplayName();
  let activeRoomId = initialRoomId;
  let currentVote = null;
  let roomState = null;
  let roomHistory = [];
  let socket = null;
  let toastTimer = null;
  let storySyncTimer = null;
  let storySavePending = false;
  let revealCountdownTimer = null;
  let selectedAvatarColor = "yellow";
  let entryAvatarColor = "#f1c963";
  let entryAvatarColorWasSelected = false;
  let roomOccupiedAvatarColors = [];
  let roomColorRequest = Promise.resolve();
  let roomColorRequestId = 0;
  let profileDirty = false;
  let profileSavePending = false;
  let sessionDeleted = false;
  let stopReconnect = false;
  let pendingAction = null;
  let modalReturnFocus = null;
  let storyModalMode = "start";
  let suggestionsSettingPending = false;
  let deckSettingPending = false;

  renderAvatarPalette(avatarPalette);
  renderAvatarPalette(entryAvatarPalette);
  const colorSwatches = Array.from(avatarPalette.querySelectorAll(".color-swatch"));
  const entryColorSwatches = Array.from(
    entryAvatarPalette.querySelectorAll(".color-swatch"),
  );
  renderEntryColorControls();
  renderCardSizeControls();
  clientStorage.migrateStoredCredentials();

  toggleAvatarPalette.addEventListener("click", () => {
    setAvatarPaletteOpen(avatarPalette.hidden);
  });

  toggleEntryAvatarPalette.addEventListener("click", () => {
    setEntryAvatarPaletteOpen(entryAvatarPalette.hidden);
  });

  cardSizeControls.forEach((control) => {
    control.addEventListener("click", () => {
      setCardSize(control.dataset.cardSize);
    });
  });

  if (initialRoomId) {
    loadRoomCredentials(initialRoomId);
    loadRoomAvatarColors(initialRoomId);
  }

  displayNameInput.value = displayName;
  if (initialRoomId) {
    roomCodeInput.value = initialRoomId;
  }

  roomCodeInput.addEventListener("input", () => {
    const roomId = roomCodeInput.value.trim().toUpperCase();
    if (!/^[A-Z0-9]{6}$/.test(roomId)) {
      roomOccupiedAvatarColors = [];
      renderEntryColorControls();
      return;
    }
    loadRoomAvatarColors(roomId);
  });

  entryForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearEntryError();

    const name = displayNameInput.value.trim();
    if (!name) {
      showEntryError("Enter your name first.");
      displayNameInput.focus();
      return;
    }

    setConnectionState("working", "Creating your room");
    try {
      const response = await fetch("/api/rooms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          participant_id: participantId,
          issue: issueInputEntry.value.trim(),
          avatar_color: entryAvatarColor,
          deck: deckInput.value,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "Could not create the room.");
      }

      hostToken = payload.host_token;
      participantId = payload.participant_id || browserParticipantId;
      participantToken = payload.participant_token || "";
      clientStorage.rememberParticipantCredentials(
        payload.room_id,
        participantId,
        participantToken,
      );
      clientStorage.rememberHostToken(payload.room_id, hostToken);
      rememberName(name);
      enterRoom(payload.room_id);
    } catch (error) {
      setConnectionState("idle", "Ready when you are");
      showEntryError(error.message);
    }
  });

  joinButton.addEventListener("click", async () => {
    clearEntryError();
    const name = displayNameInput.value.trim();
    const roomId = roomCodeInput.value.trim().toUpperCase();

    if (!name) {
      showEntryError("Enter your name first.");
      displayNameInput.focus();
      return;
    }
    if (!/^[A-Z0-9]{6}$/.test(roomId)) {
      showEntryError("Enter a six-character room code.");
      roomCodeInput.focus();
      return;
    }

    loadRoomCredentials(roomId);
    await loadRoomAvatarColors(roomId);
    selectAvailableEntryAvatarColor();

    rememberName(name);
    enterRoom(roomId, { updateAvatarColor: true });
  });

  issueInput.addEventListener("input", () => {
    if (!socket || !isHost() || roomState.revealed) {
      return;
    }
    renderStoryPreview(issueInput.value.trim());
    storySavePending = true;
    setStorySaveStatus("saving", "Saving...");
    window.clearTimeout(storySyncTimer);
    storySyncTimer = window.setTimeout(() => syncStory(), 320);
  });

  revealButton.addEventListener("click", () => {
    if (socket && isHost() && roomState && roomState.voted_count > 0) {
      socket.emit("reveal", { host_token: hostToken });
    }
  });

  revealCountdownInput.addEventListener("change", () => {
    if (!socket || !isHost() || !roomState || roomState.reveal_countdown_ends_at) {
      return;
    }
    const seconds = Number(revealCountdownInput.value);
    const minimum = Number(revealCountdownInput.min);
    const maximum = Number(revealCountdownInput.max);
    if (!Number.isInteger(seconds) || seconds < minimum || seconds > maximum) {
      revealCountdownInput.value = String(roomState.reveal_countdown_seconds);
      showToast(`Choose a reveal timer from ${minimum} to ${maximum} seconds.`, "error");
      return;
    }
    socket.emit("set_reveal_countdown", {
      seconds,
      host_token: hostToken,
    });
  });

  suggestionsEnabledInput.addEventListener("change", () => {
    if (!socket || !isHost() || !roomState) {
      renderState();
      return;
    }
    suggestionsSettingPending = true;
    renderState();
    socket.emit("set_suggestions_enabled", {
      enabled: suggestionsEnabledInput.checked,
      host_token: hostToken,
    });
  });

  roomDeckInput.addEventListener("change", () => {
    if (!socket || !isHost() || !roomState) {
      renderState();
      return;
    }
    if (roomDeckInput.value === roomState.deck_name) {
      return;
    }
    deckSettingPending = true;
    renderState();
    socket.emit("set_deck", {
      deck: roomDeckInput.value,
      host_token: hostToken,
    });
  });

  entryColorSwatches.forEach((swatch) => {
    swatch.addEventListener("click", () => {
      if (swatch.disabled || swatch.hidden) {
        return;
      }
      entryAvatarColor = swatch.dataset.avatarColor;
      entryAvatarColorWasSelected = true;
      clearEntryError();
      renderEntryColorControls();
      setEntryAvatarPaletteOpen(false);
    });
  });

  entryAvatarColorInput.addEventListener("input", () => {
    const nextColor = entryAvatarColorInput.value.toLowerCase();
    entryAvatarColor = nextColor;
    entryAvatarColorWasSelected = true;
    if (isAvatarColorOccupied(nextColor)) {
      selectAvailableEntryAvatarColor();
      renderEntryColorControls();
      return;
    }
    clearEntryError();
    renderEntryColorControls();
  });

  entryAvatarColorInput.addEventListener("change", () => {
    setEntryAvatarPaletteOpen(false);
  });

  profileNameInput.addEventListener("input", () => {
    profileDirty = true;
    renderProfileControls(currentParticipant());
  });

  colorSwatches.forEach((swatch) => {
    swatch.addEventListener("click", () => {
      selectedAvatarColor = swatch.dataset.avatarColor;
      profileDirty = true;
      renderProfileControls(currentParticipant());
      setAvatarPaletteOpen(false);
      saveProfile(false);
    });
  });

  avatarColorInput.addEventListener("input", () => {
    const nextColor = avatarColorInput.value.toLowerCase();
    if (!isAvatarColorAvailableForCurrent(nextColor)) {
      avatarColorInput.value = avatarColorValue(selectedAvatarColor);
      showToast("That avatar color is already in use. Choose another color.", "error");
      return;
    }
    selectedAvatarColor = nextColor;
    profileDirty = true;
    renderProfileControls(currentParticipant());
  });

  avatarColorInput.addEventListener("change", () => {
    setAvatarPaletteOpen(false);
    saveProfile(false);
  });

  profileForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveProfile(true);
  });

  function saveProfile(closePalette) {
    if (!socket || !socket.connected || !currentParticipant()) {
      return;
    }
    const name = profileNameInput.value.trim();
    if (!name) {
      showToast("Enter a name for your profile.", "error");
      profileNameInput.focus();
      return;
    }
    profileSavePending = true;
    saveProfileButton.disabled = true;
    if (closePalette) {
      setAvatarPaletteOpen(false);
    }
    socket.emit("update_profile", {
      name,
      avatar_color: selectedAvatarColor,
    });
  }

  resetButton.addEventListener("click", () => {
    if (socket && isHost()) {
      socket.emit("reset_round", { host_token: hostToken });
    }
  });

  startStoryButton.addEventListener("click", () => {
    if (!isHost()) {
      return;
    }
    openStoryModal("start");
  });

  suggestStoryButton.addEventListener("click", () => {
    if (!socket || !roomState || isHost() || !roomState.suggestions_enabled) {
      return;
    }
    openStoryModal("suggest");
  });

  cancelStoryButton.addEventListener("click", closeStoryModal);
  storyModal.querySelector("[data-close-story-modal]").addEventListener("click", closeStoryModal);
  storyModal.addEventListener("keydown", (event) => {
    if (storyModal.hidden) {
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeStoryModal();
      return;
    }
    if (event.key === "Tab") {
      trapModalFocus(event);
    }
  });
  newStoryForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!socket) {
      return;
    }
    const issue = newStoryInput.value.trim();
    if (!issue) {
      storyModalError.textContent = storyModalMode === "suggest"
        ? "Add a story before sending the suggestion."
        : "Add a story before starting the next round.";
      storyModalError.hidden = false;
      newStoryInput.focus();
      return;
    }
    storyModalError.hidden = true;
    newStoryButton.disabled = true;

    if (storyModalMode === "suggest") {
      if (!roomState || isHost() || !roomState.suggestions_enabled) {
        newStoryButton.disabled = false;
        storyModalError.textContent = "Story suggestions are turned off by the host.";
        storyModalError.hidden = false;
        return;
      }
      socket.emit("suggest_story", { issue });
      return;
    }

    if (!isHost()) {
      newStoryButton.disabled = false;
      return;
    }
    const previousRound = roomState ? roomState.round_number : null;
    beginPendingAction({
      isConfirmed: (nextState) => nextState
        && typeof nextState.issue === "string"
        && nextState.issue.trim() === issue
        && nextState.round_number !== previousRound
        && !nextState.revealed,
      onSuccess: () => {
        newStoryButton.disabled = false;
        closeStoryModal();
        showToast("New story shared with the room.", "success");
      },
      onFailure: (message) => {
        newStoryButton.disabled = false;
        storyModalError.textContent = message;
        storyModalError.hidden = false;
        newStoryInput.focus();
      },
    });
    socket.emit("start_new_story", { issue, host_token: hostToken });
  });

  deleteSessionButton.addEventListener("click", () => {
    if (!socket || !isHost()) {
      return;
    }
    if (!window.confirm("Delete this session and all of its history? This cannot be undone.")) {
      return;
    }
    socket.emit("delete_session", { host_token: hostToken });
  });

  copyRoomButton.addEventListener("click", () => copyRoomLink());
  copyShareButton.addEventListener("click", () => copyRoomLink());
  leaveRoomLink.addEventListener("click", () => {
    if (!isHost()) {
      return;
    }
    clientStorage.clearParticipantToken(activeRoomId);
    participantToken = "";
  });

  function enterRoom(roomId, options = {}) {
    activeRoomId = roomId.toUpperCase();
    let updateAvatarColorOnJoin = options.updateAvatarColor === true;
    loadRoomCredentials(activeRoomId);
    sessionDeleted = false;
    stopReconnect = false;
    roomCodeInput.value = activeRoomId;
    roomCodeDisplay.textContent = activeRoomId;
    shareLink.value = roomUrl(activeRoomId);
    entryView.hidden = true;
    roomView.hidden = false;
    history.replaceState({}, "", `/room/${activeRoomId}`);
    setConnectionState("connecting", "Connecting to room");

    roomState = null;
    roomHistory = [];
    currentVote = null;
    roomOccupiedAvatarColors = [];
    storySavePending = false;
    profileControls.hidden = true;
    profileDirty = false;
    profileSavePending = false;
    selectedAvatarColor = "yellow";
    suggestionsSettingPending = false;
    deckSettingPending = false;
    setAvatarPaletteOpen(false);
    clearRevealCountdownTimer();

    if (socket) {
      socket.disconnect();
    }

    const socketOptions = { autoConnect: false, transports: ["websocket", "polling"] };
    const socketOrigin = publicBaseUrl !== window.location.origin ? publicBaseUrl : "";
    const roomSocket = socketOrigin
      ? window.io(socketOrigin, socketOptions)
      : window.io(socketOptions);
    socket = roomSocket;
    let roomJoined = false;

    const sendJoinRequest = () => {
      roomSocket.emit("join_room", {
        room_id: activeRoomId,
        participant_id: participantId,
        name: displayName,
        participant_token: participantToken,
        host_token: hostToken,
        avatar_color: entryAvatarColor,
        update_avatar_color: updateAvatarColorOnJoin,
      });
    };

    roomSocket.on("connect", () => {
      setConnectionState("live", "Live");
      sendJoinRequest();
    });

    roomSocket.on("your_identity", (identity) => {
      if (roomSocket !== socket) {
        return;
      }
      const nextParticipantId = identity && identity.participant_id;
      const nextParticipantToken = identity && identity.participant_token;
      if (typeof nextParticipantId !== "string" || typeof nextParticipantToken !== "string") {
        return;
      }
      participantId = nextParticipantId;
      participantToken = nextParticipantToken;
      clientStorage.rememberParticipantCredentials(
        activeRoomId,
        participantId,
        participantToken,
      );
      renderState();
    });

    roomSocket.on("room_state", (nextState) => {
      roomJoined = true;
      updateAvatarColorOnJoin = false;
      if (
        storySavePending
        && typeof nextState.issue === "string"
        && nextState.issue.trim() === issueInput.value.trim()
      ) {
        storySavePending = false;
      }
      if (
        suggestionsSettingPending
        && Boolean(nextState.suggestions_enabled) === suggestionsEnabledInput.checked
      ) {
        suggestionsSettingPending = false;
      }
      if (
        deckSettingPending
        && nextState.deck_name === roomDeckInput.value
      ) {
        deckSettingPending = false;
      }
      if (Array.isArray(nextState.occupied_avatar_colors)) {
        roomOccupiedAvatarColors = nextState.occupied_avatar_colors;
        renderEntryColorControls();
      }
      roomState = nextState;
      resolvePendingAction(nextState);
      renderState();
    });

    roomSocket.on("profile_updated", (profile) => {
      if (roomSocket !== socket || !profile) {
        return;
      }
      profileSavePending = false;
      profileDirty = false;
      if (typeof profile.name === "string") {
        rememberName(profile.name);
        profileNameInput.value = profile.name;
      }
      if (typeof profile.avatar_color === "string") {
        selectedAvatarColor = profile.avatar_color;
      }
      renderProfileControls(currentParticipant());
      showToast("Profile updated.", "success");
    });

    roomSocket.on("story_suggestion_submitted", (payload) => {
      if (roomSocket !== socket || storyModalMode !== "suggest") {
        return;
      }
      newStoryButton.disabled = false;
      closeStoryModal();
      showToast("Story suggestion sent to the host.", "success");
      if (payload && payload.suggestion) {
        renderStoryQueue();
      }
    });

    roomSocket.on("reveal_countdown", (countdown) => {
      if (roomSocket !== socket || !roomState || !countdown) {
        return;
      }
      roomState.reveal_countdown_ends_at = countdown.ends_at;
      renderState();
    });

    roomSocket.on("room_history", (payload) => {
      roomHistory = payload && Array.isArray(payload.history) ? payload.history : [];
      renderHistory();
    });

    roomSocket.on("your_vote", ({ value }) => {
      currentVote = value;
      renderDeck();
      renderSelection();
    });

    roomSocket.on("room_error", ({ message }) => {
      if (failPendingAction(message || "Something went wrong.")) {
        return;
      }
      if (suggestionsSettingPending) {
        suggestionsSettingPending = false;
        renderState();
      }
      if (deckSettingPending) {
        deckSettingPending = false;
        renderState();
      }
      if (storyModalMode === "suggest" && !storyModal.hidden && newStoryButton.disabled) {
        newStoryButton.disabled = false;
        storyModalError.textContent = message || "Could not send the story suggestion.";
        storyModalError.hidden = false;
        newStoryInput.focus();
        return;
      }
      if (
        !roomJoined
        && message === "That participant credential is invalid."
        && participantToken
      ) {
        participantToken = "";
        clientStorage.clearParticipantToken(activeRoomId);
        sendJoinRequest();
        return;
      }
      if (!roomJoined && message === "That avatar color is already in use. Choose another color.") {
        loadRoomAvatarColors(activeRoomId);
      }
      if (!roomJoined) {
        stopReconnect = true;
        roomSocket.disconnect();
        socket = null;
        roomState = null;
        roomHistory = [];
        currentVote = null;
        roomView.hidden = true;
        entryView.hidden = false;
        roomCodeInput.value = activeRoomId;
        history.replaceState({}, "", "/");
        setConnectionState("idle", "Ready when you are");
        showEntryError(message || "Could not join the room.");
        roomCodeInput.focus();
        return;
      }
      if (profileSavePending) {
        profileSavePending = false;
        profileDirty = false;
        renderState();
      }
      if (storySavePending) {
        storySavePending = false;
        setStorySaveStatus("error", "Not saved");
      }
      showToast(message, "error");
    });

    roomSocket.on("session_deleted", ({ message }) => {
      sessionDeleted = true;
      stopReconnect = true;
      roomSocket.disconnect();
      socket = null;
      roomState = null;
      roomHistory = [];
      currentVote = null;
      storySavePending = false;
      profileControls.hidden = true;
      profileDirty = false;
      profileSavePending = false;
      selectedAvatarColor = "yellow";
      suggestionsSettingPending = false;
      deckSettingPending = false;
      setAvatarPaletteOpen(false);
      clearRevealCountdownTimer();
      hostToken = "";
      roomView.hidden = true;
      entryView.hidden = false;
      roomCodeInput.value = "";
      issueInputEntry.value = "";
      clientStorage.clearRoomCredentials(activeRoomId);
      participantId = browserParticipantId;
      participantToken = "";
      hostToken = "";
      history.replaceState({}, "", "/");
      setConnectionState("idle", "Ready when you are");
      showToast(message || "The session was deleted.", "success");
    });

    roomSocket.on("disconnect", () => {
      if (!sessionDeleted && !stopReconnect) {
        setConnectionState("connecting", "Reconnecting");
      }
    });

    roomSocket.on("connect_error", () => {
      setConnectionState("error", "Connection issue");
    });

    roomSocket.connect();
  }

  function renderState() {
    if (!roomState) {
      return;
    }

    roomCodeDisplay.textContent = roomState.room_id;
    shareLink.value = roomUrl(roomState.room_id);
    if (document.activeElement !== issueInput) {
      issueInput.value = roomState.issue;
    }

    const participant = currentParticipant();
    const host = Boolean(participant && participant.is_host && hostToken);
    const revealPending = Boolean(
      !roomState.revealed && roomState.reveal_countdown_ends_at,
    );
    const canEditStory = host && !roomState.revealed && !revealPending;
    profileControls.hidden = !participant;
    if (participant && !profileDirty && !profileSavePending) {
      if (document.activeElement !== profileNameInput) {
        profileNameInput.value = participant.name;
      }
      selectedAvatarColor = participant.avatar_color || "yellow";
    }
    renderProfileControls(participant);
    hostControls.hidden = !host;
    if (!suggestionsSettingPending) {
      suggestionsEnabledInput.checked = Boolean(roomState.suggestions_enabled);
    }
    if (!deckSettingPending && typeof roomState.deck_name === "string") {
      roomDeckInput.value = roomState.deck_name;
    }
    suggestionsEnabledInput.disabled = !host || suggestionsSettingPending;
    roomDeckInput.disabled = (
      !host
      || deckSettingPending
      || roomState.revealed
      || roomState.voted_count > 0
    );
    suggestionsSettingCaption.textContent = roomState.suggestions_enabled ? "On" : "Off";
    issueForm.hidden = !host;
    renderStoryPreview(roomState.issue);
    issueInput.readOnly = !canEditStory;
    storySaveStatus.hidden = !host;
    if (host && !storySavePending) {
      setStorySaveStatus(roomState.issue ? "saved" : "idle", roomState.issue ? "Saved" : "No story yet");
    }
    if (document.activeElement !== revealCountdownInput) {
      revealCountdownInput.value = String(roomState.reveal_countdown_seconds || 5);
    }
    revealCountdownInput.disabled = !host || roomState.revealed || revealPending;
    hostNote.textContent = canEditStory
      ? "Host editable"
      : (revealPending
        ? "Reveal countdown active"
        : (host ? "Reset to edit story" : "Host sets the story"));

    peopleCount.textContent = String(roomState.participant_count);
    roundTag.textContent = `ROUND ${roomState.round_number}`;
    voteProgress.textContent = `${roomState.voted_count} / ${roomState.participant_count} voted`;
    updateRoundStatus();
    renderParticipants();
    renderPeopleList();
    renderStoryQueue();
    renderDeck();
    renderResults();
    renderSelection();
    renderRevealCountdown();

    revealButton.disabled = roomState.revealed || revealPending || roomState.voted_count === 0;
    resetButton.disabled = revealPending || (!roomState.revealed && roomState.voted_count === 0);
    startStoryButton.disabled = !host || revealPending;
    deleteSessionButton.disabled = !host;
    refreshIcons();
  }

  function renderParticipants() {
    if (!roomState.participants.length) {
      participantTable.innerHTML = '<div class="empty-table">Waiting for your team to join.</div>';
      return;
    }

    participantTable.innerHTML = roomState.participants.map((participant) => {
      const stateLabel = roomState.revealed
        ? (participant.vote || "-")
        : (participant.has_voted ? "READY" : "THINKING");
      const stateClass = roomState.revealed
        ? "participant-vote revealed-vote"
        : `participant-vote ${participant.has_voted ? "ready" : "thinking"}`;
      const voteToneAttribute = roomState.revealed && participant.vote
        ? ` data-vote-tone="${voteTone(participant.vote)}"`
        : "";
      const youLabel = participant.id === participantId ? '<span class="you-label">YOU</span>' : "";
      return `
        <article class="participant-row ${participant.id === participantId ? "is-you" : ""}">
          <div class="participant-person">
            <span class="avatar" data-avatar-color="${escapeHtml(participant.avatar_color || "yellow")}" style="${avatarStyle(participant.avatar_color)}">${escapeHtml(initials(participant.name))}</span>
            <div>
              <strong>${escapeHtml(participant.name)}</strong>
              ${participant.is_host ? '<span class="host-label">HOST</span>' : ""}
              ${youLabel}
            </div>
          </div>
          <span class="${stateClass}"${voteToneAttribute}>${escapeHtml(stateLabel)}</span>
        </article>`;
    }).join("");
  }

  function renderPeopleList() {
    peopleList.innerHTML = roomState.participants.map((participant) => {
      const status = roomState.revealed
        ? (participant.vote || "-")
        : (participant.has_voted ? "In" : "Thinking");
      return `
        <div class="person-mini">
          <span class="avatar avatar-small" data-avatar-color="${escapeHtml(participant.avatar_color || "yellow")}" style="${avatarStyle(participant.avatar_color)}">${escapeHtml(initials(participant.name))}</span>
          <span class="person-mini-name">${escapeHtml(participant.name)}</span>
          <span class="person-mini-status">${escapeHtml(status)}</span>
        </div>`;
    }).join("");
  }

  function renderStoryQueue() {
    if (!roomState) {
      return;
    }
    const suggestions = Array.isArray(roomState.suggestions) ? roomState.suggestions : [];
    const backlog = Array.isArray(roomState.backlog) ? roomState.backlog : [];
    const host = isHost();
    const suggestionsEnabled = Boolean(roomState.suggestions_enabled);

    storyQueueCount.textContent = String(suggestions.length + backlog.length);
    suggestionCount.textContent = String(suggestions.length);
    backlogCount.textContent = String(backlog.length);
    storyQueueCaption.textContent = host
      ? (suggestionsEnabled ? "Guest suggestions are open." : "Guest suggestions are off.")
      : (suggestionsEnabled
        ? "Send a story for the host to review."
        : "Guest suggestions are off.");
    suggestStoryButton.hidden = host || !suggestionsEnabled;
    suggestStoryButton.disabled = !socket || !socket.connected || !suggestionsEnabled;
    suggestionGroup.hidden = suggestions.length === 0;
    backlogGroup.hidden = backlog.length === 0;
    renderQueueList(suggestionsList, suggestions, "suggestion", host);
    renderQueueList(backlogList, backlog, "backlog", host);
  }

  function renderQueueList(container, entries, queueType, host) {
    container.replaceChildren();
    entries.forEach((entry) => {
      const article = document.createElement("article");
      article.className = "queue-entry";

      const issue = document.createElement("div");
      issue.className = "queue-entry-story";
      appendStoryContent(issue, entry.issue);

      const meta = document.createElement("div");
      meta.className = "queue-entry-meta";
      const avatar = document.createElement("span");
      avatar.className = "avatar avatar-small";
      avatar.dataset.avatarColor = entry.avatar_color || "yellow";
      avatar.style.cssText = avatarStyle(entry.avatar_color || "yellow");
      avatar.textContent = initials(entry.suggested_by || "?");
      const author = document.createElement("span");
      author.textContent = entry.suggested_by || "Unknown";
      meta.append(avatar, author);

      const actions = document.createElement("div");
      actions.className = "queue-entry-actions";
      if (host) {
        if (queueType === "suggestion") {
          actions.append(
            queueAction("check", "Use story now", "accept_story_suggestion", entry.id, "suggestion_id"),
            queueAction("list-plus", "Move to backlog", "backlog_story_suggestion", entry.id, "suggestion_id"),
            queueAction("x", "Dismiss suggestion", "dismiss_story_suggestion", entry.id, "suggestion_id"),
          );
        } else {
          actions.append(
            queueAction("play", "Use backlog story", "use_backlog_story", entry.id, "backlog_id"),
            queueAction("trash-2", "Remove backlog story", "remove_backlog_story", entry.id, "backlog_id"),
          );
        }
      }

      article.append(issue, meta, actions);
      container.append(article);
    });
    refreshIcons();
  }

  function queueAction(icon, label, eventName, entryId, idKey) {
    const button = document.createElement("button");
    button.className = "queue-action";
    button.type = "button";
    button.title = label;
    button.setAttribute("aria-label", label);
    const iconElement = document.createElement("i");
    iconElement.dataset.lucide = icon;
    iconElement.setAttribute("aria-hidden", "true");
    button.append(iconElement);
    button.addEventListener("click", () => {
      if (!socket || !isHost()) {
        return;
      }
      socket.emit(eventName, {
        [idKey]: entryId,
        host_token: hostToken,
      });
    });
    return button;
  }

  function renderDeck() {
    if (!roomState) {
      return;
    }
    deck.dataset.cardCount = String(roomState.deck.length);
    deck.dataset.cardSize = cardSize;
    const cardsDisabled = roomState.revealed || !hasValidStory();
    const activeElement = document.activeElement;
    const activeCard = activeElement && deck.contains(activeElement)
      ? activeElement.closest(".card-option")
      : null;
    const activeCardValue = activeCard ? activeCard.dataset.value : null;
    const renderedCards = roomState.deck.map((value) => {
      const selected = currentVote === value;
      const breakCard = value === "break" ? " break-card" : "";
      const label = value === "break" ? "BREAK" : value;
      return `
        <button class="card-option${selected ? " selected" : ""}${breakCard}" type="button" data-value="${escapeHtml(value)}" aria-pressed="${selected}" ${cardsDisabled ? "disabled" : ""}>
          <span class="card-corner">${escapeHtml(label)}</span>
          <strong>${escapeHtml(label)}</strong>
          <span class="card-corner card-corner-bottom">${escapeHtml(label)}</span>
        </button>`;
    });
    const rowSize = roomState.deck.length > 8
      ? Math.ceil(roomState.deck.length / 2)
      : roomState.deck.length;
    const rows = [];
    for (let index = 0; index < renderedCards.length; index += rowSize) {
      rows.push(`<div class="deck-row">${renderedCards.slice(index, index + rowSize).join("")}</div>`);
    }
    deck.innerHTML = rows.join("");

    deck.querySelectorAll(".card-option").forEach((card) => {
      card.addEventListener("click", () => {
        if (socket && !cardsDisabled) {
          const value = currentVote === card.dataset.value ? null : card.dataset.value;
          socket.emit("vote", { value });
        }
      });
    });

    if (activeCardValue) {
      const restoredCard = Array.from(deck.querySelectorAll(".card-option"))
        .find((card) => card.dataset.value === activeCardValue);
      if (restoredCard && !restoredCard.disabled) {
        restoredCard.focus();
      }
    }
  }

  function renderResults() {
    resultsPanel.hidden = !roomState.revealed;
    if (!roomState.revealed) {
      return;
    }

    const hasBreakVote = roomState.distribution.break > 0;
    const hasQuestionVote = roomState.distribution["?"] > 0;
    resultsPanel.dataset.resultTone = hasBreakVote ? "break" : hasQuestionVote ? "question" : "ok";
    averageValue.textContent = roomState.average === null ? "-" : String(roomState.average);
    averageCaption.textContent = roomState.average === null ? "no numeric cards" : "average estimate";

    const visibleCards = roomState.deck.filter((card) => roomState.distribution[card] > 0);
    if (!visibleCards.length) {
      distribution.innerHTML = '<span class="no-results">No cards were played this round.</span>';
      return;
    }

    const highestCount = Math.max(...visibleCards.map((card) => roomState.distribution[card]));
    distribution.innerHTML = visibleCards.map((card) => {
      const count = roomState.distribution[card];
      const width = Math.max(12, Math.round((count / highestCount) * 100));
      const label = card === "break" ? "BREAK" : card;
      return `
        <div class="distribution-row" data-vote-tone="${voteTone(card)}">
          <span class="distribution-label">${escapeHtml(label)}</span>
          <span class="distribution-track"><span style="width: ${width}%"></span></span>
          <span class="distribution-count">${count}</span>
        </div>`;
    }).join("");
  }

  function renderHistory() {
    const history = roomHistory;
    historySection.hidden = history.length === 0;
    historyCount.textContent = `${history.length} ${history.length === 1 ? "story" : "stories"}`;
    if (!history.length) {
      historyList.replaceChildren();
      return;
    }

    historyList.replaceChildren();
    [...history].reverse().forEach((entry) => {
      const average = entry.average === null ? "-" : entry.average;
      const votes = entry.votes || [];
      const article = document.createElement("article");
      article.className = "history-entry";

      const top = document.createElement("div");
      top.className = "history-entry-top";
      const round = document.createElement("span");
      round.className = "history-round";
      round.textContent = `ROUND ${entry.round}`;
      const averageLabel = document.createElement("span");
      averageLabel.className = "history-average";
      averageLabel.textContent = entry.status === "skipped" ? "SKIPPED" : `AVG ${average}`;
      const historyActions = document.createElement("div");
      historyActions.className = "history-entry-actions";
      historyActions.append(averageLabel);
      if (isHost()) {
        const removeButton = document.createElement("button");
        removeButton.className = "history-remove-button";
        removeButton.type = "button";
        removeButton.title = "Remove past story";
        removeButton.setAttribute("aria-label", `Remove round ${entry.round} from session history`);
        const removeIcon = document.createElement("i");
        removeIcon.dataset.lucide = "trash-2";
        removeIcon.setAttribute("aria-hidden", "true");
        removeButton.append(removeIcon);
        removeButton.addEventListener("click", () => {
          if (!socket || !isHost()) {
            return;
          }
          if (window.confirm("Remove this story from session history?")) {
            socket.emit("remove_history_story", {
              round: entry.round,
              host_token: hostToken,
            });
          }
        });
        historyActions.append(removeButton);
      }
      top.append(round, historyActions);

      const issue = document.createElement("strong");
      issue.className = "history-issue";
      appendStoryContent(issue, entry.issue);

      const voteList = document.createElement("div");
      voteList.className = "history-votes";
      votes.forEach((vote) => {
        const voteElement = document.createElement("span");
        voteElement.className = "history-vote";
        const participant = roomState?.participants.find(
          (currentParticipant) => currentParticipant.id === vote.participant_id,
        );
        const avatarFill = avatarColorValue(
          participant?.avatar_color || vote.avatar_color || "yellow",
        );
        const name = document.createElement("span");
        name.className = "history-vote-name";
        name.style.setProperty("--history-fill", avatarFill);
        name.style.setProperty("--history-ink", avatarTextColor(avatarFill));
        name.textContent = vote.name;
        const value = document.createElement("strong");
        value.className = "history-score";
        value.dataset.voteTone = voteTone(vote.vote);
        value.textContent = vote.vote;
        voteElement.append(name, value);
        voteList.append(voteElement);
      });
      if (!votes.length) {
        const noVotes = document.createElement("span");
        noVotes.className = "history-no-votes";
        noVotes.textContent = "No votes recorded";
        voteList.append(noVotes);
      }

      article.append(top, issue, voteList);
      historyList.append(article);
    });
  }

  function renderSelection() {
    if (!hasValidStory()) {
      selectedCard.hidden = true;
      selectionCaption.textContent = "Waiting for a valid story.";
      return;
    }
    if (currentVote) {
      selectedCard.hidden = false;
      selectedCard.textContent = currentVote === "break" ? "BREAK" : currentVote;
      selectionCaption.textContent = roomState && roomState.revealed
        ? "Your card is on the table."
        : "Your card is locked in privately.";
    } else {
      selectedCard.hidden = true;
      selectionCaption.textContent = roomState && roomState.revealed
        ? "Start a new round to choose again."
        : "Pick privately. The table sees your status.";
    }
  }

  function closeStoryModal() {
    const wasOpen = !storyModal.hidden;
    storyModal.hidden = true;
    storyModalError.hidden = true;
    newStoryButton.disabled = false;
    clearPendingAction();
    if (wasOpen && modalReturnFocus && document.contains(modalReturnFocus)) {
      modalReturnFocus.focus();
    }
    modalReturnFocus = null;
    storyModalMode = "start";
  }

  function openStoryModal(mode) {
    storyModalMode = mode;
    const suggesting = mode === "suggest";
    storyModalEyebrow.textContent = suggesting ? "FOR THE HOST" : "NEXT ROUND";
    storyModalTitle.textContent = suggesting
      ? "Have a story to suggest?"
      : "What are we sizing next?";
    storyModalInputLabel.textContent = suggesting ? "Story suggestion" : "New user story";
    storyModalSubmitLabel.textContent = suggesting ? "Send suggestion" : "Start story";
    newStoryInput.placeholder = suggesting
      ? "Suggest a story for the host to review"
      : "Paste an ADO story or enter a short description";
    storyModalError.hidden = true;
    newStoryButton.disabled = false;
    modalReturnFocus = document.activeElement;
    storyModal.hidden = false;
    newStoryInput.value = "";
    newStoryInput.focus();
    refreshIcons();
  }

  function beginPendingAction(action) {
    clearPendingAction();
    pendingAction = action;
    action.timer = window.setTimeout(() => {
      failPendingAction("The room did not confirm that. Try again.");
    }, 6000);
  }

  function clearPendingAction() {
    if (!pendingAction) {
      return;
    }
    window.clearTimeout(pendingAction.timer);
    pendingAction = null;
  }

  function resolvePendingAction(nextState) {
    if (!pendingAction || !pendingAction.isConfirmed(nextState)) {
      return;
    }
    const action = pendingAction;
    clearPendingAction();
    action.onSuccess();
  }

  function failPendingAction(message) {
    if (!pendingAction) {
      return false;
    }
    const action = pendingAction;
    clearPendingAction();
    action.onFailure(message);
    return true;
  }

  function modalFocusables() {
    return Array.from(
      storyModalPanel.querySelectorAll("button, [href], input, select, textarea, [tabindex]"),
    ).filter((element) => !element.disabled && element.tabIndex !== -1 && !element.hidden);
  }

  function trapModalFocus(event) {
    const focusables = modalFocusables();
    if (!focusables.length) {
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !storyModalPanel.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !storyModalPanel.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  }

  function renderStoryPreview(issue) {
    storyPreview.replaceChildren();
    storyPreview.hidden = !issue;
    if (!issue) {
      return;
    }

    const hasLink = appendStoryContent(storyPreview, issue);
    storyPreview.classList.toggle("has-link", hasLink);
    refreshIcons();
  }

  function appendStoryContent(container, issue) {
    const storyLinkPattern = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s)]+)|\b((?:User Story|Bug|Task|Issue|Feature|Epic)\s+\d+)\b/gi;
    let lastIndex = 0;
    let match;
    let hasLink = false;

    while ((match = storyLinkPattern.exec(issue)) !== null) {
      container.append(document.createTextNode(issue.slice(lastIndex, match.index)));
      const label = match[1] || match[3] || match[4];
      const href = match[2] || match[3] || buildAdoWorkItemUrl(match[4]);
      appendStoryLink(container, label, href);
      lastIndex = storyLinkPattern.lastIndex;
      hasLink = true;
    }

    container.append(document.createTextNode(issue.slice(lastIndex)));
    return hasLink;
  }

  function appendStoryLink(container, label, href) {
    if (!href) {
      container.append(document.createTextNode(label));
      return;
    }
    const link = document.createElement("a");
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.className = "story-link";
    link.title = "Open work item in Azure DevOps";
    link.setAttribute("aria-label", `${label} - Open work item in Azure DevOps`);
    const linkLabel = document.createElement("span");
    linkLabel.textContent = label;
    const linkAction = document.createElement("span");
    linkAction.className = "story-link-action";
    linkAction.textContent = "OPEN IN ADO";
    link.append(linkLabel, linkAction);
    container.append(link);
  }

  function buildAdoWorkItemUrl(workItemLabel) {
    if (!workItemLabel || !adoWorkItemBaseUrl) {
      return "";
    }
    const id = workItemLabel.match(/\d+$/)?.[0];
    if (!id) {
      return "";
    }
    return `${adoWorkItemBaseUrl.replace(/\/?$/, "/")}${id}`;
  }

  function updateRoundStatus() {
    if (roomState.revealed) {
      roundStatus.dataset.state = "revealed";
      roundStatusText.textContent = "Cards revealed";
      return;
    }

    if (roomState.reveal_countdown_ends_at) {
      const remaining = Math.max(
        0,
        Math.ceil((roomState.reveal_countdown_ends_at * 1000 - Date.now()) / 1000),
      );
      if (remaining > 0) {
        roundStatus.dataset.state = "countdown";
        roundStatusText.textContent = `Revealing in ${remaining}s`;
        return;
      }
    }

    roundStatus.dataset.state = roomState.voted_count > 0 ? "active" : "waiting";
    if (roomState.voted_count === roomState.participant_count && roomState.participant_count > 0) {
      roundStatusText.textContent = "All cards are in";
    } else if (roomState.voted_count > 0) {
      roundStatusText.textContent = "Voting in progress";
    } else {
      roundStatusText.textContent = "Waiting for votes";
    }
  }

  function renderRevealCountdown() {
    clearRevealCountdownTimer();
    const endsAt = roomState && !roomState.revealed
      ? roomState.reveal_countdown_ends_at
      : null;
    if (!endsAt) {
      revealCountdown.hidden = true;
      return;
    }

    revealCountdown.hidden = false;
    const updateCountdown = () => {
      const remaining = Math.max(0, Math.ceil((endsAt * 1000 - Date.now()) / 1000));
      revealCountdownValue.textContent = String(remaining);
      updateRoundStatus();
      if (remaining <= 0) {
        clearRevealCountdownTimer();
      }
    };
    updateCountdown();
    revealCountdownTimer = window.setInterval(updateCountdown, 100);
  }

  function clearRevealCountdownTimer() {
    if (revealCountdownTimer === null) {
      return;
    }
    window.clearInterval(revealCountdownTimer);
    revealCountdownTimer = null;
  }

  function currentParticipant() {
    if (!roomState) {
      return null;
    }
    return roomState.participants.find((participant) => participant.id === participantId) || null;
  }

  function renderProfileControls(participant) {
    if (!participant) {
      return;
    }
    const profileName = profileNameInput.value.trim() || participant.name;
    profileAvatar.textContent = initials(profileName);
    profileAvatar.dataset.avatarColor = selectedAvatarColor || participant.avatar_color || "yellow";
    const avatarFill = avatarColorValue(profileAvatar.dataset.avatarColor);
    profileAvatar.style.backgroundColor = avatarFill;
    profileAvatar.style.color = avatarTextColor(avatarFill);
    avatarColorInput.value = avatarFill;
    avatarColorInput.title = "Choose a custom colour";
    avatarColorInput.setAttribute("aria-label", "Choose a custom colour");
    toggleAvatarPalette.style.backgroundColor = avatarFill;
    colorSwatches.forEach((swatch) => {
      swatch.setAttribute(
        "aria-pressed",
        String(avatarColorValue(swatch.dataset.avatarColor) === avatarFill),
      );
    });
    renderProfileColorAvailability(participant);
    saveProfileButton.disabled = profileSavePending || !socket || !socket.connected;
  }

  function renderAvatarPalette(container) {
    AVATAR_PALETTE.forEach(([value, label]) => {
      const swatch = document.createElement("button");
      swatch.className = "color-swatch";
      swatch.type = "button";
      swatch.dataset.avatarColor = value;
      swatch.title = `Use ${label} avatar`;
      swatch.setAttribute("aria-label", `Use ${label} avatar`);
      swatch.setAttribute("aria-pressed", "false");
      swatch.style.backgroundColor = value;
      container.append(swatch);
    });
  }

  function renderEntryColorControls() {
    const occupiedColors = new Set(roomOccupiedAvatarColors.map(colorKey));
    selectAvailableEntryAvatarColor();
    entryColorSwatches.forEach((swatch) => {
      const color = colorKey(swatch.dataset.avatarColor);
      const isOccupied = occupiedColors.has(color);
      swatch.hidden = isOccupied;
      swatch.disabled = isOccupied;
      swatch.setAttribute("aria-disabled", String(isOccupied));
      swatch.setAttribute(
        "aria-pressed",
        String(colorKey(entryAvatarColor) === color),
      );
    });
    entryAvatarColorInput.value = avatarColorValue(entryAvatarColor);
    entryAvatarColorInput.title = "Choose a custom colour";
    entryAvatarColorInput.setAttribute("aria-label", "Choose a custom colour");
    toggleEntryAvatarPalette.style.backgroundColor = avatarColorValue(entryAvatarColor);
    entryColorStatus.textContent = "Pick an available colour for your seat.";
  }

  function renderProfileColorAvailability(participant) {
    const occupiedColors = new Set(roomOccupiedAvatarColors.map(colorKey));
    const currentColor = colorKey(participant.avatar_color);
    colorSwatches.forEach((swatch) => {
      const color = colorKey(swatch.dataset.avatarColor);
      const isOccupied = occupiedColors.has(color) && color !== currentColor;
      swatch.hidden = isOccupied;
      swatch.disabled = false;
      swatch.setAttribute("aria-disabled", "false");
    });
  }

  function selectAvailableEntryAvatarColor() {
    if (!isAvatarColorOccupied(entryAvatarColor)) {
      return;
    }
    const availableSwatch = entryColorSwatches.find(
      (swatch) => !isAvatarColorOccupied(swatch.dataset.avatarColor),
    );
    if (availableSwatch) {
      entryAvatarColor = availableSwatch.dataset.avatarColor;
    }
  }

  function colorKey(value) {
    return avatarColorValue(value).toLowerCase();
  }

  function isAvatarColorOccupied(value) {
    const color = colorKey(value);
    return Boolean(color) && roomOccupiedAvatarColors.some(
      (occupiedColor) => colorKey(occupiedColor) === color,
    );
  }

  function isAvatarColorAvailableForCurrent(value) {
    const participant = currentParticipant();
    if (!participant) {
      return false;
    }
    const color = colorKey(value);
    const currentColor = colorKey(participant.avatar_color);
    return Boolean(color) && (
      color === currentColor || !isAvatarColorOccupied(value)
    );
  }

  function loadRoomAvatarColors(roomId) {
    const normalizedRoomId = roomId.toUpperCase();
    const requestId = ++roomColorRequestId;
    roomOccupiedAvatarColors = [];
    renderEntryColorControls();
    if (!/^[A-Z0-9]{6}$/.test(normalizedRoomId)) {
      roomColorRequest = Promise.resolve();
      return roomColorRequest;
    }

    const headers = {};
    if (participantToken) {
      headers["X-PointRoom-Participant-Token"] = participantToken;
    }
    if (hostToken) {
      headers["X-PointRoom-Host-Token"] = hostToken;
    }
    roomColorRequest = fetch(
      `/api/rooms/${encodeURIComponent(normalizedRoomId)}/avatar-colors`,
      { headers },
    )
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || "Could not check room colours.");
        }
        if (requestId !== roomColorRequestId) {
          return;
        }
        roomOccupiedAvatarColors = Array.isArray(payload.occupied_avatar_colors)
          ? payload.occupied_avatar_colors
          : [];
        if (
          !entryAvatarColorWasSelected
          && typeof payload.own_avatar_color === "string"
          && payload.own_avatar_color
        ) {
          entryAvatarColor = payload.own_avatar_color;
        }
        renderEntryColorControls();
      })
      .catch(() => {
        if (requestId !== roomColorRequestId) {
          return;
        }
        roomOccupiedAvatarColors = [];
        renderEntryColorControls();
      });
    return roomColorRequest;
  }

  function setAvatarPaletteOpen(isOpen) {
    avatarPalette.hidden = !isOpen;
    avatarColorInput.hidden = !isOpen;
    toggleAvatarPalette.setAttribute("aria-expanded", String(isOpen));
    toggleAvatarPalette.title = isOpen ? "Hide avatar palette" : "Show avatar palette";
    toggleAvatarPalette.setAttribute(
      "aria-label",
      isOpen ? "Hide avatar palette" : "Show avatar palette",
    );
  }

  function setEntryAvatarPaletteOpen(isOpen) {
    entryAvatarPalette.hidden = !isOpen;
    entryAvatarColorInput.hidden = !isOpen;
    toggleEntryAvatarPalette.setAttribute("aria-expanded", String(isOpen));
    toggleEntryAvatarPalette.title = isOpen ? "Hide colour palette" : "Show colour palette";
    toggleEntryAvatarPalette.setAttribute(
      "aria-label",
      isOpen ? "Hide colour palette" : "Show colour palette",
    );
  }

  function setCardSize(nextSize) {
    if (!CARD_SIZES.includes(nextSize)) {
      return;
    }
    cardSize = nextSize;
    hostCredentialStorage.setItem(cardSizeStorageKey, cardSize);
    renderCardSizeControls();
  }

  function renderCardSizeControls() {
    deck.dataset.cardSize = cardSize;
    cardSizeControls.forEach((control) => {
      control.setAttribute(
        "aria-pressed",
        String(control.dataset.cardSize === cardSize),
      );
    });
  }

  function hasValidStory() {
    return Boolean(roomState && typeof roomState.issue === "string" && roomState.issue.trim());
  }

  function syncStory() {
    if (!socket || !isHost() || roomState.revealed) {
      return;
    }
    const issue = issueInput.value.trim();
    if (!issue) {
      storySavePending = false;
      setStorySaveStatus("idle", "No story yet");
      return;
    }
    storySavePending = true;
    setStorySaveStatus("saving", "Saving...");
    socket.emit("update_issue", { issue, host_token: hostToken });
  }

  function setStorySaveStatus(state, label) {
    storySaveStatus.dataset.state = state;
    storySaveStatus.textContent = label;
  }

  function isHost() {
    const participant = currentParticipant();
    return Boolean(participant && participant.is_host && hostToken);
  }

  function rememberName(name) {
    displayName = name;
    clientStorage.rememberDisplayName(name);
  }

  function loadRoomCredentials(roomId) {
    const roomCredentials = clientStorage.loadRoomCredentials(roomId);
    participantId = roomCredentials.participantId || browserParticipantId;
    participantToken = roomCredentials.participantToken;
    hostToken = roomCredentials.hostToken;
  }

  async function copyRoomLink() {
    const link = roomUrl(activeRoomId);
    try {
      await navigator.clipboard.writeText(link);
      showToast("Room link copied.", "success");
    } catch (error) {
      const originalLink = shareLink.value;
      shareLink.value = link;
      shareLink.focus();
      shareLink.select();
      document.execCommand("copy");
      shareLink.value = originalLink;
      showToast("Room link copied.", "success");
    }
  }

  function setConnectionState(state, label) {
    connectionState.dataset.state = state;
    connectionLabel.textContent = label;
  }

  function roomUrl(roomId) {
    return `${publicBaseUrl}/room/${roomId}`;
  }

  function showEntryError(message) {
    entryError.textContent = message;
    entryError.hidden = false;
  }

  function clearEntryError() {
    entryError.textContent = "";
    entryError.hidden = true;
  }

  function showToast(message, type) {
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.dataset.type = type;
    toast.classList.add("visible");
    toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 3200);
  }

  function refreshIcons() {
    if (window.lucide) {
      window.lucide.createIcons();
    }
  }

  refreshIcons();
})();
