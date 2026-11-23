import {
  Show,
  createEffect,
  createMemo,
  createSignal,
  type Component,
} from "solid-js";
import { appState } from "./bridge";
import { ActivityPanel } from "./components/ActivityPanel";
import { AuthOnboarding } from "./components/AuthOnboarding";
import { CourseMain, type FileFilter } from "./components/CourseMain";
import { Sidebar } from "./components/Sidebar";
import { SyncBar } from "./components/SyncBar";

export const App: Component = () => {
  const [activeCourseId, setActiveCourseId] = createSignal<number | null>(null);
  const [filter, setFilter] = createSignal<FileFilter>("all");
  const [closedFolders, setClosedFolders] = createSignal<Record<string, boolean>>({});
  const [desktopSidebarOpen, setDesktopSidebarOpen] = createSignal(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = createSignal(false);
  const [activityOpen, setActivityOpen] = createSignal(false);
  const [flashFileId, setFlashFileId] = createSignal<string | null>(null);

  const isDesktop = () => window.matchMedia("(min-width: 960px)").matches;

  let wasSyncing = false;
  createEffect(() => {
    const syncing = appState.is_syncing;
    if (!wasSyncing && syncing && isDesktop()) {
      setActivityOpen(true);
    }
    wasSyncing = syncing;
  });

  createEffect(() => {
    const courses = appState.courses;
    if (courses.length === 0) {
      setActiveCourseId(null);
      return;
    }
    const cur = activeCourseId();
    if (cur === null || !courses.some((c) => c.id === cur)) {
      setActiveCourseId(courses[0].id);
    }
  });

  const activeCourse = createMemo(
    () =>
      appState.courses.find((c) => c.id === activeCourseId()) ??
      appState.courses[0] ??
      null,
  );

  const toggleFolder = (id: string) => {
    setClosedFolders((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const toggleSidebar = () => {
    if (isDesktop()) {
      setDesktopSidebarOpen((v) => !v);
    } else {
      setMobileSidebarOpen((v) => {
        const next = !v;
        if (next) setActivityOpen(false);
        return next;
      });
    }
  };

  const toggleActivity = () => {
    setActivityOpen((v) => {
      const next = !v;
      if (next && !isDesktop()) setMobileSidebarOpen(false);
      return next;
    });
  };

  const jumpToFile = (courseId: number, fileId: string) => {
    setActiveCourseId(courseId);
    setFilter("all");
    setClosedFolders({});
    if (!isDesktop()) setActivityOpen(false);
    setFlashFileId(fileId);
    queueMicrotask(() => {
      const el = document.querySelector(`[data-file-id="${CSS.escape(fileId)}"]`);
      if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    window.setTimeout(() => {
      if (flashFileId() === fileId) setFlashFileId(null);
    }, 1600);
  };

  const showWorkspace = () =>
    appState.view === "sync" && Boolean(appState.folder_path);

  return (
    <div class="win">
      <Show when={showWorkspace()} fallback={<AuthOnboarding />}>
        <div class="body">
          <Sidebar
            activeCourseId={activeCourseId}
            desktopOpen={desktopSidebarOpen}
            mobileOpen={mobileSidebarOpen}
            activityOpen={activityOpen}
            onToggleSidebar={toggleSidebar}
            onCloseMobile={() => setMobileSidebarOpen(false)}
            onToggleActivity={toggleActivity}
            onSelectCourse={(id) => {
              setActiveCourseId(id);
              setFilter("all");
              setMobileSidebarOpen(false);
            }}
          />
          <SyncBar onToggleActivity={toggleActivity} />
          <CourseMain
            course={activeCourse}
            filter={filter}
            onFilterChange={setFilter}
            closedFolders={closedFolders}
            onToggleFolder={toggleFolder}
            flashFileId={flashFileId}
          />
          <ActivityPanel
            open={activityOpen}
            onClose={() => setActivityOpen(false)}
            onJumpToFile={jumpToFile}
          />
        </div>
      </Show>
    </div>
  );
};
