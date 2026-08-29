/**
 * Types mirroring the backend's response schemas.
 *
 * Note that every money field is `number`, not `string`. That is the contract
 * the backend commits to and the contract test suite enforces - if the API ever
 * started returning `"129.99"`, arithmetic here would silently concatenate
 * rather than add, which is exactly the drift those tests exist to catch.
 */

export interface ApiError {
  error: string;
  message: string;
  details: Record<string, unknown>;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_next: boolean;
  has_previous: boolean;
}

export interface Category {
  id: number;
  name: string;
  slug: string;
  description: string | null;
}

export interface CategoryWithCount extends Category {
  product_count: number;
}

export interface Brand {
  brand: string;
  product_count: number;
}

export interface ProductSummary {
  id: number;
  sku: string;
  name: string;
  price: number;
  brand: string;
  rating: string | number;
  image_url: string | null;
  is_active: boolean;
  in_stock: boolean;
  stock_quantity: number;
  category: Category;
}

export interface ProductDetail extends ProductSummary {
  description: string;
  created_at: string;
  updated_at: string;
}

export interface User {
  id: number;
  email: string;
  full_name: string;
  phone: string | null;
  role: 'customer' | 'admin';
  is_active: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface CartItem {
  product_id: number;
  sku: string;
  name: string;
  image_url: string | null;
  unit_price: number;
  quantity: number;
  line_total: number;
  available_stock: number;
  exceeds_stock: boolean;
  is_active: boolean;
}

export interface CartTotals {
  subtotal: number;
  discount_total: number;
  tax: number;
  shipping_fee: number;
  total: number;
  currency: string;
}

export interface Cart {
  id: number | null;
  items: CartItem[];
  item_count: number;
  distinct_item_count: number;
  totals: CartTotals;
  promo_code: string | null;
  issues: string[];
  is_checkout_ready: boolean;
}

export interface Quote {
  subtotal: number;
  discount_total: number;
  tax: number;
  shipping_fee: number;
  total: number;
  currency: string;
  promo_code: string | null;
  promo_description: string | null;
  item_count: number;
  issues: string[];
  is_checkout_ready: boolean;
}

export interface Address {
  id: number;
  label: string;
  full_name: string;
  line1: string;
  line2: string | null;
  city: string;
  state: string;
  postal_code: string;
  country: string;
  phone: string | null;
  is_default: boolean;
}

export type OrderStatus =
  | 'pending'
  | 'confirmed'
  | 'processing'
  | 'shipped'
  | 'delivered'
  | 'cancelled';

export type PaymentStatus = 'pending' | 'paid' | 'failed' | 'refunded';

export interface OrderItem {
  id: number;
  product_id: number;
  product_name: string;
  sku: string;
  unit_price: number;
  quantity: number;
  line_total: number;
}

export interface Payment {
  id: number;
  provider_reference: string | null;
  amount: number;
  currency: string;
  status: PaymentStatus;
  method: string;
  card_last4: string | null;
  card_brand: string | null;
  failure_code: string | null;
  failure_message: string | null;
  attempt: number;
  created_at: string;
}

export interface ShippingAddressSnapshot {
  full_name: string;
  line1: string;
  line2: string | null;
  city: string;
  state: string;
  postal_code: string;
  country: string;
  phone: string | null;
}

export interface OrderSummary {
  id: number;
  order_number: string;
  status: OrderStatus;
  payment_status: PaymentStatus;
  total: number;
  currency: string;
  item_count: number;
  created_at: string;
}

export interface Order {
  id: number;
  order_number: string;
  user_id: number;
  status: OrderStatus;
  payment_status: PaymentStatus;
  subtotal: number;
  discount_total: number;
  tax: number;
  shipping_fee: number;
  total: number;
  currency: string;
  promo_code: string | null;
  items: OrderItem[];
  payments: Payment[];
  shipping_address: ShippingAddressSnapshot;
  cancelled_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface Inventory {
  product_id: number;
  sku: string;
  name: string;
  quantity: number;
  updated_at: string;
}

export interface AdminStats {
  products_total: number;
  products_active: number;
  out_of_stock: number;
  users_total: number;
  orders_total: number;
  orders_by_status: Record<string, number>;
  paid_revenue: number;
}

export interface PaymentDetails {
  card_number: string;
  card_holder: string;
  expiry_month: number;
  expiry_year: number;
  cvv: string;
}

export type ProductSort =
  | 'relevance'
  | 'price_asc'
  | 'price_desc'
  | 'rating_desc'
  | 'newest'
  | 'name_asc';

export interface ProductQuery {
  q?: string;
  category?: string;
  brand?: string;
  min_price?: number;
  max_price?: number;
  min_rating?: number;
  in_stock?: boolean;
  sort?: ProductSort;
  page?: number;
  page_size?: number;
}
