type Name = 'play' | 'pause' | 'next' | 'previous' | 'repeat' | 'down' | 'like' | 'shuffle' | 'exit' | 'forward' | 'back' | 'check' | 'refresh' | 'trash' | 'spark';
const paths: Record<Name, string> = {
  play: 'M8 5l12 7-12 7z', pause: 'M8 5v14M16 5v14',
  next: 'M5 5l11 7-11 7zM19 5v14', previous: 'M19 5L8 12l11 7zM5 5v14',
  repeat: 'M4 8h15l-4-4M20 16H5l4 4M4 8v5M20 16v-5',
  down: 'M5 9l7 7 7-7', like: 'M20 5c-3-3-6-1-8 1-2-2-5-4-8-1-5 5 3 11 8 15 5-4 13-10 8-15z',
  shuffle: 'M4 6h3l10 12h3M17 15l3 3-3 3M4 18h3l10-12h3M17 3l3 3-3 3',
  exit: 'M10 4H4v16h6M13 8l4 4-4 4M8 12h12',
  forward: 'M5 12h14M13 6l6 6-6 6', back: 'M19 12H5M11 6l-6 6 6 6',
  check: 'M5 12l4 4L19 6', refresh: 'M20 11a8 8 0 1 0-2 6M20 4v7h-7',
  trash: 'M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7',
  spark: 'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',
};
export function MediaIcon({name}: {name: Name}) {
  return <svg width="24" height="24" viewBox="0 0 24 24" fill={name === 'play' ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
