import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '@/api/client';
import { EmptyState, ErrorAlert, Pagination, Spinner } from '@/components/common';
import { ProductCard } from '@/components/ProductCard';
import type { Brand, CategoryWithCount, Page, ProductSort, ProductSummary } from '@/types/api';

const PAGE_SIZE = 12;

const SORT_OPTIONS: { value: ProductSort; label: string }[] = [
  { value: 'relevance', label: 'Relevance' },
  { value: 'price_asc', label: 'Price: low to high' },
  { value: 'price_desc', label: 'Price: high to low' },
  { value: 'rating_desc', label: 'Highest rated' },
  { value: 'newest', label: 'Newest' },
  { value: 'name_asc', label: 'Name A-Z' },
];

export function ProductsPage() {
  // The URL is the single source of truth for the query. That makes every
  // filtered view shareable and bookmarkable, makes back/forward behave
  // correctly, and lets a UI test navigate straight to a filter combination
  // instead of clicking its way there.
  const [searchParams, setSearchParams] = useSearchParams();

  const [results, setResults] = useState<Page<ProductSummary> | null>(null);
  const [categories, setCategories] = useState<CategoryWithCount[]>([]);
  const [brands, setBrands] = useState<Brand[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const query = useMemo(
    () => ({
      q: searchParams.get('q') ?? undefined,
      category: searchParams.get('category') ?? undefined,
      brand: searchParams.get('brand') ?? undefined,
      min_price: searchParams.get('min_price') ? Number(searchParams.get('min_price')) : undefined,
      max_price: searchParams.get('max_price') ? Number(searchParams.get('max_price')) : undefined,
      min_rating: searchParams.get('min_rating') ? Number(searchParams.get('min_rating')) : undefined,
      in_stock: searchParams.get('in_stock') === 'true' ? true : undefined,
      sort: (searchParams.get('sort') as ProductSort | null) ?? 'relevance',
      page: Number(searchParams.get('page') ?? '1'),
      page_size: PAGE_SIZE,
    }),
    [searchParams],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const page = await api.catalog.products(query);
        // Guards against a slow earlier request resolving after a newer one and
        // overwriting the current results with stale data.
        if (!cancelled) setResults(page);
      } catch (caught) {
        if (!cancelled) setError(caught);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [query]);

  useEffect(() => {
    void (async () => {
      try {
        const [categoryList, brandList] = await Promise.all([
          api.catalog.categories(),
          api.catalog.brands(),
        ]);
        setCategories(categoryList);
        setBrands(brandList);
      } catch {
        // Facets are an enhancement; the listing works without them.
      }
    })();
  }, []);

  const updateParam = useCallback(
    (key: string, value: string | null) => {
      const next = new URLSearchParams(searchParams);
      if (value === null || value === '') next.delete(key);
      else next.set(key, value);
      // Any filter change resets to page 1 - staying on page 4 of a result set
      // that now has two pages would show an empty screen.
      if (key !== 'page') next.delete('page');
      setSearchParams(next);
    },
    [searchParams, setSearchParams],
  );

  const clearFilters = () => {
    const next = new URLSearchParams();
    const term = searchParams.get('q');
    if (term) next.set('q', term);
    setSearchParams(next);
  };

  const activeFilterCount = ['category', 'brand', 'min_price', 'max_price', 'min_rating', 'in_stock']
    .filter((key) => searchParams.get(key))
    .length;

  return (
    <div className="page" data-testid="products-page">
      <div className="container">
        <div className="page-header">
          <div>
            <h1>{query.q ? `Results for "${query.q}"` : 'All products'}</h1>
            {results && (
              <p className="muted" data-testid="result-count" data-total={results.total}>
                {results.total} {results.total === 1 ? 'product' : 'products'} found
              </p>
            )}
          </div>
        </div>

        <div className="catalog-layout">
          <aside className="filter-panel card card-tight" data-testid="filter-panel">
            <div className="row row-between">
              <h3 style={{ margin: 0 }}>Filters</h3>
              {activeFilterCount > 0 && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={clearFilters}
                  data-testid="clear-filters"
                >
                  Clear ({activeFilterCount})
                </button>
              )}
            </div>

            <div className="filter-group">
              <h3>Category</h3>
              <select
                value={searchParams.get('category') ?? ''}
                onChange={(event) => updateParam('category', event.target.value || null)}
                data-testid="filter-category"
                aria-label="Filter by category"
              >
                <option value="">All categories</option>
                {categories.map((category) => (
                  <option key={category.id} value={category.slug}>
                    {category.name} ({category.product_count})
                  </option>
                ))}
              </select>
            </div>

            <div className="filter-group">
              <h3>Brand</h3>
              <select
                value={searchParams.get('brand') ?? ''}
                onChange={(event) => updateParam('brand', event.target.value || null)}
                data-testid="filter-brand"
                aria-label="Filter by brand"
              >
                <option value="">All brands</option>
                {brands.map((brand) => (
                  <option key={brand.brand} value={brand.brand}>
                    {brand.brand} ({brand.product_count})
                  </option>
                ))}
              </select>
            </div>

            <div className="filter-group">
              <h3>Price</h3>
              <div className="row" style={{ gap: 'var(--space-2)', flexWrap: 'nowrap' }}>
                <input
                  type="number"
                  min={0}
                  placeholder="Min"
                  aria-label="Minimum price"
                  defaultValue={searchParams.get('min_price') ?? ''}
                  onBlur={(event) => updateParam('min_price', event.target.value || null)}
                  data-testid="filter-min-price"
                />
                <input
                  type="number"
                  min={0}
                  placeholder="Max"
                  aria-label="Maximum price"
                  defaultValue={searchParams.get('max_price') ?? ''}
                  onBlur={(event) => updateParam('max_price', event.target.value || null)}
                  data-testid="filter-max-price"
                />
              </div>
            </div>

            <div className="filter-group">
              <h3>Minimum rating</h3>
              <select
                value={searchParams.get('min_rating') ?? ''}
                onChange={(event) => updateParam('min_rating', event.target.value || null)}
                data-testid="filter-min-rating"
                aria-label="Filter by minimum rating"
              >
                <option value="">Any rating</option>
                <option value="4.5">4.5 and up</option>
                <option value="4">4.0 and up</option>
                <option value="3">3.0 and up</option>
              </select>
            </div>

            <div className="filter-group">
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={searchParams.get('in_stock') === 'true'}
                  onChange={(event) => updateParam('in_stock', event.target.checked ? 'true' : null)}
                  data-testid="filter-in-stock"
                />
                In stock only
              </label>
            </div>
          </aside>

          <section>
            <div className="toolbar">
              <label className="row" style={{ gap: 'var(--space-2)', margin: 0 }}>
                <span className="nowrap">Sort by</span>
                <select
                  value={query.sort}
                  onChange={(event) => updateParam('sort', event.target.value)}
                  data-testid="sort-select"
                  aria-label="Sort products"
                >
                  {SORT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <ErrorAlert error={error} />
            {loading && <Spinner label="Loading products" />}

            {!loading && !error && results && results.items.length === 0 && (
              <EmptyState title="No products match those filters" testId="no-results">
                Try a different search term, or clear some filters.
              </EmptyState>
            )}

            {!loading && !error && results && results.items.length > 0 && (
              <>
                <div className="product-grid" data-testid="product-grid">
                  {results.items.map((product) => (
                    <ProductCard key={product.id} product={product} />
                  ))}
                </div>
                <Pagination
                  page={results.page}
                  totalPages={results.total_pages}
                  onChange={(next) => updateParam('page', String(next))}
                />
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
