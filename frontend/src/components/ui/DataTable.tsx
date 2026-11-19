import clsx from "clsx";
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { ArrowDown, ArrowUp } from "lucide-react";
import { Button, EmptyState, Skeleton } from "./primitives";

export interface Sort { key: string; order: "asc" | "desc" }
declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData, TValue> { align?: "right"; sortKey?: string }
}

interface Props<T> {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  caption: string;
  getId: (row: T) => string;
  sort?: Sort;
  onSort?: (key: string) => void;
  selectedId?: string | null;
  onRowClick?: (row: T) => void;
  loading?: boolean;
  empty?: { title: string; hint?: string };
}

/** Sorting is server-side: the header click only reports the sort key; the page keeps it in the URL. */
export function DataTable<T>({ data, columns, caption, getId, sort, onSort, selectedId, onRowClick, loading, empty }: Props<T>) {
  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel(), getRowId: getId, manualSorting: true });
  if (!loading && data.length === 0) return <EmptyState title={empty?.title ?? "No results"} hint={empty?.hint ?? "Try changing the filters."} />;
  return (
    <div className="overflow-x-auto">
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => {
                const m = h.column.columnDef.meta;
                const active = m?.sortKey && sort?.key === m.sortKey;
                return (
                  <th key={h.id} scope="col" className={clsx(m?.align === "right" && "text-right")} aria-sort={active ? (sort!.order === "asc" ? "ascending" : "descending") : undefined}>
                    {m?.sortKey && onSort ? (
                      <button type="button" onClick={() => onSort(m.sortKey!)} className={clsx("inline-flex items-center gap-1 font-semibold hover:text-ink", active && "text-ink")}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {active && (sort!.order === "asc" ? <ArrowUp size={12} aria-hidden /> : <ArrowDown size={12} aria-hidden />)}
                      </button>
                    ) : flexRender(h.column.columnDef.header, h.getContext())}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: 8 }).map((_, i) => <tr key={i}>{columns.map((_c, j) => <td key={j}><Skeleton className="h-4 w-full" /></td>)}</tr>)
            : table.getRowModel().rows.map((r) => (
                <tr
                  key={r.id}
                  className={clsx(onRowClick && "cursor-pointer hover:bg-bg", selectedId === r.id && "bg-navy-tint")}
                  onClick={onRowClick ? () => onRowClick(r.original) : undefined}
                  onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(r.original); } : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                  aria-selected={selectedId === r.id || undefined}
                >
                  {r.getVisibleCells().map((c) => (
                    <td key={c.id} className={clsx(c.column.columnDef.meta?.align === "right" && "num")}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>
                  ))}
                </tr>
              ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="flex items-center border-t border-line px-4 py-2.5 text-xs text-ink2">
      <span aria-live="polite">Showing {from.toLocaleString("en-IN")}{"–"}{to.toLocaleString("en-IN")} of {total.toLocaleString("en-IN")}</span>
      <span className="ml-auto flex gap-1.5">
        <Button size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
        <Button size="sm" disabled={to >= total} onClick={() => onPage(page + 1)}>Next</Button>
      </span>
    </div>
  );
}
