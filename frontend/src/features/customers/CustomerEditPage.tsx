import { type FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useCustomer, useUpdateCustomer } from "@/api/hooks/useCustomers";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function CustomerEditPage() {
  const { t } = useTranslation("common");
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const updateCustomer = useUpdateCustomer();
  const canManage = usePermission(PERMISSIONS.CUSTOMER_MANAGE);

  const { data: customer, isLoading } = useCustomer(id ?? "");

  const [name, setName] = useState("");
  const [billingAddress, setBillingAddress] = useState("");
  const [creditLimit, setCreditLimit] = useState("");
  const [taxNumber, setTaxNumber] = useState("");
  const [status, setStatus] = useState("ACTIVE");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (customer) {
      setName(customer.name);
      setBillingAddress(customer.billing_address ?? "");
      setCreditLimit(customer.credit_limit_amount?.toString() ?? "");
      setTaxNumber(customer.tax_number ?? "");
      setStatus(customer.status);
    }
  }, [customer]);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">{t("customers.noPermission")}</p>
        <Link to="/office/customers" className="mt-4 inline-block text-sm text-blue-600 hover:underline">{t("customers.backToCustomers")}</Link>
      </div>
    );
  }

  if (isLoading) return <p className="text-gray-500">{t("status.loading")}</p>;
  if (!customer) return <p className="text-red-600">{t("customers.failedToLoad")}</p>;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await updateCustomer.mutateAsync({
        customerId: id!,
        name,
        billing_address: billingAddress || undefined,
        credit_limit_amount: creditLimit ? Number(creditLimit) : undefined,
        tax_number: taxNumber || undefined,
        status,
      });
      navigate("/office/customers", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("customers.failedToDelete"));
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link to="/office/customers" className="text-sm text-blue-600 hover:underline">← {t("customers.title")}</Link>
      </div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">{t("customers.editTitle")}</h1>

      <form onSubmit={handleSubmit} className="max-w-lg space-y-4 rounded-lg border border-gray-200 bg-white p-6">
        <div>
          <label className="block text-sm font-medium text-gray-700">{t("customers.fields.code")}</label>
          <input type="text" value={customer.code} disabled className="mt-1 block w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500" />
        </div>

        <div>
          <label htmlFor="name" className="block text-sm font-medium text-gray-700">{t("customers.fields.name")}</label>
          <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)} required maxLength={160}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="billing_address" className="block text-sm font-medium text-gray-700">{t("customers.fields.billingAddress")}</label>
          <textarea id="billing_address" value={billingAddress} onChange={(e) => setBillingAddress(e.target.value)} maxLength={255} rows={2}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="credit_limit" className="block text-sm font-medium text-gray-700">{t("customers.fields.creditLimit")}</label>
          <input id="credit_limit" type="number" value={creditLimit} onChange={(e) => setCreditLimit(e.target.value)} min="0" step="0.01"
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="tax_number" className="block text-sm font-medium text-gray-700">{t("customers.fields.taxNumber")}</label>
          <input id="tax_number" type="text" value={taxNumber} onChange={(e) => setTaxNumber(e.target.value)} maxLength={40}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="status" className="block text-sm font-medium text-gray-700">{t("customers.columns.status")}</label>
          <select id="status" value={status} onChange={(e) => setStatus(e.target.value)}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
            <option value="ACTIVE">ACTIVE</option>
            <option value="INACTIVE">INACTIVE</option>
          </select>
        </div>

        {error && <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>}

        <button type="submit" disabled={updateCustomer.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50">
          {updateCustomer.isPending ? t("catalog.buttons.creating") : t("catalog.buttons.update")}
        </button>
      </form>
    </div>
  );
}
