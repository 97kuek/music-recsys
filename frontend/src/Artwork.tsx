import { useState } from 'react';
import type { Track } from './api';

export function Artwork({ track }: { track: Track }) {
  const [failed, setFailed] = useState<string>();
  const source = track.artwork && track.artwork !== failed ? track.artwork : '/favicon.svg';
  return <span className="track-artwork" data-artwork-item={track.item_id}><img src={source} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => { if (track.artwork) setFailed(track.artwork); }} /></span>;
}
