/**
 * Faculty Leave — the official BGSCET form, and the requests made on it.
 *
 * A dashboard of the faculty member's own requests, and behind each one the
 * one-page document itself: the institution heading, the strike-off line naming
 * which of Casual Leave / Permission / OOD / RH / LOP is being applied for, the
 * main table (Name, Designation, Department, Date, Purpose, Credit,
 * Sanctioned), the two signature blocks, and the Alternate Arrangements section
 * with its five-column table and its own staff signature.
 *
 * THE FORM IS THE SOURCE OF TRUTH FOR WHAT IT ASKS. Fields the printed sheet
 * does not have are not added to it — no employee id, no phone number, no leave
 * balance cell. A leave form that collects more than the college's own form is
 * a different document with the college's letterhead on it. (The allowance is
 * READ on the dashboard, beside the list of requests. That is not a field on
 * the sheet and nothing is collected by it; it is there because REEP can now
 * REFUSE a form for want of days, and a refusal whose number the applicant
 * cannot see anywhere is a refusal they cannot act on.)
 *
 * THREE THINGS ARRIVED BESIDE THE FORM IN PHASE 4, AND NONE OF THEM IS ON IT.
 * The sheet's fields, its Sign button, `signAndSubmit`'s payload and the
 * submit endpoint are byte for byte what they were — the owner's instruction,
 * restated in five places in this repository. What is new sits AROUND the
 * document, where a covering note and a filing clerk would be:
 *
 *   - ATTACHED PAPERS (B10.3). A supporting document is not a field on the
 *     form; it is the medical certificate that travels WITH it. It is offered
 *     only on a request that already exists, so nothing about signing changed,
 *     and `document_store` decides the types and the sizes — this screen
 *     restates neither and prints the server's own refusal.
 *   - WITHDRAW (B10.4). `POST /leaves/{id}/cancel`, the applicant's own, and
 *     only while the request is still awaiting a signature. Two-tapped, like
 *     the signature screen's Remove, because a request cannot be un-withdrawn.
 *   - ASKED TO COVER (B10.6). The other side of the Alternate Arrangements
 *     table: which colleagues have named THIS account, from
 *     `/leaves/alternate/mine`. It answers the reduced projection — the dates,
 *     the printed option, the state and the one row addressed to you — and
 *     carries no `reason`, because a colleague asked to take a Tuesday class
 *     is not thereby entitled to somebody's diagnosis.
 *
 * THE OTHER HALF OF B10.6 HAS NO CONTROL HERE, AND THAT IS NOT AN OVERSIGHT.
 * A row is linked to an account by `POST /leaves/{id}/alternate/assign`, which
 * is the applicant's own act — and to offer it this screen would have to list
 * the applicant's colleagues. Every faculty listing in the API is
 * `require_admin` (`admin_faculty.list_faculty`, `admin_mentoring.mentor_load`),
 * so a MENTOR applicant can reach none of them, and a staff directory a faculty
 * account may read is a scope decision nobody has made — who, exactly: their
 * department, their college, the deployment? Rather than invent one, or make
 * the applicant type a user id, the control is absent and this is the note
 * saying so. Until it exists the list below is empty for everybody, which is
 * why it is drawn only when it has rows: an empty "Colleagues who named you"
 * heading would be this screen reporting a feature it cannot offer.
 *
 * APPROVALS ARE NOT HERE. This screen is a faculty member applying for
 * themselves; the queue that decides these lives in the admin area, because the
 * PROGRAM DIRECTOR block on the form is signed by the programme director and
 * not by a peer. The server enforces that independently — staff leave has no
 * student group, so `/leaves/pending` narrows it to DIRECTOR/ADMIN.
 *
 * Name, Designation and Department are printed from the user record and are not
 * editable: they are what the institution holds, and a form whose identity
 * fields can be typed over is not evidence of anything.
 *
 * DRAFTS LIVE IN THIS BROWSER, AND SAY SO. `leave_requests` has no DRAFT status
 * — a row exists once it is signed — so "Save draft" keeps the unsigned form in
 * localStorage, one per device, labelled "saved on this device" wherever it is
 * shown. Nothing about it reaches the server until Sign & submit. "Edit &
 * resubmit" on a rejected request copies that form into a new draft; the
 * rejected row itself stays exactly as the director decided it.
 */

import { DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Component, ErrorHandler, computed, inject, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';
import {
  UNREADABLE_FILE_MESSAGE,
  UPLOAD_DROPPED_MESSAGE,
  readPickedFile,
} from '../../../core/picked-file';

interface AltRow {
  date: string;
  staff_name: string;
  cls: string;
  time: string;
  remarks: string;
  /** B10.6 added these two to the STORED row, and they are read-only here.
   *  They are optional because every row written before B10.6 — and every row
   *  whose typed name was never linked to an account — has neither, and because
   *  `submit_leave` stores whatever this form sends: an `accepted_at` the
   *  applicant could post would be a colleague's agreement forged on their
   *  behalf. This form never sends them (`signAndSubmit` builds its own five
   *  fields), and the server ignores them if it ever did. */
  user_id?: string | null;
  accepted_at?: string | null;
}

interface LeaveRow {
  id: string;
  from_date: string;
  to_date: string;
  reason: string;
  status: string;
  leave_kind: string | null;
  credit: string | null;
  alt_name: string | null;
  alt_rows: AltRow[];
  requester_name: string;
  requester_designation: string | null;
  requester_department: string | null;
  signed_at: string | null;
  director_name: string | null;
  director_decided_at: string | null;
  director_note: string | null;
  /** A decision changed after it was made (2026-10-13): the LATEST change,
   *  REVOKED or REOPENED, and the reason the approver had to write to you. */
  decision_change_kind?: 'REVOKED' | 'REOPENED' | null;
  decision_changed_at?: string | null;
  decision_change_reason?: string | null;
}

/** One allowance, as `GET /api/leaves/balances` answers it. `remaining_days`
 *  may be NEGATIVE — leave past an allowance happens and the office signs it —
 *  so it is printed as it arrives and never clamped. */
interface BalanceRow {
  kind: string;
  entitled_days: number;
  consumed_days: number;
  remaining_days: number;
}

interface BalanceSet {
  academic_year: string;
  balances: BalanceRow[];
}

/** One paper attached to a request (B10.3). `can_delete` is the SERVER's
 *  answer — the uploader's own, or the Main Admin's — because a client that
 *  re-derives that rule draws a bin that 403s. */
interface LeaveAttachment {
  id: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  uploaded_at: string;
  uploaded_by_name: string | null;
  can_delete: boolean;
}

/** The reduced projection (`LeaveBrief` in app/routers/leave.py): what somebody
 *  who is neither the applicant nor an approver may see. IT HAS NO `reason`
 *  FIELD, and that absence is the fence — not the call site. */
interface LeaveBrief {
  id: string;
  from_date: string;
  to_date: string;
  leave_kind: string | null;
  status: string;
  requester_name: string;
  alt_row: AltRow | null;
}

/** What "Save draft" keeps, in this browser only. */
interface LocalDraft {
  kind: string;
  from: string;
  to: string;
  purpose: string;
  credit: string;
  altName: string;
  altRows: AltRow[];
  savedAt: string;
}

const DRAFT_KEY = 'reep.mentor.leave.draft';

/** The five printed options, in the order the sheet lists them. */
export const LEAVE_KINDS = [
  { id: 'CASUAL', label: 'Casual Leave' },
  { id: 'PERMISSION', label: 'Permission' },
  { id: 'OOD', label: 'OOD' },
  { id: 'RH', label: 'RH' },
  { id: 'LOP', label: 'LOP' },
] as const;

interface Chip {
  tone: 'good' | 'warn' | 'risk' | 'neutral';
  icon: string;
  label: string;
}

/** Status -> chip. Text and colour together, never colour alone. */
function statusChip(status: string): Chip {
  switch (status) {
    case 'APPROVED':
      return { tone: 'good', icon: 'check_circle', label: 'Approved' };
    case 'REJECTED':
      return { tone: 'risk', icon: 'cancel', label: 'Rejected' };
    case 'CANCELLED':
      return { tone: 'neutral', icon: 'event_busy', label: 'Cancelled' };
    case 'REVOKED':
      return { tone: 'risk', icon: 'undo', label: 'Sanction revoked' };
    case 'FIRST_APPROVED':
      return { tone: 'warn', icon: 'how_to_reg', label: 'Signed once · awaiting the Main Admin' };
    default:
      return { tone: 'warn', icon: 'hourglass_top', label: 'Awaiting the Main Admin' };
  }
}

function emptyAltRow(): AltRow {
  return { date: '', staff_name: '', cls: '', time: '', remarks: '' };
}

function readDraft(): LocalDraft | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw) as LocalDraft;
    return d && typeof d === 'object' && Array.isArray(d.altRows) ? d : null;
  } catch {
    return null;
  }
}

@Component({
  selector: 'app-mentor-leave',
  standalone: true,
  imports: [DatePipe, RouterLink],
  templateUrl: './leave.component.html',
  styleUrl: './leave.component.scss',
})
export class LeaveComponent {
  private readonly auth = inject(AuthService);
  private readonly errorHandler = inject(ErrorHandler);
  readonly kinds = LEAVE_KINDS;
  readonly today = new Date();

  /** GET /api/leaves/{id}/paper.pdf - this request as the printed paper, signatures drawn in. */
  paperUrl(r: LeaveRow): string {
    return `${environment.apiBase}/leaves/${r.id}/paper.pdf`;
  }

  readonly rows = signal<LeaveRow[] | null>(null);
  readonly error = signal<string | null>(null);

  /** `GET /api/staff/signature` — whether a signature IMAGE is on file
   *  (2026-09-16). Signing the form records a name and a time; the picture
   *  drawn above them on the paper is the one uploaded under Signature in
   *  the account menu, and until now nothing on this screen said whether
   *  there was one. `null` means the question could not be asked. */
  readonly signatureOnFile = signal<boolean | null>(null);

  /// null = the dashboard; a row = reading that form; composing = filling one in.
  readonly viewing = signal<LeaveRow | null>(null);
  readonly composing = signal(false);
  readonly submitting = signal(false);
  readonly formError = signal<string | null>(null);
  /// The draft kept on this device, if any.
  readonly draft = signal<LocalDraft | null>(readDraft());
  readonly draftFlash = signal(false);

  /// Draft state for a new form.
  readonly fKind = signal<string>('CASUAL');
  readonly fFrom = signal('');
  readonly fTo = signal('');
  readonly fPurpose = signal('');
  readonly fCredit = signal('');
  readonly fAltName = signal('');
  readonly fAltRows = signal<AltRow[]>([emptyAltRow(), emptyAltRow(), emptyAltRow()]);

  /// Identity for the form being composed: the signed-in name, and the
  /// designation / department the last request printed (the user record is
  /// the source; a first-ever form shows "Not on record" until one exists).
  readonly identity = computed(() => {
    const any = (this.rows() ?? [])[0];
    return {
      name: any?.requester_name || this.auth.session()?.name || '',
      designation: any?.requester_designation ?? null,
      department: any?.requester_department ?? null,
    };
  });

  /// B10.3 — the papers on the request being read, and nothing at all while
  /// composing: there is no request to attach one to until it is signed.
  readonly attachments = signal<LeaveAttachment[] | null>(null);
  readonly attachError = signal<string | null>(null);
  readonly attachBusy = signal(false);

  /// B10.4 — withdrawing is two taps, because it cannot be undone.
  readonly confirmWithdraw = signal(false);
  readonly withdrawing = signal(false);
  readonly withdrawError = signal<string | null>(null);

  /// B10.2 — the caller's OWN allowances for the current academic year, which
  /// is what `submit_refusal` measures a new request against. An EMPTY list is
  /// not a zero balance: it means the office has recorded no allowance, and
  /// then nothing is checked at all. The card says which.
  readonly balances = signal<BalanceSet | null>(null);

  /// B10.6 — requests that name THIS account in their alternate table.
  readonly cover = signal<LeaveBrief[] | null>(null);
  readonly coverError = signal<string | null>(null);
  readonly accepting = signal<string | null>(null);

  readonly canSubmit = computed(
    () => !!this.fFrom() && !!this.fTo() && !!this.fPurpose().trim() && !this.submitting(),
  );

  /// The colleagues still waiting on an answer, which is what the dashboard
  /// heading counts. An accepted row stays on the list — "you agreed to cover
  /// this" is worth reading right up to the day.
  readonly coverAwaiting = computed(
    () => (this.cover() ?? []).filter((brief) => this.coverIsOpen(brief)).length,
  );

  constructor() {
    void this.load();
    void this.loadCover();
    void this.loadBalances();
    void this.loadSignatureState();
  }

  chip(status: string): Chip {
    return statusChip(status);
  }

  kindLabel(id: string | null): string {
    return LEAVE_KINDS.find((k) => k.id === id)?.label ?? '—';
  }

  /** The form's "Sanctioned" cell: only a final decision fills it. */
  sanctioned(status: string): string {
    if (status === 'APPROVED') return 'Sanctioned';
    if (status === 'REJECTED') return 'Not sanctioned';
    if (status === 'CANCELLED') return 'Cancelled';
    if (status === 'REVOKED') return 'Sanction revoked';
    return 'Pending';
  }

  /** The heading over the approver's reason for changing a decision. */
  changeLabel(row: LeaveRow): string {
    return row.decision_change_kind === 'REVOKED'
      ? 'Why the sanction was revoked'
      : 'Reopened for a fresh decision';
  }

  remarksLabel(status: string): string {
    return status === 'REJECTED'
      ? 'Rejected — Admin / Program Director remarks'
      : 'Admin / Program Director remarks';
  }

  /** The form's "Date" cell: one day prints as one date, a span as a range. */
  dateSpan(row: { from_date: string; to_date: string }): string {
    return row.from_date === row.to_date ? row.from_date : `${row.from_date} — ${row.to_date}`;
  }

  startNew(): void {
    this.resetDraft();
    this.composing.set(true);
    this.viewing.set(null);
    this.formError.set(null);
  }

  /** Reopen the draft kept on this device. */
  openDraft(): void {
    const d = this.draft();
    if (!d) return;
    this.fKind.set(d.kind || 'CASUAL');
    this.fFrom.set(d.from);
    this.fTo.set(d.to);
    this.fPurpose.set(d.purpose);
    this.fCredit.set(d.credit);
    this.fAltName.set(d.altName);
    this.fAltRows.set(d.altRows.length ? d.altRows : [emptyAltRow(), emptyAltRow(), emptyAltRow()]);
    this.composing.set(true);
    this.viewing.set(null);
    this.formError.set(null);
  }

  /** A rejected form, copied into a new one to correct and sign again. */
  editAndResubmit(row: LeaveRow): void {
    this.fKind.set(row.leave_kind ?? 'CASUAL');
    this.fFrom.set(row.from_date);
    this.fTo.set(row.to_date);
    this.fPurpose.set(row.reason);
    this.fCredit.set(row.credit ?? '');
    this.fAltName.set(row.alt_name ?? '');
    const rows = row.alt_rows.map((r) => ({ ...r }));
    while (rows.length < 3) rows.push(emptyAltRow());
    this.fAltRows.set(rows);
    this.composing.set(true);
    this.viewing.set(null);
    this.formError.set(null);
  }

  backToDash(): void {
    this.composing.set(false);
    this.viewing.set(null);
    this.formError.set(null);
  }

  open(row: LeaveRow): void {
    this.viewing.set(row);
    this.composing.set(false);
    this.confirmWithdraw.set(false);
    this.withdrawError.set(null);
    this.attachError.set(null);
    this.attachments.set(null);
    void this.loadAttachments(row.id);
  }

  setAltCell(i: number, key: keyof AltRow, value: string): void {
    this.fAltRows.update((rows) => rows.map((r, idx) => (idx === i ? { ...r, [key]: value } : r)));
  }

  addAltRow(): void {
    this.fAltRows.update((rows) => [...rows, emptyAltRow()]);
  }

  /** Keep the unsigned form on this device. Nothing reaches the server. */
  saveDraft(): void {
    const d: LocalDraft = {
      kind: this.fKind(),
      from: this.fFrom(),
      to: this.fTo(),
      purpose: this.fPurpose(),
      credit: this.fCredit(),
      altName: this.fAltName(),
      altRows: this.fAltRows(),
      savedAt: new Date().toISOString(),
    };
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
      this.draft.set(d);
      this.draftFlash.set(true);
      setTimeout(() => this.draftFlash.set(false), 2500);
      this.backToDash();
    } catch {
      this.formError.set('This browser would not keep the draft. Sign and submit, or copy the text.');
    }
  }

  discardDraft(): void {
    if (!window.confirm('Discard the draft saved on this device?')) return;
    try {
      localStorage.removeItem(DRAFT_KEY);
    } catch {
      // Nothing to do: the draft was never readable either.
    }
    this.draft.set(null);
    this.resetDraft();
  }

  /**
   * Sign and send. Submitting IS signing on this form — the staff signature
   * block is what sends it to the programme director — so there is no separate
   * "sign" step that could be skipped, and the server stamps `signed_at` in the
   * same write that sets the status.
   */
  async signAndSubmit(): Promise<void> {
    if (!this.canSubmit()) return;
    this.submitting.set(true);
    this.formError.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/leaves`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from_date: this.fFrom(),
          to_date: this.fTo(),
          reason: this.fPurpose().trim(),
          leave_kind: this.fKind(),
          credit: this.fCredit().trim() || null,
          alt_name: this.fAltName().trim() || null,
          // Blank rows are printed padding, not data.
          alt_rows: this.fAltRows().filter((r) =>
            [r.date, r.staff_name, r.cls, r.time, r.remarks].some((v) => v.trim()),
          ),
        }),
      });
      if (!res.ok) {
        this.formError.set(await detailOf(res));
        return;
      }
      const created = (await res.json()) as LeaveRow;
      this.rows.update((list) => [created, ...(list ?? [])]);
      // A signed form supersedes whatever draft was waiting on this device.
      try {
        localStorage.removeItem(DRAFT_KEY);
      } catch {
        // The draft card would then reappear; harmless, and rare.
      }
      this.draft.set(null);
      this.composing.set(false);
      this.viewing.set(created);
      this.resetDraft();
    } catch {
      this.formError.set('Could not reach the server.');
    } finally {
      this.submitting.set(false);
    }
  }

  // ------------------------------------------------ B10.3 · attached papers --

  /** GET /api/leaves/{id}/attachments/{aid}/file. Served `Content-Disposition:
   *  attachment` whatever this link asks for. */
  attachmentUrl(row: LeaveRow, paper: LeaveAttachment): string {
    return `${environment.apiBase}/leaves/${row.id}/attachments/${paper.id}/file`;
  }

  fileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  /** The papers on one request.
   *
   *  A FAILED READ LEAVES THE SIGNAL AT `null`, never at `[]`: the template
   *  branches on it, and `[]` draws "Nothing attached" — which would tell the
   *  applicant their certificate is gone when all that happened is that the
   *  request did not arrive. And the answer is dropped if the reader has opened
   *  a different request since, because a list of papers under the wrong form
   *  is worse than none. */
  private async loadAttachments(id: string): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/leaves/${id}/attachments`, {
        credentials: 'include',
      });
      if (this.viewing()?.id !== id) return;
      if (!res.ok) return;
      this.attachments.set((await res.json()) as LeaveAttachment[]);
    } catch {
      /* left as null: not answered is not the same as nothing attached */
    }
  }

  /**
   * Attach one document to the request being read.
   *
   * THE LIMITS ARE NOT RESTATED HERE. Which types are accepted and how large a
   * file may be are `app/document_store.py`'s, decided from the file's own
   * magic bytes; the count and the byte allowance are the router's. A copy of
   * any of those numbers in this screen is a copy that stops tracking them, so
   * a refusal is printed in the server's own words.
   */
  async onAttach(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    // The input is cleared whatever happens, or picking the same file twice in
    // a row fires no change event and the button reads as broken.
    input.value = '';
    const row = this.viewing();
    if (!file || row === null) return;
    this.attachBusy.set(true);
    this.attachError.set(null);
    try {
      // Upload an in-memory copy (core/picked-file.ts): on Android a file held
      // by Drive, Photos or another app can fail to be read at send time.
      const copy = await readPickedFile(file);
      if (!copy) {
        this.attachError.set(UNREADABLE_FILE_MESSAGE);
        this.errorHandler.handleError(
          new Error('Leave attachment: the picked file could not be read on the device'),
        );
        return;
      }
      const body = new FormData();
      body.append('file', copy);
      const res = await fetch(`${environment.apiBase}/leaves/${row.id}/attachments`, {
        method: 'POST',
        credentials: 'include',
        // NO Content-Type header: the browser sets it with the multipart
        // boundary, and setting it by hand produces a body FastAPI cannot parse.
        body,
      });
      if (!res.ok) {
        this.attachError.set(await detailOf(res));
        return;
      }
      await this.loadAttachments(row.id);
    } catch {
      this.attachError.set(UPLOAD_DROPPED_MESSAGE);
      // Reported because no server saw it. A fixed sentence, nothing from the request.
      this.errorHandler.handleError(
        new Error('Leave attachment failed: no HTTP response (network error on the device)'),
      );
    } finally {
      this.attachBusy.set(false);
    }
  }

  async removeAttachment(paper: LeaveAttachment): Promise<void> {
    const row = this.viewing();
    if (row === null) return;
    if (!window.confirm(`Remove ${paper.original_name} from this request?`)) return;
    this.attachBusy.set(true);
    this.attachError.set(null);
    try {
      const res = await fetch(
        `${environment.apiBase}/leaves/${row.id}/attachments/${paper.id}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!res.ok) {
        this.attachError.set(await detailOf(res));
        return;
      }
      await this.loadAttachments(row.id);
    } catch {
      this.attachError.set('Could not reach the server.');
    } finally {
      this.attachBusy.set(false);
    }
  }

  // ----------------------------------------------------- B10.4 · withdraw --

  /** Only while it is still awaiting a signature. A decided request is not
   *  withdrawn, it is decided, and a cancelled one is already gone. */
  canWithdraw(row: LeaveRow): boolean {
    return row.status === 'SUBMITTED' || row.status === 'FIRST_APPROVED';
  }

  async withdraw(): Promise<void> {
    const row = this.viewing();
    if (row === null || !this.canWithdraw(row)) return;
    this.withdrawing.set(true);
    this.withdrawError.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/leaves/${row.id}/cancel`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!res.ok) {
        this.withdrawError.set(await detailOf(res));
        return;
      }
      const updated = (await res.json()) as LeaveRow;
      this.viewing.set(updated);
      this.rows.update((list) => (list ?? []).map((r) => (r.id === updated.id ? updated : r)));
      this.confirmWithdraw.set(false);
    } catch {
      this.withdrawError.set('Could not reach the server.');
    } finally {
      this.withdrawing.set(false);
    }
  }

  // ------------------------------------------------- B10.2 · allowances --

  private async loadBalances(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/leaves/balances`, {
        credentials: 'include',
      });
      if (!res.ok) return;
      this.balances.set((await res.json()) as BalanceSet);
    } catch {
      /* left as null: not answered is not "no allowance", and the card that
         draws those two differently must not be shown for the wrong one. */
    }
  }

  // --------------------------------------------- B10.6 · asked to cover --

  private async loadCover(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/leaves/alternate/mine`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.cover.set([]);
        return;
      }
      this.cover.set((await res.json()) as LeaveBrief[]);
    } catch {
      this.cover.set([]);
    }
  }

  /** Is there still anything to agree to? A withdrawn or decided request is on
   *  the list because "you no longer need to cover this" is what a colleague
   *  most needs to know — but there is nothing left to accept. */
  coverIsOpen(brief: LeaveBrief): boolean {
    return brief.status === 'SUBMITTED' || brief.status === 'FIRST_APPROVED';
  }

  /** What the colleague is being told about this request, in one phrase. */
  coverState(brief: LeaveBrief): string {
    switch (brief.status) {
      case 'APPROVED':
        return 'Sanctioned — the cover is needed.';
      case 'REJECTED':
        return 'Not sanctioned — no cover is needed.';
      case 'CANCELLED':
        return 'Withdrawn — no cover is needed.';
      case 'REVOKED':
        return 'Sanction revoked — no cover is needed.';
      case 'FIRST_APPROVED':
        return 'Signed once, awaiting the Main Admin.';
      default:
        return 'Awaiting the Main Admin’s decision.';
    }
  }

  hasAccepted(brief: LeaveBrief): boolean {
    return !!brief.alt_row?.accepted_at;
  }

  async acceptCover(brief: LeaveBrief): Promise<void> {
    this.accepting.set(brief.id);
    this.coverError.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/leaves/${brief.id}/alternate/accept`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!res.ok) {
        this.coverError.set(await detailOf(res));
        return;
      }
      const updated = (await res.json()) as LeaveBrief;
      this.cover.update((list) => (list ?? []).map((b) => (b.id === updated.id ? updated : b)));
    } catch {
      this.coverError.set('Could not reach the server.');
    } finally {
      this.accepting.set(null);
    }
  }

  private resetDraft(): void {
    this.fKind.set('CASUAL');
    this.fFrom.set('');
    this.fTo.set('');
    this.fPurpose.set('');
    this.fCredit.set('');
    this.fAltName.set('');
    this.fAltRows.set([emptyAltRow(), emptyAltRow(), emptyAltRow()]);
  }

  private async loadSignatureState(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/staff/signature`, { credentials: 'include' });
      if (!res.ok) return;
      const info = (await res.json()) as { present?: boolean };
      this.signatureOnFile.set(info.present === true);
    } catch {
      /* the line stays absent rather than guessing */
    }
  }

  private async load(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/leaves/mine`, { credentials: 'include' });
      if (!res.ok) {
        this.error.set('Could not load your leave requests.');
        this.rows.set([]);
        return;
      }
      this.rows.set((await res.json()) as LeaveRow[]);
    } catch {
      this.error.set('Could not reach the server.');
      this.rows.set([]);
    }
  }
}

/** The server's own sentence, whichever shape it arrives in.
 *
 * A FastAPI schema validator answers 422 with `detail` as a LIST of error
 * objects, not a string — so `d?.detail` rendered as "[object Object]" the
 * moment `LeaveIn` grew a date-ordering check. The refusals worth reading are
 * exactly the ones that name specifics ("cannot fall before the first day.
 * You asked for 2026-12-20 to 2026-12-10"), and a generic message throws that
 * away. Same helper as governance.component.ts's `detailOf`.
 */
async function detailOf(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length && typeof detail[0]?.msg === 'string') {
      // Pydantic prefixes its own errors with "Value error, "; the sentence
      // after it is the one written for a person.
      return String(detail[0].msg).replace(/^Value error,\s*/, '');
    }
  } catch {
    /* fall through to the status */
  }
  return `Could not submit the form (${res.status}).`;
}
