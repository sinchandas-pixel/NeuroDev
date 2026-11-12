"use strict";

const STORAGE = {
  profile: "neurodev.profile.v1",
  sessions: "neurodev.sessions.v1",
  active: "neurodev.active-session.v1",
  project: "neurodev.project-id.v1",
  tabSession: "neurodev.tab-session.v1",
};

const icons = {
  close: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg>',
  voice: '<svg class="voice-icon" viewBox="0 0 16 20" fill="none" aria-hidden="true"><rect x="5" y="1.5" width="6" height="11" rx="3" stroke="currentColor" stroke-width="1.5"/><path d="M2.5 9.5v1a5.5 5.5 0 0 0 11 0v-1M8 16v2.5M5 18.5h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  send: '<svg class="send-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 13V3m0 0L4 7m4-4 4 4"/></svg>',
  copy: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="5" y="5" width="8" height="9" rx="1.5" stroke="currentColor" stroke-width="1.2"/><path d="M10.5 5V3a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h.5" stroke="currentColor" stroke-width="1.2"/></svg>',
  speak: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2.5 6v4h2.7l3.3 2.5v-9L5.2 6H2.5Z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M10.5 5.5a3.5 3.5 0 0 1 0 5m1.5-7a6 6 0 0 1 0 9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
};

const $ = (selector) => document.querySelector(selector);
const make = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};

function readStorage(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch (error) {
    console.error(`Could not read NeuroDev browser data (${key}).`, error);
    return fallback;
  }
}

function writeStorage(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.error(`Could not save NeuroDev browser data (${key}).`, error);
    showToast("Your browser could not save this change. Check available storage space or privacy settings.", true);
  }
}

function readStorageValue(key) {
  try {
    return localStorage.getItem(key);
  } catch (error) {
    console.error(`Could not read NeuroDev browser data (${key}).`, error);
    return null;
  }
}

function writeStorageValue(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (error) {
    console.error(`Could not save NeuroDev browser data (${key}).`, error);
    showToast("Your browser could not save this change. Check available storage space or privacy settings.", true);
  }
}

function readTabSession() {
  try {
    return sessionStorage.getItem(STORAGE.tabSession);
  } catch (error) {
    console.error("Could not read this tab's NeuroDev sign-in state.", error);
    return null;
  }
}

function writeTabSession(token) {
  try {
    if (token) sessionStorage.setItem(STORAGE.tabSession, token);
    else sessionStorage.removeItem(STORAGE.tabSession);
    return true;
  } catch (error) {
    console.error("Could not save this tab's NeuroDev sign-in state.", error);
    return false;
  }
}

const state = {
  account: null,
  profile: null,
  sessions: [],
  activeId: null,
  projectId: null,
  authMode: "login",
  busy: false,
  phase: "",
  recorder: null,
  voiceStream: null,
  voiceAudioContext: null,
  voiceAudioMonitor: null,
  voiceSpeechDetected: false,
  voiceStarting: false,
  voiceTranscribing: false,
  pendingImages: [],
  pendingPdfs: [],
};

function storageKey(key) {
  return state.account ? `${key}.${state.account.id}` : key;
}

function migrateLegacyStorage(accountId) {
  const marker = "neurodev.storage-account-migrated.v1";
  if (readStorageValue(marker)) return;
  try {
    for (const key of [STORAGE.sessions, STORAGE.active, STORAGE.project]) {
      const legacyValue = readStorageValue(key);
      const accountKey = `${key}.${accountId}`;
      if (legacyValue !== null && readStorageValue(accountKey) === null) {
        localStorage.setItem(accountKey, legacyValue);
      }
    }
    localStorage.setItem(marker, "true");
  } catch (error) {
    console.error("Could not migrate this browser's existing conversation data.", error);
    showToast("Existing browser conversations could not be migrated. New conversations will still be saved.", true);
  }
}

function currentSession() {
  return state.sessions.find((session) => session.id === state.activeId) || null;
}

function initials(name) {
  return (name || "N").trim().split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase() || "N";
}

function showToast(message, isError = false) {
  const toast = make("div", `toast${isError ? " error" : ""}`, message);
  $("#toast-region").append(toast);
  window.setTimeout(() => toast.remove(), 4400);
}

function friendlyError(error) {
  if (/connection timeout expired|could not connect to server|connection refused|port: ['"]?5432/i.test(error)) {
    return "PostgreSQL isn't running or reachable at localhost:5432. Start PostgreSQL, check POSTGRES_URL in .env, then retry.";
  }
  return error;
}

function persistSessions() {
  const savedSessions = state.sessions.map((session) => ({
    ...session,
    messages: session.messages.map((message) => ({
      ...message,
      images: message.images?.map(({ name, thumbnail }) => ({ name, thumbnail })),
      pdfs: message.pdfs?.map(({ name }) => ({ name })),
    })),
  }));
  writeStorage(storageKey(STORAGE.sessions), savedSessions);
}

function apiFetch(path, options) {
  if (window.location.protocol === "file:") {
    throw new Error("Open NeuroDev through the backend at http://localhost:8000/ui/ instead of opening index.html directly. Start the server with: uvicorn app.main:app --reload");
  }
  const headers = new Headers(options?.headers || {});
  const tabSession = readTabSession();
  if (tabSession) headers.set("X-NeuroDev-Tab", tabSession);
  return fetch(path, { ...options, headers, credentials: "omit" });
}

function setAuthMessage(message, isError = false) {
  const element = $("#auth-message");
  element.textContent = message;
  element.hidden = !message;
  element.dataset.error = String(isError);
}

const emailDomainSuggestions = {
  "gmail.co": "gmail.com",
  "gmail.con": "gmail.com",
  "gmail.cmo": "gmail.com",
  "gmail.om": "gmail.com",
  "gamil.com": "gmail.com",
  "gmial.com": "gmail.com",
  "gmai.com": "gmail.com",
  "yahoo.co": "yahoo.com",
  "yahoo.con": "yahoo.com",
  "yaho.com": "yahoo.com",
  "hotmai.com": "hotmail.com",
  "hotmail.co": "hotmail.com",
  "hotmail.con": "hotmail.com",
  "outlook.co": "outlook.com",
  "outlook.con": "outlook.com",
};

function updateEmailWarning() {
  const input = $("#auth-email");
  const warning = $("#auth-email-warning");
  const [localPart, domain, extra] = input.value.trim().toLowerCase().split("@");
  const suggestedDomain = !extra && localPart && domain ? emailDomainSuggestions[domain] : null;
  warning.hidden = !(state.authMode === "signup" && suggestedDomain);
  if (suggestedDomain) {
    $("#auth-email-warning-text").textContent =
      `Check this email domain. Did you mean ${localPart}@${suggestedDomain}?`;
    $("#auth-email-correct").dataset.email = `${localPart}@${suggestedDomain}`;
  }
}

function setAuthMode(mode) {
  state.authMode = mode;
  const signingUp = mode === "signup";
  const verifying = mode === "verify";
  const requestingReset = mode === "reset-request";
  const resettingPassword = mode === "reset-password";
  const enteringCode = verifying || resettingPassword;
  const needsPassword = !verifying && !requestingReset;
  $("#auth-name-label").hidden = !signingUp;
  $("#auth-name").hidden = !signingUp;
  $("#auth-name").required = signingUp;
  $("#auth-code-label").hidden = !enteringCode;
  $("#auth-code").hidden = !enteringCode;
  $("#auth-code").required = enteringCode;
  $("#auth-password-label").hidden = !needsPassword;
  $("#auth-password-label").textContent = resettingPassword ? "New password" : "Password";
  $("#auth-password").hidden = !needsPassword;
  $("#auth-password").required = needsPassword;
  $("#auth-password").autocomplete = signingUp || resettingPassword ? "new-password" : "current-password";
  $("#auth-password-visibility").hidden = !needsPassword;
  $("#auth-password").type = "password";
  $("#auth-password-visibility").setAttribute("aria-label", "Show password");
  $("#auth-password-visibility").setAttribute("aria-pressed", "false");
  $("#auth-password-visibility").innerHTML =
    '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2 10s2.9-5 8-5 8 5 8 5-2.9 5-8 5-8-5-8-5Z"/><circle cx="10" cy="10" r="2.2"/></svg>';
  $("#auth-password-hint").hidden = !(signingUp || resettingPassword);
  $("#auth-title").textContent = signingUp
    ? "Create your account."
    : verifying
      ? "Check your email."
      : "Welcome back.";
  if (requestingReset) {
    $("#auth-title").textContent = "Forgot your password?";
  } else if (resettingPassword) {
    $("#auth-title").textContent = "Choose a new password.";
  }
  $("#auth-description").textContent = signingUp
    ? "We’ll email you a six-digit code to verify your address."
    : verifying
      ? "Enter the six-digit code sent to your email address."
      : requestingReset
        ? "Enter your account email and we’ll send a password reset code if a verified account exists."
        : resettingPassword
          ? "Enter the six-digit code from your email and choose a new password."
          : "Sign in to continue to your private workspace.";
  $("#auth-submit").firstChild.textContent = signingUp
    ? "Create account "
    : verifying
      ? "Verify email "
      : requestingReset
        ? "Send reset code "
        : resettingPassword
          ? "Reset password "
          : "Sign in ";
  $("#auth-toggle-copy").firstChild.textContent = verifying
    ? "Already verified? "
    : signingUp
      ? "Already have an account? "
      : requestingReset || resettingPassword
        ? "Remembered your password? "
        : "New to NeuroDev? ";
  $("#auth-toggle").textContent = verifying || signingUp || requestingReset || resettingPassword
    ? "Sign in"
    : "Create an account";
  $("#auth-resend").hidden = !verifying;
  $("#auth-resend").textContent = "Resend verification code";
  $("#auth-forgot-password").hidden = mode !== "login";
  $("#auth-reset-resend").hidden = !resettingPassword;
}

function showAuth(message = "", isError = false) {
  $("#app-shell").hidden = true;
  $("#auth-screen").hidden = false;
  setAuthMessage(message, isError);
}

async function authRequest(path, payload) {
  const response = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg?.replace(/^Value error,\s*/, "")).filter(Boolean).join(" ")
      : data.detail;
    const error = new Error(detail || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function activateAccount(account) {
  state.account = account;
  migrateLegacyStorage(account.id);
  state.profile = readStorage(storageKey(STORAGE.profile), null);
  const storedSessions = readStorage(storageKey(STORAGE.sessions), []);
  state.sessions = Array.isArray(storedSessions) ? storedSessions : [];
  state.activeId = readStorageValue(storageKey(STORAGE.active));
  state.projectId = readStorageValue(storageKey(STORAGE.project));
  if (!currentSession()) state.activeId = null;
  $("#auth-screen").hidden = true;
  $("#app-shell").hidden = false;
  renderProfile();
  renderConversation();
  void loadAdminStatus().catch((error) => {
    console.error("Could not check administrator access.", error);
    showToast("Could not check administrator access. Reload and try again.", true);
  });
}

async function submitAuth(event) {
  event.preventDefault();
  const mode = state.authMode;
  const email = $("#auth-email").value.trim();
  const password = $("#auth-password").value;
  const name = $("#auth-name").value.trim();
  const code = $("#auth-code").value.trim();
  updateEmailWarning();
  if (!$("#auth-email").checkValidity()) {
    setAuthMessage("Enter a valid email address.", true);
    $("#auth-email").focus();
    return;
  }
  if (mode === "signup" && !$("#auth-email-warning").hidden) {
    setAuthMessage("Please correct the email domain before creating your account.", true);
    $("#auth-email").focus();
    return;
  }
  if (mode === "signup" && !name) {
    setAuthMessage("Enter your name.", true);
    $("#auth-name").focus();
    return;
  }
  if ((mode === "signup" || mode === "reset-password") && password.length < 8) {
    setAuthMessage("Choose a password with at least 8 characters. No special characters are required.", true);
    $("#auth-password").focus();
    return;
  }
  if ((mode === "verify" || mode === "reset-password") && !$("#auth-code").checkValidity()) {
    setAuthMessage("Enter the six-digit code from your email.", true);
    $("#auth-code").focus();
    return;
  }
  if (mode !== "verify" && mode !== "reset-request" && !password) {
    setAuthMessage("Enter your password.", true);
    $("#auth-password").focus();
    return;
  }
  if (mode !== "verify" && mode !== "reset-request" && password.length > 128) {
    setAuthMessage("Your password must be 128 characters or fewer.", true);
    $("#auth-password").focus();
    return;
  }
  const endpoint = mode === "signup"
    ? "/auth/signup"
    : mode === "verify"
      ? "/auth/verify"
      : mode === "reset-request"
        ? "/auth/password-reset/request"
        : mode === "reset-password"
          ? "/auth/password-reset/complete"
          : "/auth/login";
  const payload = mode === "verify"
    ? { email, code }
    : mode === "reset-request"
      ? { email }
      : mode === "reset-password"
        ? { email, code, new_password: password }
        : { email, password };
  if (mode === "signup") payload.name = name;
  $("#auth-submit").disabled = true;
  try {
    const data = await authRequest(endpoint, payload);
    if (mode === "signup") {
      setAuthMode("verify");
      $("#auth-password").value = "";
      setAuthMessage(data.message);
      return;
    }
    if (mode === "verify") {
      $("#auth-code").value = "";
      setAuthMode("login");
      setAuthMessage(data.message);
      return;
    }
    if (mode === "reset-request") {
      setAuthMode("reset-password");
      $("#auth-password").value = "";
      setAuthMessage(data.message);
      return;
    }
    if (mode === "reset-password") {
      $("#auth-code").value = "";
      $("#auth-password").value = "";
      setAuthMode("login");
      setAuthMessage(data.message);
      return;
    }
    if (!writeTabSession(data.tab_session)) {
      throw new Error("This browser could not save this tab's sign-in. Enable session storage and sign in again.");
    }
    activateAccount(data.account);
    $("#auth-password").value = "";
  } catch (error) {
    if (mode === "login" && error.status === 403) {
      $("#auth-password").value = "";
      setAuthMode("verify");
      setAuthMessage("Your email address is not verified yet. Enter the code from your email, or request a new one.", true);
    } else if (mode === "signup" && error.status === 503) {
      setAuthMode("verify");
      setAuthMessage(error.message, true);
    } else {
      setAuthMessage(error.message, true);
    }
  } finally {
    $("#auth-submit").disabled = false;
  }
}

async function resendVerification() {
  const email = $("#auth-email").value.trim();
  if (!email) {
    setAuthMessage("Enter your email address first.", true);
    $("#auth-email").focus();
    return;
  }
  try {
    await authRequest("/auth/resend-verification", { email });
    setAuthMessage(
      "Request checked. A code is sent only when this exact address belongs to a pending, unverified signup. If you used another email to sign up, return to Sign in, choose Create an account, and use the correct address. Otherwise, check spam or junk.",
    );
  } catch (error) {
    setAuthMessage(error.message, true);
  }
}

async function requestPasswordResetCode() {
  const email = $("#auth-email").value.trim();
  if (!$("#auth-email").checkValidity()) {
    setAuthMessage("Enter a valid email address first.", true);
    $("#auth-email").focus();
    return;
  }
  try {
    const data = await authRequest("/auth/password-reset/request", { email });
    setAuthMessage(data.message);
  } catch (error) {
    setAuthMessage(error.message, true);
  }
}

async function signOut() {
  try {
    await apiFetch("/auth/logout", { method: "POST" });
  } catch (error) {
    console.error("Could not sign out from the server.", error);
    showToast("Could not sign out. Check your connection and try again.", true);
    return;
  }
  writeTabSession(null);
  state.account = null;
  state.profile = null;
  state.sessions = [];
  state.activeId = null;
  state.projectId = null;
  $("#profile-dialog").close();
  setAuthMode("login");
  showAuth("You have signed out.");
}

async function bootstrapAuth() {
  setAuthMode("login");
  if (!readTabSession()) {
    showAuth();
    return;
  }
  try {
    const response = await apiFetch("/auth/me");
    if (!response.ok) {
      writeTabSession(null);
      throw new Error("This tab's session has expired. Sign in again.");
    }
    const data = await response.json();
    activateAccount(data.account);
  } catch {
    writeTabSession(null);
    showAuth();
  }
}

function relativeGroup(timestamp) {
  const date = new Date(timestamp);
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const age = Math.floor((start - new Date(date.getFullYear(), date.getMonth(), date.getDate())) / 86400000);
  if (age === 0) return "Today";
  if (age === 1) return "Yesterday";
  if (age < 7) return "Previous 7 days";
  return "Earlier";
}

function renderHistory(filter = "") {
  const list = $("#history-list");
  list.replaceChildren();
  const query = filter.trim().toLowerCase();
  const sessions = [...state.sessions]
    .sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt))
    .filter((session) => !query || session.title.toLowerCase().includes(query) || session.messages.some((message) => message.content.toLowerCase().includes(query)));

  if (!sessions.length) {
    list.append(make("p", "history-empty", query ? "No conversations match your search." : "Your conversations will appear here."));
    return;
  }

  const groups = new Map();
  sessions.forEach((session) => {
    const group = relativeGroup(session.updatedAt);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(session);
  });
  groups.forEach((items, title) => {
    const section = make("section", "history-group");
    section.append(make("h2", "history-group-label", title));
    items.forEach((session) => {
      const row = make("div", "history-row");
      const button = make("button", `history-item${session.id === state.activeId ? " active" : ""}`);
      button.type = "button";
      button.title = session.title;
      button.addEventListener("click", () => selectSession(session.id));
      button.append(make("span", "history-item-title", session.title));
      const remove = make("button", "history-item-delete", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", `Delete ${session.title}`);
      remove.addEventListener("click", () => {
        deleteSession(session.id);
      });
      row.append(button, remove);
      section.append(row);
    });
    list.append(section);
  });
}

function renderProfile() {
  const name = state.profile?.name || state.account?.name || "Your profile";
  $("#profile-name").textContent = name;
  $("#profile-caption").textContent = state.account?.email || (state.profile ? "Local profile" : "Set up your profile");
  $("#avatar-initials").textContent = initials(name);
  $("#topbar-profile").textContent = initials(name);
  $("#account-session").hidden = !state.account;
  $("#account-email").textContent = state.account?.email || "";
  $("#local-profile-note").innerHTML = state.account
    ? '<span class="lock-icon" aria-hidden="true"></span> Your account email is verified. This display name is saved only in this browser.'
    : '<span class="lock-icon" aria-hidden="true"></span> This name is saved only in this browser.';
}

async function loadAdminStatus() {
  const response = await apiFetch("/admin/status");
  if (!response.ok) {
    throw new Error(`Could not check administrator access (${response.status}).`);
  }
  const data = await response.json();
  $("#admin-roster-open").hidden = !data.is_admin;
  $("#admin-roster-toolbar").hidden = !data.is_admin;
}

function displayAdminDate(value) {
  if (!value) return "Never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown" : date.toLocaleString();
}

function csvCell(value) {
  let text = String(value ?? "");
  if (/^[\u0000-\u0020]*[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

function exportAdminAccounts(accounts) {
  const columns = [
    ["Name", "name"],
    ["Email", "email"],
    ["Verified", "verified_at"],
    ["Created at", "created_at"],
    ["Last successful login", "last_login_at"],
  ];
  const rows = [
    columns.map(([label]) => csvCell(label)).join(","),
    ...accounts.map((account) =>
      columns.map(([label, key]) => {
        if (key === "verified_at") return csvCell(account[key] ? "Yes" : "No");
        return csvCell(account[key]);
      }).join(","),
    ),
  ];
  const blob = new Blob([`\uFEFF${rows.join("\r\n")}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = make("a");
  link.href = url;
  link.download = `neurodev-accounts-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function openAdminRoster() {
  const dialog = $("#admin-dialog");
  const roster = $("#admin-roster");
  const status = $("#admin-roster-status");
  const summary = $("#admin-summary");
  const exportButton = $("#admin-export");
  roster.replaceChildren();
  summary.hidden = true;
  exportButton.hidden = true;
  exportButton.onclick = null;
  status.textContent = "Loading registered accounts…";
  dialog.showModal();
  try {
    const response = await apiFetch("/admin/accounts");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Could not load accounts (${response.status}).`);
    const accounts = data.accounts;
    $("#admin-total").textContent = String(accounts.length);
    $("#admin-verified").textContent = String(accounts.filter((account) => account.verified_at).length);
    $("#admin-logged-in").textContent = String(accounts.filter((account) => account.last_login_at).length);
    summary.hidden = false;
    exportButton.hidden = accounts.length === 0;
    exportButton.onclick = () => exportAdminAccounts(accounts);
    if (!accounts.length) {
      status.textContent = "No accounts have signed up yet.";
      return;
    }
    accounts.forEach((account) => {
      const card = make("article", "admin-account");
      card.append(
        make("strong", "", account.name),
        make("strong", "", account.email),
        make("span", "", account.verified_at ? "Verified" : "Not verified"),
        make("small", "", `Joined ${displayAdminDate(account.created_at)} · Last login ${displayAdminDate(account.last_login_at)}`),
      );
      roster.append(card);
    });
    status.textContent = `${accounts.length} registered account${accounts.length === 1 ? "" : "s"}`;
  } catch (error) {
    console.error("Could not load the administrator account roster.", error);
    status.textContent = error.message || "Could not load registered accounts.";
  }
}

function formatTime(value) {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function createAssistantMark() {
  const mark = make("span", "assistant-mark");
  const logo = make("span", "brand-mark");
  for (let i = 0; i < 4; i += 1) logo.append(make("span"));
  mark.append(logo);
  return mark;
}

function createMessageRow(message, index, session) {
  const isUser = message.role === "user";
  const row = make("article", `message-row ${isUser ? "user" : "assistant"}`);
  if (!isUser) row.append(createAssistantMark());
  const content = make("div", "message-content");
  const meta = make("div", "message-meta");
  meta.append(make("span", "", isUser ? (state.profile?.name || state.account?.name || "You") : "NeuroDev"));
  const time = make("time", "", formatTime(message.timestamp));
  time.dateTime = message.timestamp;
  meta.append(time);
  const text = make("div", "message-text", message.content);
  content.append(meta, text);
  if (message.images?.length) {
    const images = make("div", "message-images");
    message.images.forEach((image) => {
      if (!image.thumbnail) return;
      const preview = make("img", "message-image");
      preview.src = image.thumbnail;
      preview.alt = image.name || "Attached photo";
      preview.loading = "lazy";
      images.append(preview);
    });
    if (images.childElementCount) content.append(images);
  }
  if (message.pdfs?.length) {
    const attachments = make("div", "message-attachments");
    message.pdfs.forEach((pdf) => {
      const item = make("div", "pdf-attachment-item");
      item.append(make("span", "pdf-badge", "PDF"), make("span", "pdf-attachment-name", pdf.name));
      attachments.append(item);
    });
    content.append(attachments);
  }

  if (!isUser) {
    const actions = make("div", "message-actions");
    const copy = make("button", "message-action", "Copy");
    copy.type = "button";
    copy.innerHTML = icons.copy;
    copy.append(document.createTextNode(" Copy"));
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(message.content);
        showToast("Response copied.");
      } catch (error) {
        console.error("Could not copy the response.", error);
        showToast("Could not copy this response. Check your browser clipboard permissions.", true);
      }
    });
    actions.append(copy);
    if ("speechSynthesis" in window) {
      const speak = make("button", "message-action", "Read aloud");
      speak.type = "button";
      speak.innerHTML = icons.speak;
      speak.append(document.createTextNode(" Read aloud"));
      speak.addEventListener("click", () => {
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(new SpeechSynthesisUtterance(message.content));
      });
      actions.append(speak);
    }
    content.append(actions);
  }

  row.append(content);
  if (isUser) row.append(make("span", "message-avatar", initials(state.profile?.name || state.account?.name)));
  return row;
}

function renderConversation() {
  const session = currentSession();
  const welcome = $("#welcome");
  const messages = $("#message-list");
  const conversation = $("#conversation");
  messages.replaceChildren();

  if (!session || session.messages.length === 0) {
    welcome.classList.remove("is-hidden");
    messages.hidden = true;
    conversation.classList.remove("has-messages");
  } else {
    welcome.classList.add("is-hidden");
    messages.hidden = false;
    conversation.classList.add("has-messages");
    session.messages.forEach((message, index) => messages.append(createMessageRow(message, index, session)));
    if (session.error) messages.append(createErrorRow(session));
    if (state.busy && state.activeId === session.id) messages.append(createTypingRow());
    messages.scrollTop = messages.scrollHeight;
  }
  $("#send-button").disabled = state.busy || (!$("#message-input").value.trim() && !state.pendingImages.length);
  renderHistory($("#history-search").value);
}

function createErrorRow(session) {
  const row = make("article", "message-row assistant");
  row.append(createAssistantMark());
  const content = make("div", "message-content");
  const error = make("div", "inline-error", friendlyError(session.error));
  const retry = make("button", "retry-button", "Retry");
  retry.type = "button";
  retry.addEventListener("click", () => retryLastQuestion(session));
  error.append(retry);
  content.append(error);
  row.append(content);
  return row;
}

function createTypingRow() {
  const row = make("article", "message-row assistant");
  row.append(createAssistantMark());
  const content = make("div", "message-content");
  content.append(make("div", "message-meta", state.phase || "NeuroDev is thinking"));
  const dots = make("div", "typing-indicator");
  for (let i = 0; i < 3; i += 1) dots.append(make("span"));
  content.append(dots);
  row.append(content);
  return row;
}

function startNewChat() {
  const session = { id: crypto.randomUUID(), title: "New conversation", updatedAt: new Date().toISOString(), messages: [], error: "" };
  state.sessions.push(session);
  state.activeId = session.id;
  writeStorageValue(storageKey(STORAGE.active), session.id);
  persistSessions();
  renderConversation();
  closeSidebar();
  $("#message-input").focus();
}

function selectSession(id) {
  state.activeId = id;
  writeStorageValue(storageKey(STORAGE.active), id);
  renderConversation();
  closeSidebar();
}

function deleteSession(id) {
  const session = state.sessions.find((item) => item.id === id);
  if (!session || !window.confirm(`Delete "${session.title}" from this browser?`)) return;
  state.sessions = state.sessions.filter((item) => item.id !== id);
  if (state.activeId === id) {
    state.activeId = state.sessions[0]?.id || null;
    if (state.activeId) writeStorageValue(storageKey(STORAGE.active), state.activeId);
    else {
      try {
        localStorage.removeItem(storageKey(STORAGE.active));
      } catch (error) {
        console.error("Could not clear the active NeuroDev conversation.", error);
        showToast("Could not clear the active conversation from browser storage.", true);
      }
    }
  }
  persistSessions();
  renderConversation();
}

async function ensureProject() {
  if (state.projectId) return state.projectId;
  const projectName = (state.profile?.name || state.account?.name)
    ? `${state.profile?.name || state.account.name}'s workspace`
    : "My NeuroDev workspace";
  const response = await apiFetch("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: projectName, description: "Personal development thinking workspace." }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Workspace setup failed (${response.status}).`);
  }
  if (!data.project_id) throw new Error("The server did not return a workspace ID.");
  state.projectId = data.project_id;
  writeStorageValue(storageKey(STORAGE.project), state.projectId);
  return state.projectId;
}

async function askNeuroDev(session, question, images = [], pdfs = []) {
  state.busy = true;
  state.phase = "Setting up your workspace";
  session.error = "";
  renderConversation();
  try {
    const projectId = await ensureProject();
    state.phase = "Connecting to NeuroDev";
    const response = await apiFetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        session_id: session.id,
        project_id: projectId,
        images,
        pdfs: pdfs.map(({ name, dataBase64 }) => ({ name, data_base64: dataBase64 })),
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Chat request failed (${response.status}).`);
    if (typeof data.response !== "string" || !data.response.trim()) throw new Error("The server returned an empty response.");
    session.messages.push({ role: "assistant", content: data.response, timestamp: new Date().toISOString() });
    session.updatedAt = new Date().toISOString();
    session.error = "";
    persistSessions();
  } catch (error) {
    console.error("NeuroDev could not complete the chat request.", error);
    session.error = friendlyError(error instanceof Error ? error.message : "The request failed unexpectedly.");
    persistSessions();
  } finally {
    state.busy = false;
    state.phase = "";
    renderConversation();
  }
}

async function submitQuestion(question, images = state.pendingImages, pdfs = state.pendingPdfs) {
  if (state.busy) return;
  if (!currentSession()) startNewChat();
  const session = currentSession();
  if (!session) return;
  const attachedImages = images.map(({ name, dataUrl, thumbnail }) => ({ name, dataUrl, thumbnail }));
  const attachedPdfs = pdfs.map(({ name, dataBase64 }) => ({ name, dataBase64 }));
  session.messages.push({ role: "user", content: question, images: attachedImages, pdfs: attachedPdfs, timestamp: new Date().toISOString() });
  if (session.title === "New conversation") {
    session.title = question.replace(/\s+/g, " ").slice(0, 48) || "New conversation";
    if (question.length > 48) session.title = `${session.title.slice(0, 45).trimEnd()}...`;
  }
  session.updatedAt = new Date().toISOString();
  session.error = "";
  state.pendingImages = [];
  state.pendingPdfs = [];
  renderAttachments();
  $("#message-input").value = "";
  resizeComposer();
  persistSessions();
  renderConversation();
  await askNeuroDev(
    session,
    question,
    attachedImages.map(({ name, dataUrl }) => ({ name, data_url: dataUrl })),
    attachedPdfs,
  );
}

function retryLastQuestion(session) {
  if (state.busy) return;
  const lastQuestion = [...session.messages].reverse().find((message) => message.role === "user");
  if (!lastQuestion) {
    showToast("There is no question to retry.", true);
    return;
  }
  if (lastQuestion.images?.some((image) => !image.dataUrl)) {
    showToast("Please reattach the photo from the previous message before retrying.", true);
    return;
  }
  if (lastQuestion.pdfs?.some((pdf) => !pdf.dataBase64)) {
    showToast("Please reattach the PDF from the previous message before retrying.", true);
    return;
  }
  session.error = "";
  const images = (lastQuestion.images || []).map(({ name, dataUrl }) => ({ name, data_url: dataUrl }));
  askNeuroDev(session, lastQuestion.content, images, lastQuestion.pdfs || []);
}

function resizeComposer() {
  const input = $("#message-input");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  $("#send-button").disabled = state.busy || (!input.value.trim() && !state.pendingImages.length && !state.pendingPdfs.length);
}

function renderAttachments() {
  const list = $("#attachment-list");
  list.replaceChildren();
  list.hidden = state.pendingImages.length === 0 && state.pendingPdfs.length === 0;
  state.pendingImages.forEach((image, index) => {
    const item = make("div", "attachment-item");
    const preview = make("img", "attachment-preview");
    preview.src = image.thumbnail;
    preview.alt = "";
    const name = make("span", "attachment-name", image.name);
    const remove = make("button", "attachment-remove", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${image.name}`);
    remove.addEventListener("click", () => {
      state.pendingImages.splice(index, 1);
      renderAttachments();
      resizeComposer();
    });
    item.append(preview, name, remove);
    list.append(item);
  });
  state.pendingPdfs.forEach((pdf, index) => {
    const item = make("div", "pdf-attachment-item");
    const name = make("span", "pdf-attachment-name", pdf.name);
    const remove = make("button", "attachment-remove", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${pdf.name}`);
    remove.addEventListener("click", () => {
      state.pendingPdfs.splice(index, 1);
      renderAttachments();
    });
    item.append(make("span", "pdf-badge", "PDF"), name, remove);
    list.append(item);
  });
  resizeComposer();
}

async function addPdfFiles(files) {
  const selected = Array.from(files);
  if (!selected.length) return;
  if (state.pendingPdfs.length + selected.length > 3) {
    showToast("Attach up to 3 PDFs per message.", true);
    return;
  }
  if (selected.some((file) => file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf"))) {
    showToast("Choose PDF files.", true);
    return;
  }
  if (selected.some((file) => file.size > 10 * 1024 * 1024)) {
    showToast("Each PDF must be 10 MB or smaller.", true);
    return;
  }
  try {
    const prepared = await Promise.all(selected.map(async (file) => ({
      name: file.name,
      dataBase64: await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
        reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
        reader.readAsDataURL(file);
      }),
    })));
    state.pendingPdfs.push(...prepared);
    renderAttachments();
  } catch (error) {
    console.error("Could not read attached PDF.", error);
    showToast(error.message || "Could not read that PDF.", true);
  }
}

async function prepareImage(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1280 / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(1, Math.round(bitmap.width * scale));
  const height = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Could not prepare this photo in the browser.");
  context.drawImage(bitmap, 0, 0, width, height);
  const dataUrl = canvas.toDataURL("image/jpeg", 0.82);
  const thumbScale = Math.min(1, 180 / Math.max(width, height));
  const thumbCanvas = document.createElement("canvas");
  thumbCanvas.width = Math.max(1, Math.round(width * thumbScale));
  thumbCanvas.height = Math.max(1, Math.round(height * thumbScale));
  const thumbContext = thumbCanvas.getContext("2d");
  if (!thumbContext) throw new Error("Could not prepare a preview for this photo.");
  thumbContext.drawImage(bitmap, 0, 0, thumbCanvas.width, thumbCanvas.height);
  bitmap.close();
  return {
    name: file.name,
    dataUrl,
    thumbnail: thumbCanvas.toDataURL("image/jpeg", 0.65),
  };
}

async function addImageFiles(files) {
  const selected = Array.from(files);
  if (!selected.length) return;
  if (state.pendingImages.length + selected.length > 3) {
    showToast("Attach up to 3 photos per message.", true);
    return;
  }
  if (selected.some((file) => !["image/jpeg", "image/png", "image/webp"].includes(file.type))) {
    showToast("Choose JPEG, PNG, or WebP photos.", true);
    return;
  }
  if (selected.some((file) => file.size > 12 * 1024 * 1024)) {
    showToast("Each photo must be 12 MB or smaller.", true);
    return;
  }
  try {
    const prepared = await Promise.all(selected.map(prepareImage));
    if (prepared.some((image) => image.dataUrl.length > 6_000_000)) {
      showToast("A photo is still too large after compression. Choose a smaller image.", true);
      return;
    }
    state.pendingImages.push(...prepared);
    renderAttachments();
  } catch (error) {
    console.error("Could not prepare attached photo.", error);
    showToast(error.message || "Could not load that photo.", true);
  }
}

function openProfile() {
  $("#profile-name-input").value = state.profile?.name || state.account?.name || "";
  $("#profile-dialog").showModal();
  $("#profile-name-input").focus();
}

function openSidebar() {
  $("#sidebar").classList.add("is-open");
  $("#sidebar-scrim").classList.add("is-visible");
}

function closeSidebar() {
  $("#sidebar").classList.remove("is-open");
  $("#sidebar-scrim").classList.remove("is-visible");
}

function resetVoiceButton() {
  $("#voice-button").classList.remove("listening");
  $("#voice-button").disabled = false;
  $("#voice-button").setAttribute("aria-label", "Start voice input");
}

function stopVoiceStream() {
  state.voiceStream?.getTracks().forEach((track) => track.stop());
  state.voiceStream = null;
  if (state.voiceAudioMonitor !== null) {
    window.clearInterval(state.voiceAudioMonitor);
    state.voiceAudioMonitor = null;
  }
  if (state.voiceAudioContext) {
    void state.voiceAudioContext.close().catch((error) => {
      console.error("Could not close the microphone audio monitor.", error);
    });
    state.voiceAudioContext = null;
  }
}

async function monitorVoiceInput(stream) {
  const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextConstructor) {
    state.voiceSpeechDetected = true;
    return;
  }

  try {
    const audioContext = new AudioContextConstructor();
    await audioContext.resume();
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 2048;
    audioContext.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    let activeFrames = 0;
    state.voiceAudioContext = audioContext;
    state.voiceSpeechDetected = false;
    state.voiceAudioMonitor = window.setInterval(() => {
      analyser.getFloatTimeDomainData(samples);
      let sumSquares = 0;
      for (const sample of samples) sumSquares += sample * sample;
      const rms = Math.sqrt(sumSquares / samples.length);
      activeFrames = rms > 0.008 ? activeFrames + 1 : Math.max(0, activeFrames - 1);
      if (activeFrames >= 3 && !state.voiceSpeechDetected) {
        state.voiceSpeechDetected = true;
        $("#voice-transcript").textContent = "Speech detected. Finish speaking, then select “Done speaking”.";
      }
    }, 100);
  } catch (error) {
    console.error("Could not monitor microphone input levels; continuing with transcription.", error);
    state.voiceSpeechDetected = true;
  }
}

async function getVoiceInputStream() {
  const audioConstraints = {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
  const defaultStream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
  const builtInMicrophone = (await navigator.mediaDevices.enumerateDevices()).find(
    (device) => device.kind === "audioinput"
      && /\b(?:microphone array|built[\s-]?in microphone|internal microphone)\b/i.test(device.label),
  );
  const defaultDeviceId = defaultStream.getAudioTracks()[0]?.getSettings().deviceId;

  if (!builtInMicrophone || builtInMicrophone.deviceId === defaultDeviceId) {
    return { stream: defaultStream, usingBuiltInMicrophone: Boolean(builtInMicrophone) };
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { ...audioConstraints, deviceId: { exact: builtInMicrophone.deviceId } },
    });
    defaultStream.getTracks().forEach((track) => track.stop());
    return { stream, usingBuiltInMicrophone: true };
  } catch (error) {
    console.error("Could not open the built-in microphone; using the browser default.", error);
    showToast("Could not open the laptop microphone. Using the browser’s default microphone instead.", true);
    return { stream: defaultStream, usingBuiltInMicrophone: false };
  }
}

async function transcribeRecording(blob) {
  state.voiceTranscribing = true;
  $("#voice-status").textContent = "Transcribing…";
  $("#voice-transcript").textContent = "Turning your speech into text…";
  $("#voice-stop").disabled = true;
  $("#voice-stop").querySelector("span:last-child").textContent = "Transcribing…";
  try {
    const response = await apiFetch("/transcribe", {
      method: "POST",
      headers: { "Content-Type": blob.type || "audio/webm" },
      body: blob,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Speech transcription failed.");
    if (!result.text) {
      showToast("No speech was detected. Try speaking a little closer to the microphone.", true);
      return;
    }
    $("#message-input").value = result.text;
    $("#voice-transcript").textContent = result.text;
    resizeComposer();
  } catch (error) {
    console.error("Voice transcription failed.", error);
    showToast(error.message || "Speech transcription failed. Please try again.", true);
  } finally {
    state.voiceTranscribing = false;
    $("#voice-panel").hidden = true;
    $("#voice-stop").disabled = false;
    $("#voice-stop").querySelector("span:last-child").textContent = "Done speaking";
    $("#voice-status").textContent = "I’m listening";
  }
}

async function startVoiceInput() {
  if (state.recorder) {
    state.recorder.stop();
    return;
  }
  if (state.voiceStarting || state.voiceTranscribing) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    showToast("Audio recording is not supported in this browser. Try an up-to-date Chrome or Edge.", true);
    return;
  }

  state.voiceStarting = true;
  state.voiceSpeechDetected = false;
  $("#voice-button").classList.add("listening");
  $("#voice-button").setAttribute("aria-label", "Stop voice input");
  $("#voice-button").disabled = true;
  $("#voice-panel").hidden = false;
  $("#voice-status").textContent = "I’m listening";
  $("#voice-transcript").textContent = "Speak naturally, then select “Done speaking” to transcribe.";
  try {
    const { stream, usingBuiltInMicrophone } = await getVoiceInputStream();
    state.voiceStream = stream;
    const microphoneName = stream.getAudioTracks()[0]?.label || "microphone";
    $("#voice-status").textContent = usingBuiltInMicrophone
      ? "Using your laptop microphone"
      : `Using ${microphoneName}`;
    $("#voice-transcript").textContent = `Speak clearly into ${microphoneName}, then select “Done speaking”.`;
    await monitorVoiceInput(stream);
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]
      .find((type) => MediaRecorder.isTypeSupported(type));
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    const chunks = [];
    state.recorder = recorder;
    recorder.ondataavailable = (event) => {
      if (event.data.size) chunks.push(event.data);
    };
    recorder.onerror = (event) => {
      console.error("Voice recording failed.", event.error);
      stopVoiceStream();
      state.recorder = null;
      state.voiceStarting = false;
      resetVoiceButton();
      $("#voice-panel").hidden = true;
      showToast("Voice recording failed. Check microphone permissions and try again.", true);
    };
    recorder.onstop = () => {
      const audio = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      const speechDetected = state.voiceSpeechDetected;
      state.recorder = null;
      state.voiceStarting = false;
      resetVoiceButton();
      stopVoiceStream();
      if (audio.size && speechDetected) void transcribeRecording(audio);
      else if (audio.size) {
        $("#voice-panel").hidden = true;
        showToast("Your microphone captured almost no speech. Check the selected microphone and Windows input level, then try again.", true);
      }
      else {
        $("#voice-panel").hidden = true;
        showToast("No audio was recorded. Try again and speak after the microphone starts.", true);
      }
    };
    recorder.start();
    state.voiceStarting = false;
    $("#voice-button").disabled = false;
  } catch (error) {
    stopVoiceStream();
    state.recorder = null;
    state.voiceStarting = false;
    resetVoiceButton();
    $("#voice-panel").hidden = true;
    console.error("Could not access the microphone.", error);
    const message = error.name === "NotAllowedError"
      ? "Microphone access is blocked. Allow it for NeuroDev in VS Code and try again."
      : error.name === "NotFoundError"
        ? "No microphone was found. Connect a microphone and try again."
        : "Could not start voice recording. Check microphone permissions and try again.";
    showToast(message, true);
  }
}

function bindEvents() {
  $("#auth-form").addEventListener("submit", submitAuth);
  $("#auth-password-visibility").addEventListener("click", () => {
    const password = $("#auth-password");
    const visibilityButton = $("#auth-password-visibility");
    const showing = password.type === "password";
    password.type = showing ? "text" : "password";
    visibilityButton.setAttribute("aria-label", showing ? "Hide password" : "Show password");
    visibilityButton.setAttribute("aria-pressed", String(showing));
    visibilityButton.innerHTML = showing
      ? '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 3l14 14M8.6 5.2A8.8 8.8 0 0 1 10 5c5.1 0 8 5 8 5a12.8 12.8 0 0 1-2.4 2.8M5.6 5.8C3.2 7.2 2 10 2 10s2.9 5 8 5c.8 0 1.5-.1 2.2-.3"/><path d="M8.6 8.6a2 2 0 0 0 2.8 2.8"/></svg>'
      : '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2 10s2.9-5 8-5 8 5 8 5-2.9 5-8 5-8-5-8-5Z"/><circle cx="10" cy="10" r="2.2"/></svg>';
  });
  $("#auth-email").addEventListener("input", updateEmailWarning);
  $("#auth-email").addEventListener("blur", updateEmailWarning);
  $("#auth-email-correct").addEventListener("click", () => {
    $("#auth-email").value = $("#auth-email-correct").dataset.email;
    updateEmailWarning();
    setAuthMessage("");
    $("#auth-email").focus();
  });
  $("#auth-toggle").addEventListener("click", () => {
    setAuthMode(state.authMode === "login" ? "signup" : "login");
    setAuthMessage("");
  });
  $("#auth-forgot-password").addEventListener("click", () => {
    $("#auth-code").value = "";
    $("#auth-password").value = "";
    setAuthMode("reset-request");
    setAuthMessage("");
  });
  $("#auth-resend").addEventListener("click", resendVerification);
  $("#auth-reset-resend").addEventListener("click", () => void requestPasswordResetCode());
  $("#sign-out-button").addEventListener("click", () => void signOut());
  $("#admin-roster-open").addEventListener("click", () => void openAdminRoster());
  $("#admin-roster-toolbar").addEventListener("click", () => void openAdminRoster());
  $("#admin-close").addEventListener("click", () => $("#admin-dialog").close());
  $("#admin-dialog").addEventListener("click", (event) => {
    if (event.target === $("#admin-dialog")) $("#admin-dialog").close();
  });
  $("#new-chat").addEventListener("click", startNewChat);
  $("#profile-button").addEventListener("click", openProfile);
  $("#topbar-profile").addEventListener("click", openProfile);
  $("#profile-close").addEventListener("click", () => $("#profile-dialog").close());
  $("#profile-dialog").addEventListener("click", (event) => {
    if (event.target === $("#profile-dialog")) $("#profile-dialog").close();
  });
  $("#profile-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.profile = {
      name: $("#profile-name-input").value.trim(),
    };
    writeStorage(storageKey(STORAGE.profile), state.profile);
    renderProfile();
    renderConversation();
    $("#profile-dialog").close();
    showToast("Your local profile is ready.");
  });
  $("#composer-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const question = $("#message-input").value.trim();
    if ((question || state.pendingImages.length || state.pendingPdfs.length) && !state.busy) {
      submitQuestion(question || (state.pendingPdfs.length ? "Please summarize the attached PDF." : "Please describe the attached photo."));
    }
  });
  $("#message-input").addEventListener("input", resizeComposer);
  $("#message-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("#composer-form").requestSubmit();
    }
  });
  $("#voice-button").innerHTML = icons.voice;
  $("#send-button").innerHTML = icons.send;
  $("#voice-button").addEventListener("click", startVoiceInput);
  $("#voice-stop").addEventListener("click", () => {
    if (state.recorder?.state === "recording") state.recorder.stop();
  });
  $("#history-search").addEventListener("input", (event) => renderHistory(event.target.value));
  $("#suggestion-grid").addEventListener("click", (event) => {
    const button = event.target.closest("[data-prompt]");
    if (!button) return;
    if (!currentSession()) startNewChat();
    $("#message-input").value = button.dataset.prompt;
    resizeComposer();
    $("#message-input").focus();
  });
  $("#attach-button").addEventListener("click", () => $("#image-input").click());
  $("#attach-pdf-button").addEventListener("click", () => $("#pdf-input").click());
  $("#pdf-input").addEventListener("change", (event) => {
    void addPdfFiles(event.target.files);
    event.target.value = "";
  });
  $("#image-input").addEventListener("change", (event) => {
    void addImageFiles(event.target.files);
    event.target.value = "";
  });
  $("#mobile-menu").addEventListener("click", openSidebar);
  $("#sidebar-close").addEventListener("click", closeSidebar);
  $("#sidebar-scrim").addEventListener("click", closeSidebar);
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#history-search").focus();
    }
    if (event.key === "Escape") closeSidebar();
  });
}

$("#new-chat").innerHTML = `<span class="new-chat-icon" aria-hidden="true"></span><span>New conversation</span>`;
$("#sidebar-close").innerHTML = icons.close;
bindEvents();
void bootstrapAuth();
