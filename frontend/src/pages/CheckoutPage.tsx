import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, ApiRequestError } from '@/api/client';
import { ErrorAlert, Money, Spinner } from '@/components/common';
import { useCart } from '@/context/CartContext';
import { useToast } from '@/context/ToastContext';
import type { Address, PaymentDetails, Quote } from '@/types/api';

type Step = 'address' | 'payment' | 'review';

const STEPS: { key: Step; label: string }[] = [
  { key: 'address', label: 'Address' },
  { key: 'payment', label: 'Payment' },
  { key: 'review', label: 'Review' },
];

/**
 * Card numbers the mock provider recognises, surfaced in the UI so the failure
 * scenarios are discoverable without reading the source. The list is short and
 * mirrors the provider's own /test-cards endpoint.
 */
const TEST_CARDS = [
  { number: '4111111111111111', label: 'Approved' },
  { number: '4000000000000002', label: 'Declined - insufficient funds' },
  { number: '4000000000000069', label: 'Declined - expired card' },
  { number: '4000000000000119', label: 'Provider error (HTTP 500)' },
  { number: '4000000000000259', label: 'Provider timeout' },
];

const EMPTY_ADDRESS = {
  label: 'Home',
  full_name: '',
  line1: '',
  line2: '',
  city: '',
  state: '',
  postal_code: '',
  country: 'US',
  phone: '',
  is_default: false,
};

export function CheckoutPage() {
  const navigate = useNavigate();
  const { cart, reload } = useCart();
  const { notify } = useToast();

  const [step, setStep] = useState<Step>('address');
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [selectedAddressId, setSelectedAddressId] = useState<number | null>(null);
  const [newAddress, setNewAddress] = useState(EMPTY_ADDRESS);
  const [showAddressForm, setShowAddressForm] = useState(false);

  const [payment, setPayment] = useState<PaymentDetails>({
    card_number: '4111111111111111',
    card_holder: '',
    expiry_month: 12,
    expiry_year: new Date().getFullYear() + 3,
    cvv: '123',
  });

  const [promoCode, setPromoCode] = useState('');
  const [quote, setQuote] = useState<Quote | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [placing, setPlacing] = useState(false);

  // One key per mounted checkout. Regenerated only after a failure that leaves
  // no order behind, so a double-clicked "Place order" - or a retry after a
  // dropped response - can never create two orders.
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID().replace(/-/g, ''));

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [list, initialQuote] = await Promise.all([api.addresses.list(), api.orders.quote()]);
        if (cancelled) return;
        setAddresses(list);
        setQuote(initialQuote);
        const preferred = list.find((address) => address.is_default) ?? list[0];
        if (preferred) setSelectedAddressId(preferred.id);
        else setShowAddressForm(true);
      } catch (caught) {
        if (!cancelled) setError(caught);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshQuote = async (code?: string) => {
    setError(null);
    try {
      setQuote(await api.orders.quote(code || undefined));
    } catch (caught) {
      setError(caught);
    }
  };

  const handleCreateAddress = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      const created = await api.addresses.create({
        ...newAddress,
        line2: newAddress.line2 || null,
        phone: newAddress.phone || null,
      });
      setAddresses((current) => [created, ...current]);
      setSelectedAddressId(created.id);
      setShowAddressForm(false);
      setNewAddress(EMPTY_ADDRESS);
      notify('Address saved.', 'success');
    } catch (caught) {
      setError(caught);
    }
  };

  const placeOrder = async () => {
    if (selectedAddressId === null) {
      setStep('address');
      return;
    }
    setPlacing(true);
    setError(null);
    try {
      const order = await api.orders.checkout(
        { address_id: selectedAddressId, payment, promo_code: promoCode || null },
        idempotencyKey,
      );
      await reload();
      navigate(`/orders/${order.id}/confirmation`, { replace: true, state: { order } });
    } catch (caught) {
      setError(caught);
      if (caught instanceof ApiRequestError) {
        notify(caught.message, 'error');
        // A declined card or provider error cancels the order and returns the
        // stock, so a retry is a genuinely new order and needs a new key. A
        // timeout is different: the original order still exists as pending, so
        // the key is kept to avoid creating a duplicate alongside it.
        if (caught.status !== 504) {
          setIdempotencyKey(crypto.randomUUID().replace(/-/g, ''));
        }
        await reload();
      }
    } finally {
      setPlacing(false);
    }
  };

  const selectedAddress = useMemo(
    () => addresses.find((address) => address.id === selectedAddressId) ?? null,
    [addresses, selectedAddressId],
  );

  if (loading) return <Spinner label="Preparing checkout" />;

  if (cart.items.length === 0) {
    return (
      <div className="page" data-testid="checkout-page">
        <div className="container">
          <div className="alert alert-info" data-testid="checkout-empty-cart">
            Your cart is empty. <Link to="/products">Browse the catalogue</Link> to add something.
          </div>
        </div>
      </div>
    );
  }

  const stepIndex = STEPS.findIndex((entry) => entry.key === step);

  return (
    <div className="page" data-testid="checkout-page" data-step={step}>
      <div className="container">
        <h1>Checkout</h1>

        <div className="checkout-steps" data-testid="checkout-steps">
          {STEPS.map((entry, index) => (
            <div
              key={entry.key}
              className={`checkout-step ${entry.key === step ? 'active' : index < stepIndex ? 'done' : ''}`}
              data-testid={`checkout-step-${entry.key}`}
              data-state={entry.key === step ? 'active' : index < stepIndex ? 'done' : 'pending'}
            >
              <span className="step-index">{index + 1}</span>
              {entry.label}
            </div>
          ))}
        </div>

        <ErrorAlert error={error} testId="checkout-error" />

        <div className="cart-layout">
          <div className="card">
            {step === 'address' && (
              <div className="stack" data-testid="address-step">
                <h2>Shipping address</h2>

                {addresses.length > 0 && !showAddressForm && (
                  <div className="stack-sm">
                    {addresses.map((address) => (
                      <label
                        key={address.id}
                        className={`selectable ${address.id === selectedAddressId ? 'selected' : ''}`}
                        data-testid="address-option"
                        data-address-id={address.id}
                      >
                        <div className="row" style={{ alignItems: 'flex-start' }}>
                          <input
                            type="radio"
                            name="address"
                            checked={address.id === selectedAddressId}
                            onChange={() => setSelectedAddressId(address.id)}
                            style={{ width: 'auto', marginTop: 4 }}
                            data-testid="address-radio"
                          />
                          <div>
                            <strong>{address.full_name}</strong>
                            {address.is_default && (
                              <span className="badge badge-info" style={{ marginLeft: 8 }}>
                                Default
                              </span>
                            )}
                            <div className="muted">
                              {address.line1}
                              {address.line2 ? `, ${address.line2}` : ''}, {address.city},{' '}
                              {address.state} {address.postal_code}, {address.country}
                            </div>
                          </div>
                        </div>
                      </label>
                    ))}
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setShowAddressForm(true)}
                      data-testid="add-address-toggle"
                    >
                      + Use a different address
                    </button>
                  </div>
                )}

                {showAddressForm && (
                  <form onSubmit={handleCreateAddress} className="stack" data-testid="address-form">
                    <div className="form-grid">
                      <div className="full">
                        <label htmlFor="addr-name">Full name</label>
                        <input
                          id="addr-name"
                          required
                          value={newAddress.full_name}
                          onChange={(e) => setNewAddress({ ...newAddress, full_name: e.target.value })}
                          data-testid="address-full-name"
                        />
                      </div>
                      <div className="full">
                        <label htmlFor="addr-line1">Address line 1</label>
                        <input
                          id="addr-line1"
                          required
                          value={newAddress.line1}
                          onChange={(e) => setNewAddress({ ...newAddress, line1: e.target.value })}
                          data-testid="address-line1"
                        />
                      </div>
                      <div className="full">
                        <label htmlFor="addr-line2">Address line 2 (optional)</label>
                        <input
                          id="addr-line2"
                          value={newAddress.line2}
                          onChange={(e) => setNewAddress({ ...newAddress, line2: e.target.value })}
                          data-testid="address-line2"
                        />
                      </div>
                      <div>
                        <label htmlFor="addr-city">City</label>
                        <input
                          id="addr-city"
                          required
                          value={newAddress.city}
                          onChange={(e) => setNewAddress({ ...newAddress, city: e.target.value })}
                          data-testid="address-city"
                        />
                      </div>
                      <div>
                        <label htmlFor="addr-state">State</label>
                        <input
                          id="addr-state"
                          required
                          value={newAddress.state}
                          onChange={(e) => setNewAddress({ ...newAddress, state: e.target.value })}
                          data-testid="address-state"
                        />
                      </div>
                      <div>
                        <label htmlFor="addr-postal">Postal code</label>
                        <input
                          id="addr-postal"
                          required
                          value={newAddress.postal_code}
                          onChange={(e) =>
                            setNewAddress({ ...newAddress, postal_code: e.target.value })
                          }
                          data-testid="address-postal-code"
                        />
                      </div>
                      <div>
                        <label htmlFor="addr-country">Country</label>
                        <input
                          id="addr-country"
                          required
                          maxLength={2}
                          value={newAddress.country}
                          onChange={(e) => setNewAddress({ ...newAddress, country: e.target.value })}
                          data-testid="address-country"
                        />
                      </div>
                    </div>
                    <div className="row">
                      <button type="submit" className="btn" data-testid="address-save">
                        Save address
                      </button>
                      {addresses.length > 0 && (
                        <button
                          type="button"
                          className="btn btn-secondary"
                          onClick={() => setShowAddressForm(false)}
                          data-testid="address-cancel"
                        >
                          Cancel
                        </button>
                      )}
                    </div>
                  </form>
                )}

                {!showAddressForm && (
                  <button
                    type="button"
                    className="btn"
                    disabled={selectedAddressId === null}
                    onClick={() => setStep('payment')}
                    data-testid="continue-to-payment"
                  >
                    Continue to payment
                  </button>
                )}
              </div>
            )}

            {step === 'payment' && (
              <div className="stack" data-testid="payment-step">
                <h2>Payment details</h2>
                <div className="alert alert-info">
                  This is a mock payment provider. No real card is ever charged, and only the last
                  four digits are stored.
                </div>

                <div className="form-grid">
                  <div className="full">
                    <label htmlFor="card-number">Card number</label>
                    <input
                      id="card-number"
                      required
                      value={payment.card_number}
                      onChange={(e) => setPayment({ ...payment, card_number: e.target.value })}
                      data-testid="card-number"
                    />
                  </div>
                  <div className="full">
                    <label htmlFor="card-holder">Name on card</label>
                    <input
                      id="card-holder"
                      required
                      value={payment.card_holder}
                      onChange={(e) => setPayment({ ...payment, card_holder: e.target.value })}
                      data-testid="card-holder"
                    />
                  </div>
                  <div>
                    <label htmlFor="card-month">Expiry month</label>
                    <input
                      id="card-month"
                      type="number"
                      min={1}
                      max={12}
                      required
                      value={payment.expiry_month}
                      onChange={(e) =>
                        setPayment({ ...payment, expiry_month: Number(e.target.value) })
                      }
                      data-testid="card-expiry-month"
                    />
                  </div>
                  <div>
                    <label htmlFor="card-year">Expiry year</label>
                    <input
                      id="card-year"
                      type="number"
                      min={2024}
                      max={2100}
                      required
                      value={payment.expiry_year}
                      onChange={(e) =>
                        setPayment({ ...payment, expiry_year: Number(e.target.value) })
                      }
                      data-testid="card-expiry-year"
                    />
                  </div>
                  <div>
                    <label htmlFor="card-cvv">CVV</label>
                    <input
                      id="card-cvv"
                      required
                      value={payment.cvv}
                      onChange={(e) => setPayment({ ...payment, cvv: e.target.value })}
                      data-testid="card-cvv"
                    />
                  </div>
                </div>

                <details data-testid="test-cards">
                  <summary className="subtle" style={{ cursor: 'pointer' }}>
                    Test cards for simulating failures
                  </summary>
                  <div className="test-card-list" style={{ marginTop: 'var(--space-2)' }}>
                    {TEST_CARDS.map((card) => (
                      <button
                        key={card.number}
                        type="button"
                        onClick={() => setPayment({ ...payment, card_number: card.number })}
                        data-testid="test-card-option"
                        data-card={card.number}
                      >
                        {card.number} - {card.label}
                      </button>
                    ))}
                  </div>
                </details>

                <div className="row">
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => setStep('address')}
                    data-testid="back-to-address"
                  >
                    Back
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={!payment.card_number || !payment.card_holder}
                    onClick={() => setStep('review')}
                    data-testid="continue-to-review"
                  >
                    Review order
                  </button>
                </div>
              </div>
            )}

            {step === 'review' && (
              <div className="stack" data-testid="review-step">
                <h2>Review your order</h2>

                <div className="card card-tight">
                  <h3>Shipping to</h3>
                  {selectedAddress && (
                    <p className="muted" data-testid="review-address" style={{ marginBottom: 0 }}>
                      {selectedAddress.full_name}, {selectedAddress.line1}
                      {selectedAddress.line2 ? `, ${selectedAddress.line2}` : ''},{' '}
                      {selectedAddress.city}, {selectedAddress.state} {selectedAddress.postal_code}
                    </p>
                  )}
                </div>

                <div className="card card-tight">
                  <h3>Paying with</h3>
                  <p className="muted mono" data-testid="review-card" style={{ marginBottom: 0 }}>
                    &bull;&bull;&bull;&bull; {payment.card_number.slice(-4)}
                  </p>
                </div>

                <div className="card card-tight">
                  <h3>Items</h3>
                  {cart.items.map((item) => (
                    <div className="summary-row" key={item.product_id} data-testid="review-item">
                      <span>
                        {item.name} &times; {item.quantity}
                      </span>
                      <Money amount={item.line_total} />
                    </div>
                  ))}
                </div>

                <div className="row">
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => setStep('payment')}
                    data-testid="back-to-payment"
                  >
                    Back
                  </button>
                  <button
                    type="button"
                    className="btn grow"
                    onClick={() => void placeOrder()}
                    disabled={placing}
                    data-testid="place-order-button"
                  >
                    {placing ? 'Placing order...' : 'Place order'}
                  </button>
                </div>
              </div>
            )}
          </div>

          <aside className="card" data-testid="checkout-summary">
            <h2>Summary</h2>

            <div className="stack-sm" style={{ marginBottom: 'var(--space-3)' }}>
              <label htmlFor="promo-code">Promotion code</label>
              <div className="row" style={{ flexWrap: 'nowrap' }}>
                <input
                  id="promo-code"
                  value={promoCode}
                  onChange={(e) => setPromoCode(e.target.value.toUpperCase())}
                  placeholder="WELCOME10"
                  data-testid="promo-input"
                />
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => void refreshQuote(promoCode)}
                  data-testid="promo-apply"
                >
                  Apply
                </button>
              </div>
            </div>

            {quote?.issues.map((issue) => (
              <div className="alert alert-warning" key={issue} data-testid="quote-issue">
                {issue}
              </div>
            ))}

            {quote && (
              <>
                <div className="summary-row">
                  <span>Subtotal</span>
                  <Money amount={quote.subtotal} currency={quote.currency} testId="checkout-subtotal" />
                </div>
                {quote.discount_total > 0 && (
                  <div className="summary-row">
                    <span>Discount {quote.promo_code ? `(${quote.promo_code})` : ''}</span>
                    <span className="discount">
                      &minus;
                      <Money
                        amount={quote.discount_total}
                        currency={quote.currency}
                        testId="checkout-discount"
                      />
                    </span>
                  </div>
                )}
                <div className="summary-row">
                  <span>Tax</span>
                  <Money amount={quote.tax} currency={quote.currency} testId="checkout-tax" />
                </div>
                <div className="summary-row">
                  <span>Shipping</span>
                  {quote.shipping_fee === 0 ? (
                    <span data-testid="checkout-shipping" data-amount="0">
                      Free
                    </span>
                  ) : (
                    <Money
                      amount={quote.shipping_fee}
                      currency={quote.currency}
                      testId="checkout-shipping"
                    />
                  )}
                </div>
                <div className="summary-row total">
                  <span>Total</span>
                  <Money amount={quote.total} currency={quote.currency} testId="checkout-total" />
                </div>
              </>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
