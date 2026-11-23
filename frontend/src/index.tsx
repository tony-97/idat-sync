import { render } from "solid-js/web";
import "./styles/global.css";
import { App } from "./App";
import { initBridge } from "./bridge";

initBridge();

const root = document.getElementById("root");
if (root) {
  render(() => <App />, root);
}
