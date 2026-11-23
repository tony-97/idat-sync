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
  const [sheetOpen, setSheetOpen] = createSignal(false);
  const [flashFileId, setFlashFileId] = createSignal<string | null>(null);

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

  const jumpToFile = (courseId: number, fileId: string) => {
    setActiveCourseId(courseId);
    setFilter("all");
    setClosedFolders({});
    setSheetOpen(false);
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
            onSelectCourse={(id) => {
              setActiveCourseId(id);
              setFilter("all");
            }}
          />
          <SyncBar onOpenSheet={() => setSheetOpen(true)} />
          <CourseMain
            course={activeCourse}
            filter={filter}
            onFilterChange={setFilter}
            closedFolders={closedFolders}
            onToggleFolder={toggleFolder}
            flashFileId={flashFileId}
          />
          <ActivityPanel
            open={sheetOpen}
            onClose={() => setSheetOpen(false)}
            onJumpToFile={jumpToFile}
          />
        </div>
      </Show>
    </div>
  );
};
