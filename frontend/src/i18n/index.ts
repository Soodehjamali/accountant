import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import enCommon from "./locales/en/common.json";
import enLogin from "./locales/en/login.json";
import enDashboard from "./locales/en/dashboard.json";
import faCommon from "./locales/fa/common.json";
import faLogin from "./locales/fa/login.json";
import faDashboard from "./locales/fa/dashboard.json";

const STORAGE_KEY = "app_language";

/** Detect the initial language from localStorage, then browser. */
function detectLanguage(): string {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "fa" || stored === "en") return stored;

  const browserLang = navigator.language || (navigator.languages?.[0] ?? "");
  if (browserLang.startsWith("fa")) return "fa";
  return "en";
}

/** Toggle <html dir> and <html lang> to match the active language. */
function applyDirection(lng: string) {
  const dir = lng === "fa" ? "rtl" : "ltr";
  document.documentElement.dir = dir;
  document.documentElement.lang = lng;
}

const detectedLng = detectLanguage();

i18n.use(initReactI18next).init({
  resources: {
    en: {
      common: enCommon,
      login: enLogin,
      dashboard: enDashboard,
    },
    fa: {
      common: faCommon,
      login: faLogin,
      dashboard: faDashboard,
    },
  },
  lng: detectedLng,
  fallbackLng: "en",
  ns: ["common"],
  defaultNS: "common",
  interpolation: {
    escapeValue: false, // React already escapes
  },
});

// Apply direction on init
applyDirection(detectedLng);

// Persist language and toggle direction on change
i18n.on("languageChanged", (lng: string) => {
  localStorage.setItem(STORAGE_KEY, lng);
  applyDirection(lng);
});

export default i18n;
