import { Show, type Component } from "solid-js";
import { appState, callApi } from "../bridge";
import { Icon } from "./Icons";

interface SyncBarProps {
  onToggleActivity: () => void;
}

export const SyncBar: Component<SyncBarProps> = (props) => {
  const act = () => appState.activity;
  const activeNames = () =>
    act()
      .downloading.map((f) => f.name)
      .join(" · ");

  return (
    <div
      class={`syncbar${appState.is_syncing ? " run" : appState.error ? " err" : ""}`}
      onClick={() => props.onToggleActivity()}
    >
      <Show
        when={appState.is_syncing}
        fallback={
          <Show
            when={appState.error}
            fallback={
              <>
                <div class="sbm">
                  <b>
                    <Show when={appState.status === "Sync done."}>
                      <span class="ck">
                        <Icon name="check" size={15} />
                      </span>
                    </Show>
                    {appState.status || "Ready to sync"}
                  </b>
                  <span>
                    {appState.courses.filter((c) => c.checked).length > 0
                      ? `${appState.courses.filter((c) => c.checked).length} selected`
                      : "Falls back to active term"}
                  </span>
                </div>
                <button
                  type="button"
                  class="btn pri"
                  disabled={!appState.can_sync}
                  onClick={(e) => {
                    e.stopPropagation();
                    callApi("sync");
                  }}
                >
                  <Icon name="dl" size={14} />
                  Sync
                </button>
              </>
            }
          >
            <div class="sbm">
              <b style={{ color: "var(--err)" }}>
                <Icon name="alert" size={14} />
                {appState.auth_expired ? "Credentials expired" : "Sync error"}
              </b>
              <span class="sbn" title={appState.error}>
                {appState.error}
              </span>
            </div>
            <Show
              when={appState.auth_expired}
              fallback={
                <Show
                  when={!appState.courses_loaded}
                  fallback={
                    <button
                      type="button"
                      class="btn pri"
                      disabled={!appState.can_sync}
                      onClick={(e) => {
                        e.stopPropagation();
                        callApi("sync");
                      }}
                    >
                      Retry
                    </button>
                  }
                >
                  <button
                    type="button"
                    class="btn pri"
                    onClick={(e) => {
                      e.stopPropagation();
                      callApi("retry_load");
                    }}
                  >
                    Retry
                  </button>
                </Show>
              }
            >
              <button
                type="button"
                class="btn danger"
                onClick={(e) => {
                  e.stopPropagation();
                  callApi("logout");
                }}
              >
                Sign out
              </button>
            </Show>
          </Show>
        }
      >
        <div class="sbm">
          <div class="sbt">
            <b>
              {act().downloading.length > 0
                ? `Downloading ${act().downloading.length} file${act().downloading.length === 1 ? "" : "s"}`
                : appState.status || "Syncing…"}
            </b>
            <span>
              {act().queued.length} queued · {act().percent}%
            </span>
          </div>
          <div class="bar">
            <u
              class={act().total_pending === 0 ? "indet" : ""}
              style={{
                width: act().total_pending === 0 ? "40%" : `${act().percent}%`,
              }}
            />
          </div>
          <div class="sbn">{activeNames() || appState.status || "…"}</div>
        </div>
        <button
          type="button"
          class="btn danger"
          onClick={(e) => {
            e.stopPropagation();
            callApi("cancel_sync");
          }}
        >
          Cancel
        </button>
      </Show>
      <span class="chev">
        <Icon name="chev" />
      </span>
    </div>
  );
};
