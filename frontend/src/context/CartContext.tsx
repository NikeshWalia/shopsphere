/**
 * Cart state.
 *
 * The server is the single source of truth: every mutation returns the freshly
 * priced cart and that response replaces local state wholesale. There is no
 * client-side total arithmetic anywhere in this file, which is what makes it
 * structurally impossible for the UI to disagree with what the customer is
 * charged.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { api, ApiRequestError } from '@/api/client';
import type { Cart } from '@/types/api';
import { useAuth } from '@/context/AuthContext';

const EMPTY_CART: Cart = {
  id: null,
  items: [],
  item_count: 0,
  distinct_item_count: 0,
  totals: {
    subtotal: 0,
    discount_total: 0,
    tax: 0,
    shipping_fee: 0,
    total: 0,
    currency: 'USD',
  },
  promo_code: null,
  issues: [],
  is_checkout_ready: false,
};

interface CartContextValue {
  cart: Cart;
  loading: boolean;
  /** Set while a mutation is in flight, so buttons can be disabled. */
  busy: boolean;
  addItem: (productId: number, quantity: number) => Promise<void>;
  updateItem: (productId: number, quantity: number) => Promise<void>;
  removeItem: (productId: number) => Promise<void>;
  clear: () => Promise<void>;
  reload: () => Promise<void>;
}

const CartContext = createContext<CartContextValue | null>(null);

export function CartProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, initialising } = useAuth();
  const [cart, setCart] = useState<Cart>(EMPTY_CART);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    if (!isAuthenticated) {
      setCart(EMPTY_CART);
      return;
    }
    setLoading(true);
    try {
      setCart(await api.cart.get());
    } catch (error) {
      // A 401 here means the session ended; AuthContext handles the sign-out,
      // so all this needs to do is stop showing a stale cart.
      if (error instanceof ApiRequestError && error.status === 401) setCart(EMPTY_CART);
      else throw error;
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    if (initialising) return;
    void reload();
  }, [initialising, reload]);

  /** Runs a mutation and replaces local state with the server's response. */
  const mutate = useCallback(async (operation: () => Promise<Cart>) => {
    setBusy(true);
    try {
      setCart(await operation());
    } finally {
      setBusy(false);
    }
  }, []);

  const value = useMemo<CartContextValue>(
    () => ({
      cart,
      loading,
      busy,
      addItem: (productId, quantity) => mutate(() => api.cart.addItem(productId, quantity)),
      updateItem: (productId, quantity) => mutate(() => api.cart.updateItem(productId, quantity)),
      removeItem: (productId) => mutate(() => api.cart.removeItem(productId)),
      clear: () => mutate(() => api.cart.clear()),
      reload,
    }),
    [cart, loading, busy, mutate, reload],
  );

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartContextValue {
  const context = useContext(CartContext);
  if (!context) throw new Error('useCart must be used inside a CartProvider');
  return context;
}
