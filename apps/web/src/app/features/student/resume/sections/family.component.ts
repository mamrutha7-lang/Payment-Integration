/**
 * Resume Builder — "Family Details" section (stepper key `family`).
 *
 * Owns `data.family` = { father{…}, mother{…}, guardians[…] }, where each
 * parent is { name, occupation, organisation, designation, email, phone } and a
 * guardian is a repeatable next-of-kin record. Organisation and designation are
 * free-text inputs (the mockup's single-option <select> stubs carry no option
 * list, so a bound text field is the faithful, usable equivalent).
 *
 * Local model seeded from svc.section('family', …), re-hydrated once svc.load()
 * resolves, and flushed with svc.patch('family', …) on every edit.
 */

import { Component, effect, inject, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ResumeBuilderService } from '../resume-builder.service';

interface Person {
  name: string;
  occupation: string;
  organisation: string;
  designation: string;
  email: string;
  phone: string;
}
interface Guardian {
  name: string;
  relationship: string;
  phone: string;
}
interface FamilyData {
  father: Person;
  mother: Person;
  guardians: Guardian[];
}

@Component({
  selector: 'rb-family',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './family.component.html',
})
export class RbFamilyComponent {
  private readonly svc = inject(ResumeBuilderService);

  m: FamilyData = this.normalize(this.svc.section('family', {}));

  private hydrated = false;

  constructor() {
    effect(() => {
      if (this.svc.loaded() && !this.hydrated) {
        this.hydrated = true;
        this.m = untracked(() => this.normalize(this.svc.section('family', {})));
      }
    });
  }

  private person(raw: Partial<Person> | undefined): Person {
    return {
      name: raw?.name ?? '',
      occupation: raw?.occupation ?? '',
      organisation: raw?.organisation ?? '',
      designation: raw?.designation ?? '',
      email: raw?.email ?? '',
      phone: raw?.phone ?? '',
    };
  }

  private normalize(raw: Partial<FamilyData>): FamilyData {
    return {
      father: this.person(raw.father),
      mother: this.person(raw.mother),
      guardians: Array.isArray(raw.guardians)
        ? raw.guardians.map((g) => ({
            name: g?.name ?? '',
            relationship: g?.relationship ?? '',
            phone: g?.phone ?? '',
          }))
        : [],
    };
  }

  push(): void {
    this.svc.patch('family', JSON.parse(JSON.stringify(this.m)));
  }

  addGuardian(): void {
    this.m.guardians = [...this.m.guardians, { name: '', relationship: '', phone: '' }];
    this.push();
  }

  removeGuardian(i: number): void {
    this.m.guardians = this.m.guardians.filter((_, idx) => idx !== i);
    this.push();
  }
}
