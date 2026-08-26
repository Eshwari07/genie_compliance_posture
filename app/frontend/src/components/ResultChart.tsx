import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Cell as CellValue, Column } from "../types";
import { humanize } from "./ResultTable";

/**
 * Decide whether a result is worth charting, and how.
 *
 * Deliberately conservative. A chart that misrepresents the data is worse than no chart,
 * so this only fires when the shape is unambiguous: one categorical column, one numeric
 * column, and few enough rows to read. Everything else falls through to the table.
 */
export function chartSpec(columns: Column[], rows: Record<string, CellValue>[]) {
  if (rows.length < 2 || rows.length > 25) return null;

  const cols = columns.length
    ? columns
    : Object.keys(rows[0]).map((name) => ({ name, type: "STRING" }));

  const numeric = cols.filter(
    (c) =>
      /INT|LONG|DOUBLE|FLOAT|DECIMAL/i.test(c.type) ||
      rows.every((r) => r[c.name] === null || !isNaN(Number(r[c.name]))),
  );
  const categorical = cols.filter((c) => !numeric.includes(c));
  if (!numeric.length || !categorical.length) return null;

  // Prefer a percentage or an obviously headline metric over an incidental count.
  const metric =
    numeric.find((c) => /_pct$|percent|score/i.test(c.name)) ??
    numeric.find((c) => /count|gaps|obligations|closed|total/i.test(c.name)) ??
    numeric[0];

  // Prefer the shortest categorical label so axis text stays legible.
  const label = categorical
    .slice()
    .sort(
      (a, b) =>
        Math.max(...rows.map((r) => String(r[a.name] ?? "").length)) -
        Math.max(...rows.map((r) => String(r[b.name] ?? "").length)),
    )[0];

  const maxLabelLen = Math.max(...rows.map((r) => String(r[label.name] ?? "").length));
  if (maxLabelLen > 42) return null;

  return { labelKey: label.name, metricKey: metric.name };
}

function barColor(value: number, isPct: boolean): string {
  if (!isPct) return "#2563eb";
  if (value >= 75) return "#0f7b52";
  if (value >= 55) return "#a4670a";
  return "#b3261e";
}

interface Props {
  columns: Column[];
  rows: Record<string, CellValue>[];
}

export default function ResultChart({ columns, rows }: Props) {
  const spec = chartSpec(columns, rows);
  if (!spec) return null;

  const isPct = /_pct$|percent/i.test(spec.metricKey);
  const data = rows.map((r) => ({
    label: String(r[spec.labelKey] ?? ""),
    value: Number(r[spec.metricKey] ?? 0),
  }));

  // Horizontal bars when labels are long — rotated axis text is hard to read.
  const horizontal = Math.max(...data.map((d) => d.label.length)) > 14;

  return (
    <div style={{ width: "100%", height: Math.max(210, data.length * (horizontal ? 32 : 26) + 60), marginBottom: 14 }}>
      <ResponsiveContainer>
        <BarChart
          data={data}
          layout={horizontal ? "vertical" : "horizontal"}
          margin={{ top: 4, right: 26, bottom: 4, left: horizontal ? 8 : 0 }}
        >
          <CartesianGrid strokeDasharray="2 4" stroke="#e8edf4" vertical={!horizontal} horizontal={horizontal} />
          {horizontal ? (
            <>
              <XAxis type="number" tick={{ fontSize: 11, fill: "#6b7a90" }} axisLine={false} tickLine={false}
                     unit={isPct ? "%" : undefined} />
              <YAxis type="category" dataKey="label" width={186} tick={{ fontSize: 11.5, fill: "#37455c" }}
                     axisLine={false} tickLine={false} />
            </>
          ) : (
            <>
              <XAxis dataKey="label" tick={{ fontSize: 11.5, fill: "#37455c" }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: "#6b7a90" }} axisLine={false} tickLine={false}
                     unit={isPct ? "%" : undefined} />
            </>
          )}
          <Tooltip
            cursor={{ fill: "rgba(37,99,235,.06)" }}
            contentStyle={{
              borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 12.5,
              boxShadow: "0 4px 14px rgba(16,24,39,.09)",
            }}
            formatter={(v: number) => [isPct ? `${v}%` : v, humanize(spec.metricKey)]}
          />
          <Bar dataKey="value" radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={30}>
            {data.map((d, i) => (
              <Cell key={i} fill={barColor(d.value, isPct)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
