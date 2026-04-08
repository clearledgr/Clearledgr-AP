const SVG_NS = 'http://www.w3.org/2000/svg';

const ROUTE_ICON_MARKUP = {
  home: '<path d="M3.5 8.75 10 3.75l6.5 5v7a1 1 0 0 1-1 1h-3.75v-4.5h-3.5v4.5H4.5a1 1 0 0 1-1-1z"/>',
  pipeline: '<rect x="3.5" y="4" width="13" height="3" rx="1.25"/><rect x="5.5" y="8.5" width="9" height="3" rx="1.25"/><rect x="7.5" y="13" width="5" height="3" rx="1.25"/>',
  review: '<path d="M6 4.75h8"/><path d="M6 8.5h8"/><path d="M6 12.25h4.25"/><path d="M4.75 3.75h10.5a1 1 0 0 1 1 1v10.5a1 1 0 0 1-1 1H4.75a1 1 0 0 1-1-1V4.75a1 1 0 0 1 1-1Z"/><path d="m11.75 13.75 1.5 1.5 3-3"/>',
  activity: '<path d="M3.5 13.5h2.5l2-6 3.2 8 2-5h3.3"/>',
  connections: '<path d="M7 6.25h-1.5A1.75 1.75 0 0 0 3.75 8v4A1.75 1.75 0 0 0 5.5 13.75H7"/><path d="M13 6.25h1.5A1.75 1.75 0 0 1 16.25 8v4A1.75 1.75 0 0 1 14.5 13.75H13"/><path d="M6.75 10h6.5"/>',
  vendors: '<path d="M4.25 7.25 10 4l5.75 3.25v7.5H4.25z"/><path d="M7 16.75V10h6v6.75"/><path d="M2.75 16.75h14.5"/>',
  rules: '<path d="M5 5.5h10"/><path d="M5 10h10"/><path d="M5 14.5h10"/><circle cx="8" cy="5.5" r="1.5"/><circle cx="12.5" cy="10" r="1.5"/><circle cx="9.5" cy="14.5" r="1.5"/>',
  settings: '<circle cx="10" cy="10" r="2.25"/><path d="M10 3.75v1.5"/><path d="M10 14.75v1.5"/><path d="m5.58 5.58 1.06 1.06"/><path d="m13.36 13.36 1.06 1.06"/><path d="M3.75 10h1.5"/><path d="M14.75 10h1.5"/><path d="m5.58 14.42 1.06-1.06"/><path d="m13.36 6.64 1.06-1.06"/>',
  team: '<circle cx="7" cy="8" r="2.25"/><circle cx="13.25" cy="7.25" r="1.75"/><path d="M3.75 15c.7-2.1 2.3-3.25 4.75-3.25S12.55 12.9 13.25 15"/><path d="M11.75 14.75c.4-1.35 1.35-2.05 2.9-2.05 1.15 0 2.05.4 2.6 1.25"/>',
  company: '<rect x="4" y="3.75" width="12" height="12.5" rx="1.5"/><path d="M7.25 7h1.25"/><path d="M11.5 7h1.25"/><path d="M7.25 10.25h1.25"/><path d="M11.5 10.25h1.25"/><path d="M7.25 13.5h5.5"/>',
  plan: '<rect x="3.75" y="5" width="12.5" height="10" rx="1.75"/><path d="M6.5 8.25h7"/><path d="M6.5 11.25h4.5"/>',
  recon: '<path d="M5 6.5h8.5"/><path d="m11.5 4.5 2 2-2 2"/><path d="M15 13.5H6.5"/><path d="m8.5 11.5-2 2 2 2"/>',
  health: '<path d="M3.75 10h3l1.5-3.25 3.25 6.5 1.75-3.25h3"/>',
  view: '<rect x="3.75" y="4.25" width="5.25" height="5.25" rx="1"/><rect x="11" y="4.25" width="5.25" height="5.25" rx="1"/><rect x="3.75" y="11.5" width="5.25" height="5.25" rx="1"/><rect x="11" y="11.5" width="5.25" height="5.25" rx="1"/>',
  upcoming: '<path d="M10 4.5v5.5l3.5 2"/><circle cx="10" cy="10" r="6.25"/>',
  templates: '<path d="M5.5 4.25h9a1 1 0 0 1 1 1v9.5a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1v-9.5a1 1 0 0 1 1-1Z"/><path d="M7.25 8h5.5"/><path d="M7.25 11h3.5"/>',
  reports: '<path d="M6 15.5V9.5"/><path d="M10 15.5V6"/><path d="M14 15.5v-4"/><path d="M4 15.5h12"/>',
};

const ROUTE_ICON_TREATMENT = {
  connections: { scale: 1.16, strokeWidth: 1.82 },
  rules: { scale: 1.11, strokeWidth: 1.74 },
  settings: { scale: 1.18, strokeWidth: 1.84 },
  templates: { scale: 1.13, strokeWidth: 1.74 },
};

const iconCache = new Map();

function buildRouteIconUrl(iconKey) {
  const resolvedKey = ROUTE_ICON_MARKUP[iconKey] ? iconKey : 'activity';
  if (iconCache.has(resolvedKey)) return iconCache.get(resolvedKey);
  const treatment = ROUTE_ICON_TREATMENT[resolvedKey] || {};
  const scale = Number.isFinite(treatment.scale) ? treatment.scale : 1;
  const strokeWidth = Number.isFinite(treatment.strokeWidth) ? treatment.strokeWidth : 1.6;
  const translateOffset = scale === 1 ? 0 : Number((10 - (10 * scale)).toFixed(3));
  const transformedMarkup = scale === 1
    ? ROUTE_ICON_MARKUP[resolvedKey]
    : `<g transform="translate(${translateOffset} ${translateOffset}) scale(${scale})">${ROUTE_ICON_MARKUP[resolvedKey]}</g>`;
  const svg = `
    <svg xmlns="${SVG_NS}" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="#64748B" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      ${transformedMarkup}
    </svg>
  `.trim().replace(/\s+/g, ' ');
  const dataUrl = `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
  iconCache.set(resolvedKey, dataUrl);
  return dataUrl;
}

export function getRouteIconUrl(routeOrIconKey) {
  const iconKey = typeof routeOrIconKey === 'string'
    ? routeOrIconKey
    : String(routeOrIconKey?.icon || '').trim();
  return buildRouteIconUrl(iconKey || 'activity');
}

export function getPipelineViewIconUrl() {
  return buildRouteIconUrl('view');
}
