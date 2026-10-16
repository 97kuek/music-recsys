type Name = 'play' | 'pause' | 'next' | 'previous' | 'repeat' | 'down' | 'like' | 'save';
const paths: Record<Name, string> = {
  play: 'M8 5l12 7-12 7z', pause: 'M8 5v14M16 5v14',
  next: 'M5 5l11 7-11 7zM19 5v14', previous: 'M19 5L8 12l11 7zM5 5v14',
  repeat: 'M4 8h15l-4-4M20 16H5l4 4M4 8v5M20 16v-5',
  down: 'M5 9l7 7 7-7', like: 'M20 5c-3-3-6-1-8 1-2-2-5-4-8-1-5 5 3 11 8 15 5-4 13-10 8-15z',
  save: 'M12 5v14M5 12h14',
};
export function MediaIcon({name}: {name: Name}) {
  return <svg width="24" height="24" viewBox="0 0 24 24" fill={name === 'play' ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
