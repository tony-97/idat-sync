import type { Component } from "solid-js";

const PATHS = {
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  chev: "m9 6 6 6-6 6",
  check: "m5 12 5 5 9-10",
  x: "M6 6l12 12M18 6 6 18",
  dl: "M12 4v11m-5-4 5 5 5-5M5 20h14",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
  logout: "M10 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4M15 8l4 4-4 4M19 12H9",
  shield: "M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6zm-3 9 2 2 4-4",
  alert: "M12 9v4m0 4h.01M10.3 3.9 1.8 18.4A2 2 0 0 0 3.5 21h17a2 2 0 0 0 1.7-2.6L13.7 3.9a2 2 0 0 0-3.4 0z",
} as const;

export type IconName = keyof typeof PATHS;

export const Icon: Component<{ name: IconName; size?: number }> = (props) => (
  <svg
    width={props.size ?? 16}
    height={props.size ?? 16}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path d={PATHS[props.name]} />
  </svg>
);
