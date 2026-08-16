import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./fonts.css";
import "./themes.css";
/* Обвязка подключается после общих стилей: она задаёт раскладку приложения,
   и её правила должны перебивать старые, а не наоборот. */
import "./styles.css";
import "./chrome.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
