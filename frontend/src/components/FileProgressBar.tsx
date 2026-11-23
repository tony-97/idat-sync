import type { Component } from "solid-js";
import { progressById } from "../bridge";
import { formatBytes, formatSpeed } from "../format";

interface FileProgressBarProps {
  id: string;
  percent: number | null;
  downloaded: number;
  total: number | null;
  speedBps: number | null;
}

export const FileProgressBar: Component<FileProgressBarProps> = (props) => {
  const live = () => progressById[props.id];
  const pct = () => (live() ? live().percent : props.percent);
  const downloaded = () => (live() ? live().downloaded : props.downloaded);
  const total = () => (live() ? live().total : props.total);
  const speed = () => (live() ? live().speed_bps : props.speedBps);
  const waiting = () => downloaded() === 0 && total() === null;
  const indet = () => waiting() || pct() === null;

  const text = () => {
    if (waiting()) return "Waiting…";
    const parts: string[] = [];
    const p = pct();
    if (p !== null && p !== undefined) parts.push(`${p}%`);
    const dl = formatBytes(downloaded());
    const tot = formatBytes(total());
    if (dl && tot) parts.push(`${dl} / ${tot}`);
    else if (dl) parts.push(dl);
    const sp = formatSpeed(speed());
    if (sp) parts.push(sp);
    return parts.join(" · ") || "…";
  };

  return (
    <div class="fp">
      <div class="bar">
        <u
          class={indet() ? "indet" : ""}
          style={{ width: indet() ? "40%" : `${pct()}%` }}
        />
      </div>
      <span class="pt">{text()}</span>
    </div>
  );
};
