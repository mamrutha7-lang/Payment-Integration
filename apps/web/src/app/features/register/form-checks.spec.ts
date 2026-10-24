import {
  CONNECTION_DROPPED_MESSAGE,
  collegeEmailProblem,
  describeValidationError,
  edgeTimeoutMessage,
  newSubmissionKey,
} from './form-checks';

describe('collegeEmailProblem', () => {
  it('passes a college address beside a personal one', () => {
    expect(collegeEmailProblem('1mp25mdm01@bgscet.ac.in', 'asha@gmail.com')).toBeNull();
  });

  it('refuses the same address in both boxes, whatever the case', () => {
    expect(collegeEmailProblem('Asha@Gmail.com ', 'asha@gmail.com')).toContain('are the same');
  });

  it('refuses a public mail address in the college box', () => {
    expect(collegeEmailProblem('asha@gmail.com', 'asha.rao@yahoo.com')).toContain('personal address');
  });

  it('leaves an address on an unlisted college domain to the office', () => {
    expect(collegeEmailProblem('asha@some-college.edu.in', 'asha@gmail.com')).toBeNull();
  });
});

describe('describeValidationError', () => {
  it("names the form's box and drops pydantic's prefix", () => {
    const detail = [
      {
        loc: ['body', 'body', 'linkedin_url'],
        msg: 'Value error, enter your LinkedIn profile link, e.g. linkedin.com/in/your-name',
      },
    ];
    expect(describeValidationError(detail)).toBe(
      'LinkedIn profile: enter your LinkedIn profile link, e.g. linkedin.com/in/your-name',
    );
  });

  it('lists every refused field once', () => {
    const detail = [
      { loc: ['body', 'phone'], msg: 'Field required' },
      { loc: ['body', 'photo'], msg: 'Field required' },
      { loc: ['body', 'phone'], msg: 'Field required' },
    ];
    expect(describeValidationError(detail)).toBe('Phone: Field required Photo: Field required');
  });

  it('is null for a string detail or nothing', () => {
    expect(describeValidationError('A valid email is required.')).toBeNull();
    expect(describeValidationError(undefined)).toBeNull();
    expect(describeValidationError([])).toBeNull();
  });
});

describe('newSubmissionKey', () => {
  it('is 32 lowercase hex characters the server accepts', () => {
    const key = newSubmissionKey();
    expect(key).toMatch(/^[0-9a-f]{32}$/);
  });

  it('differs every time, so two forms never share one', () => {
    expect(newSubmissionKey()).not.toEqual(newSubmissionKey());
  });

  it('uses all sixteen random bytes', () => {
    const fixed = { getRandomValues: <T extends ArrayBufferView | null>(a: T) => {
      (a as unknown as Uint8Array).set([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 255]);
      return a;
    } } as Pick<Crypto, 'getRandomValues'>;
    expect(newSubmissionKey(fixed)).toBe('000102030405060708090a0b0c0d0eff');
  });
});

describe('the connection messages', () => {
  it('never tells a student about the developer port', () => {
    expect(CONNECTION_DROPPED_MESSAGE).not.toContain('3300');
    expect(CONNECTION_DROPPED_MESSAGE).toContain('press Submit again');
  });

  it('answers the three edge statuses and keeps the code in the sentence', () => {
    for (const status of [502, 503, 504]) {
      expect(edgeTimeoutMessage(status)).toContain(`(${status})`);
    }
  });

  it('leaves every other status to the caller', () => {
    for (const status of [400, 403, 409, 413, 422, 429, 500]) {
      expect(edgeTimeoutMessage(status)).toBeNull();
    }
  });
});
