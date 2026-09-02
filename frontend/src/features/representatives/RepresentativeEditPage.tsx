import { type FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useRepresentatives, useUpdateRepresentative } from "@/api/hooks/useRepresentatives";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function RepresentativeEditPage() {
  const { t } = useTranslation("common");
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const updateRepresentative = useUpdateRepresentative();
  const canManage = usePermission(PERMISSIONS.REPRESENTATIVE_MANAGE);

  const { data: representatives, isLoading } = useRepresentatives();
  const rep = (representatives ?? []).find((r: any) => r.id === id);

  const [personName, setPersonName] = useState("");
  const [nationalId, setNationalId] = useState("");
  const [taxId, setTaxId] = useState("");
  const [status, setStatus] = useState("ACTIVE");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (rep) {
      setPersonName(rep.person_name);
      setNationalId(rep.national_id ?? "");
      setTaxId(rep.tax_id ?? "");
      setStatus(rep.status);
    }
  }, [rep]);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">{t("representatives.noPermission")}</p>
        <Link to="/office/representatives" className="mt-4 inline-block text-sm text-blue-600 hover:underline">{t("representatives.backToRepresentatives")}</Link>
      </div>
    );
  }

  if (isLoading) return <p className="text-gray-500">{t("status.loading")}</p>;
  if (!rep) return <p className="text-red-600">{t("representatives.failedToLoad")}</p>;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await updateRepresentative.mutateAsync({
        representativeId: id!,
        person_name: personName,
        national_id: nationalId || undefined,
        tax_id: taxId || undefined,
        status,
      });
      navigate("/office/representatives", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("representatives.failedToDelete"));
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link to="/office/representatives" className="text-sm text-blue-600 hover:underline">← {t("representatives.title")}</Link>
      </div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">{t("representatives.editTitle")}</h1>

      <form onSubmit={handleSubmit} className="max-w-lg space-y-4 rounded-lg border border-gray-200 bg-white p-6">
        <div>
          <label className="block text-sm font-medium text-gray-700">{t("representatives.fields.code")}</label>
          <input type="text" value={rep.code} disabled className="mt-1 block w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500" />
        </div>

        <div>
          <label htmlFor="person_name" className="block text-sm font-medium text-gray-700">{t("representatives.fields.fullName")}</label>
          <input id="person_name" type="text" value={personName} onChange={(e) => setPersonName(e.target.value)} required maxLength={160}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="national_id" className="block text-sm font-medium text-gray-700">{t("representatives.fields.nationalId")}</label>
          <input id="national_id" type="text" value={nationalId} onChange={(e) => setNationalId(e.target.value)} maxLength={40}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="tax_id" className="block text-sm font-medium text-gray-700">{t("representatives.fields.taxId")}</label>
          <input id="tax_id" type="text" value={taxId} onChange={(e) => setTaxId(e.target.value)} maxLength={40}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="status" className="block text-sm font-medium text-gray-700">{t("representatives.columns.status")}</label>
          <select id="status" value={status} onChange={(e) => setStatus(e.target.value)}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
            <option value="ACTIVE">ACTIVE</option>
            <option value="SUSPENDED">SUSPENDED</option>
            <option value="OFFBOARDED">OFFBOARDED</option>
          </select>
        </div>

        {error && <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>}

        <button type="submit" disabled={updateRepresentative.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50">
          {updateRepresentative.isPending ? t("catalog.buttons.creating") : t("catalog.buttons.update")}
        </button>
      </form>
    </div>
  );
}
