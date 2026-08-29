import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, ApiRequestError } from '@/api/client';
import {
  BackLink,
  EmptyState,
  ErrorAlert,
  Money,
  OrderStatusBadge,
  Pagination,
  PaymentStatusBadge,
  Spinner,
} from '@/components/common';
import { useCart } from '@/context/CartContext';
import { useToast } from '@/context/ToastContext';
import { formatDate, formatDateTime } from '@/utils/format';
import type { Order, OrderSummary, Page } from '@/types/api';

/** Order history list. */
export function OrdersPage() {
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<Page<OrderSummary> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const orders = await api.orders.list(page, 10);
        if (!cancelled) setResult(orders);
      } catch (caught) {
        if (!cancelled) setError(caught);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [page]);

  if (loading) return <Spinner label="Loading your orders" />;

  return (
    <div className="page" data-testid="orders-page">
      <div className="container">
        <h1>Your orders</h1>
        <ErrorAlert error={error} />

        {result && result.items.length === 0 ? (
          <EmptyState
            title="No orders yet"
            testId="empty-orders"
            action={
              <Link to="/products" className="btn">
                Start shopping
              </Link>
            }
          >
            Orders you place will appear here.
          </EmptyState>
        ) : (
          <>
            <div className="table-wrap">
              <table className="table" data-testid="orders-table">
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Placed</th>
                    <th>Items</th>
                    <th>Status</th>
                    <th>Payment</th>
                    <th className="right">Total</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {result?.items.map((order) => (
                    <tr key={order.id} data-testid="order-row" data-order-id={order.id}>
                      <td className="mono" data-testid="order-number">
                        {order.order_number}
                      </td>
                      <td className="nowrap">{formatDate(order.created_at)}</td>
                      <td>{order.item_count}</td>
                      <td>
                        <OrderStatusBadge status={order.status} />
                      </td>
                      <td>
                        <PaymentStatusBadge status={order.payment_status} />
                      </td>
                      <td className="right">
                        <Money amount={order.total} currency={order.currency} testId="order-total" />
                      </td>
                      <td className="right">
                        <Link
                          to={`/orders/${order.id}`}
                          className="btn btn-secondary btn-sm"
                          data-testid="view-order"
                        >
                          View
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {result && (
              <Pagination page={result.page} totalPages={result.total_pages} onChange={setPage} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** Full order detail, with cancellation. */
export function OrderDetailPage() {
  const { orderId } = useParams<{ orderId: string }>();
  const { notify } = useToast();
  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [cancelling, setCancelling] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const detail = await api.orders.get(Number(orderId));
        if (!cancelled) setOrder(detail);
      } catch (caught) {
        if (!cancelled) setError(caught);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orderId]);

  const handleCancel = async () => {
    if (!order) return;
    setCancelling(true);
    try {
      const updated = await api.orders.cancel(order.id, 'Cancelled from order detail');
      setOrder(updated);
      notify('Order cancelled. Stock has been returned.', 'success');
    } catch (caught) {
      setError(caught);
      if (caught instanceof ApiRequestError) notify(caught.message, 'error');
    } finally {
      setCancelling(false);
    }
  };

  if (loading) return <Spinner label="Loading order" />;

  if (error instanceof ApiRequestError && error.status === 404) {
    return (
      <div className="page">
        <div className="container">
          <EmptyState title="Order not found" testId="order-not-found">
            This order does not exist, or it belongs to another account.
          </EmptyState>
        </div>
      </div>
    );
  }

  if (!order) {
    return (
      <div className="page">
        <div className="container">
          <ErrorAlert error={error} />
        </div>
      </div>
    );
  }

  const cancellable = ['pending', 'confirmed', 'processing'].includes(order.status);

  return (
    <div className="page" data-testid="order-detail-page" data-order-id={order.id}>
      <div className="container stack">
        <BackLink to="/orders">Back to orders</BackLink>

        <div className="page-header">
          <div>
            <h1 className="mono" data-testid="order-number" style={{ fontSize: 24 }}>
              {order.order_number}
            </h1>
            <p className="muted">Placed {formatDateTime(order.created_at)}</p>
          </div>
          <div className="row">
            <OrderStatusBadge status={order.status} />
            <PaymentStatusBadge status={order.payment_status} />
          </div>
        </div>

        <ErrorAlert error={error} testId="order-error" />

        {order.cancelled_reason && (
          <div className="alert alert-warning" data-testid="cancelled-reason">
            {order.cancelled_reason}
          </div>
        )}

        <div className="cart-layout">
          <div className="stack">
            <div className="card">
              <h2>Items</h2>
              <div className="table-wrap">
                <table className="table" data-testid="order-items">
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th>SKU</th>
                      <th className="right">Unit price</th>
                      <th className="right">Qty</th>
                      <th className="right">Line total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {order.items.map((item) => (
                      <tr key={item.id} data-testid="order-item" data-product-id={item.product_id}>
                        <td>
                          <Link to={`/products/${item.product_id}`}>{item.product_name}</Link>
                        </td>
                        <td className="mono">{item.sku}</td>
                        <td className="right">
                          <Money amount={item.unit_price} currency={order.currency} />
                        </td>
                        <td className="right">{item.quantity}</td>
                        <td className="right">
                          <Money
                            amount={item.line_total}
                            currency={order.currency}
                            testId="order-item-total"
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="card">
              <h2>Payment attempts</h2>
              <div className="table-wrap">
                <table className="table" data-testid="payment-attempts">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Status</th>
                      <th>Card</th>
                      <th>Reference</th>
                      <th>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {order.payments.map((attempt) => (
                      <tr key={attempt.id} data-testid="payment-attempt">
                        <td>{attempt.attempt}</td>
                        <td>
                          <PaymentStatusBadge status={attempt.status} />
                        </td>
                        <td className="mono">
                          {attempt.card_brand} &bull;&bull;&bull;&bull; {attempt.card_last4 ?? '----'}
                        </td>
                        <td className="mono subtle">{attempt.provider_reference ?? '-'}</td>
                        <td className="subtle">{attempt.failure_message ?? 'Approved'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <aside className="stack">
            <div className="card">
              <h2>Summary</h2>
              <div className="summary-row">
                <span>Subtotal</span>
                <Money amount={order.subtotal} currency={order.currency} testId="order-subtotal" />
              </div>
              {order.discount_total > 0 && (
                <div className="summary-row">
                  <span>Discount {order.promo_code ? `(${order.promo_code})` : ''}</span>
                  <span className="discount">
                    &minus;
                    <Money
                      amount={order.discount_total}
                      currency={order.currency}
                      testId="order-discount"
                    />
                  </span>
                </div>
              )}
              <div className="summary-row">
                <span>Tax</span>
                <Money amount={order.tax} currency={order.currency} testId="order-tax" />
              </div>
              <div className="summary-row">
                <span>Shipping</span>
                <Money
                  amount={order.shipping_fee}
                  currency={order.currency}
                  testId="order-shipping"
                />
              </div>
              <div className="summary-row total">
                <span>Total</span>
                <Money amount={order.total} currency={order.currency} testId="order-total" />
              </div>
            </div>

            <div className="card">
              <h2>Shipping address</h2>
              <p className="muted" data-testid="order-address" style={{ marginBottom: 0 }}>
                {order.shipping_address.full_name}
                <br />
                {order.shipping_address.line1}
                {order.shipping_address.line2 && (
                  <>
                    <br />
                    {order.shipping_address.line2}
                  </>
                )}
                <br />
                {order.shipping_address.city}, {order.shipping_address.state}{' '}
                {order.shipping_address.postal_code}
                <br />
                {order.shipping_address.country}
              </p>
            </div>

            {cancellable && (
              <button
                type="button"
                className="btn btn-danger btn-block"
                onClick={() => void handleCancel()}
                disabled={cancelling}
                data-testid="cancel-order-button"
              >
                {cancelling ? 'Cancelling...' : 'Cancel order'}
              </button>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}

/** Post-checkout confirmation. */
export function OrderConfirmationPage() {
  const { orderId } = useParams<{ orderId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const { reload } = useCart();

  // Checkout passes the order through navigation state, which avoids a
  // redundant fetch on the happy path; a direct visit or refresh falls back to
  // loading it.
  const passed = (location.state as { order?: Order } | null)?.order ?? null;
  const [order, setOrder] = useState<Order | null>(passed);
  const [loading, setLoading] = useState(passed === null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (passed) return;
    let cancelled = false;
    void (async () => {
      try {
        const detail = await api.orders.get(Number(orderId));
        if (!cancelled) setOrder(detail);
      } catch (caught) {
        if (!cancelled) setError(caught);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orderId, passed]);

  if (loading) return <Spinner label="Loading confirmation" />;
  if (!order) {
    return (
      <div className="page">
        <div className="container">
          <ErrorAlert error={error} />
        </div>
      </div>
    );
  }

  return (
    <div className="page" data-testid="order-confirmation-page" data-order-id={order.id}>
      <div className="container">
        <div className="card" style={{ textAlign: 'center' }} data-testid="confirmation-card">
          <div style={{ fontSize: 44 }} aria-hidden="true">
            &#10003;
          </div>
          <h1>Thank you for your order</h1>
          <p className="muted">
            Order{' '}
            <strong className="mono" data-testid="confirmation-order-number">
              {order.order_number}
            </strong>{' '}
            has been placed.
          </p>

          <div className="row" style={{ justifyContent: 'center' }}>
            <OrderStatusBadge status={order.status} />
            <PaymentStatusBadge status={order.payment_status} />
          </div>

          <p style={{ marginTop: 'var(--space-4)' }}>
            Total charged:{' '}
            <Money
              amount={order.total}
              currency={order.currency}
              className="price"
              testId="confirmation-total"
            />
          </p>

          <div className="row" style={{ justifyContent: 'center', marginTop: 'var(--space-4)' }}>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => navigate(`/orders/${order.id}`)}
              data-testid="view-order-detail"
            >
              View order details
            </button>
            <Link to="/products" className="btn" data-testid="continue-shopping">
              Continue shopping
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
