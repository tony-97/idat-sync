import { Show, createSignal, type Component } from "solid-js";
import { appState, callApi } from "../bridge";
import { toggleTheme } from "../format";
import { Icon } from "./Icons";

export const AuthOnboarding: Component = () => {
  const [username, setUsername] = createSignal(appState.username);
  const [password, setPassword] = createSignal("");
  const [showPw, setShowPw] = createSignal(false);
  const [mfaCode, setMfaCode] = createSignal("");
  const [localErr, setLocalErr] = createSignal("");

  const submitLogin = (e: SubmitEvent) => {
    e.preventDefault();
    const u = username().trim();
    const p = password();
    if (!u || !p) {
      setLocalErr("Enter your username and password.");
      return;
    }
    setLocalErr("");
    callApi("login", u, p);
  };

  const submitMfa = (e: SubmitEvent) => {
    e.preventDefault();
    const code = mfaCode().trim();
    if (code.length < 6) {
      setLocalErr("Enter the 6-digit verification code.");
      return;
    }
    setLocalErr("");
    callApi("submit_mfa", code);
    setMfaCode("");
  };

  const errorText = () => localErr() || appState.error;

  return (
    <div class="onb">
      <div class="onb-tools">
        <button
          type="button"
          class="btn ico"
          title="Toggle theme"
          onClick={toggleTheme}
        >
          <Icon name="moon" />
        </button>
        <Show when={appState.view === "sync"}>
          <button
            type="button"
            class="btn ico"
            title="Sign out"
            disabled={appState.is_syncing}
            onClick={() => callApi("logout")}
          >
            <Icon name="logout" />
          </button>
        </Show>
      </div>

      <Show when={appState.view === "login"}>
        <Show
          when={!appState.mfa_open}
          fallback={
            <form class="ob" onSubmit={submitMfa}>
              <div class="obi">
                <Icon name="shield" size={30} />
              </div>
              <h1>Enter your verification code</h1>
              <p>Enter the 6-digit verification code from your authenticator or SMS.</p>
              <input
                class="otp"
                inputmode="numeric"
                maxlength="6"
                autocomplete="one-time-code"
                placeholder="······"
                value={mfaCode()}
                onInput={(e) =>
                  setMfaCode(e.currentTarget.value.replace(/\D/g, "").slice(0, 6))
                }
                autofocus
              />
              <Show when={errorText()}>
                <div class="note err">{errorText()}</div>
              </Show>
              <button type="submit" class="btn pri lg w">
                Verify and continue
              </button>
              <div class="rs">
                <button
                  type="button"
                  class="lnk"
                  onClick={() => {
                    setMfaCode("");
                    setLocalErr("");
                    callApi("cancel_mfa");
                  }}
                >
                  Cancel verification
                </button>
              </div>
            </form>
          }
        >
          <Show
            when={!appState.is_login}
            fallback={
              <div class="ob">
                <div class="obi">
                  <i class="spin" style={{ width: "26px", height: "26px" }} />
                </div>
                <h1>Signing you in…</h1>
                <p>Checking your credentials with Moodle and SharePoint.</p>
                <div class="bar">
                  <u class="indet" />
                </div>
              </div>
            }
          >
            <form class="ob" onSubmit={submitLogin}>
              <div class="obi">
                <Icon name="dl" size={30} />
              </div>
              <h1>Sign in to IDAT Sync</h1>
              <p>Use your institutional account to load and synchronize your courses.</p>
              <label class="fld">
                Username or email
                <input
                  autocomplete="username"
                  placeholder="user@idat.edu.pe"
                  value={username()}
                  onInput={(e) => setUsername(e.currentTarget.value)}
                  autofocus
                />
              </label>
              <label class="fld">
                Password
                <div class="pw">
                  <input
                    type={showPw() ? "text" : "password"}
                    autocomplete="current-password"
                    placeholder="Your password"
                    value={password()}
                    onInput={(e) => setPassword(e.currentTarget.value)}
                  />
                  <button type="button" onClick={() => setShowPw((v) => !v)}>
                    {showPw() ? "Hide" : "Show"}
                  </button>
                </div>
              </label>
              <Show when={errorText()}>
                <div class="note err">{errorText()}</div>
              </Show>
              <button type="submit" class="btn pri lg w">
                Sign in
              </button>
            </form>
          </Show>
        </Show>
      </Show>

      <Show when={appState.view === "sync"}>
        <Show
          when={!appState.courses_loaded && appState.is_syncing}
          fallback={
            <div class="ob">
              <div class="obi">
                <Icon name={appState.error ? "alert" : "folder"} size={30} />
              </div>
              <h1>Keep your course files in sync</h1>
              <p>
                Pick a folder on this device. IDAT Sync creates one folder per
                course and downloads only files that are new or changed.
              </p>
              <Show when={appState.error}>
                <div class="note err">{appState.error}</div>
              </Show>
              <div class="rs">
                <Show
                  when={!appState.courses_loaded && appState.error}
                  fallback={
                    <button
                      type="button"
                      class="btn pri lg"
                      onClick={() => callApi("browse_folder")}
                    >
                      Choose folder…
                    </button>
                  }
                >
                  <button
                    type="button"
                    class="btn pri lg"
                    onClick={() => callApi("retry_load")}
                  >
                    Retry
                  </button>
                </Show>
                <Show when={appState.auth_expired || (!appState.courses_loaded && appState.error)}>
                  <button
                    type="button"
                    class="btn lg"
                    onClick={() => callApi("logout")}
                  >
                    Sign out
                  </button>
                </Show>
              </div>
              <small>You can change the sync folder anytime</small>
            </div>
          }
        >
          <div class="ob">
            <div class="obi">
              <i class="spin" style={{ width: "26px", height: "26px" }} />
            </div>
            <h1>Loading your courses…</h1>
            <p>{appState.status || "Connecting to IDAT Moodle and SharePoint…"}</p>
            <div class="bar">
              <u class="indet" />
            </div>
          </div>
        </Show>
      </Show>
    </div>
  );
};
