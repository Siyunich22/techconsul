const money = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const dateFmt = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });

export function formatMoney(amount: string | number | null | undefined, currency = "KZT"): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  return `${money.format(Number(amount))} ${currency}`;
}

export function formatDate(iso: string | null | undefined): string {
  return iso ? dateFmt.format(new Date(iso)) : "—";
}

export function formatPct(value: string | number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(Number(value))}%`;
}
