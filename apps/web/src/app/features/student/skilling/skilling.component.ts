/**
 * Student skilling — claim a skill with a certificate, then watch the badge
 * board light up.
 *
 * The screen is two things stacked. The CLAIM CARD takes a certificate and the
 * badge it proves; the BADGE BOARD shows every badge in the catalogue, grouped
 * by category, outlined when available and lit when the student holds it
 * EARNED. That pairing is the whole point: the form says what you can earn and
 * the board says what you have, so the two dropdowns and the board read from the
 * SAME catalogue rather than from two lists that could drift.
 *
 * THE CATALOGUE IS THE 48-BADGE ONE (`GET /student/badges`, app/models/badge.py):
 * Managerial · Sectoral · Platform / Technical · Thinking · Career Readiness —
 * the categories the handoff's board is drawn with. A claim is a piece of
 * evidence against a badge (`POST /student/badges/{code}/evidence`), and only a
 * staff APPROVE on it mints the EARNED row that lights the pill: a certificate
 * is evidence, a badge is the verified recognition. Readiness badges refuse
 * evidence — they arrive on assessment thresholds — so they are on the board
 * but never in the claim dropdowns.
 *
 * THE TWO SELECTS ARE DEPENDENT, and deliberately. Picking a badge out of one
 * flat list of ~48 was the thing students got wrong most often — the categories
 * are how the programme talks about skills, so choosing a category first turns
 * one long list into five short ones. Badge is disabled until a category is
 * chosen and clears when the category changes, so the pair can never submit a
 * badge from a category the student is no longer looking at.
 *
 * The claim form does NOT ask for a level or an evidence type. The student
 * asserts the certificate; a faculty reviewer judges it on review.
 */

import { Component, ErrorHandler, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { failedReadMessage } from '../../../core/feature-refusal';
import { UNREADABLE_FILE_MESSAGE, readPickedFile } from '../../../core/picked-file';

interface EvidenceRow {
  id: string;
  evidence_type: string;
  status: string; // PENDING_VERIFICATION | APPROVED | REJECTED | MORE_INFO_REQUIRED
  title: string;
  provider: string | null;
  review_note: string | null;
  created_at: string;
}

interface Badge {
  code: string;
  name: string;
  category: string;
  category_label: string;
  track_label: string | null;
  staff_awarded: boolean;
  status: string; // NOT_STARTED | IN_PROGRESS | VERIFICATION_PENDING | EARNED
  evidence: EvidenceRow[];
}

interface Category {
  key: string;
  label: string;
  earned: number;
  total: number;
  badges: Badge[];
}

interface Dashboard {
  stage: string;
  points_total: number;
  earned_total: number;
  badge_total: number;
  categories: Category[];
}

interface BoardBadge {
  code: string;
  name: string;
  /** Held EARNED — the pill glows, carries the blue verified tick, and nothing
   *  can dim it. The tick is the reviewer's approval and appears on no other
   *  state: an unclaimed badge is unmarked, a claim in review is marked as
   *  waiting, a tapped preview lights the tile but never wears the tick. */
  acquired: boolean;
  /** A claim is waiting for review: the tile says so rather than reading as
   *  unclaimed, because the student filed it and is waiting on somebody. */
  pending: boolean;
  /** Tapped for a preview of the earned state; purely visual, never stored. */
  previewed: boolean;
  /** The status a hover/tap explains: what this pill is waiting on. */
  hint: string;
}

interface BoardCategory {
  key: string;
  label: string;
  note: string;
  badges: BoardBadge[];
}

interface OpenClaim {
  id: string;
  badgeName: string;
  chipClass: string;
  chipIcon: string;
  chipLabel: string;
  reviewNote: string | null;
}

/** Anything the student still needs to see: in flight, or sent back. */
function openClaimChip(status: string): Omit<OpenClaim, 'id' | 'badgeName' | 'reviewNote'> | null {
  // MORE_INFO_REQUIRED and REJECTED look similar and mean different things: the
  // first is an instruction to claim again with better evidence, the second is
  // a no. The label has to carry that, because it is all the student gets.
  if (status === 'MORE_INFO_REQUIRED')
    return { chipClass: 'warn', chipIcon: 'edit_note', chipLabel: 'Needs changes' };
  if (status === 'REJECTED')
    return { chipClass: 'risk', chipIcon: 'cancel', chipLabel: 'Not verified' };
  if (status === 'PENDING_VERIFICATION')
    return { chipClass: 'warn', chipIcon: 'schedule', chipLabel: 'Waiting for review' };
  return null; // APPROVED needs no row — the badge on the board is already lit.
}

const STATUS_HINT: Record<string, string> = {
  EARNED: 'Verified and acquired',
  VERIFICATION_PENDING: 'Verification pending — waiting for review',
  IN_PROGRESS: 'In progress — not yet verified',
  NOT_STARTED: 'Available — claim it with a certificate',
};

/** The design's per-category caption: "12 badges", "4 readiness badges". */
function categoryNote(cat: Category): string {
  const noun = cat.key === 'READINESS' ? 'readiness badges' : 'badges';
  return cat.earned > 0
    ? `${cat.total} ${noun} · ${cat.earned} earned`
    : `${cat.total} ${noun}`;
}

const MAX_CERT_BYTES = 5 * 1024 * 1024;

/** What the picker's `accept` attribute allows, said again where a DROPPED
 *  file can be checked against it. */
const ACCEPTED_CERT_TYPES = ['application/pdf', 'image/jpeg'];

/**
 * The server's own sentence where there is one, and the STATUS where there is
 * not — never the bare fallback alone.
 *
 * This screen printed "Certificate upload failed (PDF or JPEG, up to 5 MB)."
 * for every refusal whose body was not JSON with a `detail` string, and one
 * refusal was exactly that: the edge WAF answering a multipart POST 403 with
 * an HTML body, before the API ever saw the file (infra/cdk/reep_core/edge.py,
 * 2026-09-17). A student attaching a 2 MB JPEG was told their JPEG was the
 * wrong kind of file, and the one fact that would have pointed at the edge —
 * the status code — was the one thing the message left out. FastAPI's schema
 * refusals arrive as a `detail` LIST, so those are read for their message too
 * rather than rendering "[object Object]" (leave.component.ts's trap).
 */
async function detailOf(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0 && typeof detail[0]?.msg === 'string') {
      return String(detail[0].msg).replace(/^Value error,\s*/, '');
    }
  } catch {
    /* not JSON — fall through to the status */
  }
  return `${fallback} (${response.status})`;
}

/**
 * What the form says when `fetch` THROWS — no HTTP answer arrived at all. It
 * used to say "Could not reach the server.", which sent students to report the
 * server down on a day it accepted 138 certificates (2026-10-14). The request
 * that throws is almost always the multipart upload from a phone, so the
 * sentence says what to do about the connection and that nothing is lost.
 */
const UPLOAD_DROPPED_MESSAGE =
  'The certificate did not upload: the connection dropped. Check your internet ' +
  '(Wi-Fi works best for files) and press Submit claim again. Everything you ' +
  'filled in is still here.';

/** The certificate landed and the small claim request after it did not. */
const CLAIM_DROPPED_MESSAGE =
  'Your certificate was uploaded, but the connection dropped before the claim ' +
  'was filed. Press Submit claim again to finish; the certificate will not be ' +
  'uploaded twice.';

@Component({
  selector: 'app-student-skilling',
  standalone: true,
  // RouterLink: the "All my documents" link to /student/uploads. Without it the
  // `routerLink` attribute is inert markup — it renders, it looks like a link,
  // and clicking it does nothing at all.
  imports: [RouterLink],
  templateUrl: './skilling.component.html',
  styleUrl: './skilling.component.scss',
})
export class SkillingComponent {
  private readonly errorHandler = inject(ErrorHandler);

  /// null = still loading.
  readonly dashboard = signal<Dashboard | null>(null);
  readonly boardError = signal<string | null>(null);

  /// Claim form.
  readonly claimCategory = signal('');
  readonly claimBadge = signal('');
  readonly claimFile = signal<File | null>(null);
  readonly claimIssuer = signal('');
  readonly claimNote = signal('');
  readonly claiming = signal(false);
  readonly claimError = signal<string | null>(null);
  readonly claimSubmitted = signal(false);
  /// true while a picked certificate is being copied into memory.
  readonly readingFile = signal(false);
  /// Bumped on every pick, so a slow read of an earlier file can never land
  /// over a later one.
  private pickSeq = 0;
  /// The upload behind the claim being filed. Kept when the claim request
  /// fails after the upload landed, so pressing Submit again files the claim
  /// against it instead of uploading the same certificate a second time.
  private uploadedId: string | null = null;

  /// Badge codes the student has tapped to preview the earned state.
  private readonly previewed = signal<Set<string>>(new Set());

  /// Categories a student can actually claim into — readiness badges are
  /// staff-awarded, so a category made only of those never appears here.
  readonly categories = computed(() =>
    (this.dashboard()?.categories ?? [])
      .filter((c) => c.badges.some((b) => !b.staff_awarded))
      .map((c) => ({ key: c.key, label: c.label })),
  );

  /// Only the chosen category's claimable badges — empty until one is chosen,
  /// which is what disables and re-labels the second select.
  readonly badgeOptions = computed<Badge[]>(() => {
    const chosen = this.claimCategory();
    if (!chosen) return [];
    const cat = (this.dashboard()?.categories ?? []).find((c) => c.key === chosen);
    return (cat?.badges ?? []).filter((b) => !b.staff_awarded);
  });

  readonly claimFileName = computed(() => this.claimFile()?.name ?? '');
  readonly canSubmitClaim = computed(
    () => !!this.claimFile() && !!this.claimCategory() && !!this.claimBadge() && !this.claiming(),
  );

  /**
   * The glyph drawn inside each category's hexagon, per the 2026-09 board.
   *
   * `settings` is DECLARED in tools/fonts/icon-names.extra.txt rather than
   * discovered: collect-icon-names.py reads templates, and this name only ever
   * reaches the DOM through this map, which the scanner cannot see. A glyph
   * missing from the subset renders as nothing at all — an empty hexagon.
   */
  private readonly CATEGORY_ICON: Record<string, string> = {
    MANAGERIAL: 'groups',
    SECTORAL: 'insights',
    PLATFORM: 'settings',
    THINKING: 'lightbulb',
    READINESS: 'lightbulb',
  };

  categoryIcon(key: string): string {
    return this.CATEGORY_ICON[key] ?? 'workspace_premium';
  }

  readonly board = computed<BoardCategory[] | null>(() => {
    const dash = this.dashboard();
    if (dash === null) return null;
    const previewed = this.previewed();
    return dash.categories.map((cat) => ({
      key: cat.key,
      label: cat.label,
      note: categoryNote(cat),
      badges: cat.badges.map((b) => ({
        code: b.code,
        name: b.name,
        acquired: b.status === 'EARNED',
        pending: b.status === 'VERIFICATION_PENDING',
        previewed: b.status !== 'EARNED' && previewed.has(b.code),
        hint: STATUS_HINT[b.status] ?? b.status,
      })),
    }));
  });

  /// "N skills currently illuminated" — earned pills plus the ones being
  /// previewed, which is exactly what is glowing on screen right now.
  readonly litCount = computed(() =>
    (this.board() ?? []).reduce(
      (n, cat) => n + cat.badges.filter((b) => b.acquired || b.previewed).length,
      0,
    ),
  );

  /**
   * Claims the student still has something to do about. The handoff drops the
   * claims list entirely once a claim is filed, which is right for the happy
   * path and wrong for a rejection — a mentor's "this certificate is for a
   * different badge" would never reach the student. So the panel renders only
   * when there IS something outstanding, and the screen matches the handoff
   * exactly whenever there is not.
   */
  readonly openClaims = computed<OpenClaim[]>(() => {
    const out: OpenClaim[] = [];
    for (const cat of this.dashboard()?.categories ?? []) {
      for (const b of cat.badges) {
        if (b.status === 'EARNED') continue;
        for (const e of b.evidence) {
          const chip = openClaimChip(e.status);
          if (chip) out.push({ id: e.id, badgeName: b.name, reviewNote: e.review_note, ...chip });
        }
      }
    }
    return out;
  });

  constructor() {
    void this.loadBoard();
  }

  onCategoryChange(value: string): void {
    this.claimCategory.set(value);
    // A badge from the previous category must not survive the switch.
    this.claimBadge.set('');
  }

  onFilePicked(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    // Cleared so re-picking the SAME file still fires a change event.
    input.value = '';
    void this.acceptFile(file);
  }

  // --- drag and drop -------------------------------------------------------
  //
  // The dropzone said "Click to upload or drop a file" and implemented only
  // the click. A drop on an element with no dragover handler keeps the
  // BROWSER's default, which is to navigate the top-level window to the
  // dropped file: the SPA unloads and the half-filled claim beside it —
  // category, badge, issuer, the note to the mentor — goes with it, with no
  // error and nothing to go back to. preventDefault on dragover/dragenter is
  // what makes this a drop target at all; without it `drop` never fires here.

  /// true while a file is being dragged over the certificate dropzone.
  readonly certDragOver = signal(false);

  onCertDragOver(event: DragEvent): void {
    event.preventDefault();
    this.certDragOver.set(true);
  }

  onCertDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.certDragOver.set(false);
  }

  onCertDrop(event: DragEvent): void {
    event.preventDefault();
    this.certDragOver.set(false);
    const file = event.dataTransfer?.files?.[0];
    // Only when there IS one: a drag that carried text must not silently
    // discard the certificate the student already chose.
    if (file) void this.acceptFile(file);
  }

  /**
   * The one place a chosen certificate is accepted or refused, whichever way
   * it arrived. The picker and the dropzone share it rather than each keeping
   * their own rules — the size floor is this screen's (5 MB, stricter than the
   * server's 10) and a second copy of it would drift.
   *
   * The type check exists FOR the drop path: `accept` on the input filters the
   * file dialog and has no say over what gets dragged in, so without it a
   * dropped PNG or DOCX is accepted here and refused by the server at Submit,
   * after the whole claim has been filled in. A browser that reports no type
   * at all is not second-guessed — the server sniffs magic bytes and is the
   * authority on what a file really is.
   *
   * What is kept is an in-memory COPY (core/picked-file.ts): a file the phone
   * can no longer read by the time Submit is pressed made the upload throw,
   * and the screen blamed the server for it.
   */
  private async acceptFile(file: File | null): Promise<void> {
    const seq = ++this.pickSeq;
    this.claimError.set(null);
    this.readingFile.set(false);
    this.uploadedId = null;
    if (file && file.size > MAX_CERT_BYTES) {
      this.claimFile.set(null);
      this.claimError.set('That file is over 5 MB. Export a smaller PDF or JPEG and try again.');
      return;
    }
    if (file && file.type && !ACCEPTED_CERT_TYPES.includes(file.type)) {
      this.claimFile.set(null);
      this.claimError.set(
        'That file is not a PDF or a JPEG. Attach the certificate as one of those.',
      );
      return;
    }
    if (!file) {
      this.claimFile.set(null);
      return;
    }
    this.claimFile.set(null);
    this.readingFile.set(true);
    const copy = await readPickedFile(file);
    // A later pick owns the form now.
    if (seq !== this.pickSeq) return;
    this.readingFile.set(false);
    if (!copy) {
      this.claimError.set(UNREADABLE_FILE_MESSAGE);
      // Reported because no server can see it: nothing was sent. A fixed
      // sentence and nothing from the form, not even the file name.
      this.errorHandler.handleError(
        new Error('Skill claim: the picked certificate could not be read on the device'),
      );
      return;
    }
    this.claimFile.set(copy);
  }

  /** Tap a pill to preview the earned state. An EARNED pill is already lit and
   *  does not toggle — nothing on this board can dim a verified badge. */
  togglePreview(badge: BoardBadge): void {
    if (badge.acquired) return;
    this.previewed.update((set) => {
      const next = new Set(set);
      if (next.has(badge.code)) next.delete(badge.code);
      else next.add(badge.code);
      return next;
    });
  }

  claimAnother(): void {
    this.claimSubmitted.set(false);
    this.claimCategory.set('');
    this.claimBadge.set('');
    this.claimFile.set(null);
    this.claimIssuer.set('');
    this.claimNote.set('');
    this.claimError.set(null);
    this.pickSeq++;
    this.readingFile.set(false);
    this.uploadedId = null;
  }

  /// Store the certificate, then file the evidence against the badge. Two
  /// calls, in that order: the claim needs an upload id, so a failed upload must
  /// not leave a claim pointing at nothing.
  async submitClaim(): Promise<void> {
    const file = this.claimFile();
    const code = this.claimBadge();
    if (!file || !code) return;
    const badge = this.badgeOptions().find((b) => b.code === code);
    this.claiming.set(true);
    this.claimError.set(null);
    const issuer = this.claimIssuer().trim();
    let uploadId = this.uploadedId;
    try {
      if (!uploadId) {
        const form = new FormData();
        form.append('file', file);
        form.append('kind', 'CERTIFICATE_PROOF');
        form.append(
          'title',
          issuer ? `${badge?.name ?? file.name} — ${issuer}` : (badge?.name ?? file.name),
        );
        const up = await fetch(`${environment.apiBase}/student/uploads`, {
          method: 'POST',
          credentials: 'include',
          body: form,
        });
        if (!up.ok) {
          this.claimError.set(
            await detailOf(up, 'The certificate could not be uploaded. Try again in a minute'),
          );
          return;
        }
        uploadId = (await up.json()).id as string;
        this.uploadedId = uploadId;
      }
      const note = this.claimNote().trim();
      const claim = await fetch(`${environment.apiBase}/student/badges/${code}/evidence`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          evidence_type: 'EXTERNAL_VERIFIED',
          upload_id: uploadId,
          title: badge?.name ?? file.name,
          // The issuer belongs on the evidence as its provider — it describes the
          // certificate, which is what a reviewer opens.
          provider: issuer || null,
          note: note || null,
        }),
      });
      if (!claim.ok) {
        this.claimError.set(await detailOf(claim, 'Could not file the claim. Please try again'));
        return;
      }
      // The response is the refreshed dashboard, so the board and the open
      // claims update from the same document the write returned.
      this.dashboard.set((await claim.json()) as Dashboard);
      this.uploadedId = null;
      this.claimSubmitted.set(true);
    } catch {
      const uploaded = this.uploadedId !== null;
      this.claimError.set(uploaded ? CLAIM_DROPPED_MESSAGE : UPLOAD_DROPPED_MESSAGE);
      // Reported, the registration form's rule: a request that never got an
      // HTTP answer leaves no trace on any server. A fixed sentence and
      // nothing from the form, so the report carries the fact and the
      // browser's own context, never the student.
      this.errorHandler.handleError(
        new Error(
          uploaded
            ? 'Skill claim failed after the upload: no HTTP response (network error on the device)'
            : 'Skill claim upload failed: no HTTP response (network error on the device)',
        ),
      );
    } finally {
      this.claiming.set(false);
    }
  }

  private async loadBoard(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/student/badges`, { credentials: 'include' });
      if (!res.ok) {
        this.boardError.set(await failedReadMessage(res, 'Could not load the badge catalogue.'));
        return;
      }
      this.dashboard.set((await res.json()) as Dashboard);
    } catch {
      this.boardError.set('Could not reach the server.');
    }
  }
}
