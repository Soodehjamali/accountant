import { type FormEvent, useState } from "react";
import { useNavigate, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useCreateCustomer } from "@/api/hooks/useCustomers";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function CustomerCreatePage() {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const createCustomer = useCreateCustomer();
  const canManage = usePermission(PERMISSIONS.CUSTOMER_MANAGE);

  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<"INDIVIDUAL" | "CORPORATE">("CORPORATE");
  const [currencyId, setCurrencyId] = useState("");
  const [billingAddress, setBillingAddress] = useState("");
  const [creditLimit, setCreditLimit] = useState("0");
  const [taxNumber, setTaxNumber] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">
          {t("customers.noPermission")}
        </p>
        <Link
          to="/office/customers"
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          {t("customers.backToCustomers")}
        </Link>
      </div>
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    try {
      await createCustomer.mutateAsync({
        code,
        name,
        type,
        currency_id: currencyId,
        billing_address: billingAddress || undefined,
        credit_limit_amount: creditLimit,
        tax_number: taxNumber || undefined,
      });
      navigate("/office/customers", { replace: true });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("customers.failedToCreate"),
      );
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          to="/office/customers"
          className="text-sm text-blue-600 hover:underline"
        >
          {t("customers.backToCustomers")}
        </Link>
      </div>

      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        {t("customers.createTitle")}
      </h1>

      <form
        onSubmit={handleSubmit}
        className="max-w-lg space-y-4 rounded-lg border border-gray-200 bg-white p-6"
      >
        <div>
          <label htmlFor="code" className="block text-sm font-medium text-gray-700">
            {t("customers.fields.code")}
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
        </div>

        <div>
          <label htmlFor="name" className="block text-sm font-medium text-gray-700">
            {t("customers.fields.name")}
          </label>
          <input
            id="name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={160}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label htmlFor="type" className="block text-sm font-medium text-gray-700">
            {t("customers.fields.type")}
          </label>
          <select
            id="type"
            value={type}
            onChange={(e) => setType(e.target.value as "INDIVIDUAL" | "CORPORATE")}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value="CORPORATE">{t("customers.typeOptions.CORPORATE")}</option>
            <option value="INDIVIDUAL">{t("customers.typeOptions.INDIVIDUAL")}</option>
          </select>
        </div>

        <div>
          <label htmlFor="currency_id" className="block text-sm font-medium text-gray-700">
            {t("customers.fields.currencyId")}
          </label>
          <input
            id="currency_id"
            type="text"
            value={currencyId}
            onChange={(e) => setCurrencyId(e.target.value)}
            required
            placeholder={t("forms.uuidPlaceholder")}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label htmlFor="credit_limit" className="block text-sm font-medium text-gray-700">
            {t("customers.fields.creditLimit")}
          </label>
          <input
            id="credit_limit"
            type="text"
            value={creditLimit}
            onChange={(e) => setCreditLimit(e.target.value)}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label htmlFor="billing_address" className="block text-sm font-medium text-gray-700">
            {t("customers.fields.billingAddress")}
          </label>
          <textarea
            id="billing_address"
            value={billingAddress}
            onChange={(e) => setBillingAddress(e.target.value)}
            maxLength={255}
            rows={2}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label htmlFor="tax_number" className="block text-sm font-medium text-gray-700">
            {t("customers.fields.taxNumber")}
          </label>
          <input
            id="tax_number"
            type="text"
            value={taxNumber}
            onChange={(e) => setTaxNumber(e.target.value)}
            maxLength={40}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={createCustomer.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
        >
          {createCustomer.isPending ? t("customers.buttons.creating") : t("customers.buttons.create")}
        </button>
      </form>
    </div>
  );
}
