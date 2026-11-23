export type RowStatus =
  | "Idle"
  | "Queued"
  | "Fetching contents..."
  | "Fetching & Syncing"
  | "Syncing"
  | "Synced"
  | "New"
  | "Outdated"
  | "Downloading"
  | "Completed"
  | "Failed";

export interface FileNode {
  id: string;
  type: "file";
  name: string;
  rel_path: string;
  status: RowStatus;
  percent: number | null;
  downloaded: number;
  total: number | null;
  speed_bps: number | null;
  error: string;
}

export interface FolderNode {
  id: string;
  type: "folder";
  name: string;
  status: RowStatus;
  open: boolean;
  children: Array<FolderNode | FileNode>;
}

export interface CourseNode {
  id: number;
  name: string;
  term: string;
  in_progress: boolean;
  checked: boolean;
  status: RowStatus;
  progress: string;
  open: boolean;
  children: Array<FolderNode | FileNode>;
}

export interface ActivityItemBase {
  id: string;
  course_id: number;
  course_name: string;
  name: string;
  rel_path: string;
}

export interface ActivityDownloadingItem extends ActivityItemBase {
  downloaded: number;
  total: number | null;
  percent: number | null;
  speed_bps: number | null;
}

export interface ActivityQueuedItem extends ActivityItemBase {
  status: Extract<RowStatus, "New" | "Outdated">;
}

export interface ActivityFinishedItem extends ActivityItemBase {
  downloaded: number;
  total: number | null;
}

export interface ActivityFailedItem extends ActivityItemBase {
  error: string;
}

export interface ActivitySnapshot {
  total_pending: number;
  completed: number;
  skipped: number;
  failed_count: number;
  percent: number;
  downloading: ActivityDownloadingItem[];
  queued: ActivityQueuedItem[];
  finished: ActivityFinishedItem[];
  failed: ActivityFailedItem[];
}

export interface FileProgressPayload {
  id: string;
  course_id: number;
  downloaded: number;
  total: number | null;
  percent: number | null;
  speed_bps: number | null;
}

export interface AppState {
  view: "login" | "sync" | "";
  username: string;
  is_login: boolean;
  mfa_open: boolean;
  folder_path: string;
  status: string;
  error: string;
  auth_expired: boolean;
  courses_loaded: boolean;
  is_syncing: boolean;
  can_sync: boolean;
  courses: CourseNode[];
  activity: ActivitySnapshot;
}

export interface PywebviewApi {
  get_state(): Promise<Partial<AppState>> | Partial<AppState>;
  login(username: string, password: string): Promise<void> | void;
  submit_mfa(code: string): Promise<void> | void;
  cancel_mfa(): Promise<void> | void;
  browse_folder(): Promise<string> | string;
  select_in_progress(): Promise<void> | void;
  select_all(): Promise<void> | void;
  clear_selection(): Promise<void> | void;
  toggle_course(courseId: number): Promise<void> | void;
  toggle_term(term: string): Promise<void> | void;
  sync(): Promise<void> | void;
  sync_course(courseId: number): Promise<void> | void;
  cancel_sync(): Promise<void> | void;
  retry_load(): Promise<void> | void;
  open_file(fileId: string): Promise<void> | void;
  logout(): Promise<void> | void;
}

declare global {
  interface Window {
    pywebview?: { api?: PywebviewApi };
    __onPythonState?: (state: Partial<AppState>) => void;
    __onFileProgress?: (payload: FileProgressPayload) => void;
  }
}
