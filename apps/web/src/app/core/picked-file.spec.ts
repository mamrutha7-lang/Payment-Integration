import { readPickedFile } from './picked-file';

/** Plain numbers: the test DOM and Node each have their own Uint8Array. */
async function bytesOf(file: File): Promise<number[]> {
  return Array.from(new Uint8Array(await file.arrayBuffer()));
}

/**
 * A picked file is read once, at pick time, and the upload sends that copy.
 * The case that matters is the second test: a file the phone can no longer
 * read by the time Submit is pressed must still upload.
 */
describe('a picked file', () => {
  const pdf = new TextEncoder().encode('%PDF-1.7 certificate');
  const pdfBytes = Array.from(pdf);

  it('is copied with its bytes, name and type', async () => {
    const original = new File([pdf], 'nism.pdf', { type: 'application/pdf', lastModified: 7 });
    const copy = await readPickedFile(original);
    expect(copy).not.toBeNull();
    expect(copy!.name).toBe('nism.pdf');
    expect(copy!.type).toBe('application/pdf');
    expect(copy!.lastModified).toBe(7);
    expect(await bytesOf(copy!)).toEqual(pdfBytes);
  });

  it('still uploads after the original stops being readable', async () => {
    let reads = 0;
    const cloudFile = {
      name: 'drive-cert.pdf',
      type: 'application/pdf',
      lastModified: 1,
      arrayBuffer: async () => {
        reads += 1;
        if (reads > 1) throw new DOMException('changed', 'NotReadableError');
        return pdf.buffer.slice(0);
      },
    } as unknown as File;

    const copy = await readPickedFile(cloudFile);
    await expect(cloudFile.arrayBuffer()).rejects.toThrow();
    expect(await bytesOf(copy!)).toEqual(pdfBytes);
  });

  it('is refused on the spot when it cannot be read at all', async () => {
    const unreadable = {
      name: 'cert.jpg',
      type: 'image/jpeg',
      lastModified: 1,
      arrayBuffer: () => Promise.reject(new DOMException('gone', 'NotReadableError')),
    } as unknown as File;
    expect(await readPickedFile(unreadable)).toBeNull();
  });

  it('is refused when it has no content', async () => {
    expect(await readPickedFile(new File([], 'empty.pdf', { type: 'application/pdf' }))).toBeNull();
  });
});
