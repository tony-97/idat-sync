import { For, Show, createMemo, createSignal, type Component } from "solid-js";
import { appState, callApi } from "../bridge";
import {
  collectFiles,
  courseColor,
  groupCoursesByTerm,
  toggleTheme,
} from "../format";
import type { CourseNode, RowStatus } from "../types";
import { Icon } from "./Icons";

interface SidebarProps {
  activeCourseId: () => number | null;
  onSelectCourse: (id: number) => void;
}

const ACTIVE_STATUSES = new Set<RowStatus>([
  "Queued",
  "Fetching contents...",
  "Fetching & Syncing",
  "Syncing",
]);

function courseBadge(course: CourseNode): { cls: string; text: string; ok?: boolean } {
  const files = collectFiles(course.children);
  const failed = files.filter((f) => f.status === "Failed").length;
  const active = files.filter((f) => f.status === "Downloading").length;
  const pending = files.filter(
    (f) => f.status === "New" || f.status === "Outdated",
  ).length;
  if (ACTIVE_STATUSES.has(course.status) || active > 0) {
    return { cls: "bdg acc", text: course.progress || "…" };
  }
  if (course.status === "Failed" || failed > 0) {
    return { cls: "bdg err", text: String(failed || "!") };
  }
  if (pending > 0) {
    return { cls: "bdg warn", text: String(pending) };
  }
  if (course.status === "Completed" || course.status === "Synced") {
    return { cls: "bdg ok", text: "", ok: true };
  }
  return { cls: "bdg", text: "" };
}

export const Sidebar: Component<SidebarProps> = (props) => {
  const [mobileOpen, setMobileOpen] = createSignal(false);
  const [collapsedTerms, setCollapsedTerms] = createSignal<Record<string, boolean>>({});

  const termGroups = createMemo(() => groupCoursesByTerm(appState.courses));
  const newestTerm = createMemo(() => termGroups()[0]?.term ?? "");

  const isTermCollapsed = (term: string): boolean => {
    const manual = collapsedTerms()[term];
    if (manual !== undefined) return manual;
    return term !== newestTerm();
  };

  const toggleTermFold = (term: string) => {
    setCollapsedTerms((prev) => ({ ...prev, [term]: !isTermCollapsed(term) }));
  };

  const selectedCount = createMemo(
    () => appState.courses.filter((c) => c.checked).length,
  );

  const activeCourse = createMemo(
    () =>
      appState.courses.find((c) => c.id === props.activeCourseId()) ??
      appState.courses[0] ??
      null,
  );

  return (
    <>
      <div class="pathbar">
        <span class="pi">
          <Icon name="folder" size={20} />
        </span>
        <div class="pp">
          <small>Local folder</small>
          <b title={appState.folder_path}>{appState.folder_path}</b>
        </div>
        <div class="pactions">
          <button
            type="button"
            class="btn"
            disabled={appState.is_syncing}
            onClick={() => callApi("browse_folder")}
          >
            Change
          </button>
          <button
            type="button"
            class="btn ico"
            title="Toggle theme"
            onClick={toggleTheme}
          >
            <Icon name="moon" />
          </button>
          <button
            type="button"
            class="btn ico"
            title="Sign out"
            disabled={appState.is_syncing}
            onClick={() => callApi("logout")}
          >
            <Icon name="logout" />
          </button>
        </div>
      </div>

      <nav class={`courses${mobileOpen() ? " open" : ""}`}>
        <Show when={activeCourse()}>
          {(cur) => (
            <button
              type="button"
              class="ctog"
              onClick={() => setMobileOpen((v) => !v)}
            >
              <span
                class="dot"
                style={{ background: courseColor(cur().id) }}
              />
              <span class="cn">
                <b>{cur().name}</b>
                <small>{cur().term} · tap to switch course</small>
              </span>
              <span class="chv">
                <Icon name="chev" />
              </span>
            </button>
          )}
        </Show>

        <Show when={selectedCount() > 0}>
          <div class="selbar">
            <span>
              <b>
                {selectedCount()} course{selectedCount() === 1 ? "" : "s"}
              </b>{" "}
              selected
            </span>
            <button
              type="button"
              class="btn pri"
              disabled={!appState.can_sync}
              onClick={() => callApi("sync")}
            >
              <Icon name="dl" size={14} />
              Sync
            </button>
            <button
              type="button"
              class="btn"
              disabled={appState.is_syncing}
              onClick={() => callApi("clear_selection")}
            >
              Clear
            </button>
          </div>
        </Show>

        <div class="cl">
          <div class="chd">
            <b>Courses by term</b>
            <button
              type="button"
              class="lnk"
              disabled={appState.is_syncing}
              onClick={() => callApi("select_in_progress")}
            >
              Active term
            </button>
          </div>

          <For each={termGroups()}>
            {(group) => {
              const checkedInTerm = () =>
                group.courses.filter((c) => c.checked).length;
              const triState = () => {
                const n = checkedInTerm();
                if (n === group.courses.length && n > 0) return "on";
                if (n > 0) return "mix";
                return "";
              };
              const collapsed = () => isTermCollapsed(group.term);

              return (
                <div class={`term${collapsed() ? "" : " open"}`}>
                  <div class="th">
                    <button
                      type="button"
                      class="cb"
                      title={`Select all courses in ${group.term}`}
                      disabled={appState.is_syncing}
                      onClick={() => callApi("toggle_term", group.term)}
                    >
                      <i class={`bx ${triState()}`}>
                        <Show when={triState() === "on"}>
                          <Icon name="check" size={12} />
                        </Show>
                        <Show when={triState() === "mix"}>
                          <u />
                        </Show>
                      </i>
                    </button>
                    <button
                      type="button"
                      class="tbt"
                      onClick={() => toggleTermFold(group.term)}
                    >
                      <Icon name="chev" size={14} />
                      <b>{group.term}</b>
                      <small>
                        {group.courses.length} course
                        {group.courses.length === 1 ? "" : "s"}
                      </small>
                    </button>
                  </div>

                  <Show when={!collapsed()}>
                    <For each={group.courses}>
                      {(course) => {
                        const badge = () => courseBadge(course);
                        return (
                          <div class="crow">
                            <button
                              type="button"
                              class="cb"
                              title="Select course"
                              disabled={appState.is_syncing}
                              onClick={() => callApi("toggle_course", course.id)}
                            >
                              <i class={`bx ${course.checked ? "on" : ""}`}>
                                <Show when={course.checked}>
                                  <Icon name="check" size={12} />
                                </Show>
                              </i>
                            </button>
                            <button
                              type="button"
                              class={`course${props.activeCourseId() === course.id ? " on" : ""}`}
                              onClick={() => {
                                props.onSelectCourse(course.id);
                                setMobileOpen(false);
                              }}
                            >
                              <span
                                class="dot"
                                style={{ background: courseColor(course.id) }}
                              />
                              <span class="cn">
                                <b>{course.name}</b>
                                <small>{course.status}</small>
                              </span>
                              <Show when={badge().ok || badge().text}>
                                <span class={badge().cls}>
                                  <Show when={badge().ok} fallback={badge().text}>
                                    <Icon name="check" size={12} />
                                  </Show>
                                </span>
                              </Show>
                            </button>
                          </div>
                        );
                      }}
                    </For>
                  </Show>
                </div>
              );
            }}
          </For>
        </div>
      </nav>
    </>
  );
};
