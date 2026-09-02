import { type FormEvent, useState } from "react";
import { useNavigate, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useCreateRepresentative } from "@/api/hooks/useRepresentatives";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function RepresentativeCreatePage() {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const createRepresentative = useCreateRepresentative();
  const canManage = usePermission(PERMISSIONS.REPRESENTATIVE_MANAGE);

  const [code, setCode] = useState("");
  const [personName, setPersonName] = useState("");
  const [nationalId, setNationalId] = useState("");
  const [taxId, setTaxId] = useState("");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">
          {t("representatives.noPermission")}
        </p>
        <Link
          to="/office/representatives"
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          {t("representatives.backToRepresentatives")}
        </Link>
      </div>
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    try {
      await createRepresentative.mutateAsync({
        code,
        person_name: personName,
        national_id: nationalId || undefined,
        tax_id: taxId || undefined,
        phone_number: phoneNumber || undefined,
      });
      navigate("/office/representatives", { replace: true });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("representatives.failedToCreate"),
      );
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          to="/office/representatives"
          className="text-sm text-blue-600 hover:underline"
        >
          {t("representatives.backToRepresentatives")}
        </Link>
      </div>

      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        {t("representatives.createTitle")}
      </h1>

      <form
        onSubmit={handleSubmit}
        className="max-w-lg space-y-4 rounded-lg border border-gray-200 bg-white p-6"
      >
        <div>
          <label htmlFor="code" className="block text-sm font-medium text-gray-700">
            {t("representatives.fields.code")}
          </label>
          <input
            id="code"
            type="text"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
            maxLength={40}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <p className="mt-1 text-xs text-gray-500">
            {t("representatives.fields.codeHelp")}
          </p>
        </div>

        <div>
          <label htmlFor="person_name" className="block text-sm font-medium text-gray-700">
            {t("representatives.fields.fullName")}
          </label>
          <input
            id="person_name"
            type="text"
            value={personName}
            onChange={(e) => setPersonName(e.target.value)}
            required
            maxLength={160}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label htmlFor="national_id" className="block text-sm font-medium text-gray-700">
            {t("representatives.fields.nationalId")}
          </label>
          <input
            id="national_id"
            type="text"
            value={nationalId}
            onChange={(e) => setNationalId(e.target.value)}
            maxLength={40}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label htmlFor="tax_id" className="block text-sm font-medium text-gray-700">
            {t("representatives.fields.taxId")}
          </label>
          <input
            id="tax_id"
            type="text"
            value={taxId}
            onChange={(e) => setTaxId(e.target.value)}
            maxLength={40}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label htmlFor="phone_number" className="block text-sm font-medium text-gray-700">
            {t("representatives.fields.phoneNumber")}
          </label>
          <input
            id="phone_number"
            dir="ltr"
            type="tel"
            value={phoneNumber}
            onChange={(e) => setPhoneNumber(e.target.value)}
            placeholder="+989123456789"
            maxLength={20}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-left text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <p className="mt-1 text-xs text-gray-500">
            {t("representatives.fields.phoneNumberHelp")}
          </p>
        </div>

        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={createRepresentative.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
        >
          {createRepresentative.isPending ? t("representatives.buttons.creating") : t("representatives.buttons.create")}
        </button>
      </form>
    </div>
  );
}
