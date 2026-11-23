import { For, Show, createMemo, type Component } from "solid-js";
import { appState, callApi } from "../bridge";
import {
  collectFiles,
  compactFolder,
  courseColor,
  extColor,
  fileExt,
} from "../format";
import type { CourseNode, FileNode, FolderNode, RowStatus } from "../types";
import { FileProgressBar } from "./FileProgressBar";
import { Icon } from "./Icons";

export type FileFilter = "all" | "need" | "sync" | "done" | "failed";

interface CourseMainProps {
  course: () => CourseNode | null;
  filter: () => FileFilter;
  onFilterChange: (f: FileFilter) => void;
  closedFolders: () => Record<string, boolean>;
  onToggleFolder: (id: string) => void;
  flashFileId: () => string | null;
}

function matchesFilter(file: FileNode, filter: FileFilter): boolean {
  switch (filter) {
    case "need":
      return file.status === "New" || file.status === "Outdated";
    case "sync":
      return file.status === "Downloading";
    case "done":
      return file.status === "Completed" || file.status === "Synced";
    case "failed":
      return file.status === "Failed";
    default:
      return true;
  }
}

interface StatusMeta {
  label: string;
  cls: string;
  subtitle: string;
  spin?: boolean;
  ok?: boolean;
}

const STATUS_META: Partial<Record<RowStatus, StatusMeta>> = {
  Synced: { label: "Up to date", cls: "pill ok", subtitle: "synced", ok: true },
  Completed: { label: "Updated", cls: "pill ok", subtitle: "updated just now", ok: true },
  Outdated: { label: "Outdated", cls: "pill warn", subtitle: "newer version online" },
  New: { label: "New", cls: "pill new", subtitle: "not downloaded yet" },
  Downloading: { label: "Downloading", cls: "pill acc", subtitle: "downloading", spin: true },
  Failed: { label: "Failed", cls: "pill err", subtitle: "download failed" },
};

function getStatusMeta(status: RowStatus): StatusMeta {
  return (
    STATUS_META[status] ?? {
      label: status,
      cls: "pill mut",
      subtitle: status.toLowerCase(),
    }
  );
}

const FileRow: Component<{ file: FileNode; flash: boolean }> = (props) => {
  const isDownloading = () => props.file.status === "Downloading";
  const canOpen = () =>
    props.file.status === "Synced" || props.file.status === "Completed";
  const meta = () => getStatusMeta(props.file.status);

  return (
    <div
      class={`file${props.flash ? " flash" : ""}${canOpen() ? " clickable" : ""}`}
      data-file-id={props.file.id}
      role={canOpen() ? "button" : undefined}
      tabIndex={canOpen() ? 0 : undefined}
      onClick={() => {
        if (canOpen()) callApi("open_file", props.file.id);
      }}
      onKeyDown={(e) => {
        if (canOpen() && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          callApi("open_file", props.file.id);
        }
      }}
    >
      <span
        class="ext"
        style={{ "--c": extColor(props.file.name) }}
      >
        {fileExt(props.file.name)}
      </span>
      <div class="fi">
        <b title={props.file.name}>{props.file.name}</b>
        <small>{meta().subtitle}</small>
        <Show when={props.file.status === "Failed" && props.file.error}>
          <span class="ferr">{props.file.error}</span>
        </Show>
        <Show when={isDownloading()}>
          <FileProgressBar
            id={props.file.id}
            percent={props.file.percent}
            downloaded={props.file.downloaded}
            total={props.file.total}
            speedBps={props.file.speed_bps}
          />
        </Show>
      </div>
      <span class={meta().cls}>
        <Show when={meta().spin}>
          <i class="spin" />
        </Show>
        <Show when={meta().ok}>
          <Icon name="check" size={12} />
        </Show>
        {meta().label}
      </span>
    </div>
  );
};

const FolderBranch: Component<{
  folder: FolderNode;
  filter: () => FileFilter;
  closedFolders: () => Record<string, boolean>;
  onToggleFolder: (id: string) => void;
  flashFileId: () => string | null;
}> = (props) => {
  const compacted = createMemo(() => compactFolder(props.folder));
  const allFiles = createMemo(() => collectFiles(compacted().node.children));
  const visibleFiles = createMemo(() =>
    allFiles().filter((f) => matchesFilter(f, props.filter())),
  );
  const isOpen = () => !props.closedFolders()[compacted().id];
  const activeCount = () =>
    allFiles().filter((f) => f.status === "Downloading").length;
  const needCount = () =>
    allFiles().filter((f) => f.status === "New" || f.status === "Outdated").length;
  const failedCount = () =>
    allFiles().filter((f) => f.status === "Failed").length;

  return (
    <Show when={visibleFiles().length > 0}>
      <div class={`fold${isOpen() ? " open" : ""}`}>
        <button
          type="button"
          class="fh"
          onClick={() => props.onToggleFolder(compacted().id)}
        >
          <Icon name="chev" size={14} />
          <Icon name="folder" size={16} />
          <b title={compacted().label}>{compacted().label}</b>
          <span class="fc">{allFiles().length} files</span>
          <Show when={activeCount() > 0}>
            <span class="bdg acc">↓ {activeCount()}</span>
          </Show>
          <Show when={activeCount() === 0 && failedCount() > 0}>
            <span class="bdg err">{failedCount()} failed</span>
          </Show>
          <Show when={activeCount() === 0 && failedCount() === 0 && needCount() > 0}>
            <span class="bdg warn">{needCount()} to update</span>
          </Show>
        </button>

        <Show when={isOpen()}>
          <div class="fch">
            <For each={compacted().node.children}>
              {(child) =>
                child.type === "folder" ? (
                  <FolderBranch
                    folder={child}
                    filter={props.filter}
                    closedFolders={props.closedFolders}
                    onToggleFolder={props.onToggleFolder}
                    flashFileId={props.flashFileId}
                  />
                ) : (
                  <Show when={matchesFilter(child, props.filter())}>
                    <FileRow
                      file={child}
                      flash={props.flashFileId() === child.id}
                    />
                  </Show>
                )
              }
            </For>
          </div>
        </Show>
      </div>
    </Show>
  );
};

export const CourseMain: Component<CourseMainProps> = (props) => {
  const allFiles = createMemo(() =>
    props.course() ? collectFiles(props.course()!.children) : [],
  );
  const syncedCount = () =>
    allFiles().filter((f) => f.status === "Synced" || f.status === "Completed").length;
  const outdatedCount = () =>
    allFiles().filter((f) => f.status === "Outdated").length;
  const newCount = () => allFiles().filter((f) => f.status === "New").length;
  const syncCount = () =>
    allFiles().filter((f) => f.status === "Downloading").length;
  const failedCount = () =>
    allFiles().filter((f) => f.status === "Failed").length;
  const visibleCount = () =>
    allFiles().filter((f) => matchesFilter(f, props.filter())).length;

  return (
    <main class="main">
      <Show
        when={props.course()}
        fallback={
          <div class="empty">
            {appState.courses_loaded
              ? "No courses found."
              : "Loading enrolled courses…"}
          </div>
        }
      >
        {(course) => (
          <>
            <div class="chead">
              <div class="ct">
                <span
                  class="dot"
                  style={{
                    background: courseColor(course().id),
                    width: "14px",
                    height: "14px",
                  }}
                />
                <div style={{ "min-width": 0 }}>
                  <h1>{course().name}</h1>
                  <p>
                    <Icon name="folder" size={13} />
                    {appState.folder_path}/{course().name}
                  </p>
                </div>
              </div>
              <button
                type="button"
                class="btn pri"
                disabled={!appState.can_sync}
                onClick={() => callApi("sync_course", course().id)}
              >
                <Icon name="dl" size={14} />
                {appState.is_syncing ? "Syncing…" : "Sync course"}
              </button>
              <div class="stats">
                <span>
                  <i class="sd" />
                  {syncedCount()} up to date
                </span>
                <span>
                  <i class="sd warn" />
                  {outdatedCount()} outdated
                </span>
                <span>
                  <i class="sd new" />
                  {newCount()} new
                </span>
                <Show when={failedCount() > 0}>
                  <span>
                    <i class="sd err" />
                    {failedCount()} failed
                  </span>
                </Show>
                <span>{allFiles().length} discovered</span>
              </div>
            </div>

            <div class="filters">
              <button
                type="button"
                class={`chip${props.filter() === "all" ? " on" : ""}`}
                onClick={() => props.onFilterChange("all")}
              >
                All <em>{allFiles().length}</em>
              </button>
              <button
                type="button"
                class={`chip${props.filter() === "need" ? " on" : ""}`}
                onClick={() => props.onFilterChange("need")}
              >
                Needs update <em>{newCount() + outdatedCount()}</em>
              </button>
              <button
                type="button"
                class={`chip${props.filter() === "sync" ? " on" : ""}${syncCount() > 0 ? " live" : ""}`}
                onClick={() => props.onFilterChange("sync")}
              >
                Syncing <em>{syncCount()}</em>
              </button>
              <button
                type="button"
                class={`chip${props.filter() === "done" ? " on" : ""}`}
                onClick={() => props.onFilterChange("done")}
              >
                Updated <em>{syncedCount()}</em>
              </button>
              <Show when={failedCount() > 0}>
                <button
                  type="button"
                  class={`chip${props.filter() === "failed" ? " on" : ""}`}
                  onClick={() => props.onFilterChange("failed")}
                >
                  Failed <em>{failedCount()}</em>
                </button>
              </Show>
            </div>

            <div class="tree">
              <Show
                when={visibleCount() > 0}
                fallback={
                  <div class="empty">
                    {allFiles().length === 0
                      ? 'No files discovered yet. Click "Sync course" to scan and download.'
                      : props.filter() === "sync"
                        ? "Nothing is downloading in this course right now."
                        : "No files match this filter."}
                  </div>
                }
              >
                <For each={course().children}>
                  {(child) =>
                    child.type === "folder" ? (
                      <FolderBranch
                        folder={child}
                        filter={props.filter}
                        closedFolders={props.closedFolders}
                        onToggleFolder={props.onToggleFolder}
                        flashFileId={props.flashFileId}
                      />
                    ) : (
                      <Show when={matchesFilter(child, props.filter())}>
                        <FileRow
                          file={child}
                          flash={props.flashFileId() === child.id}
                        />
                      </Show>
                    )
                  }
                </For>
              </Show>
            </div>
          </>
        )}
      </Show>
    </main>
  );
};
