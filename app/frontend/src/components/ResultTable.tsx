import type { Cell, Column } from "../types";

const STATUS = new Set(["covered", "partial", "gap"]);
const CRITICALITY = new Set(["high", "medium", "low"]);

function badgeClass(value: string): string | null {
  const v = value.trim().toLowerCase();
  if (STATUS.has(v)) return v;
  if (CRITICALITY.has(v)) return v;
  return null;
}

function isNumeric(type: string): boolean {
  return /INT|LONG|DOUBLE|FLOAT|DECIMAL|SHORT|BYTE/i.test(type);
}

/** Prettify snake_case column names without losing acronyms like SQL, PCI, CSF. */
export function humanize(name: string): string {
  return name
    .split("_")
    .map((w) =>
      /^(id|sql|pci|iso|soc|nist|csf|ffiec|pct|uc)$/i.test(w)
        ? w.toUpperCase()
        : w.charAt(0).toUpperCase() + w.slice(1),
    )
    .join(" ");
}

function render(value: Cell, type: string) {
  if (value === null || value === undefined || value === "") {
    return <span style={{ color: "#b3bdcc" }}>—</span>;
  }
  const s = String(value);

  const cls = badgeClass(s);
  if (cls) return <span className={`badge ${cls}`}>{s}</span>;

  if (s === "true") return <span className="badge high">Yes</span>;
  if (s === "false") return <span className="badge low">No</span>;

  // Percentages read better with an explicit sign than as a bare float.
  if (isNumeric(type) && /_pct$/i.test(type)) return `${s}%`;

  return s;
}

interface Props {
  columns: Column[];
  rows: Record<string, Cell>[];
  onRowClick?: (row: Record<string, Cell>) => void;
}

export default function ResultTable({ columns, rows, onRowClick }: Props) {
  if (!rows.length) {
    return (
      <div className="empty">
        <h3>No rows returned</h3>
        <p>Genie ran the query successfully but nothing matched.</p>
      </div>
    );
  }

  const cols = columns.length
    ? columns
    : Object.keys(rows[0]).map((name) => ({ name, type: "STRING" }));

  // Row clicks only make sense when the row identifies a single obligation.
  const idCol = cols.find((c) => c.name === "obligation_id");
  const clickable = Boolean(onRowClick && idCol);

  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.name} title={c.type}>
                {humanize(c.name)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              className={clickable ? "clickable" : undefined}
              onClick={clickable ? () => onRowClick?.(row) : undefined}
              title={clickable ? "View evidence and cross-framework impact" : undefined}
            >
              {cols.map((c) => (
                <td
                  key={c.name}
                  className={isNumeric(c.type) ? "num" : undefined}
                >
                  {render(row[c.name], c.name)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
