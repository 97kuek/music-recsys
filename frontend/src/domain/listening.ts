export type Tick = {wall: number; position: number};

// Preserve observed forward playback only; neither seeking nor a position jump is listening.
export function listeningDelta(previous: Tick | null, next: Tick, seeking: boolean): number {
  if (!previous || seeking) return 0;
  const wall = (next.wall - previous.wall) / 1000;
  const media = next.position - previous.position;
  return wall >= 0 && media >= 0 && media <= wall + .5 ? Math.min(wall, media) : 0;
}
