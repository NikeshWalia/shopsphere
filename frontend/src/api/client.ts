/**
 * Typed HTTP client for the ShopSphere API.
 *
 * All requests go through `request()`, which gives one place to attach the
 * bearer token, one place to translate the API's error envelope into a thrown
 * `ApiRequestError`, and one place to generate the correlation id that ties a
 * browser action to a backend log line.
 */

import type {
  Address,
  AdminStats,
  ApiError,
  Brand,
  Cart,
  CategoryWithCount,
  Inventory,
  Order,
  OrderStatus,
  OrderSummary,
  Page,
  PaymentDetails,
  ProductDetail,
  ProductQuery,
  ProductSummary,
  Quote,
  TokenResponse,
  User,
} from '@/types/api';

const API_BASE = '/api/v1';
const TOKEN_KEY = 'shopsphere.token';

/** Thrown for every non-2xx response, carrying the API's structured error. */
export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, body: Partial<ApiError>) {
    super(body.message ?? `Request failed with status ${status}`);
    this.name = 'ApiRequestError';
    this.status = status;
    this.code = body.error ?? 'UNKNOWN_ERROR';
    this.details = body.details ?? {};
  }
}

export const tokenStore = {
  get: (): string | null => localStorage.getItem(TOKEN_KEY),
  set: (token: string): void => localStorage.setItem(TOKEN_KEY, token),
  clear: (): void => localStorage.removeItem(TOKEN_KEY),
};

/** Listeners notified when the API rejects our token, so the app can log out. */
type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, unknown>;
  headers?: Record<string, string>;
  auth?: boolean;
}

function buildQuery(query: Record<string, unknown> | undefined): string {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    // Omitting empty values matters: `?brand=` is a filter for the empty string
    // as far as a URL is concerned, and would return nothing.
    if (value === undefined || value === null || value === '') continue;
    params.append(key, String(value));
  }
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, headers = {}, auth = true } = options;

  const finalHeaders: Record<string, string> = {
    Accept: 'application/json',
    // Echoed back by the backend and included in its logs, so a failing UI
    // action can be traced to the exact server-side request.
    'X-Request-ID': crypto.randomUUID().replace(/-/g, ''),
    ...headers,
  };
  if (body !== undefined) finalHeaders['Content-Type'] = 'application/json';

  const token = tokenStore.get();
  if (auth && token) finalHeaders.Authorization = `Bearer ${token}`;

  const response = await fetch(`${API_BASE}${path}${buildQuery(query)}`, {
    method,
    headers: finalHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    if (response.status === 401 && token) {
      // The token is present but the server rejected it - expired, revoked, or
      // the account was deactivated. Clearing it here stops every subsequent
      // request failing the same way.
      tokenStore.clear();
      onUnauthorized?.();
    }
    throw new ApiRequestError(response.status, (parsed ?? {}) as Partial<ApiError>);
  }

  return parsed as T;
}

export const api = {
  auth: {
    register: (payload: {
      email: string;
      password: string;
      password_confirm: string;
      full_name: string;
      phone?: string;
    }) => request<TokenResponse>('/auth/register', { method: 'POST', body: payload, auth: false }),

    login: (email: string, password: string) =>
      request<TokenResponse>('/auth/login', {
        method: 'POST',
        body: { email, password },
        auth: false,
      }),

    me: () => request<User>('/auth/me'),

    updateProfile: (payload: { full_name?: string; phone?: string }) =>
      request<User>('/auth/me', { method: 'PATCH', body: payload }),

    changePassword: (current_password: string, new_password: string) =>
      request<{ message: string }>('/auth/me/password', {
        method: 'POST',
        body: { current_password, new_password },
      }),
  },

  catalog: {
    products: (query: ProductQuery = {}) =>
      request<Page<ProductSummary>>('/products', { query: query as Record<string, unknown>, auth: false }),
    product: (id: number) => request<ProductDetail>(`/products/${id}`, { auth: false }),
    categories: () => request<CategoryWithCount[]>('/categories', { auth: false }),
    brands: () => request<Brand[]>('/products/brands', { auth: false }),
  },

  cart: {
    get: () => request<Cart>('/cart'),
    addItem: (product_id: number, quantity: number) =>
      request<Cart>('/cart/items', { method: 'POST', body: { product_id, quantity } }),
    updateItem: (product_id: number, quantity: number) =>
      request<Cart>(`/cart/items/${product_id}`, { method: 'PATCH', body: { quantity } }),
    removeItem: (product_id: number) =>
      request<Cart>(`/cart/items/${product_id}`, { method: 'DELETE' }),
    clear: () => request<Cart>('/cart', { method: 'DELETE' }),
  },

  addresses: {
    list: () => request<Address[]>('/addresses'),
    create: (payload: Omit<Address, 'id'>) =>
      request<Address>('/addresses', { method: 'POST', body: payload }),
    update: (id: number, payload: Partial<Omit<Address, 'id'>>) =>
      request<Address>(`/addresses/${id}`, { method: 'PATCH', body: payload }),
    remove: (id: number) => request<{ message: string }>(`/addresses/${id}`, { method: 'DELETE' }),
  },

  orders: {
    quote: (promo_code?: string) =>
      request<Quote>('/checkout/quote', { method: 'POST', body: { promo_code: promo_code ?? null } }),

    checkout: (payload: { address_id: number; payment: PaymentDetails; promo_code?: string | null },
               idempotencyKey: string) =>
      request<Order>('/orders', {
        method: 'POST',
        body: payload,
        // Generated once per checkout attempt by the caller, so a double-click
        // or a retry after a network blip cannot create two orders.
        headers: { 'Idempotency-Key': idempotencyKey },
      }),

    list: (page = 1, page_size = 10) =>
      request<Page<OrderSummary>>('/orders', { query: { page, page_size } }),

    get: (id: number) => request<Order>(`/orders/${id}`),

    cancel: (id: number, reason?: string) =>
      request<Order>(`/orders/${id}/cancel`, { method: 'POST', body: { reason: reason ?? null } }),
  },

  admin: {
    stats: () => request<AdminStats>('/admin/stats'),
    users: (query: { search?: string; page?: number; page_size?: number } = {}) =>
      request<Page<User>>('/admin/users', { query }),
    setUserActive: (id: number, is_active: boolean) =>
      request<User>(`/admin/users/${id}/active`, { method: 'PATCH', query: { is_active } }),

    orders: (query: { status?: string; search?: string; page?: number; page_size?: number } = {}) =>
      request<Page<OrderSummary>>('/admin/orders', { query }),
    order: (id: number) => request<Order>(`/admin/orders/${id}`),
    setOrderStatus: (id: number, status: OrderStatus) =>
      request<Order>(`/admin/orders/${id}/status`, { method: 'PATCH', body: { status } }),

    createProduct: (payload: Record<string, unknown>) =>
      request<ProductDetail>('/admin/products', { method: 'POST', body: payload }),
    updateProduct: (id: number, payload: Record<string, unknown>) =>
      request<ProductDetail>(`/admin/products/${id}`, { method: 'PATCH', body: payload }),
    deactivateProduct: (id: number) =>
      request<ProductDetail>(`/admin/products/${id}`, { method: 'DELETE' }),
    setStock: (id: number, quantity: number) =>
      request<Inventory>(`/admin/products/${id}/stock`, { method: 'PUT', body: { quantity } }),
    inventory: (
      query: { low_stock_threshold?: number; search?: string; page?: number; page_size?: number } = {},
    ) => request<Page<Inventory>>('/admin/inventory', { query }),
  },
};
