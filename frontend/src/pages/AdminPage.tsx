import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiRequestError } from '@/api/client';
import { ErrorAlert, Money, OrderStatusBadge, PaymentStatusBadge, Spinner } from '@/components/common';
import { useToast } from '@/context/ToastContext';
import { formatDate } from '@/utils/format';
import type { AdminStats, Inventory, OrderStatus, OrderSummary, Page, User } from '@/types/api';

type Tab = 'overview' | 'inventory' | 'orders' | 'users';

const NEXT_STATUS: Partial<Record<OrderStatus, OrderStatus>> = {
  pending: 'confirmed',
  confirmed: 'processing',
  processing: 'shipped',
  shipped: 'delivered',
};

export function AdminPage() {
  const { notify } = useToast();
  const [tab, setTab] = useState<Tab>('overview');
  const [error, setError] = useState<unknown>(null);

  return (
    <div className="page" data-testid="admin-page" data-tab={tab}>
      <div className="container">
        <h1>Administration</h1>

        <div className="tabs" role="tablist">
          {(['overview', 'inventory', 'orders', 'users'] as Tab[]).map((entry) => (
            <button
              key={entry}
              type="button"
              className={`tab ${tab === entry ? 'active' : ''}`}
              onClick={() => {
                setTab(entry);
                setError(null);
              }}
              data-testid={`admin-tab-${entry}`}
            >
              {entry[0]?.toUpperCase()}
              {entry.slice(1)}
            </button>
          ))}
        </div>

        <ErrorAlert error={error} testId="admin-error" />

        {tab === 'overview' && <OverviewPanel onError={setError} />}
        {tab === 'inventory' && <InventoryPanel onError={setError} notify={notify} />}
        {tab === 'orders' && <OrdersPanel onError={setError} notify={notify} />}
        {tab === 'users' && <UsersPanel onError={setError} notify={notify} />}
      </div>
    </div>
  );
}

function OverviewPanel({ onError }: { onError: (error: unknown) => void }) {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      try {
        setStats(await api.admin.stats());
      } catch (caught) {
        onError(caught);
      } finally {
        setLoading(false);
      }
    })();
  }, [onError]);

  if (loading) return <Spinner label="Loading dashboard" />;
  if (!stats) return null;

  return (
    <div className="stack" data-testid="admin-overview">
      <div className="stat-grid">
        <div className="stat-tile">
          <div className="value" data-testid="stat-products">
            {stats.products_active}
          </div>
          <div className="label">Active products</div>
        </div>
        <div className="stat-tile">
          <div className="value" data-testid="stat-out-of-stock">
            {stats.out_of_stock}
          </div>
          <div className="label">Out of stock</div>
        </div>
        <div className="stat-tile">
          <div className="value" data-testid="stat-orders">
            {stats.orders_total}
          </div>
          <div className="label">Orders</div>
        </div>
        <div className="stat-tile">
          <div className="value" data-testid="stat-users">
            {stats.users_total}
          </div>
          <div className="label">Users</div>
        </div>
        <div className="stat-tile">
          <div className="value">
            <Money amount={stats.paid_revenue} testId="stat-revenue" />
          </div>
          <div className="label">Paid revenue</div>
        </div>
      </div>

      <div className="card">
        <h2>Orders by status</h2>
        <div className="table-wrap">
          <table className="table" data-testid="orders-by-status">
            <thead>
              <tr>
                <th>Status</th>
                <th className="right">Count</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(stats.orders_by_status).map(([status, count]) => (
                <tr key={status}>
                  <td>
                    <OrderStatusBadge status={status} />
                  </td>
                  <td className="right">{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function InventoryPanel({
  onError,
  notify,
}: {
  onError: (error: unknown) => void;
  notify: (message: string, variant?: 'success' | 'error' | 'info') => void;
}) {
  const [result, setResult] = useState<Page<Inventory> | null>(null);
  const [loading, setLoading] = useState(true);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [search, setSearch] = useState('');

  const load = useCallback(
    async (term = '') => {
      setLoading(true);
      try {
        setResult(await api.admin.inventory({ search: term || undefined, page_size: 50 }));
      } catch (caught) {
        onError(caught);
      } finally {
        setLoading(false);
      }
    },
    [onError],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (productId: number) => {
    const raw = drafts[productId];
    if (raw === undefined) return;
    const quantity = Number(raw);
    if (!Number.isInteger(quantity) || quantity < 0) {
      notify('Stock must be a whole number of zero or more.', 'error');
      return;
    }
    try {
      await api.admin.setStock(productId, quantity);
      notify('Stock updated.', 'success');
      setDrafts((current) => {
        const next = { ...current };
        delete next[productId];
        return next;
      });
      await load(search);
    } catch (caught) {
      onError(caught);
      if (caught instanceof ApiRequestError) notify(caught.message, 'error');
    }
  };

  return (
    <div className="stack" data-testid="admin-inventory">
      <div className="row">
        <input
          type="search"
          placeholder="Search by SKU or name"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          data-testid="inventory-search"
          aria-label="Search inventory"
        />
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => void load(search)}
          data-testid="inventory-search-submit"
        >
          Search
        </button>
      </div>

      {loading ? <Spinner label="Loading inventory" /> : (
      <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>SKU</th>
            <th>Product</th>
            <th className="right">Stock</th>
            <th>Set to</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {result?.items.map((row) => (
            <tr key={row.product_id} data-testid="inventory-row" data-product-id={row.product_id}>
              <td className="mono">{row.sku}</td>
              <td>
                <Link to={`/products/${row.product_id}`}>{row.name}</Link>
              </td>
              <td className="right" data-testid="inventory-quantity" data-quantity={row.quantity}>
                {row.quantity}
              </td>
              <td style={{ width: 120 }}>
                <input
                  type="number"
                  min={0}
                  value={drafts[row.product_id] ?? ''}
                  placeholder={String(row.quantity)}
                  onChange={(e) =>
                    setDrafts((current) => ({ ...current, [row.product_id]: e.target.value }))
                  }
                  data-testid="inventory-input"
                  aria-label={`Set stock for ${row.sku}`}
                />
              </td>
              <td>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => void save(row.product_id)}
                  disabled={drafts[row.product_id] === undefined}
                  data-testid="inventory-save"
                >
                  Save
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      )}
    </div>
  );
}

function OrdersPanel({
  onError,
  notify,
}: {
  onError: (error: unknown) => void;
  notify: (message: string, variant?: 'success' | 'error' | 'info') => void;
}) {
  const [result, setResult] = useState<Page<OrderSummary> | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setResult(await api.admin.orders({ page_size: 25 }));
    } catch (caught) {
      onError(caught);
    } finally {
      setLoading(false);
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const advance = async (id: number, status: OrderStatus) => {
    try {
      await api.admin.setOrderStatus(id, status);
      notify(`Order moved to ${status}.`, 'success');
      await load();
    } catch (caught) {
      onError(caught);
      if (caught instanceof ApiRequestError) notify(caught.message, 'error');
    }
  };

  if (loading) return <Spinner label="Loading orders" />;

  return (
    <div className="table-wrap" data-testid="admin-orders">
      <table className="table">
        <thead>
          <tr>
            <th>Order</th>
            <th>Placed</th>
            <th>Status</th>
            <th>Payment</th>
            <th className="right">Total</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {result?.items.map((order) => {
            const next = NEXT_STATUS[order.status];
            return (
              <tr key={order.id} data-testid="admin-order-row" data-order-id={order.id}>
                <td className="mono">{order.order_number}</td>
                <td className="nowrap">{formatDate(order.created_at)}</td>
                <td>
                  <OrderStatusBadge status={order.status} />
                </td>
                <td>
                  <PaymentStatusBadge status={order.payment_status} />
                </td>
                <td className="right">
                  <Money amount={order.total} currency={order.currency} />
                </td>
                <td>
                  <div className="row">
                    {/* Only the one legal next transition is offered. The API
                        validates it again; this simply avoids showing an
                        admin a button that is guaranteed to 409. */}
                    {next && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => void advance(order.id, next)}
                        data-testid="advance-order"
                        data-next-status={next}
                      >
                        Mark {next}
                      </button>
                    )}
                    {['pending', 'confirmed', 'processing'].includes(order.status) && (
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => void advance(order.id, 'cancelled')}
                        data-testid="cancel-order-admin"
                      >
                        Cancel
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function UsersPanel({
  onError,
  notify,
}: {
  onError: (error: unknown) => void;
  notify: (message: string, variant?: 'success' | 'error' | 'info') => void;
}) {
  const [result, setResult] = useState<Page<User> | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  const load = useCallback(
    async (term: string) => {
      setLoading(true);
      try {
        setResult(await api.admin.users({ search: term || undefined, page_size: 25 }));
      } catch (caught) {
        onError(caught);
      } finally {
        setLoading(false);
      }
    },
    [onError],
  );

  useEffect(() => {
    void load('');
  }, [load]);

  const toggle = async (user: User) => {
    try {
      await api.admin.setUserActive(user.id, !user.is_active);
      notify(`${user.email} ${user.is_active ? 'deactivated' : 'activated'}.`, 'success');
      await load(search);
    } catch (caught) {
      onError(caught);
      if (caught instanceof ApiRequestError) notify(caught.message, 'error');
    }
  };

  return (
    <div className="stack" data-testid="admin-users">
      <div className="row">
        <input
          type="search"
          placeholder="Search by email or name"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          data-testid="user-search"
          aria-label="Search users"
        />
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => void load(search)}
          data-testid="user-search-submit"
        >
          Search
        </button>
      </div>

      {loading ? (
        <Spinner label="Loading users" />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Name</th>
                <th>Role</th>
                <th>Status</th>
                <th>Joined</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {result?.items.map((user) => (
                <tr key={user.id} data-testid="user-row" data-user-id={user.id}>
                  <td data-testid="user-email">{user.email}</td>
                  <td>{user.full_name}</td>
                  <td>
                    <span className={`badge ${user.role === 'admin' ? 'badge-info' : 'badge-neutral'}`}>
                      {user.role}
                    </span>
                  </td>
                  <td>
                    <span className={`badge ${user.is_active ? 'badge-success' : 'badge-danger'}`}>
                      {user.is_active ? 'active' : 'inactive'}
                    </span>
                  </td>
                  <td className="nowrap">{formatDate(user.created_at)}</td>
                  <td className="right">
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => void toggle(user)}
                      data-testid="toggle-user-active"
                    >
                      {user.is_active ? 'Deactivate' : 'Activate'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
