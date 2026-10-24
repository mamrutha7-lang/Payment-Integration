/**
 * Fees — what the office has asked the student to pay, and Razorpay Checkout to
 * pay it. (A course's enrolment fee is paid from the Courses screen, but it is
 * listed here too, so a payment interrupted half-way can be finished.)
 *
 * The list is reloaded from the server after a payment rather than patched
 * locally; see core/razorpay-checkout.ts for why the browser's word is not taken.
 */

import { Component, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import {
  CheckoutResponse,
  PAYMENT_UNCONFIRMED,
  RazorpayOrder,
  confirmPayment,
  openCheckout,
} from '../../../core/razorpay-checkout';

interface Fee {
  id: string;
  title: string;
  amount: string;
  currency: string;
  due_date: string | null;
  status: 'PENDING' | 'PAID' | 'CANCELLED';
  overdue: boolean;
  paid_at: string | null;
  course_code: string | null;
}

function formatDate(iso: string | null): string {
  if (!iso) return '';
  const at = new Date(iso);
  return Number.isNaN(at.getTime())
    ? ''
    : at.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

@Component({
  selector: 'app-fees',
  standalone: true,
  templateUrl: './fees.component.html',
  styleUrl: './fees.component.scss',
})
export class FeesComponent {
  readonly state = signal<'loading' | 'data' | 'error'>('loading');
  readonly fees = signal<Fee[]>([]);
  /** The id of the fee being paid, so one button is busy and the rest stay usable. */
  readonly paying = signal<string | null>(null);
  readonly notice = signal<{ tone: 'good' | 'risk'; text: string } | null>(null);

  readonly formatDate = formatDate;

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/student/fees`, { credentials: 'include' });
      if (!res.ok) {
        this.state.set('error');
        return;
      }
      this.fees.set((await res.json()) as Fee[]);
      this.state.set('data');
    } catch {
      this.state.set('error');
    }
  }

  async pay(fee: Fee): Promise<void> {
    if (this.paying()) return;
    this.paying.set(fee.id);
    this.notice.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/fees/${fee.id}/order`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        this.notice.set({ tone: 'risk', text: body.detail ?? 'Could not start the payment.' });
        this.paying.set(null);
        return;
      }
      await openCheckout((await res.json()) as RazorpayOrder, {
        onPaid: (r) => void this.confirm(r),
        onFailed: (text) => this.notice.set({ tone: 'risk', text }),
        onDismiss: () => this.paying.set(null),
      });
    } catch {
      this.notice.set({ tone: 'risk', text: 'Could not start the payment. Please try again.' });
      this.paying.set(null);
    }
  }

  private async confirm(r: CheckoutResponse): Promise<void> {
    const ok = await confirmPayment(r);
    this.notice.set(
      ok
        ? { tone: 'good', text: 'Payment received. Thank you.' }
        : { tone: 'risk', text: PAYMENT_UNCONFIRMED },
    );
    this.paying.set(null);
    await this.load();
  }
}
