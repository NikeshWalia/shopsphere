import { Link } from 'react-router-dom';
import { Money, ProductImage, StockBadge } from '@/components/common';
import { formatRating } from '@/utils/format';
import type { ProductSummary } from '@/types/api';

export function ProductCard({ product }: { product: ProductSummary }) {
  return (
    // Every attribute a test might want to filter or assert on is exposed as a
    // data-* attribute, so assertions never have to parse rendered currency
    // strings or infer state from styling.
    <article
      className="product-card"
      data-testid="product-card"
      data-product-id={product.id}
      data-sku={product.sku}
      data-price={product.price}
      data-brand={product.brand}
      data-category={product.category.slug}
      data-rating={product.rating}
      data-in-stock={product.in_stock}
    >
      <Link to={`/products/${product.id}`} aria-label={product.name}>
        <ProductImage src={product.image_url} alt={product.name} />
      </Link>

      <div className="product-body">
        <span className="subtle">{product.brand}</span>
        <Link to={`/products/${product.id}`} className="product-name" data-testid="product-name">
          {product.name}
        </Link>

        <div className="row row-between" style={{ marginTop: 'auto' }}>
          <Money amount={product.price} className="price" testId="product-price" />
          <span className="rating" data-testid="product-rating">
            &#9733; {formatRating(product.rating)}
          </span>
        </div>

        <StockBadge quantity={product.stock_quantity} />
      </div>
    </article>
  );
}
