import { useEffect, useState, type FormEvent } from 'react';
import { api, ApiRequestError } from '@/api/client';
import { ErrorAlert, Spinner } from '@/components/common';
import { useAuth } from '@/context/AuthContext';
import { useToast } from '@/context/ToastContext';
import { formatDate } from '@/utils/format';
import type { Address } from '@/types/api';

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

export function ProfilePage() {
  const { user, refresh } = useAuth();
  const { notify } = useToast();

  const [tab, setTab] = useState<'profile' | 'addresses' | 'password'>('profile');
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [loadingAddresses, setLoadingAddresses] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const [profileForm, setProfileForm] = useState({ full_name: '', phone: '' });
  const [passwordForm, setPasswordForm] = useState({ current_password: '', new_password: '' });
  const [addressForm, setAddressForm] = useState(EMPTY_ADDRESS);
  const [showAddressForm, setShowAddressForm] = useState(false);

  useEffect(() => {
    if (user) setProfileForm({ full_name: user.full_name, phone: user.phone ?? '' });
  }, [user]);

  const loadAddresses = async () => {
    setLoadingAddresses(true);
    try {
      setAddresses(await api.addresses.list());
    } catch (caught) {
      setError(caught);
    } finally {
      setLoadingAddresses(false);
    }
  };

  useEffect(() => {
    void loadAddresses();
  }, []);

  const saveProfile = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      await api.auth.updateProfile({
        full_name: profileForm.full_name,
        phone: profileForm.phone || undefined,
      });
      await refresh();
      notify('Profile updated.', 'success');
    } catch (caught) {
      setError(caught);
    }
  };

  const changePassword = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      await api.auth.changePassword(passwordForm.current_password, passwordForm.new_password);
      setPasswordForm({ current_password: '', new_password: '' });
      notify('Password changed.', 'success');
    } catch (caught) {
      setError(caught);
      if (caught instanceof ApiRequestError) notify(caught.message, 'error');
    }
  };

  const createAddress = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      await api.addresses.create({
        ...addressForm,
        line2: addressForm.line2 || null,
        phone: addressForm.phone || null,
      });
      setAddressForm(EMPTY_ADDRESS);
      setShowAddressForm(false);
      await loadAddresses();
      notify('Address added.', 'success');
    } catch (caught) {
      setError(caught);
    }
  };

  const removeAddress = async (id: number) => {
    setError(null);
    try {
      await api.addresses.remove(id);
      await loadAddresses();
      notify('Address deleted.', 'success');
    } catch (caught) {
      setError(caught);
      if (caught instanceof ApiRequestError) notify(caught.message, 'error');
    }
  };

  const makeDefault = async (id: number) => {
    try {
      await api.addresses.update(id, { is_default: true });
      await loadAddresses();
    } catch (caught) {
      setError(caught);
    }
  };

  if (!user) return <Spinner label="Loading profile" />;

  return (
    <div className="page" data-testid="profile-page">
      <div className="container">
        <h1>Your account</h1>

        <div className="tabs" role="tablist">
          <button
            type="button"
            className={`tab ${tab === 'profile' ? 'active' : ''}`}
            onClick={() => setTab('profile')}
            data-testid="tab-profile"
          >
            Profile
          </button>
          <button
            type="button"
            className={`tab ${tab === 'addresses' ? 'active' : ''}`}
            onClick={() => setTab('addresses')}
            data-testid="tab-addresses"
          >
            Addresses
          </button>
          <button
            type="button"
            className={`tab ${tab === 'password' ? 'active' : ''}`}
            onClick={() => setTab('password')}
            data-testid="tab-password"
          >
            Password
          </button>
        </div>

        <ErrorAlert error={error} testId="profile-error" />

        {tab === 'profile' && (
          <div className="card" data-testid="profile-panel">
            <dl className="spec-list" style={{ marginBottom: 'var(--space-5)' }}>
              <dt>Email</dt>
              <dd data-testid="profile-email">{user.email}</dd>
              <dt>Role</dt>
              <dd data-testid="profile-role">{user.role}</dd>
              <dt>Member since</dt>
              <dd>{formatDate(user.created_at)}</dd>
            </dl>

            <form onSubmit={saveProfile} className="stack" data-testid="profile-form">
              <div>
                <label htmlFor="profile-name">Full name</label>
                <input
                  id="profile-name"
                  value={profileForm.full_name}
                  onChange={(e) => setProfileForm({ ...profileForm, full_name: e.target.value })}
                  data-testid="profile-name-input"
                />
              </div>
              <div>
                <label htmlFor="profile-phone">Phone</label>
                <input
                  id="profile-phone"
                  value={profileForm.phone}
                  onChange={(e) => setProfileForm({ ...profileForm, phone: e.target.value })}
                  data-testid="profile-phone-input"
                />
              </div>
              <button type="submit" className="btn" data-testid="profile-save">
                Save changes
              </button>
            </form>
          </div>
        )}

        {tab === 'addresses' && (
          <div className="stack" data-testid="addresses-panel">
            {loadingAddresses ? (
              <Spinner label="Loading addresses" />
            ) : (
              <div className="stack-sm">
                {addresses.map((address) => (
                  <div
                    className="card card-tight"
                    key={address.id}
                    data-testid="address-card"
                    data-address-id={address.id}
                  >
                    <div className="row row-between">
                      <div>
                        <strong>{address.label}</strong>
                        {address.is_default && (
                          <span className="badge badge-info" style={{ marginLeft: 8 }} data-testid="default-badge">
                            Default
                          </span>
                        )}
                        <div className="muted">
                          {address.full_name}, {address.line1}, {address.city}, {address.state}{' '}
                          {address.postal_code}, {address.country}
                        </div>
                      </div>
                      <div className="row">
                        {!address.is_default && (
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => void makeDefault(address.id)}
                            data-testid="make-default"
                          >
                            Make default
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => void removeAddress(address.id)}
                          data-testid="delete-address"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {showAddressForm ? (
              <form onSubmit={createAddress} className="card stack" data-testid="new-address-form">
                <h2>New address</h2>
                <div className="form-grid">
                  <div>
                    <label htmlFor="p-label">Label</label>
                    <input
                      id="p-label"
                      value={addressForm.label}
                      onChange={(e) => setAddressForm({ ...addressForm, label: e.target.value })}
                      data-testid="new-address-label"
                    />
                  </div>
                  <div>
                    <label htmlFor="p-name">Full name</label>
                    <input
                      id="p-name"
                      required
                      value={addressForm.full_name}
                      onChange={(e) => setAddressForm({ ...addressForm, full_name: e.target.value })}
                      data-testid="new-address-full-name"
                    />
                  </div>
                  <div className="full">
                    <label htmlFor="p-line1">Address line 1</label>
                    <input
                      id="p-line1"
                      required
                      value={addressForm.line1}
                      onChange={(e) => setAddressForm({ ...addressForm, line1: e.target.value })}
                      data-testid="new-address-line1"
                    />
                  </div>
                  <div>
                    <label htmlFor="p-city">City</label>
                    <input
                      id="p-city"
                      required
                      value={addressForm.city}
                      onChange={(e) => setAddressForm({ ...addressForm, city: e.target.value })}
                      data-testid="new-address-city"
                    />
                  </div>
                  <div>
                    <label htmlFor="p-state">State</label>
                    <input
                      id="p-state"
                      required
                      value={addressForm.state}
                      onChange={(e) => setAddressForm({ ...addressForm, state: e.target.value })}
                      data-testid="new-address-state"
                    />
                  </div>
                  <div>
                    <label htmlFor="p-postal">Postal code</label>
                    <input
                      id="p-postal"
                      required
                      value={addressForm.postal_code}
                      onChange={(e) =>
                        setAddressForm({ ...addressForm, postal_code: e.target.value })
                      }
                      data-testid="new-address-postal-code"
                    />
                  </div>
                  <div>
                    <label htmlFor="p-country">Country</label>
                    <input
                      id="p-country"
                      required
                      maxLength={2}
                      value={addressForm.country}
                      onChange={(e) => setAddressForm({ ...addressForm, country: e.target.value })}
                      data-testid="new-address-country"
                    />
                  </div>
                </div>
                <div className="row">
                  <button type="submit" className="btn" data-testid="new-address-save">
                    Save address
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => setShowAddressForm(false)}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <button
                type="button"
                className="btn"
                onClick={() => setShowAddressForm(true)}
                data-testid="add-address-button"
              >
                Add an address
              </button>
            )}
          </div>
        )}

        {tab === 'password' && (
          <div className="card" data-testid="password-panel">
            <form onSubmit={changePassword} className="stack" data-testid="password-form">
              <div>
                <label htmlFor="current-password">Current password</label>
                <input
                  id="current-password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={passwordForm.current_password}
                  onChange={(e) =>
                    setPasswordForm({ ...passwordForm, current_password: e.target.value })
                  }
                  data-testid="current-password"
                />
              </div>
              <div>
                <label htmlFor="new-password">New password</label>
                <input
                  id="new-password"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={passwordForm.new_password}
                  onChange={(e) => setPasswordForm({ ...passwordForm, new_password: e.target.value })}
                  data-testid="new-password"
                />
              </div>
              <button type="submit" className="btn" data-testid="change-password-submit">
                Change password
              </button>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
