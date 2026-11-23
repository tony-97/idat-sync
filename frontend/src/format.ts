import type { CourseNode, FileNode, FolderNode } from "./types";

const COURSE_COLORS = [
  "#e5484d",
  "#30a46c",
  "#8e4ec6",
  "#3e63dd",
  "#12a594",
  "#d6409f",
  "#f76b15",
  "#978365",
];

const EXT_COLORS: Record<string, string> = {
  PDF: "#e5484d",
  MP4: "#8e4ec6",
  MKV: "#8e4ec6",
  PPTX: "#f76b15",
  PPT: "#f76b15",
  ZIP: "#667085",
  RAR: "#667085",
  "7Z": "#667085",
  DOCX: "#3e63dd",
  DOC: "#3e63dd",
  XLSX: "#30a46c",
  XLS: "#30a46c",
  CSV: "#30a46c",
  HTML: "#0b7288",
  IPYNB: "#d98a00",
};

export function courseColor(courseId: number): string {
  return COURSE_COLORS[Math.abs(courseId) % COURSE_COLORS.length];
}

export function fileExt(name: string): string {
  const idx = name.lastIndexOf(".");
  if (idx <= 0 || idx === name.length - 1) return "FILE";
  const ext = name.slice(idx + 1).toUpperCase();
  return ext.length > 5 ? ext.slice(0, 5) : ext;
}

export function extColor(name: string): string {
  return EXT_COLORS[fileExt(name)] ?? "#667085";
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

export function formatSpeed(bps: number | null | undefined): string {
  if (!bps || bps <= 0) return "";
  const mbps = bps / (1024 * 1024);
  if (mbps >= 0.1) return `${mbps.toFixed(1)} MB/s`;
  const kbps = bps / 1024;
  return `${Math.round(kbps)} KB/s`;
}

export function collectFiles(children: Array<FolderNode | FileNode>): FileNode[] {
  const out: FileNode[] = [];
  for (const child of children) {
    if (child.type === "file") {
      out.push(child);
    } else {
      out.push(...collectFiles(child.children));
    }
  }
  return out;
}

export interface CompactFolder {
  id: string;
  label: string;
  node: FolderNode;
}

export function compactFolder(folder: FolderNode): CompactFolder {
  const labels = [folder.name];
  let cur = folder;
  while (
    cur.children.length === 1 &&
    cur.children[0].type === "folder"
  ) {
    cur = cur.children[0];
    labels.push(cur.name);
  }
  return {
    id: cur.id,
    label: labels.join(" › "),
    node: cur,
  };
}

export function groupCoursesByTerm(
  courses: CourseNode[],
): Array<{ term: string; courses: CourseNode[] }> {
  const map = new Map<string, CourseNode[]>();
  for (const c of courses) {
    const t = c.term || "Other";
    const list = map.get(t);
    if (list) list.push(c);
    else map.set(t, [c]);
  }
  return Array.from(map.entries()).map(([term, list]) => ({
    term,
    courses: list,
  }));
}

export function toggleTheme(): void {
  const root = document.documentElement;
  const isDark = root.dataset.theme
    ? root.dataset.theme === "dark"
    : window.matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = isDark ? "light" : "dark";
}
