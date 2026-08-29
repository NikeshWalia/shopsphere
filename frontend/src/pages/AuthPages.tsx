import { useState, type FormEvent } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { ApiRequestError } from '@/api/client';
import { ErrorAlert } from '@/components/common';
import { useAuth } from '@/context/AuthContext';
import { useToast } from '@/context/ToastContext';

interface LocationState {
  from?: string;
}

export function LoginPage() {
  const { login, isAuthenticated, initialising } = useAuth();
  const { notify } = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const redirectTo = (location.state as LocationState | null)?.from ?? '/';

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!initialising && isAuthenticated) return <Navigate to={redirectTo} replace />;

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const user = await login(email, password);
      notify(`Welcome back, ${user.full_name.split(' ')[0]}.`, 'success');
      navigate(redirectTo, { replace: true });
    } catch (caught) {
      setError(caught);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page" data-testid="login-page">
      <div className="container">
        <div className="auth-shell card">
          <h1>Sign in</h1>
          <p className="muted">Use your ShopSphere account to continue.</p>

          <ErrorAlert error={error} testId="login-error" />

          <form onSubmit={handleSubmit} className="stack" noValidate data-testid="login-form">
            <div>
              <label htmlFor="login-email">Email</label>
              <input
                id="login-email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                data-testid="login-email"
              />
            </div>
            <div>
              <label htmlFor="login-password">Password</label>
              <input
                id="login-password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                data-testid="login-password"
              />
            </div>
            <button type="submit" className="btn btn-block" disabled={submitting} data-testid="login-submit">
              {submitting ? 'Signing in...' : 'Sign in'}
            </button>
          </form>

          <p className="muted" style={{ marginTop: 'var(--space-4)', marginBottom: 0 }}>
            No account yet?{' '}
            <Link to="/register" data-testid="link-to-register">
              Create one
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}

export function RegisterPage() {
  const { register, isAuthenticated, initialising } = useAuth();
  const { notify } = useToast();
  const navigate = useNavigate();

  const [form, setForm] = useState({
    full_name: '',
    email: '',
    password: '',
    password_confirm: '',
    phone: '',
  });
  const [error, setError] = useState<unknown>(null);
  const [clientError, setClientError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!initialising && isAuthenticated) return <Navigate to="/" replace />;

  const update = (field: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [field]: event.target.value }));

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setClientError(null);

    // Checked here purely to save a round trip and give instant feedback. The
    // server validates the same rule, and that check is the authoritative one.
    if (form.password !== form.password_confirm) {
      setClientError('Password and confirmation do not match.');
      return;
    }

    setSubmitting(true);
    try {
      const user = await register({
        email: form.email,
        password: form.password,
        password_confirm: form.password_confirm,
        full_name: form.full_name,
        phone: form.phone || undefined,
      });
      notify(`Account created. Welcome, ${user.full_name.split(' ')[0]}.`, 'success');
      navigate('/', { replace: true });
    } catch (caught) {
      setError(caught);
      if (caught instanceof ApiRequestError && caught.code === 'EMAIL_ALREADY_REGISTERED') {
        notify('That email address is already registered.', 'error');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page" data-testid="register-page">
      <div className="container">
        <div className="auth-shell card">
          <h1>Create an account</h1>
          <p className="muted">
            Passwords need at least 8 characters, including upper case, lower case and a digit.
          </p>

          {clientError && (
            <div className="alert alert-error" role="alert" data-testid="register-error">
              {clientError}
            </div>
          )}
          <ErrorAlert error={error} testId="register-error" />

          <form onSubmit={handleSubmit} className="stack" noValidate data-testid="register-form">
            <div>
              <label htmlFor="register-name">Full name</label>
              <input
                id="register-name"
                type="text"
                autoComplete="name"
                required
                value={form.full_name}
                onChange={update('full_name')}
                data-testid="register-name"
              />
            </div>
            <div>
              <label htmlFor="register-email">Email</label>
              <input
                id="register-email"
                type="email"
                autoComplete="email"
                required
                value={form.email}
                onChange={update('email')}
                data-testid="register-email"
              />
            </div>
            <div>
              <label htmlFor="register-phone">Phone (optional)</label>
              <input
                id="register-phone"
                type="text"
                autoComplete="tel"
                value={form.phone}
                onChange={update('phone')}
                data-testid="register-phone"
              />
            </div>
            <div>
              <label htmlFor="register-password">Password</label>
              <input
                id="register-password"
                type="password"
                autoComplete="new-password"
                required
                value={form.password}
                onChange={update('password')}
                data-testid="register-password"
              />
            </div>
            <div>
              <label htmlFor="register-password-confirm">Confirm password</label>
              <input
                id="register-password-confirm"
                type="password"
                autoComplete="new-password"
                required
                value={form.password_confirm}
                onChange={update('password_confirm')}
                data-testid="register-password-confirm"
              />
            </div>
            <button
              type="submit"
              className="btn btn-block"
              disabled={submitting}
              data-testid="register-submit"
            >
              {submitting ? 'Creating account...' : 'Create account'}
            </button>
          </form>

          <p className="muted" style={{ marginTop: 'var(--space-4)', marginBottom: 0 }}>
            Already registered?{' '}
            <Link to="/login" data-testid="link-to-login">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
