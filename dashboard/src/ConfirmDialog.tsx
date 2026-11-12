import { useEffect, useRef } from "react";

export function ConfirmDialog({
  title,
  onConfirm,
  onCancel,
  confirmLabel = "Confirm settings change",
  description = "This change applies to future work. Review the values before confirming.",
}: Readonly<{
  title: string;
  onConfirm: () => void;
  onCancel: () => void;
  confirmLabel?: string;
  description?: string;
}>) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="confirmation-dialog"
      onCancel={onCancel}
      aria-label={title}
    >
      <h2>{title}</h2>
      <p>{description}</p>
      <div className="run-controls">
        <button type="button" className="secondary-action" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="primary-action" onClick={onConfirm}>
          {confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
