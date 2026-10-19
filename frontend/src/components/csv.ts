/** CSV export. The rows behind a panel, in the form an analyst will actually re-use. */

export interface CsvTable {
  /** Becomes the filename, prefixed with the simulation name. */
  name: string;
  columns: readonly string[];
  rows: readonly (readonly (string | number | null | undefined)[])[];
}

function cell(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  // Quote only when the delimiter, a quote or a newline would otherwise break the row.
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(table: CsvTable): string {
  const lines = [table.columns.map(cell).join(",")];
  for (const row of table.rows) lines.push(row.map(cell).join(","));
  // Trailing newline: POSIX tools and Excel both prefer it.
  return `${lines.join("\n")}\n`;
}

function slug(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 60) || "export"
  );
}

export function downloadCsv(table: CsvTable, context?: string): void {
  const name = context ? `${slug(context)}--${slug(table.name)}` : slug(table.name);
  const blob = new Blob([toCsv(table)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${name}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking immediately races the download in Safari; a tick is enough.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
