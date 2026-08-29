import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiRequestError } from '@/api/client';
import { EmptyState, ErrorAlert, Money, ProductImage, QuantityStepper, Spinner } from '@/components/common';
import { useCart } from '@/context/CartContext';
import { useToast } from '@/context/ToastContext';

export function CartPage() {
  const { cart, loading, busy, updateItem, removeItem, clear } = useCart();
  const { notify } = useToast();
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>(null);

  const run = async (operation: () => Promise<void>, successMessage?: string) => {
    setError(null);
    try {
      await operation();
      if (successMessage) notify(successMessage, 'success');
    } catch (caught) {
      setError(caught);
      if (caught instanceof ApiRequestError) notify(caught.message, 'error');
    }
  };

  if (loading) return <Spinner label="Loading your cart" />;

  if (cart.items.length === 0) {
    return (
      <div className="page" data-testid="cart-page">
        <div className="container">
          <h1>Your cart</h1>
          <EmptyState
            title="Your cart is empty"
            testId="empty-cart"
            action={
              <Link to="/products" className="btn" data-testid="empty-cart-shop">
                Start shopping
              </Link>
            }
          >
            Add something from the catalogue and it will show up here.
          </EmptyState>
        </div>
      </div>
    );
  }

  const { totals } = cart;

  return (
    <div className="page" data-testid="cart-page">
      <div className="container">
        <div className="page-header">
          <h1>Your cart</h1>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => void run(clear, 'Cart emptied.')}
            disabled={busy}
            data-testid="clear-cart"
          >
            Empty cart
          </button>
        </div>

        <ErrorAlert error={error} testId="cart-error" />

        {/* Issues come from the server: a line whose stock has fallen short, or
            whose product was deactivated. They also gate the checkout button. */}
        {cart.issues.length > 0 && (
          <div className="alert alert-warning" role="alert" data-testid="cart-issues">
            Some items need attention before you can check out:
            <ul>
              {cart.issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="cart-layout" style={{ marginTop: 'var(--space-4)' }}>
          <div className="card" data-testid="cart-items">
            {cart.items.map((item) => (
              <div
                className="cart-line"
                key={item.product_id}
                data-testid="cart-line"
                data-product-id={item.product_id}
                data-sku={item.sku}
                data-quantity={item.quantity}
              >
                <Link to={`/products/${item.product_id}`}>
                  <ProductImage src={item.image_url} alt={item.name} />
                </Link>

                <div className="stack-sm">
                  <Link to={`/products/${item.product_id}`} data-testid="cart-line-name">
                    {item.name}
                  </Link>
                  <span className="subtle mono">{item.sku}</span>
                  <span className="subtle">
                    <Money amount={item.unit_price} testId="cart-line-unit-price" /> each
                  </span>
                  {item.exceeds_stock && (
                    <span className="badge badge-danger" data-testid="cart-line-stock-warning">
                      Only {item.available_stock} available
                    </span>
                  )}
                  {!item.is_active && (
                    <span className="badge badge-danger" data-testid="cart-line-inactive">
                      No longer sold
                    </span>
                  )}
                </div>

                <div className="stack-sm right">
                  <Money
                    amount={item.line_total}
                    className="price"
                    testId="cart-line-total"
                  />
                  <QuantityStepper
                    value={item.quantity}
                    max={Math.max(item.quantity, item.available_stock)}
                    disabled={busy}
                    onChange={(next) => void run(() => updateItem(item.product_id, next))}
                    testId="cart-line-quantity"
                  />
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => void run(() => removeItem(item.product_id), 'Item removed.')}
                    disabled={busy}
                    data-testid="cart-line-remove"
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>

          <aside className="card" data-testid="cart-summary">
            <h2>Order summary</h2>

            {/* Every figure below is rendered straight from the API response.
                Nothing on this page performs arithmetic on money. */}
            <div className="summary-row">
              <span>Subtotal ({cart.item_count} items)</span>
              <Money amount={totals.subtotal} currency={totals.currency} testId="summary-subtotal" />
            </div>
            {totals.discount_total > 0 && (
              <div className="summary-row">
                <span>Discount</span>
                <span className="discount">
                  &minus;
                  <Money
                    amount={totals.discount_total}
                    currency={totals.currency}
                    testId="summary-discount"
                  />
                </span>
              </div>
            )}
            <div className="summary-row">
              <span>Tax</span>
              <Money amount={totals.tax} currency={totals.currency} testId="summary-tax" />
            </div>
            <div className="summary-row">
              <span>Shipping</span>
              {totals.shipping_fee === 0 ? (
                <span data-testid="summary-shipping" data-amount="0">
                  Free
                </span>
              ) : (
                <Money
                  amount={totals.shipping_fee}
                  currency={totals.currency}
                  testId="summary-shipping"
                />
              )}
            </div>
            <div className="summary-row total">
              <span>Total</span>
              <Money amount={totals.total} currency={totals.currency} testId="summary-total" />
            </div>

            <button
              type="button"
              className="btn btn-block"
              style={{ marginTop: 'var(--space-4)' }}
              disabled={!cart.is_checkout_ready || busy}
              onClick={() => navigate('/checkout')}
              data-testid="checkout-button"
            >
              Proceed to checkout
            </button>

            {!cart.is_checkout_ready && (
              <p className="subtle" style={{ marginTop: 'var(--space-2)', marginBottom: 0 }}>
                Resolve the issues above to continue.
              </p>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
