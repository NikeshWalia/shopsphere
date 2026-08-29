/**
 * Small shared building blocks.
 *
 * Every component that a test needs to find carries a stable `data-testid`.
 * Tests target those rather than CSS classes or visible copy, so restyling or
 * rewording the UI does not break the suite - only genuinely changing what an
 * element *is* does.
 */

import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ApiRequestError } from '@/api/client';
import { formatMoney, initialsFor, orderStatusVariant, paymentStatusVariant } from '@/utils/format';

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="loading-block" data-testid="loading">
      <span className="spinner" aria-hidden="true" />
      <span>{label}...</span>
    </div>
  );
}

export function ErrorAlert({ error, testId = 'error-message' }: { error: unknown; testId?: string }) {
  if (!error) return null;

  let message = 'Something went wrong. Please try again.';
  let code: string | null = null;
  const fieldErrors: string[] = [];

  if (error instanceof ApiRequestError) {
    message = error.message;
    code = error.code;
    // The API returns per-field detail for 422s; surfacing it is far more
    // useful than the generic summary alone.
    const fields = error.details?.fields;
    if (Array.isArray(fields)) {
      for (const entry of fields) {
        const field = entry as { field?: string; message?: string };
        if (field.message) fieldErrors.push(field.field ? `${field.field}: ${field.message}` : field.message);
      }
    }
  } else if (error instanceof Error) {
    message = error.message;
  }

  return (
    <div className="alert alert-error" role="alert" data-testid={testId} data-error-code={code ?? ''}>
      {message}
      {fieldErrors.length > 1 && (
        <ul>
          {fieldErrors.map((entry) => (
            <li key={entry}>{entry}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
  testId = 'empty-state',
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  testId?: string;
}) {
  return (
    <div className="empty-state" data-testid={testId}>
      <h2>{title}</h2>
      {children && <p className="muted">{children}</p>}
      {action}
    </div>
  );
}

export function Money({ amount, currency = 'USD', className, testId }: {
  amount: number;
  currency?: string;
  className?: string;
  testId?: string;
}) {
  return (
    // data-amount exposes the raw number so a test can assert on the value
    // without having to parse "$1,249.00".
    <span className={className} data-testid={testId} data-amount={amount}>
      {formatMoney(amount, currency)}
    </span>
  );
}

export function OrderStatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge ${orderStatusVariant(status)}`} data-testid="order-status" data-status={status}>
      {status}
    </span>
  );
}

export function PaymentStatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`badge ${paymentStatusVariant(status)}`}
      data-testid="payment-status"
      data-status={status}
    >
      {status}
    </span>
  );
}

export function StockBadge({ quantity }: { quantity: number }) {
  if (quantity <= 0) {
    return (
      <span className="badge badge-danger" data-testid="stock-badge" data-stock="0">
        Out of stock
      </span>
    );
  }
  if (quantity <= 5) {
    return (
      <span className="badge badge-warning" data-testid="stock-badge" data-stock={quantity}>
        Only {quantity} left
      </span>
    );
  }
  return (
    <span className="badge badge-success" data-testid="stock-badge" data-stock={quantity}>
      In stock
    </span>
  );
}

/**
 * Product image with a graceful fallback.
 *
 * Seed images are fetched from a public placeholder service. When there is no
 * internet access - offline development, a locked-down CI runner - the fallback
 * tile keeps the layout intact rather than leaving a broken-image icon, so
 * screenshots and visual checks stay meaningful either way.
 */
export function ProductImage({ src, alt }: { src: string | null; alt: string }) {
  const [failed, setFailed] = useState(false);
  const showFallback = !src || failed;

  return (
    <div className="product-thumb">
      {showFallback ? (
        <div className="thumb-fallback" data-testid="product-image-fallback" aria-label={alt}>
          {initialsFor(alt)}
        </div>
      ) : (
        <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} data-testid="product-image" />
      )}
    </div>
  );
}

export function QuantityStepper({
  value,
  min = 1,
  max,
  disabled = false,
  onChange,
  testId = 'quantity-stepper',
}: {
  value: number;
  min?: number;
  max: number;
  disabled?: boolean;
  onChange: (next: number) => void;
  testId?: string;
}) {
  const canDecrease = !disabled && value > min;
  const canIncrease = !disabled && value < max;

  return (
    <div className="qty-stepper" data-testid={testId} data-quantity={value} data-max={max}>
      <button
        type="button"
        onClick={() => onChange(value - 1)}
        disabled={!canDecrease}
        aria-label="Decrease quantity"
        data-testid="quantity-decrease"
      >
        &minus;
      </button>
      <span className="qty-value" data-testid="quantity-value">
        {value}
      </span>
      <button
        type="button"
        onClick={() => onChange(value + 1)}
        disabled={!canIncrease}
        aria-label="Increase quantity"
        data-testid="quantity-increase"
      >
        +
      </button>
    </div>
  );
}

export function Pagination({
  page,
  totalPages,
  onChange,
}: {
  page: number;
  totalPages: number;
  onChange: (next: number) => void;
}) {
  if (totalPages <= 1) return null;
  return (
    <nav className="pagination" data-testid="pagination" data-page={page} data-total-pages={totalPages}>
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        onClick={() => onChange(page - 1)}
        disabled={page <= 1}
        data-testid="pagination-previous"
      >
        Previous
      </button>
      <span className="muted" data-testid="pagination-label">
        Page {page} of {totalPages}
      </span>
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        onClick={() => onChange(page + 1)}
        disabled={page >= totalPages}
        data-testid="pagination-next"
      >
        Next
      </button>
    </nav>
  );
}

export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="subtle" data-testid="back-link">
      &larr; {children}
    </Link>
  );
}
