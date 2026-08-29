import { Route, Routes } from 'react-router-dom';
import { Header } from '@/components/Header';
import { EmptyState } from '@/components/common';
import { ProtectedRoute } from '@/components/ProtectedRoute';
import { AuthProvider } from '@/context/AuthContext';
import { CartProvider } from '@/context/CartContext';
import { ToastProvider } from '@/context/ToastContext';
import { AdminPage } from '@/pages/AdminPage';
import { LoginPage, RegisterPage } from '@/pages/AuthPages';
import { CartPage } from '@/pages/CartPage';
import { CheckoutPage } from '@/pages/CheckoutPage';
import { HomePage } from '@/pages/HomePage';
import { OrderConfirmationPage, OrderDetailPage, OrdersPage } from '@/pages/OrderPages';
import { ProductDetailPage } from '@/pages/ProductDetailPage';
import { ProductsPage } from '@/pages/ProductsPage';
import { ProfilePage } from '@/pages/ProfilePage';

function NotFoundPage() {
  return (
    <div className="page">
      <div className="container">
        <EmptyState title="Page not found" testId="not-found">
          The page you were looking for does not exist.
        </EmptyState>
      </div>
    </div>
  );
}

export function App() {
  return (
    // Toasts are outermost so any provider below can raise one; Auth wraps Cart
    // because the cart is per-user and must reload when the session changes.
    <ToastProvider>
      <AuthProvider>
        <CartProvider>
          <div className="app-shell">
            <Header />

            <main>
              <Routes>
                <Route path="/" element={<HomePage />} />
                <Route path="/products" element={<ProductsPage />} />
                <Route path="/products/:productId" element={<ProductDetailPage />} />
                <Route path="/login" element={<LoginPage />} />
                <Route path="/register" element={<RegisterPage />} />

                <Route
                  path="/cart"
                  element={
                    <ProtectedRoute>
                      <CartPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/checkout"
                  element={
                    <ProtectedRoute>
                      <CheckoutPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/orders"
                  element={
                    <ProtectedRoute>
                      <OrdersPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/orders/:orderId"
                  element={
                    <ProtectedRoute>
                      <OrderDetailPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/orders/:orderId/confirmation"
                  element={
                    <ProtectedRoute>
                      <OrderConfirmationPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/profile"
                  element={
                    <ProtectedRoute>
                      <ProfilePage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/admin"
                  element={
                    <ProtectedRoute requireAdmin>
                      <AdminPage />
                    </ProtectedRoute>
                  }
                />

                <Route path="*" element={<NotFoundPage />} />
              </Routes>
            </main>

            <footer className="site-footer">
              <div className="container row row-between">
                <span>ShopSphere - a demo storefront built to be tested.</span>
                <a href="/docs" target="_blank" rel="noreferrer" data-testid="api-docs-link">
                  API documentation
                </a>
              </div>
            </footer>
          </div>
        </CartProvider>
      </AuthProvider>
    </ToastProvider>
  );
}
