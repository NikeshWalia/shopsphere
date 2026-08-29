/**
 * Presentation helpers.
 *
 * `formatMoney` only ever *renders* a number the API already computed. There is
 * deliberately no function here that adds, taxes or discounts anything.
 */

export function formatMoney(amount: number, currency = 'USD'): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatRating(rating: string | number): string {
  return Number(rating).toFixed(1);
}

/** Badge colour for an order status. */
export function orderStatusVariant(status: string): string {
  switch (status) {
    case 'delivered':
      return 'badge-success';
    case 'shipped':
    case 'processing':
      return 'badge-info';
    case 'confirmed':
      return 'badge-info';
    case 'cancelled':
      return 'badge-danger';
    default:
      return 'badge-neutral';
  }
}

/** Badge colour for a payment status. */
export function paymentStatusVariant(status: string): string {
  switch (status) {
    case 'paid':
      return 'badge-success';
    case 'failed':
      return 'badge-danger';
    case 'refunded':
      return 'badge-warning';
    default:
      return 'badge-neutral';
  }
}

/** Stable initials used by the image fallback tile. */
export function initialsFor(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0] ?? '')
    .join('')
    .toUpperCase();
}
