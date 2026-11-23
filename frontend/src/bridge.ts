import { createStore, reconcile } from "solid-js/store";
import type {
  ActivitySnapshot,
  AppState,
  FileProgressPayload,
  PywebviewApi,
} from "./types";

const EMPTY_ACTIVITY: ActivitySnapshot = {
  total_pending: 0,
  completed: 0,
  skipped: 0,
  failed_count: 0,
  percent: 0,
  downloading: [],
  queued: [],
  finished: [],
  failed: [],
};

const INITIAL_STATE: AppState = {
  view: "",
  username: "",
  is_login: false,
  mfa_open: false,
  folder_path: "",
  status: "",
  error: "",
  auth_expired: false,
  courses_loaded: false,
  is_syncing: false,
  can_sync: false,
  courses: [],
  activity: EMPTY_ACTIVITY,
};

const [appState, setAppState] = createStore<AppState>(INITIAL_STATE);
const [progressById, setProgressById] = createStore<
  Record<string, FileProgressPayload>
>({});

function normalizeState(raw: Partial<AppState> | null | undefined): AppState {
  if (!raw) return INITIAL_STATE;
  return {
    view: raw.view ?? "",
    username: raw.username ?? "",
    is_login: Boolean(raw.is_login),
    mfa_open: Boolean(raw.mfa_open),
    folder_path: raw.folder_path ?? "",
    status: raw.status ?? "",
    error: raw.error ?? "",
    auth_expired: Boolean(raw.auth_expired),
    courses_loaded: Boolean(raw.courses_loaded),
    is_syncing: Boolean(raw.is_syncing),
    can_sync: Boolean(raw.can_sync),
    courses: raw.courses ?? [],
    activity: raw.activity ?? EMPTY_ACTIVITY,
  };
}

function applyPythonState(raw: Partial<AppState>): void {
  const next = normalizeState(raw);
  setAppState(reconcile(next, { key: "id" }));
  if (!next.is_syncing) {
    setProgressById(reconcile({}));
  }
}

function applyFileProgress(payload: FileProgressPayload): void {
  if (!payload || !payload.id) return;
  setProgressById(payload.id, payload);
}

async function hydrateFromPython(): Promise<void> {
  const api = window.pywebview?.api;
  if (!api) return;
  try {
    const raw = await api.get_state();
    applyPythonState(raw);
  } catch {
    // Ignore transient hydration errors before webview bridge settles
  }
}

export function initBridge(): void {
  window.__onPythonState = applyPythonState;
  window.__onFileProgress = applyFileProgress;
  window.addEventListener("pywebviewready", () => {
    void hydrateFromPython();
  });
  void hydrateFromPython();
}

export function callApi<K extends keyof PywebviewApi>(
  method: K,
  ...args: Parameters<PywebviewApi[K]>
): void {
  const api = window.pywebview?.api;
  if (!api) return;
  const fn = api[method] as (...a: unknown[]) => unknown;
  if (typeof fn === "function") {
    void fn(...args);
  }
}

export { appState, progressById };
