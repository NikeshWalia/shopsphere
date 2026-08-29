import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '@/api/client';
import { ErrorAlert, Spinner } from '@/components/common';
import { ProductCard } from '@/components/ProductCard';
import type { CategoryWithCount, ProductSummary } from '@/types/api';

export function HomePage() {
  const [categories, setCategories] = useState<CategoryWithCount[]>([]);
  const [featured, setFeatured] = useState<ProductSummary[]>([]);
  const [newest, setNewest] = useState<ProductSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        // Fetched in parallel: three sequential round trips would triple the
        // time to first meaningful paint for no benefit.
        const [categoryList, topRated, latest] = await Promise.all([
          api.catalog.categories(),
          api.catalog.products({ sort: 'rating_desc', page_size: 8, in_stock: true }),
          api.catalog.products({ sort: 'newest', page_size: 4 }),
        ]);
        if (cancelled) return;
        setCategories(categoryList);
        setFeatured(topRated.items);
        setNewest(latest.items);
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

  return (
    <div className="page" data-testid="home-page">
      <div className="container">
        <section className="hero">
          <h1>Everything for the desk, the pocket and the shelf</h1>
          <p>
            A demo storefront built to be tested: real inventory rules, real payment failures and a
            backend that decides every price.
          </p>
          <Link
            to="/products"
            className="btn btn-secondary"
            style={{ marginTop: 'var(--space-4)' }}
            data-testid="hero-shop-button"
          >
            Browse the catalogue
          </Link>
        </section>

        <ErrorAlert error={error} />
        {loading && <Spinner label="Loading the storefront" />}

        {!loading && !error && (
          <>
            <h2>Shop by category</h2>
            <div className="category-strip" data-testid="category-strip">
              {categories.map((category) => (
                <Link
                  key={category.id}
                  to={`/products?category=${category.slug}`}
                  className="category-tile"
                  data-testid="category-tile"
                  data-category={category.slug}
                >
                  {category.name}
                  <span>{category.product_count} items</span>
                </Link>
              ))}
            </div>

            <div className="page-header">
              <h2>Highest rated</h2>
              <Link to="/products?sort=rating_desc" data-testid="see-all-featured">
                See all
              </Link>
            </div>
            <div className="product-grid" data-testid="featured-products">
              {featured.map((product) => (
                <ProductCard key={product.id} product={product} />
              ))}
            </div>

            <div className="page-header" style={{ marginTop: 'var(--space-6)' }}>
              <h2>New arrivals</h2>
              <Link to="/products?sort=newest" data-testid="see-all-newest">
                See all
              </Link>
            </div>
            <div className="product-grid" data-testid="newest-products">
              {newest.map((product) => (
                <ProductCard key={product.id} product={product} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
