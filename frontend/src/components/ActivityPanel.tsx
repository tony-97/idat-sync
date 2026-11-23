import { For, Show, type Component } from "solid-js";
import { appState, callApi, progressById } from "../bridge";
import {
  courseColor,
  extColor,
  fileExt,
  formatSpeed,
} from "../format";
import { FileProgressBar } from "./FileProgressBar";
import { Icon } from "./Icons";

interface ActivityPanelProps {
  open: () => boolean;
  onClose: () => void;
  onJumpToFile: (courseId: number, fileId: string) => void;
}

export const ActivityPanel: Component<ActivityPanelProps> = (props) => {
  const act = () => appState.activity;

  const totalSpeed = () => {
    let sum = 0;
    for (const item of act().downloading) {
      const live = progressById[item.id];
      const sp = live ? live.speed_bps : item.speed_bps;
      if (sp && sp > 0) sum += sp;
    }
    return sum;
  };

  return (
    <>
      <div
        class={`scrim${props.open() ? " on" : ""}`}
        onClick={props.onClose}
      />
      <aside class={`act${props.open() ? " open" : ""}`}>
        <div class="ah">
          <div class="ahr">
            <h2>Activity</h2>
            <div class="ahb">
              <Show when={appState.is_syncing}>
                <button
                  type="button"
                  class="btn danger"
                  onClick={() => callApi("cancel_sync")}
                >
                  Cancel
                </button>
              </Show>
              <button
                type="button"
                class="btn ico"
                title="Close"
                onClick={props.onClose}
              >
                <Icon name="x" size={14} />
              </button>
            </div>
          </div>

          <Show
            when={act().total_pending > 0}
            fallback={
              <div class="ov">
                <small>
                  {appState.is_syncing
                    ? "Scanning course contents…"
                    : 'No sync running. Click "Sync" to start.'}
                </small>
              </div>
            }
          >
            <div class="ov">
              <b>{act().percent}%</b>
              <small>
                {act().completed} of {act().total_pending} files
                {appState.is_syncing && totalSpeed() > 0
                  ? ` · ${formatSpeed(totalSpeed())}`
                  : ""}
              </small>
            </div>
            <div class="bar">
              <u style={{ width: `${act().percent}%` }} />
            </div>
          </Show>
        </div>

        <div class="ab">
          <div class="sec">
            <h3>
              Downloading now<span>{act().downloading.length}</span>
            </h3>
            <Show
              when={act().downloading.length > 0}
              fallback={<small>Nothing downloading</small>}
            >
              <For each={act().downloading}>
                {(item) => (
                  <button
                    type="button"
                    class="ai"
                    onClick={() => props.onJumpToFile(item.course_id, item.id)}
                  >
                    <span
                      class="ext"
                      style={{ "--c": extColor(item.name) }}
                    >
                      {fileExt(item.name)}
                    </span>
                    <div class="fi">
                      <b title={item.name}>{item.name}</b>
                      <small>{item.course_name}</small>
                      <FileProgressBar
                        id={item.id}
                        percent={item.percent}
                        downloaded={item.downloaded}
                        total={item.total}
                        speedBps={item.speed_bps}
                      />
                    </div>
                  </button>
                )}
              </For>
            </Show>
          </div>

          <Show when={act().queued.length > 0}>
            <div class="sec">
              <h3>
                Up next<span>{act().queued.length}</span>
              </h3>
              <For each={act().queued.slice(0, 6)}>
                {(item) => (
                  <div class="mini">
                    <span
                      class="dot"
                      style={{ background: courseColor(item.course_id) }}
                    />
                    <span class="mn" title={item.name}>
                      {item.name}
                    </span>
                    <small>{item.course_name}</small>
                  </div>
                )}
              </For>
              <Show when={act().queued.length > 6}>
                <small>+{act().queued.length - 6} more</small>
              </Show>
            </div>
          </Show>

          <Show when={act().failed.length > 0}>
            <div class="sec">
              <h3 style={{ color: "var(--err)" }}>
                Failed<span>{act().failed.length}</span>
              </h3>
              <For each={act().failed}>
                {(item) => (
                  <button
                    type="button"
                    class="ai"
                    onClick={() => props.onJumpToFile(item.course_id, item.id)}
                  >
                    <span
                      class="ext"
                      style={{ "--c": "var(--err)" }}
                    >
                      {fileExt(item.name)}
                    </span>
                    <div class="fi">
                      <b title={item.name}>{item.name}</b>
                      <small>{item.course_name}</small>
                      <span class="ferr">{item.error || "Failed"}</span>
                    </div>
                  </button>
                )}
              </For>
            </div>
          </Show>

          <Show when={act().finished.length > 0}>
            <div class="sec">
              <h3>
                Finished<span>{act().finished.length}</span>
              </h3>
              <For each={act().finished.slice(-6).reverse()}>
                {(item) => (
                  <div class="mini">
                    <span
                      class="dot"
                      style={{ background: courseColor(item.course_id) }}
                    />
                    <span class="mn" title={item.name}>
                      {item.name}
                    </span>
                    <span class="ck">
                      <Icon name="check" size={13} />
                    </span>
                  </div>
                )}
              </For>
            </div>
          </Show>
        </div>

        <div class="af">
          <Icon name="check" size={14} />
          {act().skipped} files already up to date were skipped
        </div>
      </aside>
    </>
  );
};
