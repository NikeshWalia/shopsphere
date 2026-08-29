import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Spinner } from '@/components/common';
import { useAuth } from '@/context/AuthContext';

/**
 * Route guard.
 *
 * Renders nothing decisive until the stored token has been validated. Without
 * that wait, a page refresh on a protected route would briefly redirect to
 * login before the session was confirmed - visible to a user as a flash, and to
 * a test as a genuinely flaky redirect.
 *
 * This is a convenience for the UI only. It is not a security control: the API
 * enforces authentication and authorisation on every request regardless of what
 * the browser chooses to render.
 */
export function ProtectedRoute({
  children,
  requireAdmin = false,
}: {
  children: ReactNode;
  requireAdmin?: boolean;
}) {
  const { isAuthenticated, isAdmin, initialising } = useAuth();
  const location = useLocation();

  if (initialising) return <Spinner label="Checking your session" />;

  if (!isAuthenticated) {
    // The attempted path is preserved so login can return the user to where
    // they were heading instead of dumping them on the home page.
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }

  if (requireAdmin && !isAdmin) {
    return (
      <div className="page">
        <div className="container">
          <div className="alert alert-error" role="alert" data-testid="forbidden-message">
            This area is restricted to administrators.
          </div>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
