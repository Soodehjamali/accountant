import { useState } from "react";
import { Link } from "react-router";
import {
  useReportTypes,
  useReportDefinitions,
  useCreateReportDefinition,
  useRunReport,
} from "@/api/hooks/useReports";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

const PAGE_SIZE = 50;

export function ReportListPage() {
  const canManage = usePermission(PERMISSIONS.REPORT_MANAGE);
  const [page, setPage] = useState(0);
  const [showCreateForm, setShowCreateForm] = useState(false);

  const { data: definitions, isLoading } = useReportDefinitions({
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Reports</h1>
        {canManage && (
          <button
            onClick={() => setShowCreateForm(!showCreateForm)}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            {showCreateForm ? "Cancel" : "New Report Definition"}
          </button>
        )}
      </div>

      {showCreateForm && canManage && (
        <CreateReportDefinitionForm
          onCreated={() => setShowCreateForm(false)}
        />
      )}

      {isLoading && <p className="text-gray-500">Loading…</p>}

      {!isLoading && (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Name
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Type
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Format
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Created
                  </th>
                  {canManage && (
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      Actions
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {(definitions ?? []).length === 0 ? (
                  <tr>
                    <td
                      colSpan={6}
                      className="px-4 py-8 text-center text-sm text-gray-500"
                    >
                      No report definitions found. Create one to get started.
                    </td>
                  </tr>
                ) : (
                  (definitions ?? []).map((def) => (
                    <DefinitionRow
                      key={def.id}
                      definition={def}
                      canManage={canManage}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex items-center justify-between">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Previous
            </button>
            <span className="text-sm text-gray-500">Page {page + 1}</span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={(definitions ?? []).length < PAGE_SIZE}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Definition row with run action
// ---------------------------------------------------------------------------

function DefinitionRow({
  definition,
  canManage,
}: {
  definition: {
    id: string;
    name: string;
    report_type_id: string;
    output_format: string;
    is_active: boolean;
    created_at: string;
  };
  canManage: boolean;
}) {
  const runReport = useRunReport();
  const [runResult, setRunResult] = useState<{
    runId: string;
    status: string;
    rowCount: number | null;
  } | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  async function handleRun() {
    setRunError(null);
    setRunResult(null);
    try {
      const result = await runReport.mutateAsync(definition.id);
      setRunResult({
        runId: result.run.id,
        status: result.run.status,
        rowCount: result.run.row_count,
      });
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "Run failed");
    }
  }

  return (
    <tr className="hover:bg-gray-50">
      <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
        {definition.name}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
        <span className="font-mono text-xs bg-gray-100 px-2 py-0.5 rounded">
          {definition.report_type_id.slice(0, 8)}
        </span>
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
        {definition.output_format}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-sm">
        <span
          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
            definition.is_active
              ? "bg-green-100 text-green-800"
              : "bg-gray-100 text-gray-800"
          }`}
        >
          {definition.is_active ? "Active" : "Inactive"}
        </span>
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
        {new Date(definition.created_at).toLocaleDateString()}
      </td>
      {canManage && (
        <td className="whitespace-nowrap px-4 py-3 text-sm">
          <div className="flex items-center gap-3">
            <button
              onClick={handleRun}
              disabled={runReport.isPending}
              className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {runReport.isPending ? "Running…" : "Run Now"}
            </button>
            {runResult && (
              <Link
                to={`/office/reports/runs/${runResult.runId}`}
                className="text-xs text-blue-600 hover:underline"
              >
                View Result ({runResult.rowCount ?? 0} rows)
              </Link>
            )}
            {runError && (
              <span className="text-xs text-red-600">{runError}</span>
            )}
          </div>
        </td>
      )}
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Create report definition form
// ---------------------------------------------------------------------------

function CreateReportDefinitionForm({ onCreated }: { onCreated: () => void }) {
  const { data: reportTypes } = useReportTypes();
  const createDefinition = useCreateReportDefinition();

  const [name, setName] = useState("");
  const [reportTypeId, setReportTypeId] = useState("");
  const [outputFormat, setOutputFormat] = useState("PDF");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (!reportTypeId) {
      setError("Report type is required.");
      return;
    }

    try {
      await createDefinition.mutateAsync({
        report_type_id: reportTypeId,
        name: name.trim(),
        output_format: outputFormat,
        parameters: {},
      });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create");
    }
  }

  return (
    <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
      <h2 className="mb-4 text-lg font-semibold text-gray-900">
        New Report Definition
      </h2>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Name *
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              placeholder="e.g. Monthly AR Aging"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Report Type *
            </label>
            <select
              value={reportTypeId}
              onChange={(e) => setReportTypeId(e.target.value)}
              required
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">Select type…</option>
              {(reportTypes ?? []).map((rt) => (
                <option key={rt.id} value={rt.id}>
                  {rt.code}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Output Format
            </label>
            <select
              value={outputFormat}
              onChange={(e) => setOutputFormat(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="PDF">PDF</option>
              <option value="CSV">CSV</option>
              <option value="XLSX">XLSX</option>
            </select>
          </div>
        </div>

        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={createDefinition.isPending}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {createDefinition.isPending ? "Creating…" : "Create Definition"}
        </button>
      </form>
    </div>
  );
}
