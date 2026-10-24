/**
 * Resume Builder — "Contact Details" section (stepper key `contact`).
 *
 * Owns `data.contact` = { other_phones[{code,number}], personal_emails[str],
 * web_links[{type,url}], current_address{line1,line2,country,state,city,postal},
 * permanent_same, permanent_address{…} }. The primary phone and college email
 * are LOCKED — the email is the signed-in session email (AuthService); the
 * synced primary phone has no client source yet and shows blank-locked.
 *
 * Repeatable rows (.rowline + .addlink) drive the phone / email / link lists;
 * unchecking "Same as current address" reveals a second full address form. The
 * local model is seeded from svc.section('contact', …), re-hydrated on load, and
 * flushed with svc.patch('contact', …) on every edit.
 */

import { Component, computed, effect, inject, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AuthService } from '../../../../core/auth.service';
import { ResumeBuilderService } from '../resume-builder.service';
import { ResumeIdentityService } from '../resume-identity.service';

interface Phone {
  code: string;
  number: string;
}
interface WebLink {
  type: string;
  url: string;
}
interface Address {
  line1: string;
  line2: string;
  country: string;
  state: string;
  city: string;
  postal: string;
}
interface ContactData {
  other_phones: Phone[];
  personal_emails: string[];
  web_links: WebLink[];
  current_address: Address;
  permanent_same: boolean;
  permanent_address: Address;
}

const LINK_TYPES = ['LinkedIn', 'GitHub', 'Portfolio', 'Other'] as const;

@Component({
  selector: 'rb-contact',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './contact.component.html',
  styles: [
    `
      /* small "why it matters" pill on placement-critical fields */
      .reqp {
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0;
        text-transform: none;
        color: var(--brand-purple);
        background: rgba(138, 90, 30, 0.1);
        padding: 2px 6px;
        border-radius: 5px;
        margin-left: 6px;
        white-space: nowrap;
      }

      /* Phone width (2026-09-22): this label is 10px, the smallest text in the app,
   and it is the one that tells a student a field is required for their
   placement profile. Mobile only — the desktop builder is unchanged. It has to
   live here rather than in the builder's own stylesheet, because Angular scopes
   a component's styles to its OWN template and this span is rendered by this
   child component. */
      @media (max-width: 640px) {
        .reqp {
          font-size: 11px;
        }
      }
    `,
  ],
})
export class RbContactComponent {
  private readonly svc = inject(ResumeBuilderService);
  private readonly auth = inject(AuthService);
  private readonly identity = inject(ResumeIdentityService);

  readonly linkTypes = LINK_TYPES;

  /** Locked college / official email from the signed-in session. */
  readonly email = computed(() => this.auth.session()?.email ?? '');

  /**
   * The primary phone on the student's record.
   *
   * This field showed the placeholder "Synced from record" and nothing else,
   * for a record that holds the number: `GET /api/student/profile` returns
   * `phone` and this screen never read it. Marked required with an asterisk and
   * disabled, it was a field the student could neither fill nor get filled.
   */
  readonly syncedPhone = this.identity.phone;

  m: ContactData = this.normalize(this.svc.section('contact', {}));

  private hydrated = false;

  constructor() {
    effect(() => {
      if (this.svc.loaded() && !this.hydrated) {
        this.hydrated = true;
        this.m = untracked(() => this.normalize(this.svc.section('contact', {})));
      }
    });
    void this.identity.load();
  }

  private address(raw: Partial<Address> | undefined): Address {
    return {
      line1: raw?.line1 ?? '',
      line2: raw?.line2 ?? '',
      country: raw?.country ?? 'India',
      state: raw?.state ?? '',
      city: raw?.city ?? '',
      postal: raw?.postal ?? '',
    };
  }

  private normalize(raw: Partial<ContactData>): ContactData {
    return {
      other_phones: Array.isArray(raw.other_phones)
        ? raw.other_phones.map((p) => ({ code: p?.code ?? '+91', number: p?.number ?? '' }))
        : [],
      personal_emails: Array.isArray(raw.personal_emails)
        ? raw.personal_emails.map((e) => String(e ?? ''))
        : [],
      web_links: Array.isArray(raw.web_links)
        ? raw.web_links.map((l) => ({ type: l?.type ?? 'LinkedIn', url: l?.url ?? '' }))
        : [],
      current_address: this.address(raw.current_address),
      permanent_same: raw.permanent_same ?? true,
      permanent_address: this.address(raw.permanent_address),
    };
  }

  push(): void {
    this.svc.patch('contact', JSON.parse(JSON.stringify(this.m)));
  }

  // --- other phones ---
  addPhone(): void {
    this.m.other_phones = [...this.m.other_phones, { code: '+91', number: '' }];
    this.push();
  }
  removePhone(i: number): void {
    this.m.other_phones = this.m.other_phones.filter((_, idx) => idx !== i);
    this.push();
  }

  // --- personal emails (array of plain strings) ---
  addEmail(): void {
    this.m.personal_emails = [...this.m.personal_emails, ''];
    this.push();
  }
  setEmail(i: number, v: string): void {
    this.m.personal_emails = this.m.personal_emails.map((e, idx) => (idx === i ? v : e));
    this.push();
  }
  removeEmail(i: number): void {
    this.m.personal_emails = this.m.personal_emails.filter((_, idx) => idx !== i);
    this.push();
  }

  // --- web links ---
  addLink(): void {
    this.m.web_links = [...this.m.web_links, { type: 'LinkedIn', url: '' }];
    this.push();
  }
  removeLink(i: number): void {
    this.m.web_links = this.m.web_links.filter((_, idx) => idx !== i);
    this.push();
  }
}
