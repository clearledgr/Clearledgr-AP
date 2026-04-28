import { describe, it, expect } from 'vitest';
import { decideFaviconHref, formatBadgeCount } from './faviconBadge.js';

describe('formatBadgeCount', () => {
  it('returns empty string for zero or negative', () => {
    expect(formatBadgeCount(0)).toBe('');
    expect(formatBadgeCount(-3)).toBe('');
  });

  it('returns the number as a string for 1..9', () => {
    expect(formatBadgeCount(1)).toBe('1');
    expect(formatBadgeCount(9)).toBe('9');
  });

  it('caps at "9+" for any count >= 10 — keeps badge text width predictable', () => {
    expect(formatBadgeCount(10)).toBe('9+');
    expect(formatBadgeCount(42)).toBe('9+');
    expect(formatBadgeCount(999)).toBe('9+');
  });

  it('coerces non-numeric input', () => {
    expect(formatBadgeCount('5')).toBe('5');
    expect(formatBadgeCount(null)).toBe('');
    expect(formatBadgeCount(undefined)).toBe('');
    expect(formatBadgeCount(NaN)).toBe('');
  });

  it('floors fractional counts (defensive against stale stats arithmetic)', () => {
    expect(formatBadgeCount(1.7)).toBe('1');
    expect(formatBadgeCount(9.99)).toBe('9');
  });
});

describe('decideFaviconHref', () => {
  const baseDataUrl = 'data:image/png;base64,BASE';
  const paintedDataUrl = 'data:image/png;base64,PAINTED';

  it('returns the painted data URL when count > 0', () => {
    expect(
      decideFaviconHref({ count: 3, baseDataUrl, paintedDataUrl })
    ).toBe(paintedDataUrl);
  });

  it('falls back to the base data URL when count > 0 but no paint exists', () => {
    expect(
      decideFaviconHref({ count: 5, baseDataUrl, paintedDataUrl: null })
    ).toBe(baseDataUrl);
  });

  it('returns the base data URL when count is zero', () => {
    expect(
      decideFaviconHref({ count: 0, baseDataUrl, paintedDataUrl })
    ).toBe(baseDataUrl);
  });

  it('returns the base data URL when count is negative', () => {
    expect(
      decideFaviconHref({ count: -1, baseDataUrl, paintedDataUrl })
    ).toBe(baseDataUrl);
  });

  it('falls back to the static /favicon.png path when no data URLs are cached yet', () => {
    expect(
      decideFaviconHref({ count: 0, baseDataUrl: null, paintedDataUrl: null })
    ).toBe('/favicon.png');
    expect(
      decideFaviconHref({ count: 7, baseDataUrl: null, paintedDataUrl: null })
    ).toBe('/favicon.png');
  });
});
