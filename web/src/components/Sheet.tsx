import { useEffect, useRef, type ReactNode } from "react";
import { Icon } from "./Icon";

/** A bottom sheet on phones and a centred panel on wide screens. The native <dialog> gives focus trapping, Esc and a backdrop. */
export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title?: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current!;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="sheet" onClose={onClose} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      {open && (
        <div className="sheet-body">
          <div className="sheet-grab" aria-hidden="true" />
          {title && (
            <div className="sheet-head">
              <h2>{title}</h2>
              <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="close" /></button>
            </div>
          )}
          {children}
        </div>
      )}
    </dialog>
  );
}
