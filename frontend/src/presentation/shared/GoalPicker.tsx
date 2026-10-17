import { useEffect, useRef, useState } from 'react';
import { MediaIcon } from './MediaIcon';

const goals = [
  { value: 'auto', label: 'おまかせ', icon: 'shuffle' },
  { value: 'discover', label: '新しい曲', icon: 'spark' },
  { value: 'familiar', label: 'いつもの好み', icon: 'repeat' },
] as const;

export function GoalPicker({ value, onChange, disabled }: { value: string; onChange: (value: string) => void; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const selected = goals.find(g => g.value === value) || goals[0];
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', outside);
    root.current?.querySelector<HTMLButtonElement>('[aria-selected=true]')?.focus();
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  const close = () => { setOpen(false); trigger.current?.focus(); };
  return <div className="goal-picker" ref={root} onKeyDown={e => {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    if (open && ['ArrowDown', 'ArrowUp'].includes(e.key)) {
      e.preventDefault(); const items = Array.from(root.current!.querySelectorAll<HTMLButtonElement>('[role=option]'));
      const index = items.indexOf(document.activeElement as HTMLButtonElement);
      items[(index + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
    }
    if (e.key === 'Tab') setOpen(false);
  }}>
    <button ref={trigger} className="goal-trigger" aria-label={`聴き方：${selected.label}`} aria-haspopup="listbox" aria-expanded={open} disabled={disabled} onClick={() => setOpen(v => !v)}><MediaIcon name={selected.icon} /><span>{selected.label}</span><MediaIcon name="down" /></button>
    {open && <div className="goal-options" role="listbox" aria-label="聴き方">{goals.map(g => <button key={g.value} role="option" aria-selected={g.value === value} onClick={() => { onChange(g.value); close(); }}><MediaIcon name={g.icon} /><span>{g.label}</span>{g.value === value && <MediaIcon name="check" />}</button>)}</div>}
  </div>;
}
