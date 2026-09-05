import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  useBotConfigs,
  useSaveBotConfig,
  useTestBotConnection,
} from "@/api/hooks/useBotConfig";

/** Status badge labels (backend tokens). */
const STATUS_LABELS: Record<string, string> = {
  NOT_CONFIGURED: "تنظیم نشده",
  DISABLED: "غیرفعال",
  STOPPED: "متوقف",
  RUNNING: "در حال اجرا",
  ERROR: "خطا",
};

const STATUS_COLORS: Record<string, string> = {
  NOT_CONFIGURED: "bg-gray-100 text-gray-700",
  DISABLED: "bg-yellow-100 text-yellow-800",
  STOPPED: "bg-yellow-100 text-yellow-800",
  RUNNING: "bg-green-100 text-green-800",
  ERROR: "bg-red-100 text-red-800",
};

interface PlatformCardProps {
  platform: string;
  title: string;
  enabled: boolean;
  tokenConfigured: boolean;
  tokenHint: string | null;
  status: string;
  botUsername: string | null;
  botName: string | null;
}

function PlatformCard({
  platform,
  title,
  enabled,
  tokenConfigured,
  tokenHint,
  status,
  botUsername,
  botName,
}: PlatformCardProps) {
  const { t } = useTranslation("common");
  const [tokenInput, setTokenInput] = useState("");
  const [enabledLocal, setEnabledLocal] = useState(enabled);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  // Keep the local toggle in sync after a refetch/save.
  useEffect(() => {
    setEnabledLocal(enabled);
  }, [enabled]);

  const queryClient = useQueryClient();
  const saveConfig = useSaveBotConfig();
  const testConnection = useTestBotConnection();

  const handleSave = () => {
    setMessage(null);
    const hasNewToken = tokenInput.trim() !== "";
    saveConfig.mutate(
      {
        platform: platform.toLowerCase(),
        enabled: enabledLocal,
        token: hasNewToken ? tokenInput.trim() : undefined,
      },
      {
        onSuccess: () => {
          // Never echo the token back: confirm with a plain message instead.
          setMessage({
            ok: true,
            text: hasNewToken ? t("botSettings.tokenConfigured") : t("botSettings.saved"),
          });
          setTokenInput("");
        },
        onError: (err) => setMessage({ ok: false, text: err.message }),
      },
    );
  };

  const handleTest = () => {
    setMessage(null);
    testConnection.mutate(platform.toLowerCase(), {
      onSuccess: (data) => {
        setMessage({
          ok: data.ok,
          text: data.ok
            ? `${t("botSettings.connected")}: ${data.detail}`
            : data.detail,
        });
        if (data.ok) {
          // Refresh the card so the real bot identity (from getMe) shows up.
          queryClient.invalidateQueries({ queryKey: ["bot-configs"] });
        }
      },
      onError: (err) => setMessage({ ok: false, text: err.message }),
    });
  };

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-lg font-bold text-gray-900">{title}</h3>
        <span
          className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${STATUS_COLORS[status] ?? STATUS_COLORS.NOT_CONFIGURED}`}
        >
          {STATUS_LABELS[status] ?? status}
        </span>
      </div>

      <div className="mb-4 text-sm text-gray-600">
        <label className="flex items-center justify-between rounded-md bg-gray-50 px-3 py-2">
          <span>{t("botSettings.enabled")}</span>
          <input
            type="checkbox"
            checked={enabledLocal}
            onChange={(e) => setEnabledLocal(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
        </label>
        <div className="mt-2 flex items-center justify-between rounded-md bg-gray-50 px-3 py-2">
          <span>{t("botSettings.tokenStatus")}</span>
          <span className="text-gray-900">
            {tokenConfigured
              ? `•••• ${tokenHint ?? ""}`
              : t("botSettings.tokenNotConfigured")}
          </span>
        </div>
        {(botName || botUsername) && (
          <div className="mt-2 rounded-md bg-gray-50 px-3 py-2">
            {botName && (
              <div className="flex items-center justify-between">
                <span>{t("botSettings.botName")}</span>
                <span className="font-medium text-gray-900">{botName}</span>
              </div>
            )}
            {botUsername && (
              <div className="mt-1 flex items-center justify-between">
                <span>{t("botSettings.botUsername")}</span>
                <span className="font-medium text-gray-900" dir="ltr">
                  @{botUsername}
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      <label className="mb-1 block text-sm font-medium text-gray-700">
        {t("botSettings.tokenInput")}
      </label>
      <input
        type="password"
        dir="ltr"
        value={tokenInput}
        onChange={(e) => setTokenInput(e.target.value)}
        placeholder={
          tokenConfigured
            ? t("botSettings.tokenPlaceholderConfigured")
            : t("botSettings.tokenPlaceholder")
        }
        className="w-full rounded-md border border-gray-300 px-3 py-2 text-left text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
      />

      {message && (
        <div
          className={`mt-3 rounded-md px-3 py-2 text-sm ${
            message.ok
              ? "bg-green-50 text-green-700"
              : "bg-red-50 text-red-700"
          }`}
        >
          {message.text}
        </div>
      )}

      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saveConfig.isPending}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {saveConfig.isPending ? t("botSettings.saving") : t("botSettings.save")}
        </button>
        <button
          onClick={handleTest}
          disabled={testConnection.isPending}
          className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          {testConnection.isPending
            ? t("botSettings.testing")
            : t("botSettings.testConnection")}
        </button>
      </div>
    </div>
  );
}

export function BotSettingsPage() {
  const { t } = useTranslation("common");
  const { data: configs, isLoading, error } = useBotConfigs();

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold text-gray-900">
        {t("botSettings.title")}
      </h1>
      <p className="mb-6 text-sm text-gray-500">{t("botSettings.subtitle")}</p>

      {isLoading && <p className="text-gray-500">{t("status.loading")}</p>}
      {error && <p className="text-red-600">{t("botSettings.failedToLoad")}</p>}

      {!isLoading && !error && (
        <div className="grid gap-6 md:grid-cols-2">
          {(configs ?? []).map((cfg: any) => (
            <PlatformCard
              key={cfg.platform}
              platform={cfg.platform}
              title={
                cfg.platform === "TELEGRAM"
                  ? t("botSettings.telegram")
                  : t("botSettings.bale")
              }
              enabled={cfg.enabled}
              tokenConfigured={cfg.token_configured}
              tokenHint={cfg.token_hint}
              status={cfg.status}
              botUsername={cfg.bot_username}
              botName={cfg.bot_name}
            />
          ))}
        </div>
      )}

      <div className="mt-8 rounded-md border border-blue-100 bg-blue-50 p-4 text-sm text-blue-800">
        {t("botSettings.helpNote")}
      </div>
    </div>
  );
}