import { useEffect, useState, type FormEvent } from 'react';
import { Link, NavLink, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { useCart } from '@/context/CartContext';
import { useToast } from '@/context/ToastContext';

export function Header() {
  const { user, isAuthenticated, isAdmin, logout } = useAuth();
  const { cart } = useCart();
  const { notify } = useToast();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [term, setTerm] = useState(searchParams.get('q') ?? '');

  // Keeps the box in step with the URL, so navigating back to a search or
  // following a shared link shows the term that produced the results.
  useEffect(() => {
    setTerm(searchParams.get('q') ?? '');
  }, [searchParams]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = term.trim();
    navigate(trimmed ? `/products?q=${encodeURIComponent(trimmed)}` : '/products');
  };

  const handleLogout = () => {
    logout();
    notify('You have been signed out.', 'info');
    navigate('/');
  };

  return (
    <header className="site-header" data-testid="site-header">
      <div className="container">
        <Link to="/" className="brand" data-testid="brand-link">
          ShopSphere
        </Link>

        <nav className="site-nav" aria-label="Main">
          <NavLink to="/products" className="nav-link" data-testid="nav-products">
            Products
          </NavLink>
          {isAuthenticated && (
            <NavLink to="/orders" className="nav-link" data-testid="nav-orders">
              Orders
            </NavLink>
          )}
          {isAdmin && (
            <NavLink to="/admin" className="nav-link" data-testid="nav-admin">
              Admin
            </NavLink>
          )}
        </nav>

        <form className="header-search" role="search" onSubmit={submitSearch}>
          <input
            type="search"
            placeholder="Search products"
            aria-label="Search products"
            value={term}
            onChange={(event) => setTerm(event.target.value)}
            data-testid="header-search-input"
          />
          <button type="submit" className="btn btn-secondary" data-testid="header-search-submit">
            Search
          </button>
        </form>

        <div className="row" style={{ marginLeft: 'auto', gap: 'var(--space-3)' }}>
          <Link to="/cart" className="cart-pill" data-testid="nav-cart">
            Cart
            {/* Rendered only when non-empty so a test can assert the badge's
                absence for an empty cart rather than checking for "0". */}
            {cart.item_count > 0 && (
              <span className="cart-count" data-testid="cart-count">
                {cart.item_count}
              </span>
            )}
          </Link>

          {isAuthenticated ? (
            <div className="row" style={{ gap: 'var(--space-2)' }}>
              <Link to="/profile" className="nav-link" data-testid="nav-profile">
                {user?.full_name?.split(' ')[0] ?? 'Profile'}
              </Link>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={handleLogout}
                data-testid="logout-button"
              >
                Sign out
              </button>
            </div>
          ) : (
            <div className="row" style={{ gap: 'var(--space-2)' }}>
              <Link to="/login" className="nav-link" data-testid="nav-login">
                Sign in
              </Link>
              <Link to="/register" className="btn btn-sm" data-testid="nav-register">
                Register
              </Link>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
