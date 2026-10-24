/**
 * Razorpay Checkout, shared by every screen that takes a payment (Fees, and
 * enrolling in a priced course).
 *
 * THE BROWSER NEVER DECIDES A PAYMENT SUCCEEDED. `onPaid` receives what
 * Razorpay returned and the caller forwards it to POST /student/fees/verify;
 * the server's answer is the only thing that counts. Closing the window is
 * `onDismiss`, a non-event: nothing was paid and nothing changed.
 */

import { environment } from '../../environments/environment';

export interface RazorpayOrder {
  key_id: string;
  order_id: string;
  amount: number;
  currency: string;
  title: string;
}

export interface CheckoutResponse {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}

interface RazorpayInstance {
  open(): void;
  on(event: 'payment.failed', cb: (r: { error?: { description?: string } }) => void): void;
}

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => RazorpayInstance;
  }
}

const CHECKOUT_SRC = 'https://checkout.razorpay.com/v1/checkout.js';

export const PAYMENT_UNCONFIRMED =
  'We could not confirm your payment yet. If money was deducted it will show here shortly; do not pay again.';

/** Loaded on first use, not at boot: most visits to this app never pay. */
function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = CHECKOUT_SRC;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('checkout script failed to load'));
    document.head.appendChild(script);
  });
}

export async function openCheckout(
  order: RazorpayOrder,
  handlers: {
    onPaid: (r: CheckoutResponse) => void;
    onFailed: (message: string) => void;
    onDismiss: () => void;
  },
): Promise<void> {
  await loadCheckout();
  const checkout = new window.Razorpay!({
    key: order.key_id,
    order_id: order.order_id,
    amount: order.amount,
    currency: order.currency,
    name: 'REEP',
    description: order.title,
    handler: handlers.onPaid,
    modal: { ondismiss: handlers.onDismiss },
  });
  checkout.on('payment.failed', (r) =>
    handlers.onFailed(r.error?.description ?? 'The payment failed. You have not been charged.'),
  );
  checkout.open();
}

/** Forward Checkout's result to the server. True only if the server confirmed it. */
export async function confirmPayment(r: CheckoutResponse): Promise<boolean> {
  try {
    const res = await fetch(`${environment.apiBase}/student/fees/verify`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(r),
    });
    return res.ok;
  } catch {
    return false;
  }
}
