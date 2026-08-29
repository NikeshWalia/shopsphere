import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiRequestError } from '@/api/client';
import {
  BackLink,
  EmptyState,
  ErrorAlert,
  Money,
  ProductImage,
  QuantityStepper,
  Spinner,
  StockBadge,
} from '@/components/common';
import { useAuth } from '@/context/AuthContext';
import { useCart } from '@/context/CartContext';
import { useToast } from '@/context/ToastContext';
import { formatRating } from '@/utils/format';
import type { ProductDetail } from '@/types/api';

export function ProductDetailPage() {
  const { productId } = useParams<{ productId: string }>();
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const { addItem, busy } = useCart();
  const { notify } = useToast();

  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [quantity, setQuantity] = useState(1);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [addError, setAddError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    void (async () => {
      try {
        const detail = await api.catalog.product(Number(productId));
        if (!cancelled) {
          setProduct(detail);
          setQuantity(1);
        }
      } catch (caught) {
        if (!cancelled) setLoadError(caught);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [productId]);

  const handleAdd = async () => {
    if (!product) return;
    if (!isAuthenticated) {
      // Carts are server-side and per-user, so adding requires a session.
      // Sending the customer back here after login keeps the flow intact.
      navigate('/login', { state: { from: `/products/${product.id}` } });
      return;
    }
    setAddError(null);
    try {
      await addItem(product.id, quantity);
      notify(`${product.name} added to your cart.`, 'success');
      // Reflect the stock the server just reserved against, so the stepper's
      // ceiling stays honest without a full reload.
      setProduct({ ...product, stock_quantity: product.stock_quantity });
    } catch (caught) {
      setAddError(caught);
      if (caught instanceof ApiRequestError) notify(caught.message, 'error');
    }
  };

  if (loading) return <Spinner label="Loading product" />;

  if (loadError instanceof ApiRequestError && loadError.status === 404) {
    return (
      <div className="page">
        <div className="container">
          <EmptyState title="Product not found" testId="product-not-found">
            This product does not exist, or is no longer available.
          </EmptyState>
        </div>
      </div>
    );
  }

  if (loadError || !product) {
    return (
      <div className="page">
        <div className="container">
          <ErrorAlert error={loadError} />
        </div>
      </div>
    );
  }

  const maxQuantity = Math.max(1, Math.min(product.stock_quantity, 99));

  return (
    <div className="page" data-testid="product-detail-page" data-product-id={product.id}>
      <div className="container stack">
        <BackLink to="/products">Back to products</BackLink>

        <div className="product-detail">
          <ProductImage src={product.image_url} alt={product.name} />

          <div className="stack">
            <div>
              <span className="subtle">
                {product.brand} &middot; {product.category.name}
              </span>
              <h1 data-testid="product-name">{product.name}</h1>
              <div className="row">
                <Money amount={product.price} className="price price-lg" testId="product-price" />
                <span className="rating" data-testid="product-rating">
                  &#9733; {formatRating(product.rating)}
                </span>
              </div>
            </div>

            <StockBadge quantity={product.stock_quantity} />

            <p data-testid="product-description">{product.description}</p>

            <dl className="spec-list">
              <dt>SKU</dt>
              <dd className="mono" data-testid="product-sku">
                {product.sku}
              </dd>
              <dt>Brand</dt>
              <dd data-testid="product-brand">{product.brand}</dd>
              <dt>Category</dt>
              <dd data-testid="product-category">{product.category.name}</dd>
              <dt>Availability</dt>
              <dd data-testid="product-stock" data-stock={product.stock_quantity}>
                {product.stock_quantity} in stock
              </dd>
            </dl>

            <ErrorAlert error={addError} testId="add-to-cart-error" />

            {product.stock_quantity > 0 ? (
              <div className="row">
                <QuantityStepper
                  value={quantity}
                  max={maxQuantity}
                  onChange={setQuantity}
                  disabled={busy}
                />
                <button
                  type="button"
                  className="btn"
                  onClick={handleAdd}
                  disabled={busy}
                  data-testid="add-to-cart-button"
                >
                  {busy ? 'Adding...' : 'Add to cart'}
                </button>
              </div>
            ) : (
              <button type="button" className="btn" disabled data-testid="add-to-cart-button">
                Out of stock
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
