import "@fontsource-variable/inter/standard.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import App from "./App";
import { applyTheme, storedTheme } from "./lib/theme";
import "./styles.css";

applyTheme(storedTheme()); // before the first paint, so a chosen theme never flashes the other one

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
